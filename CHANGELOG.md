# Changelog — StrataCTL

All notable changes to StrataCTL. Format loosely follows Keep a Changelog;
versions follow the package version in `pyproject.toml`.

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
