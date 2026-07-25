variable "region" {
  type        = string
  description = "AWS region to build + host the image in."
  default     = "us-east-1"
}

variable "name_prefix" {
  type        = string
  description = "Prefix for the ECR repo, CodeBuild project, and IAM role names."
  default     = "stratabi"
}

variable "ecr_repo_name" {
  type        = string
  description = "ECR repository name. Empty => <name_prefix>-runtime."
  default     = ""
}

variable "wheel_bucket" {
  type        = string
  description = "S3 bucket holding the account-entitled runtime wheel (upload it there after `stratactl fetch-runtime`)."
}

variable "wheel_key" {
  type        = string
  description = "S3 key of the runtime wheel, e.g. runtime/stratabi_runtime-1.0.0-py3-none-any.whl."
}

variable "image_tag" {
  type        = string
  description = "Tag to push, e.g. the runtime version or 'latest'."
  default     = "latest"
}

variable "base_image" {
  type        = string
  description = "PUBLIC base image for the runtime. Override for a private/air-gapped registry."
  default     = "python:3.11-slim"
}
