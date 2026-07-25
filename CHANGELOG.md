# Changelog — StrataCTL

All notable changes to StrataCTL. Format loosely follows Keep a Changelog;
versions follow the package version in `pyproject.toml`.

## [Unreleased]

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
