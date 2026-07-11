# ============================================================================
# build_template — StrataBI runtime image build pipeline (REFERENCE, NOT A PRODUCT)
#
# Stands up a CodeBuild project that builds the StrataBI runtime container image
# from the account-entitled runtime wheel (fetched with `stratacli fetch-runtime`
# and uploaded to S3), using a PUBLIC base image, and pushes it to an ECR repo in
# your account. StrataCI deliberately does NOT run Docker builds — you own the
# image build. This is a starting point you can customize; it is labeled
# build_template because it is not part of the licensed StrataBI product.
# ============================================================================

locals {
  ecr_repo = var.ecr_repo_name != "" ? var.ecr_repo_name : "${var.name_prefix}-runtime"
}

data "aws_region" "here" {}

resource "aws_ecr_repository" "runtime" {
  name                 = local.ecr_repo
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_cloudwatch_log_group" "build" {
  name              = "/codebuild/${var.name_prefix}-image-build"
  retention_in_days = 30
}

resource "aws_iam_role" "build" {
  name = "${var.name_prefix}-image-build-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "codebuild.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "build" {
  name = "${var.name_prefix}-image-build-policy"
  role = aws_iam_role.build.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid      = "Logs",
        Effect   = "Allow",
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = "${aws_cloudwatch_log_group.build.arn}:*"
      },
      {
        Sid      = "EcrAuth",
        Effect   = "Allow",
        Action   = "ecr:GetAuthorizationToken",
        Resource = "*"
      },
      {
        Sid    = "EcrPush",
        Effect = "Allow",
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
          "ecr:PutImage", "ecr:BatchGetImage"
        ],
        Resource = aws_ecr_repository.runtime.arn
      },
      {
        Sid      = "ReadWheel",
        Effect   = "Allow",
        Action   = ["s3:GetObject"],
        Resource = "arn:aws:s3:::${var.wheel_bucket}/${var.wheel_key}"
      }
    ]
  })
}

resource "aws_codebuild_project" "image" {
  name         = "${var.name_prefix}-runtime-image-build"
  service_role = aws_iam_role.build.arn
  description  = "Builds the StrataBI runtime image from the entitled wheel (reference template)."

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true # required for `docker build`

    environment_variable {
      name  = "AWS_REGION"
      value = data.aws_region.here.name
    }
    environment_variable {
      name  = "ECR_URI"
      value = aws_ecr_repository.runtime.repository_url
    }
    environment_variable {
      name  = "WHEEL_BUCKET"
      value = var.wheel_bucket
    }
    environment_variable {
      name  = "WHEEL_KEY"
      value = var.wheel_key
    }
    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag
    }
    environment_variable {
      name  = "BASE_IMAGE"
      value = var.base_image
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build.name
    }
  }

  # NO_SOURCE: the build is self-contained — the buildspec pulls the wheel from S3
  # and writes its own Dockerfile. Nothing to check out.
  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/buildspec.yml")
  }
}
