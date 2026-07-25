# Security Policy — StrataCLI

## Reporting a vulnerability

Email **info@shaleio.com** with details and reproduction steps. Please do **not** open
a public issue for security reports. No paid bug-bounty program.

## Threat model

StrataCLI runs on your machine with **your** AWS credentials and orchestrates
infrastructure in **your** AWS account.

- `stratacli bootstrap` is the only command that runs OpenTofu locally; it is a **dry
  run by default** and applies only with `--run` and explicit confirmation (`--yes`).
- All other commands launch work in an **ephemeral CodeBuild runner** inside the target
  account. StrataCLI does not exfiltrate credentials or data.
- Developer Edition installation never contacts StrataHQ.
- Artifact integrity: the runner verifies a bundle's SHA-256 always, and its signature
  when signature verification is required. Custom/unsigned URLs are never treated as
  official signed releases.

## Secrets

Never commit AWS credentials, `.env`, `terraform.tfvars`, or Terraform state (all
git-ignored). StrataCLI stores only non-secret config/state under platform-appropriate
directories (via `platformdirs`).

## Supported versions

Fixes land on `main`; no back-port guarantee for older tags.
