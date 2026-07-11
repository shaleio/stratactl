# build_template — StrataBI runtime image build (reference, not a product)

A **starting-point** Terraform module that stands up a CodeBuild pipeline to build
the StrataBI runtime container image from your **account-entitled runtime wheel**
and push it to an ECR repo in your account, using a **public** base image.

This is deliberately **not** part of the licensed StrataBI product — StrataCI runs
your infrastructure (OpenTofu) but does **not** run Docker builds; you own the image
build. Copy this, change it, replace the base image with your private registry —
whatever fits your supply chain. It's labeled `build_template` for exactly that
reason.

## What it creates

- An **ECR repository** (`<name_prefix>-runtime`).
- A **CodeBuild project** (privileged, for Docker) that: pulls the wheel from S3,
  writes a minimal `Dockerfile FROM <public base>` that `pip install`s the wheel,
  builds, and pushes to ECR.
- A scoped **IAM role** (ECR push, CloudWatch Logs, read the one wheel object).

## How the wheel gets here

The runtime wheel is account-entitled, so you fetch it with your own credentials,
then hand it to the build:

```bash
stratacli fetch-runtime --version 1.0.0            # -> ./dist/stratabi_runtime-1.0.0-py3-none-any.whl
aws s3 cp ./dist/stratabi_runtime-1.0.0-py3-none-any.whl \
  s3://<your-bucket>/runtime/stratabi_runtime-1.0.0-py3-none-any.whl
```

## Use

```bash
terraform init
terraform apply \
  -var wheel_bucket=<your-bucket> \
  -var wheel_key=runtime/stratabi_runtime-1.0.0-py3-none-any.whl \
  -var image_tag=1.0.0

# trigger the build
aws codebuild start-build --project-name "$(terraform output -raw codebuild_project)"

# the image URI to feed the platform install
terraform output -raw image_uri
```

Hand `image_uri` to `stratacli init` as the runtime image, then
`stratacli install-stratabi` deploys the platform infrastructure that runs it.

## Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `region` | `us-east-1` | Build + ECR region. |
| `name_prefix` | `stratabi` | Names for ECR/CodeBuild/IAM. |
| `ecr_repo_name` | `<prefix>-runtime` | Override the repo name. |
| `wheel_bucket` | — (required) | Bucket holding the entitled wheel. |
| `wheel_key` | — (required) | Key of the wheel object. |
| `image_tag` | `latest` | Usually the runtime version. |
| `base_image` | `python:3.11-slim` | Public base; swap for a private/air-gapped registry. |
