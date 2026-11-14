# Changelog — StrataCTL

All notable changes to StrataCTL. Format loosely follows Keep a Changelog;
versions follow the package version in `pyproject.toml`.

## [1.7.8] — 2026-11-14

Upgrade safety: an update can no longer pick up a different network from the operator's
working directory, and no apply can silently replace load-bearing infrastructure.

### Added
- **Environment config** — `s3://<strataci-bucket>/config/env/<env>.auto.tfvars.json` holds
  the account's network + identity values (`name_prefix`, `region`, `vpc_id`,
  `public_subnet_ids`, `enable_private_subnets`, `private_subnet_ids`) once per `--env`. The
  runner loads it last (`zzz-env.auto.tfvars.json`), so every module shares it and it wins
  over module tfvars. Seeded by the first install.
- **`stratactl env show | set | import | validate | export`.** `import` reads the values from
  the deployed platform's tofu state (the migration path for pre-1.7.8 installs); `export`
  writes the file for a BYO CI/CD pipeline.
- **Network preflight** before every install/update: VPC and subnets exist, subnets are in
  the VPC, public subnets span ≥2 AZs (warns and skips without `ec2:Describe*`).
- **Plan guard** — the runner runs `tofu show -json` on the plan and refuses to apply when
  protected resources (VPC-bound security groups, ALB/listeners/target groups, ECS
  cluster/service, S3 buckets, DynamoDB tables, Glue catalog, KMS, ECR, Cognito, …) would be
  destroyed or replaced. `--allow-replace` opts in. Also available standalone for BYO
  pipelines: `stratactl plan-guard plan.json`.

### Changed
- **`update` reuses the stored configuration** and no longer re-reads `./strata.yaml`.
  `--reconfigure` re-reads it, shows the diff, validates the network, and asks before writing.
  `install` refuses to change an existing environment's network without `--reconfigure`.
- `update` on a pre-1.7.8 install whose stored tfvars carry network values stops and asks
  for `stratactl env import` first.

### Fixed
- The failed-install hint printed `stratactl update-stratabi`; it now prints the real command.

## [1.7.7] — 2026-11-14

### Fixed
- **`stratactl bootstrap` now creates a privileged runner.** The infra packaged in the CLI
  (`strata/bootstrap/infra`) still had `privileged_mode = false`; the 1.7.1 fix only landed in
  the repo copy (`strataci-runner/infra`), which `bootstrap` does not apply. Without it the
  platform module's in-account image build (Docker in CodeBuild) cannot run, so
  `install-stratabi` / `update stratabi` never produce a new image.

> Existing accounts: `pipx upgrade stratactl` then re-run `stratactl bootstrap` to flip the
> runner to privileged (an in-place CodeBuild project update; state and tables are kept).

## [1.7.6] — 2026-11-14

Platform image-roll + deploy-role fixes surfaced during the sandbox 1.7.5 refresh.

### Fixed
- **Runner threads the resolved version into tofu** (`runner/main.py`): sets
  `TF_VAR_runtime_version` from the entitled version so the platform image tag becomes
  `repo:<version>` (not the constant `latest`). Previously the ECS task-definition image
  string never changed on an update, so Fargate never rolled to the newly-built image and
  `image_build` didn't even re-trigger. Now an update rebuilds + rolls on its own.
- **StrataCI deploy role widened** (`bootstrap/infra` + `strataci-runner/infra`): added
  `scheduler:*`, `ses:*`, `sagemaker:*` to the broad base role so modules that create
  EventBridge Scheduler / SES / SageMaker resources (StrataNotebook, StrataMail) can deploy.
  The base role is the ceiling the session policy narrows, so these must be present in the base.

> NOTE: the CodeBuild runner pip-installs `stratactl` from PyPI (pinned via `STRATA_PKG`),
> so these fixes only take effect once 1.7.6 is published to PyPI and the operator upgrades
> their local CLI (`pipx upgrade stratactl`) and re-runs `stratactl bootstrap` (to pick up the
> deploy-role policy — or keep the one-off `strataci-extra-services` inline policy until then).

## [1.7.1] — 2026-11-09

Enterprise install fixes from the first customer-style install (E-series).

### Fixed
- **Version is read dynamically** from `strata.__version__` (pyproject was hardcoded, so
  builds shipped the wrong version even after a bump).
- **Runner CodeBuild is now `privileged_mode = true`** so the platform install can build
  the StrataBI image in-account and push it to the customer ECR (E12).
- **Post-install health gate (E13):** after apply, the runner waits for the ECS service to
  reach steady state and prints the URL; if it can't start it fails with the stopped-task
  reason instead of reporting SUCCEEDED over a dead service.
- **`init` no longer requires a container image URL (E2):** `stratabi_image` is now an
  optional override; by default the platform module builds and pushes the image for you.

### Added (install UX)
- **`init` network preflight (E1):** discovers the account's VPC + subnets, pre-fills them,
  and offers to create a default VPC if there's none (ALB needs >=2 AZs).
- **Inbound defaults to the caller's /32 (E4)** instead of `0.0.0.0/0`.
- **Ctrl-C during the wizard saves your answers (E3)** so a re-run resumes.
- **A failed install prints next steps (E11):** safe re-run, `stratactl logs <job_id>`,
  and the common causes.

### Note
- The runner `privileged_mode` change is in `strataci-runner/infra`; re-run
  `stratactl bootstrap` in the customer account to apply it.

## [1.7.0] — 2026-11-07

Fixes from the StrataBI Developer Edition new-user install test.

### Fixed
- **Runner pins the `strata` package to the launching CLI's version** by default
  (`STRATA_PKG=stratactl==<local version>`) instead of installing the latest from PyPI,
  so the CodeBuild runner can't silently drift from the operator's CLI. Override still
  available via `STRATA_RUNNER_PKG`.
