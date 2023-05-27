# frozen_string_literal: true

#
# Cookbook:: aws-parallelcluster
# Recipe:: cuda
#
# Copyright:: 2013-2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the
# License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.

return unless nvidia_enabled?

# Cuda installer from https://developer.nvidia.com/cuda-toolkit-archive
# Cuda installer naming: cuda_11.8.0_520.61.05_linux
cuda_version = '11.8'
cuda_patch = '0'
cuda_complete_version = "#{cuda_version}.#{cuda_patch}"
cuda_version_suffix = '520.61.05'
cuda_arch = arm_instance? ? 'linux_sbsa' : 'linux'
cuda_url = "https://developer.download.nvidia.com/compute/cuda/#{cuda_complete_version}/local_installers/cuda_#{cuda_complete_version}_#{cuda_version_suffix}_#{cuda_arch}.run"
cuda_samples_version = '11.8'
cuda_samples_url = "https://github.com/NVIDIA/cuda-samples/archive/refs/tags/v#{cuda_samples_version}.tar.gz"

node.default['cluster']['nvidia']['cuda']['version'] = cuda_version
node_attributes 'Save cuda version for InSpec tests'

# Get CUDA run file
remote_file "/tmp/cuda.run" do
  source cuda_url
  mode '0755'
  retries 3
  retry_delay 5
  not_if { ::File.exist?("/usr/local/cuda-#{cuda_version}") }
end

# Install CUDA driver
bash 'cuda.run advanced' do
  user 'root'
  group 'root'
  cwd '/tmp'
  code <<-CUDA
    set -e
    ./cuda.run --silent --toolkit --samples
    rm -f /tmp/cuda.run
  CUDA
  creates "/usr/local/cuda-#{cuda_version}"
end

# Get CUDA Sample Files
remote_file "/tmp/cuda-sample.tar.gz" do
  source cuda_samples_url
  mode '0644'
  retries 3
  retry_delay 5
  not_if { ::File.exist?("/usr/local/cuda-#{cuda_version}/samples") }
end

# Unpack CUDA Samples
bash 'cuda.sample install' do
  user 'root'
  group 'root'
  cwd '/tmp'
  code <<-CUDA
    set -e
    tar xf "/tmp/cuda-sample.tar.gz" --directory "/usr/local/"
    rm -f "/tmp/cuda-sample.tar.gz"
  CUDA
  creates "/usr/local/cuda-#{cuda_version}/samples"
end