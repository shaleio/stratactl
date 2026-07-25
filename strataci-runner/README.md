# StrataCI Runner — customer-account execution plane

Deploy this **once per AWS account**. It creates the ephemeral runner that StrataCTL
uses to run OpenTofu for module + platform installs — inside *your* account, with
*your* credentials. Shaleio never receives credentials here.

## What `infra/` creates

| Resource | Name | Purpose |
|----------|------|---------|
| S3 bucket | `<name_prefix>-<account>-<region>` | tofu state, DynamoDB locks, cached bundles, the CLI wheel + customer tfvars |
| DynamoDB | `<prefix>_install_registry`, `<prefix>_jobs`, `<prefix>_tf_locks` | what's installed, job status, state locks |
| IAM role | `<prefix>-runner-role` | the CodeBuild runner (scoped: bucket, tables, logs, assume-deploy) |
| IAM role | `<prefix>-deploy-role` | broad role the runner assumes **with a per-module session policy** (effective perms = role ∩ module scope) |
| CodeBuild | `<prefix>-runner` | the ephemeral runner (`python -m strata.runner`) |
| IAM policy | `<prefix>-operator` | attach to the engineer who runs StrataCTL |

## The runner needs the `strata` package

The runner runs `python -m strata.runner`, so it must be able to install `strata`.
Two ways (the buildspec handles both):

- **S3 wheel (simplest):** build the CLI wheel and pass its S3 URI as `strata_pkg`.
- **Prebaked image:** build `image/` (tofu + strata baked in), push to ECR, pass `runner_image`.

## Scaffold (sample)

```bash
export AWS_PROFILE=<your-account-profile>
PREFIX=sandbox
BUCKET=$PREFIX-$(aws sts get-caller-identity --query Account --output text)-us-east-1

# 1. build the CLI wheel (see ../build_cli_wheel.sh)
../build_cli_wheel.sh
WHEEL=$(ls ../stratactl/dist/*.whl)

# 2. apply the scaffold (strata_pkg is read only when a build runs, so a single apply is fine)
cd infra && terraform init
terraform apply -var name_prefix=$PREFIX -var strata_pkg="s3://$BUCKET/cli/$(basename $WHEEL)"

# 3. upload the wheel where the runner will look, and wire the CLI to this account
aws s3 cp "$WHEEL" "s3://$BUCKET/cli/$(basename $WHEEL)"
terraform output -raw stratactl_customer_config > ~/.stratactl/config.toml

# 4. (optional) grant yourself the operator permissions
aws iam attach-user-policy --user-name <you> --policy-arn "$(terraform output -raw operator_policy_arn)"
```

## Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `region` | `us-east-1` | |
| `name_prefix` | `strataci` | Prefixes every resource **and the bucket name** (`<prefix>-<account>-<region>`). |
| `bucket_name` | `""` | Override the bucket name; empty ⇒ the convention above. |
| `runner_image` | `aws/codebuild/amazonlinux-x86_64-standard:5.0` | Standard image installs tofu+strata via buildspec; or set a prebaked ECR image. |
| `runner_compute_type` | `BUILD_GENERAL1_SMALL` | |
| `strata_pkg` | `""` | `s3://…/strata_cli-*.whl` or a pip spec. Empty only if the image is prebaked. |
| `tofu_version` | `1.8.5` | Installed by the buildspec when not prebaked. |

> The `<name_prefix>` **must** match what StrataCTL uses for this account. Paste
> `terraform output stratactl_customer_config` into your CLI config so the bucket +
> table names line up.

## Prebaked runner image (`image/`)

For reproducible runs, bake tofu + the `strata` package into an image:

```bash
cd image
ACCT=$(aws sts get-caller-identity --query Account --output text)
aws ecr create-repository --repository-name strataci-runner 2>/dev/null || true
aws ecr get-login-password --region us-east-1 | docker login --username AWS \
  --password-stdin $ACCT.dkr.ecr.us-east-1.amazonaws.com
docker build -t $ACCT.dkr.ecr.us-east-1.amazonaws.com/strataci-runner:latest -f Dockerfile ..
docker push $ACCT.dkr.ecr.us-east-1.amazonaws.com/strataci-runner:latest
# then scaffold with: terraform apply -var runner_image=$ACCT.dkr.ecr.us-east-1.amazonaws.com/strataci-runner:latest
```

## Teardown

```bash
# uninstall any modules/platform first (so their tofu is destroyed), then:
cd infra && terraform destroy -var name_prefix=$PREFIX
```