- **Customer tfvars are filtered to the variables each module declares** before being
  written next to a module's OpenTofu. The shared install schema emitted enterprise-only
  keys (`enable_rbac`, `enable_ai`, `vpc_id`, …) that a developer module doesn't declare,
  producing "undeclared variable" warnings; those keys are now dropped (and logged).
- **`dev configure-local` writes an ASCII-only `.env` header.** The em dash rendered as
  mojibake (`â€”`) in Windows PowerShell 5.1.

### Changed
- The runner prints a prominent **warning** when a bundle's signature is not verified
  (no signature/public key configured), and points at `STRATA_REQUIRE_SIGNATURE=1` to
  enforce signing. The SHA-256 is still always checked.

## [1.0.4] — 2026-08-01

### Added
- **`bootstrap --destroy`**: full StrataCI teardown — pulls the durable state from S3
  and `tofu destroy`s **locally** (a teardown-only override disables `prevent_destroy`
  and force-empties the bucket so it completes). With `dev uninstall`, this is the
  complete "remove everything." Guarded by an account-id confirmation.
- **Durable bootstrap state**: after `bootstrap` applies, the local StrataCI tofu
  state is copied to `s3://<strataci-bucket>/bootstrap/terraform.tfstate` so teardown
  isn't tied to the machine that ran the install (groundwork for `bootstrap destroy`).
- **Forgiving argument order**: `--profile`/`--region` now work *after* the
  subcommand too (`stratactl bootstrap --profile X --region Y`), not just before.
  This was the "bootstrap command was wrong in docs" footgun and the one AI-written
  commands hit. argv is normalized before parsing.

## [1.0.3] — 2026-08-01

### Fixed
- **Python 3.13 / 3.14 compatibility**: `strata.bootstrap` no longer imports
  `importlib.abc.Traversable` at runtime (removed in 3.13). It's now a type-only
  import, so `bootstrap` and every command import cleanly on 3.13. (`requires-python`
  is `>=3.11`, so 3.13 must work.)

## [1.0.2] — 2026-07-29

### Added
- **`stratactl dashboards`** made usable end-to-end: a generic, bucket-only `push`
  (validate against the packaged dashboard schema → flat-upload to
  `analyst/dashboards/<name>.json`), plus the git-sync flow (`--new`/`list`/`pull`/
  `diff`) for version-controlled dashboards. The git-registry table is only required
  for the git-sync actions, so the plain funnel works without it.
- Bundled `dashboard.schema.json` as package data and enabled schema validation on
  push (auto-detects the 2020-12 draft). Added `jsonschema` dependency.

### Fixed
- **Zero-config dashboards**: resolve the system bucket + git-registry table from the
  recorded `dev install` tofu outputs, so `dashboards` needs no manual `[dashboards]`
  config or `.env`.
- `dashboards push`: no longer crashes writing a manifest into a non-existent local
  dir — the manifest is only persisted for a real git working dir or a git-flow op.
- **`dev configure-local`**: unwrap `tofu output -json` `{value,…}` wrappers (the
  generated `.env` was a stringified dict), and write `AWS_DEFAULT_REGION` as well as
  `AWS_REGION` so older botocore resolves a client region (fixes app `NoRegionError`).

## [1.0.1] — 2026-07-26

### Fixed
- **Bootstrap**: the runner buildspec is now inlined into the packaged infra
  (`bootstrap/infra/buildspec.yml`, referenced via `${path.module}/buildspec.yml`) so
  `tofu` no longer fails on an out-of-module `file()` path.
- **Bootstrap**: pass `AWS_PROFILE`/`AWS_REGION` into the `tofu` subprocess, so StrataCI
  is created in the account the CLI validated instead of silently falling back to the
  default provider chain.
- **Runner**: install the `strata` package on a standard CodeBuild image via a per-build
  `STRATA_PKG` (defaults to the `stratactl` PyPI dist; override with `STRATA_RUNNER_PKG`);
  fixed the inconsistent empty `strata_pkg` default that broke installs on a standard image.
- **Runner**: inject an `s3` backend when a module bundle declares none, so tofu state
  persists in the customer bucket across the ephemeral CodeBuild runner (fixes "already
  exists" errors on re-apply / update / uninstall).
- **`dev configure-local`**: writes the data plane's `env_file_preview` block, so the
  generated `.env` carries the full `STRATABI_*` settings (previously only region/profile).

### Changed
- `bootstrap_infra_files()` also lists `.yml`/`.yaml` packaged assets.
- Synced `strata.__version__` (was stale at `0.1.0`) to the release version.

## [1.0.0] — 2026-07-26

### Added
- `stratactl bootstrap` — stand StrataCI up in a customer AWS account (validates
  creds/tofu, extracts version-matched packaged infra into a `platformdirs` working
  dir; **dry-run by default**, applies only with `--run --yes`/confirmation).
- `stratactl dev` group — `install` (HQ-free), `status`, `outputs`, `logs`,
  `uninstall`, `configure-local` (writes non-secret `.env`, never AWS keys).
- Public-URL artifact source (`KIND_PUBLIC`): install the Developer Edition from a
  public GitHub release with **no StrataHQ contact**.
- Packaged StrataCI bootstrap infrastructure as immutable package data
  (`strata/bootstrap/infra/`).
- Release CI scaffold; `dev` extra (pytest/build/twine); `platformdirs` dependency.

### Changed
- Distribution name `strata-cli` → **`stratactl`** (intended PyPI name).
- Richer `pyproject` metadata + broadened package-data.

### Housekeeping
- Added root `.gitignore`; stopped tracking bytecode/egg-info (index only).
