# Strata orchestrator (StrataCTL · StrataCI · StrataHQ)

A free installer/orchestrator layer for StrataBI modules. An engineer runs a
local CLI; the actual work happens in an **ephemeral CodeBuild runner inside the
customer's own AWS account**; Shaleio's control plane only gates licensed
artifacts. No long-lived Shaleio credentials ever enter a customer account.

```
stratactl install iceberg-explorer --source stratahq --env dev
```

## What's in this repo

| Dir | Layer | What it is | Docs |
|---|---|---|---|
| `stratactl/` | **StrataCTL** | Local Python CLI (+ the runner program — one shared `strata` package). | [README](stratactl/README.md) |
| `strataci-runner/` | **StrataCI Runner** | The ephemeral execution surface: CodeBuild `buildspec`/`Dockerfile` + the customer-account Terraform (S3, DynamoDB, IAM, CodeBuild project). | [README](strataci-runner/README.md) |
| `build_template/` | **Image build (reference)** | Optional CodeBuild pipeline that builds the StrataBI runtime image from the entitled wheel and pushes it to your ECR. | [README](build_template/README.md) |

> **StrataHQ** — Shaleio's control plane (artifacts bucket + the DynamoDB entitlement
> gate + entitlement Lambda/API + SSM discovery) — lives in its own internal repo.

### End-to-end standup (Enterprise)

1. **Scaffold the runner** once per account — [`strataci-runner`](strataci-runner/README.md).
2. **Build the runtime image** → ECR — [`build_template`](build_template/README.md).
3. **Configure + deploy** — [`stratactl`](stratactl/README.md): `stratactl init` → `stratactl install-stratabi`.

## Credential boundary (the core idea)

- **Execution plane = customer account, driven by the operator's own credentials.**
  The CLI calls `codebuild:StartBuild` with the engineer's creds; the runner role
  is a customer-account role.
- **Control plane = Shaleio account, request/response only.** StrataHQ answers
  "entitled?" and returns a short-lived, artifact-scoped **presigned URL**. It
  never holds customer credentials and never reaches into customer accounts.

## Install flow

1. `stratactl install <module>` resolves config (5-layer) and the artifact source.
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
#   attach the operator policy to yourself; paste `stratactl_customer_config`
#   into ~/.stratactl/config.toml

# 3. StrataCTL (operator laptop)
cd ../../stratactl && pip install -e .
export STRATA_HQ_TOKEN=...        # the raw token you seeded in step 1
stratactl init                    # show resolved config + expected resources
stratactl install iceberg-explorer --source stratahq --env dev
stratactl list
```

## ForgeWorks AWS demo

Once a StrataBI Dev data plane is installed (`stratactl dev install`), you can stand
up the **ForgeWorks AWS Demo** — synthetic data uploaded to your account and queried
through Athena. StrataCTL delegates to the `stratabi-dev` CLI (which owns the demo),
so the Apache-licensed CLI never imports the SGCL-licensed Dev Edition.

```bash
stratactl dev demo install --profile <profile> --region <region>   # confirm; --yes to skip
stratactl dev demo status
stratactl dev demo remove
```

Creates only an isolated demo namespace (`s3://<bucket>/demo/forgeworks/v1/`, Glue
database `stratabi_dev_demo`, the `forgeworks_athena_demo` dashboard); removal deletes
only that namespace. Small AWS charges may apply; no data is sent to Shaleio. Requires
`pipx install stratabi-dev`.

> Quick Demo validates the local StrataBI experience. AWS Demo validates the
> customer-owned Athena data plane.

## Configuration

The StrataHQ location is a literal in exactly one place —
`stratactl/src/strata/config/defaults.toml`. Everything resolves through
`strata.config.provider.load_settings()` in this precedence (highest first):
CLI flag → env (`STRATA_*`) → local file (`./.stratactl.toml`, `~/.stratactl/config.toml`)
→ AWS SSM (`/strataci/hq/*`) → packaged defaults.

## Versioning

Shaleio versions are **`Major.Sprint.Patch`**, not SemVer. The middle number is the
Shaleio **sprint** the release was cut in — a shared 2-week clock across the whole
ecosystem (Sprint 0 begins 2026-07-27), so it is the same for every Shaleio project
released in that sprint. `PATCH` increments within a sprint; `MAJOR` is a deliberate
milestone. The versions remain valid `MAJOR.MINOR.PATCH` triples, so SemVer tooling
parses them fine — the middle number just carries ecosystem meaning.

## Status

MVP scaffold. See `OPEN_QUESTIONS.md` for assumptions made and decisions still
open (notably runner IAM scoping, artifact signing, and token lifecycle).
