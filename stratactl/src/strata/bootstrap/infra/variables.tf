variable "region" {
  type        = string
  description = "AWS region for the StrataCI execution plane."
  default     = "us-east-1"
}

variable "name_prefix" {
  type        = string
  description = "Prefix for StrataCI resource names."
  default     = "strataci"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,20}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric/hyphen, start with a letter, <= 21 chars."
  }
}

variable "bucket_name" {
  type        = string
  description = "Override the StrataCI bucket name. Empty => strataci-<account_id>-<region>."
  default     = ""
}

variable "runner_image" {
  type        = string
  description = "CodeBuild container image for the runner. Use a standard AWS image (installs tofu+strata via buildspec) or your prebaked ECR image."
  default     = "aws/codebuild/amazonlinux-x86_64-standard:5.0"
}

variable "runner_compute_type" {
  type        = string
  description = "CodeBuild compute size for the runner."
  default     = "BUILD_GENERAL1_SMALL"
}

variable "strata_pkg" {
  type        = string
  description = "How the runner installs the strata package when not prebaked: an s3:// wheel URI or a pip requirement. Empty only if the image already has it. Defaults to the PyPI dist (import package `strata`); the CLI also sends this per-build (STRATA_RUNNER_PKG) so it tracks the launching version."
  default     = "stratactl"
}

variable "tofu_version" {
  type        = string
  description = "OpenTofu version the runner installs when not prebaked."
  default     = "1.8.5"
}
