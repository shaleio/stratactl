# StrataCTL

The local command-line tool for operators. It installs, updates, and removes
StrataBI modules and the StrataBI platform in **your** AWS account, fetches the
account-entitled runtime, and version-controls dashboards. It runs on your machine
with your own AWS credentials — it is not a hosted service.

The same `strata` package is also the **runner program** that executes inside the
ephemeral CodeBuild runner (`python -m strata.runner`).

## Install

```bash
python3 -m venv ~/.venvs/strata && source ~/.venvs/strata/bin/activate
pip install -e .            # from this directory; exposes the `stratactl` command
stratactl --help
```

## Configure

`strata init` collects a deployment's configuration into `strata.yaml` (the source
of truth) and generates the Terraform variable files — you don't hand-edit `.tfvars`.

```bash
stratactl init                     # interactive; re-run to reuse answers + fill gaps
stratactl init --non-interactive   # config + defaults only (automation)
```

Settings resolve in this precedence (highest first): **CLI flag → env (`STRATA_*`)
→ `./.stratactl.toml` → `~/.stratactl/config.toml` → AWS SSM (`/strataci/hq/*`) →
packaged defaults**. Point it at a customer account by pasting the
`stratactl_customer_config` output from `strataci-runner` into your config, and set
your license token:

```bash
export STRATA_HQ_TOKEN=<your license token>
```

AWS credentials come from your normal profile/keys (`--profile`, `AWS_PROFILE`, env).
What you can deploy is governed by your own IAM in the target account.

## Commands

```bash
stratactl init                            # configure a deployment (writes strata.yaml + tfvars)
stratactl fetch-runtime --version 1.0.0   # pull the account-entitled runtime wheel -> ./dist (verified)
stratactl install <module>                # entitlement -> runner -> tofu apply, in your account
stratactl install-stratabi                # install the StrataBI platform (Enterprise)
stratactl install-stratabi-dev            # install the Developer/Community edition
stratactl update <module> --to <version>
stratactl uninstall <module>              # runs the module's tofu destroy (always available)
stratactl list                            # what's installed + delivery mode
stratactl logs <job-id>                   # tail/replay a runner build
stratactl dashboards push <file>          # validate + upload a dashboard to analyst/dashboards/
stratactl dashboards pull|list|diff       # dashboards-as-code: git-sync the registered set
```

`--source stratahq` (default) fetches a signed bundle from Shaleio's control plane;
`--source customer_s3` installs from your own bucket (offline / air-gapped).

`dashboards` resolves the system bucket + git-registry table from the recorded
`dev install` (no manual config). A plain `push <file>` needs only the bucket and
validates against the packaged schema; the git-sync flow (`--new`/`pull`/`diff`) uses
the registry for version control.

## End-to-end: deploy StrataBI Enterprise

```bash
# 0. one-time per account: scaffold the runner  (see ../strataci-runner)
# 1. build the runtime container image          (see ../build_template)  -> ECR image URI
# 2. configure + deploy:
export STRATA_HQ_TOKEN=<token>
stratactl init            # set stratabi_image=<ECR URI>, vpc_id, subnets, allowed_ip_cidrs
stratactl install-stratabi
stratactl list
```

Your `strata init` values ride through to the ephemeral runner automatically (the
CLI uploads them; the runner applies them over the bundle's baked defaults), so the
platform deploys with your networking/image without any manual `.tfvars` on the runner.

## Layout

| Path | What |
|------|------|
| `src/strata/commands/` | CLI subcommands (`init`, `install`, `fetch_runtime`, `dashboards`, …) |
| `src/strata/hq/` | StrataHQ client (SigV4-signed entitlement calls) |
| `src/strata/runner/` | the program that runs inside CodeBuild |
| `src/strata/install/` | the `strata init` wizard (schema / prompt / store / tfvars) |
| `src/strata/artifacts/` | bundle download / verify / manifest |
| `tests/` | unit tests (`pytest`) |
