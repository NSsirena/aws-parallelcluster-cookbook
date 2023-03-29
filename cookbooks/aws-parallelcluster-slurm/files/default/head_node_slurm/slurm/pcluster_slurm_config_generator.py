# Copyright 2021 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file.
# This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied.
# See the License for the specific language governing permissions and limitations under the License.
import argparse
import functools
import json
import logging

import re
from os import makedirs, path
from config_renderer import QueueRenderer
from socket import gethostname
from urllib.parse import ParseResult, urlparse

import requests
import yaml
from jinja2 import FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment

METADATA_REQUEST_TIMEOUT = 60
log = logging.getLogger()
instance_types_data = {}


class CriticalError(Exception):
    """Critical error for the script."""

    pass


def generate_slurm_config_files(
    output_directory,
    template_directory,
    input_file,
    instance_types_data_path,
    dryrun,
    no_gpu,
    compute_node_bootstrap_timeout,
    realmemory_to_ec2memory_ratio,
    slurmdbd_user,
    cluster_name,
):
    """
    Generate Slurm configuration files.

    For each queue, generate slurm_parallelcluster_{QueueName}_partitions.conf
    and slurm_parallelcluster_{QueueName}_gres.conf, which contain node info.

    Generate slurm_parallelcluster.conf and slurm_parallelcluster_gres.conf,
    which includes queue specifc configuration files.

    slurm_parallelcluster.conf is included in main slurm.conf
    and slurm_parallelcluster_gres.conf is included in gres.conf.
    """
    # Make output directories
    output_directory = path.abspath(output_directory)
    pcluster_subdirectory = path.join(output_directory, "pcluster")
    makedirs(pcluster_subdirectory, exist_ok=True)
    env = _get_jinja_env(template_directory)

    cluster_config = _load_cluster_config(input_file)
    head_node_config = _get_head_node_config()
    queues = cluster_config["Scheduling"]["SlurmQueues"]

    global instance_types_data  # pylint: disable=C0103,W0603 (global-statement)
    with open(instance_types_data_path, encoding="utf-8") as instance_types_input_file:
        instance_types_data = json.load(instance_types_input_file)

    # Generate slurm_parallelcluster_{QueueName}_partitions.conf and slurm_parallelcluster_{QueueName}_gres.conf
    is_default_queue = True  # The first queue in the queues list is the default queue
    for queue in queues:
        for file_type in ["partition", "gres"]:
            _generate_queue_config(
                queue["Name"],
                queue,
                is_default_queue,
                file_type,
                pcluster_subdirectory,
                realmemory_to_ec2memory_ratio,
                dryrun,
                no_gpu=no_gpu,
            )
        is_default_queue = False

    # Generate include files for slurm configuration files
    for template_name in [
        "slurm_parallelcluster.conf",
        "slurm_parallelcluster_gres.conf",
        "slurm_parallelcluster_cgroup.conf",
        "slurm_parallelcluster_slurmdbd.conf",
    ]:
        _generate_slurm_parallelcluster_configs(
            queues,
            head_node_config,
            cluster_config["Scheduling"]["SlurmSettings"],
            cluster_name,
            slurmdbd_user,
            template_name,
            compute_node_bootstrap_timeout,
            env,
            output_directory,
            dryrun,
        )

    log.info("Finished.")


def _load_cluster_config(input_file_path):
    """
    Load queues_info and add information used to render templates.

    :return: queues_info containing id for first queue, head_node_hostname and queue_name
    """
    with open(input_file_path, encoding="utf-8") as input_file:
        return yaml.load(input_file, Loader=yaml.SafeLoader)


def _get_head_node_config():
    return {
        "head_node_hostname": gethostname(),
        "head_node_ip": _get_head_node_private_ip(),
    }


def _get_head_node_private_ip():
    """Get head node private ip from EC2 metadata."""
    return _get_metadata("local-ipv4")


def _generate_queue_config(
    queue_name,
    queue_config,
    is_default_queue,
    file_type,
    output_dir,
    realmemory_to_ec2memory_ratio,
    dryrun,
    no_gpu=False,
):
    log.info("Generating slurm_parallelcluster_%s_%s.conf", queue_name, file_type)
    renderer = QueueRenderer(
        queue_config,
        no_gpu,
        realmemory_to_ec2memory_ratio,
        instance_types_data,
        conf_type=file_type,
        default=is_default_queue,
    )
    rendered_config = renderer.render_config()

    if not dryrun:
        filename = path.join(output_dir, f"slurm_parallelcluster_{queue_name}_{file_type}.conf")
        if file_type == "gres" and no_gpu:
            _write_rendered_template_to_file(
                "# This file is automatically generated by pcluster\n"
                "# Skipping GPUs configuration because Nvidia driver is not installed",
                filename,
            )
        else:
            _write_rendered_template_to_file(rendered_config, filename)


def _generate_slurm_parallelcluster_configs(
    queues,
    head_node_config,
    scaling_config,
    cluster_name,
    slurmdbd_user,
    template_name,
    compute_node_bootstrap_timeout,
    jinja_env,
    output_dir,
    dryrun,
):
    log.info("Generating %s", template_name)
    rendered_template = jinja_env.get_template(f"{template_name}").render(
        queues=queues,
        head_node_config=head_node_config,
        scaling_config=scaling_config,
        cluster_name=cluster_name,
        slurmdbd_user=slurmdbd_user,
        compute_node_bootstrap_timeout=compute_node_bootstrap_timeout,
        output_dir=output_dir,
    )
    if not dryrun:
        filename = f"{output_dir}/{template_name}"
        _write_rendered_template_to_file(rendered_template, filename)


