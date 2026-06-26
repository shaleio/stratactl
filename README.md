# Strata orchestrator (StrataCLI · StrataCI · StrataHQ)

A free installer/orchestrator layer for StrataBI modules. An engineer runs a
local CLI; the actual work happens in an **ephemeral CodeBuild runner inside the
customer's own AWS account**; Shaleio's control plane only gates licensed
artifacts. No long-lived Shaleio credentials ever enter a customer account.

```
stratacli install iceberg-explorer --source stratahq --env dev
```

## The three layers

| Dir | Layer | What it is |
|---|---|---|
| `stratacli/` | **StrataCLI** | Local Python CLI (+ the runner program — one shared `strata` package). |
| `strataci-runner/` | **StrataCI Runner** | The ephemeral execution surface: CodeBuild `buildspec`/`Dockerfile` + the customer-account Terraform (S3, DynamoDB, IAM, CodeBuild project). |
| `stratahq/` | **StrataHQ** | Shaleio control plane: artifacts bucket + the **DynamoDB entitlement gate** + entitlement Lambda/API + SSM discovery. |

## Credential boundary (the core idea)

- **Execution plane = customer account, driven by the operator's own credentials.**
  The CLI calls `codebuild:StartBuild` with the engineer's creds; the runner role
  is a customer-account role.
- **Control plane = Shaleio account, request/response only.** StrataHQ answers
  "entitled?" and returns a short-lived, artifact-scoped **presigned URL**. It
  never holds customer credentials and never reaches into customer accounts.

## Install flow

1. `stratacli install <module>` resolves config (5-layer) and the artifact source.
2. `source=customer_s3` → no HQ call. `source=stratahq` → POST `/v1/entitlement`
   (token-gated) → the DynamoDB gate returns allow + presigned URL + sha256.
3. CLI creates a job record and launches the CodeBuild runner with the job spec.
4. Runner: download → **verify sha256** → safe-extract → validate manifest →
   `tofu init/plan/apply` against the customer's S3 backend + Dynamo lock.
5. Runner caches the verified bundle to customer S3 (so uninstall/update work
   even if HQ is unreachable or the entitlement later lapses), writes outputs +
   updates the registry.
6. CLI streams CloudWatch logs to the terminal until the build is terminal.

`uninstall` runs `tofu destroy` from the cached bundle for the installed version;
`update` applies a new version against the same state; `list`/`logs` read the
registries.

## Quickstart (dev)

```bash
# 1. StrataHQ (Shaleio account) — once
cd stratahq/infra && tofu init && tofu apply
python ../seed/seed.py --table "$(tofu output -raw entitlements_table)" \
                       --file ../seed/entitlements.example.json

# 2. StrataCI execution plane (customer account) — once per account
cd ../../strataci-runner/infra && tofu init && tofu apply
#   attach the operator policy to yourself; paste `stratacli_customer_config`
#   into ~/.stratacli/config.toml

# 3. StrataCLI (operator laptop)
cd ../../stratacli && pip install -e .
export STRATA_HQ_TOKEN=...        # the raw token you seeded in step 1
stratacli init                    # show resolved config + expected resources
stratacli install iceberg-explorer --source stratahq --env dev
stratacli list
```

## Configuration

The StrataHQ location is a literal in exactly one place —
`stratacli/src/strata/config/defaults.toml`. Everything resolves through
`strata.config.provider.load_settings()` in this precedence (highest first):
CLI flag → env (`STRATA_*`) → local file (`./.stratacli.toml`, `~/.stratacli/config.toml`)
→ AWS SSM (`/strataci/hq/*`) → packaged defaults.

## Status

MVP scaffold. See `OPEN_QUESTIONS.md` for assumptions made and decisions still
open (notably runner IAM scoping, artifact signing, and token lifecycle).
