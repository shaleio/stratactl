"""Environment config: the account's network + identity values, stored once.

    s3://<strataci-bucket>/config/env/<env>.auto.tfvars.json

Every module install/update/uninstall in that environment reads it (the runner layers it
over the module's own tfvars, and its keys WIN), so the platform and every module share one
VPC/subnet/name definition and an upgrade can never pick up a different network from
whatever ``strata.yaml`` happens to be in the operator's working directory.

Written by the first install (seeded from ``strata.yaml``), ``stratactl env set``,
``stratactl env import`` (read from the deployed platform's state) or an explicit
``--reconfigure``. ``update`` never rewrites it. The StrataCI bucket is versioned, so every
change is recoverable.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# tfvar names that belong to the environment, not to a module.
ENV_KEYS = (
    "name_prefix",
    "region",
    "vpc_id",
    "public_subnet_ids",
    "enable_private_subnets",
    "private_subnet_ids",
)
LIST_KEYS = {"public_subnet_ids", "private_subnet_ids"}
BOOL_KEYS = {"enable_private_subnets"}


def key(env: str) -> str:
    return f"config/env/{env}.auto.tfvars.json"


def module_key(module_id: str) -> str:
    return f"config/{module_id}.auto.tfvars.json"


def split(tfvars: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(environment part, module part) of a rendered tfvars dict."""
    env = {k: v for k, v in tfvars.items() if k in ENV_KEYS}
    mod = {k: v for k, v in tfvars.items() if k not in ENV_KEYS}
    return env, mod


def _get_json(s3, bucket: str, k: str) -> Optional[dict[str, Any]]:
    try:
        raw = s3.get_object(Bucket=bucket, Key=k)["Body"].read()
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound") or type(exc).__name__ == "NoSuchKey":
            return None
        raise
    data = json.loads(raw or b"{}")
    return data if isinstance(data, dict) else None


def load(s3, bucket: str, env: str) -> Optional[dict[str, Any]]:
    return _get_json(s3, bucket, key(env))


def load_module(s3, bucket: str, module_id: str) -> Optional[dict[str, Any]]:
    return _get_json(s3, bucket, module_key(module_id))


def save(s3, bucket: str, env: str, data: dict[str, Any]) -> str:
    clean = {k: v for k, v in data.items() if k in ENV_KEYS}
    s3.put_object(Bucket=bucket, Key=key(env), Body=json.dumps(clean, indent=2).encode("utf-8"),
                  ContentType="application/json")
    return key(env)


def save_module(s3, bucket: str, module_id: str, data: dict[str, Any]) -> str:
    s3.put_object(Bucket=bucket, Key=module_key(module_id),
                  Body=json.dumps(data, indent=2).encode("utf-8"), ContentType="application/json")
    return module_key(module_id)


def parse_assignment(text: str) -> tuple[str, Any]:
    """'vpc_id=vpc-1' / 'public_subnet_ids=subnet-a,subnet-b' / 'enable_private_subnets=true'."""
    if "=" not in text:
        raise ValueError(f"expected KEY=VALUE, got {text!r}")
    k, v = text.split("=", 1)
    k, v = k.strip(), v.strip()
    if k not in ENV_KEYS:
        raise ValueError(f"{k!r} is not an environment key (allowed: {', '.join(ENV_KEYS)})")
    if k in LIST_KEYS:
        return k, [s.strip() for s in v.split(",") if s.strip()]
    if k in BOOL_KEYS:
        return k, v.lower() in ("1", "true", "yes", "on")
    return k, v


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """[(key, old, new)] for environment keys whose value would change."""
    out = []
    for k in ENV_KEYS:
        if k in new and old.get(k) != new.get(k):
            out.append((k, old.get(k), new.get(k)))
    return out


def format_diff(changes: list[tuple[str, Any, Any]]) -> str:
    return "\n".join(f"  {k}: {o!r} -> {n!r}" for k, o, n in changes)


# ---------------------------------------------------------------------------
# Validation (preflight)
# ---------------------------------------------------------------------------
def validate_network(ec2, data: dict[str, Any]) -> list[str]:
    """Problems with the network values, checked against the account. Empty = OK.
    Raises PermissionError when the caller can't describe EC2 (so callers can warn)."""
    problems: list[str] = []
    vpc = data.get("vpc_id")
    subnets = list(data.get("public_subnet_ids") or [])
    if data.get("enable_private_subnets"):
        subnets += list(data.get("private_subnet_ids") or [])
    if not vpc and not subnets:
        return problems

    try:
        if vpc:
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "vpc-id", "Values": [vpc]}]).get("Vpcs", [])
            if not vpcs:
                problems.append(f"vpc_id {vpc} does not exist in this account/region")
        if subnets:
            found = {
                s["SubnetId"]: s
                for s in ec2.describe_subnets(
                    Filters=[{"Name": "subnet-id", "Values": subnets}]
                ).get("Subnets", [])
            }
            for sid in subnets:
                s = found.get(sid)
                if not s:
                    problems.append(f"subnet {sid} does not exist in this account/region")
                elif vpc and s.get("VpcId") != vpc:
                    problems.append(f"subnet {sid} is in {s.get('VpcId')}, not {vpc}")
            azs = {found[s]["AvailabilityZone"] for s in data.get("public_subnet_ids") or [] if s in found}
            if data.get("public_subnet_ids") and len(azs) < 2:
                problems.append("public_subnet_ids must span at least 2 availability zones (ALB)")
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if "UnauthorizedOperation" in code or "AccessDenied" in code:
            raise PermissionError(code) from exc
        raise
    return problems


# ---------------------------------------------------------------------------
# Import from what's deployed
# ---------------------------------------------------------------------------
def _resources(state: dict[str, Any], rtype: str, name: str, mode: str = "managed"):
    for r in state.get("resources", []):
        if r.get("type") == rtype and r.get("name") == name and r.get("mode", "managed") == mode:
            return r.get("instances", [])
    return []


def from_state(state: dict[str, Any], region: str) -> dict[str, Any]:
    """Recover the environment values from the platform's tofu state: what is actually
    deployed, independent of any (possibly stale) tfvars file."""
    out: dict[str, Any] = {"region": region}

    for sg in ("alb", "ecs_tasks"):
        inst = _resources(state, "aws_security_group", sg)
        if inst and inst[0].get("attributes", {}).get("vpc_id"):
            out["vpc_id"] = inst[0]["attributes"]["vpc_id"]
            break

    def _data_ids(name: str) -> list[str]:
        inst = sorted(_resources(state, "aws_subnet", name, mode="data"), key=lambda i: i.get("index_key", 0))
        return [i.get("attributes", {}).get("id") for i in inst if i.get("attributes", {}).get("id")]

    public = _data_ids("public")
    if not public:
        lb = _resources(state, "aws_lb", "stratabi")
        public = list((lb[0].get("attributes", {}).get("subnets") or [])) if lb else []
    if public:
        out["public_subnet_ids"] = public

    private = _data_ids("private")
    out["private_subnet_ids"] = private
    out["enable_private_subnets"] = bool(private)

    cluster = _resources(state, "aws_ecs_cluster", "stratabi")
    name = cluster[0].get("attributes", {}).get("name", "") if cluster else ""
    if name.endswith("-cluster"):
        out["name_prefix"] = name[: -len("-cluster")]
    return out