def _get_jinja_env(template_directory):
    """Return jinja environment with trim_blocks/lstrip_blocks set to True."""
    file_loader = FileSystemLoader(template_directory)
    # A nosec comment is appended to the following line in order to disable the B701 check.
    # The contents of the default templates are known and the input configuration data is
    # validated by the CLI.
    env = SandboxedEnvironment(loader=file_loader, trim_blocks=True, lstrip_blocks=True)  # nosec nosemgrep
    env.filters["sanify_name"] = lambda value: re.sub(r"[^A-Za-z0-9]", "", value)
    env.filters["uri_host"] = functools.partial(_parse_uri, attr="host")
    env.filters["uri_port"] = functools.partial(_parse_uri, attr="port")

    return env


def _parse_netloc(uri: str, uri_parse: ParseResult, attr: str) -> str:
    try:
        netloc = uri_parse.netloc
    except ValueError as e:
        error_msg = f"Failure to parse uri with error '{str(e)}'. Please review the provided URI ('{uri}')"
        log.critical(error_msg)
        raise CriticalError(error_msg)
    if not netloc:
        error_msg = f"Invalid URI specified. Please review the provided URI ('{uri}')"
        log.critical(error_msg)
        raise CriticalError(error_msg)
    if attr == "host":
        ret = uri_parse.hostname
    elif attr == "port":
        ret = uri_parse.port
        # Provide default MySQL port if port is not explicitely set
        if not ret:
            ret = "3306"
    return ret


def _parse_uri(uri, attr) -> str:
    """Get a host from a URI/URL using urlparse."""
    uri_parse = urlparse(uri)
    if not uri_parse.netloc:
        # This happens if users provide an URI without explicit scheme followed by ://
        # (for example 'test.example.com:3306' instead of 'mysql://test.example.com:3306`).
        uri_parse = urlparse("//" + uri)

    # Parse netloc to get hostname or port
    return _parse_netloc(uri, uri_parse, attr)


def _write_rendered_template_to_file(rendered_template, filename):
    log.info("Writing contents of %s", filename)
    with open(filename, "w", encoding="utf-8") as output_file:
        output_file.write(rendered_template)


def _setup_logger():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - [%(name)s:%(funcName)s] - %(levelname)s - %(message)s"
    )


def _get_metadata(metadata_path):
    """
    Get EC2 instance metadata.

    :param metadata_path: the metadata relative path
    :return the metadata value.
    """
    try:
        token = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
            timeout=METADATA_REQUEST_TIMEOUT,
        )
        headers = {}
        if token.status_code == requests.codes.ok:
            headers["X-aws-ec2-metadata-token"] = token.content
        metadata_url = f"http://169.254.169.254/latest/meta-data/{metadata_path}"
        metadata_value = requests.get(metadata_url, headers=headers, timeout=METADATA_REQUEST_TIMEOUT).text
    except Exception as e:
        error_msg = f"Unable to get {metadata_path} metadata. Failed with exception: {e}"
        log.critical(error_msg)
        raise CriticalError(error_msg)

    log.debug("%s=%s", metadata_path, metadata_value)
    return metadata_value


def main():
    def memory_ratio_float(arg):
        """Type function for realmemory_to_ec2memory_ratio with custom lower and upper bounds."""
        # We cannot allow 0 as minimum value because `RealMemory=0` is not valid in Slurm.
        # We put a minimum value= 0.1. It doesn't make sense to put such a low value anyway.
        min_value = 0.1
        max_value = 1.0
        try:
            f = float(arg)
        except ValueError:
            raise argparse.ArgumentTypeError("The argument must be a floating point number")
        if f < min_value or f > max_value:
            raise argparse.ArgumentTypeError(
                f"The argument must be greater or equal than {str(min_value)} and less "
                f"or equal than {str(max_value)}"
            )
        return f

    try:
        _setup_logger()
        log.info("Running ParallelCluster Slurm Config Generator")
        parser = argparse.ArgumentParser(description="Take in slurm configuration generator related parameters")
        parser.add_argument(
            "--output-directory", help="The output directory for generated slurm configs", required=True
        )
        parser.add_argument("--template-directory", help="The directory storing slurm config templates", required=True)
        parser.add_argument("--input-file", help="Yaml file containing pcluster configuration file", required=True)
        parser.add_argument("--instance-types-data", help="JSON file containing info about instance types")
        parser.add_argument(
            "--dryrun",
            action="store_true",
            help="dryrun",
            required=False,
            default=False,
        )
        parser.add_argument(
            "--no-gpu",
            action="store_true",
            help="no gpu configuration",
            required=False,
            default=False,
        )
        parser.add_argument(
            "--compute-node-bootstrap-timeout",
            type=int,
            help="Configure ResumeTimeout",
            required=False,
            default=1800,
        )
        parser.add_argument(
            "--realmemory-to-ec2memory-ratio",
            type=memory_ratio_float,
            help="Configure ratio between RealMemory and memory advertised by EC2",
            required=True,
        )
        parser.add_argument("--slurmdbd-user", help="User for the slurmdbd service.", required=True)
        parser.add_argument("--cluster-name", help="Name of the cluster.", required=True)
        args = parser.parse_args()
        generate_slurm_config_files(
            args.output_directory,
            args.template_directory,
            args.input_file,
            args.instance_types_data,
            args.dryrun,
            args.no_gpu,
            args.compute_node_bootstrap_timeout,
            args.realmemory_to_ec2memory_ratio,
            args.slurmdbd_user,
            args.cluster_name,
        )
    except Exception as e:
        log.exception("Failed to generate slurm configurations, exception: %s", e)
        raise


if __name__ == "__main__":
    main()
