# ============================================================================
# StrataCI execution plane — customer AWS account.
#
# Provisions the resources `stratacli` and the ephemeral runner use:
#   * S3 bucket (artifacts cache, tofu state, logs)
#   * DynamoDB: install registry, jobs, tofu state lock
#   * CloudWatch log group
#   * CodeBuild project = the ephemeral runner
#   * Runner IAM role (assumed by CodeBuild) and an operator IAM policy
#
# The operator (engineer) attaches the operator policy to their own identity and
# drives everything with their own credentials. Shaleio holds no credentials here.
# ============================================================================

locals {
  account     = data.aws_caller_identity.current.account_id
  bucket_name = var.bucket_name != "" ? var.bucket_name : "${var.name_prefix}-${local.account}-${var.region}"

  install_table = "${var.name_prefix}_install_registry"
  jobs_table    = "${var.name_prefix}_jobs"
  lock_table    = "${var.name_prefix}_tf_locks"
  log_group     = "/${var.name_prefix}/runner"
  project_name  = "${var.name_prefix}-runner"
}

# ----------------------------------------------------------------------------
# S3: artifacts cache + tofu state + logs
# ----------------------------------------------------------------------------
resource "aws_s3_bucket" "strataci" {
  bucket = local.bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "strataci" {
  bucket = aws_s3_bucket.strataci.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "strataci" {
  bucket = aws_s3_bucket.strataci.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "strataci" {
  bucket                  = aws_s3_bucket.strataci.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ----------------------------------------------------------------------------
# DynamoDB: install registry + jobs + tofu state lock
# ----------------------------------------------------------------------------
resource "aws_dynamodb_table" "install_registry" {
  name         = local.install_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "module_id"

  attribute {
    name = "module_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_dynamodb_table" "jobs" {
  name         = local.jobs_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "tf_locks" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

# ----------------------------------------------------------------------------
# CloudWatch log group for the runner
# ----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "runner" {
  name              = local.log_group
  retention_in_days = 30
}

# ----------------------------------------------------------------------------
# Runner IAM role (assumed by CodeBuild)
# ----------------------------------------------------------------------------
resource "aws_iam_role" "runner" {
  name = "${var.name_prefix}-runner-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "codebuild.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "runner" {
  name = "${var.name_prefix}-runner-policy"
  role = aws_iam_role.runner.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid    = "StrataCIStateAndArtifacts",
        Effect = "Allow",
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"],
        Resource = [
          aws_s3_bucket.strataci.arn,
          "${aws_s3_bucket.strataci.arn}/*"
        ]
      },
      {
        Sid    = "StrataCITables",
        Effect = "Allow",
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"],
        Resource = [
          aws_dynamodb_table.install_registry.arn,
          aws_dynamodb_table.jobs.arn,
          aws_dynamodb_table.tf_locks.arn
        ]
      },
      {
        Sid      = "RunnerLogs",
        Effect   = "Allow",
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"],
        Resource = ["${aws_cloudwatch_log_group.runner.arn}", "${aws_cloudwatch_log_group.runner.arn}:*"]
      },
      {
        # Decision A: the runner does NOT hold broad deploy permissions. It
        # assumes the deploy role below with a session policy derived from the
        # module manifest's required_permissions, which narrows that broad role
        # down to exactly what the module declared.
        Sid      = "AssumeScopedDeployRole",
        Effect   = "Allow",
        Action   = ["sts:AssumeRole"],
        Resource = aws_iam_role.deploy.arn
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# Deploy role — broad on purpose. The runner assumes it WITH a module-scoped
# session policy (sts:AssumeRole Policy=...), and a session policy can only
# intersect/narrow these permissions. Effective perms = this role ∩ module scope.
# Only the runner role may assume it.
# ----------------------------------------------------------------------------
resource "aws_iam_role" "deploy" {
  name = "${var.name_prefix}-deploy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { AWS = aws_iam_role.runner.arn },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  name = "${var.name_prefix}-deploy-policy"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid    = "BroadDeployNarrowedBySessionPolicy",
        Effect = "Allow",
        Action = [
          "s3:*", "dynamodb:*", "lambda:*", "iam:*", "logs:*",
          "athena:*", "glue:*", "sqs:*", "events:*", "cloudwatch:*",
          "ssm:*", "states:*", "apigateway:*", "ecr:*", "kms:*"
        ],
        Resource = "*"
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# CodeBuild project = the ephemeral runner
# ----------------------------------------------------------------------------
resource "aws_codebuild_project" "runner" {
  name         = local.project_name
  description  = "StrataCI ephemeral runner: fetches a module bundle and runs OpenTofu."
  service_role = aws_iam_role.runner.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type            = "LINUX_CONTAINER"
    image           = var.runner_image
    compute_type    = var.runner_compute_type
    privileged_mode = false

    environment_variable {
      name  = "TOFU_VERSION"
      value = var.tofu_version
    }
    environment_variable {
      name  = "STRATA_PKG"
      value = var.strata_pkg
    }
    environment_variable {
      name  = "STRATA_DEPLOY_ROLE_ARN"
      value = aws_iam_role.deploy.arn
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/../image/buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.runner.name
    }
  }
}

# ----------------------------------------------------------------------------
# Operator IAM policy — attach to the engineer who runs `stratacli`.
# ----------------------------------------------------------------------------
resource "aws_iam_policy" "operator" {
  name        = "${var.name_prefix}-operator"
  description = "Permissions for an engineer running StrataCLI against this account."

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid      = "LaunchAndWatchRunner",
        Effect   = "Allow",
        Action   = ["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
        Resource = aws_codebuild_project.runner.arn
      },
      {
        Sid      = "ReadRunnerLogs",
        Effect   = "Allow",
        Action   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"],
        Resource = ["${aws_cloudwatch_log_group.runner.arn}", "${aws_cloudwatch_log_group.runner.arn}:*"]
      },
      {
        Sid    = "ReadWriteRegistries",
        Effect = "Allow",
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan"],
        Resource = [
          aws_dynamodb_table.install_registry.arn,
          aws_dynamodb_table.jobs.arn
        ]
      },
      {
        Sid      = "ArtifactBucket",
        Effect   = "Allow",
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
        Resource = [aws_s3_bucket.strataci.arn, "${aws_s3_bucket.strataci.arn}/*"]
      },
      {
        Sid      = "Identity",
        Effect   = "Allow",
        Action   = ["sts:GetCallerIdentity"],
        Resource = "*"
      }
    ]
  })
}
