# frozen_string_literal: true

# Copyright:: 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at http://aws.amazon.com/apache2.0/
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.

resource_name :s3_object
provides :s3_object
unified_mode true

# Resource to retrieve an S3 Object either using the S3 or HTTPS URI format
property :url, required: true,
         description: 'Source URI of the remote file'
property :destination, required: true,
         description: 'destination path where to store the file'

default_action :get

action :get do
  if !new_resource.url.empty? && !new_resource.destination.empty?
    source_url = new_resource.url
    local_path = new_resource.destination
    Chef::Log.debug("Retrieving S3 Object from #{source_url} to #{local_path} using S3 protocol")

    # if running a test skip credential check
    no_sign_request = kitchen_test? ? "--no-sign-request" : ""

    if source_url.start_with?("s3")
      # download file using s3 protocol
      fetch_command = "#{node['cluster']['cookbook_virtualenv_path']}/bin/aws s3 cp" \
                  " --region #{node['cluster']['region']}" \
                  " #{no_sign_request}" \
                  " #{source_url}" \
                  " #{local_path}"

      Chef::Log.warn("executing command #{fetch_command} ")
      execute "retrieve_object_with_s3_protocol" do
        command fetch_command
        retries 3
        retry_delay 5
      end
    else
      Chef::Log.debug("Retrieving S3 Object from #{source_url} to #{local_path} using HTTPS protocol")

      # download file using https protocol
      remote_file "retrieve_object_with_https_protocol" do
        path local_path
        source source_url
        retries 3
        retry_delay 5
      end
    end
  else
    Chef::Log.warn("Either source or destination is not defined: #{new_resource.url} to #{new_resource.destination}")
  end

end
