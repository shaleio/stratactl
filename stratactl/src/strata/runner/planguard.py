"""Plan guard: refuse an apply that would destroy or replace load-bearing infrastructure.

Reads ``tofu show -json <planfile>`` output. Any resource of a protected type whose planned
actions include ``delete`` (a destroy, or a replace = delete+create) blocks the apply unless
explicitly allowed. Used by the StrataCI runner before every install/update apply, and by
``stratactl plan-guard`` for bring-your-own CI/CD pipelines.

Normal upgrade churn is NOT protected: task definitions, Lambda code, image builds
(null_resource), IAM policies, S3 objects and table items replace freely.
"""

from __future__ import annotations

import json
from typing import Any

PROTECTED_TYPES = frozenset({
    # networking / ingress
    "aws_vpc", "aws_subnet", "aws_security_group", "aws_route_table", "aws_internet_gateway",
    "aws_nat_gateway", "aws_eip",
    "aws_lb", "aws_lb_listener", "aws_lb_target_group",
    # compute surface users hit
    "aws_ecs_cluster", "aws_ecs_service",
    # data + identity
    "aws_s3_bucket", "aws_dynamodb_table", "aws_glue_catalog_database", "aws_kms_key",
    "aws_cognito_user_pool", "aws_ecr_repository", "aws_efs_file_system", "aws_db_instance",
})


def destructive_changes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Protected resources the plan would delete or replace."""
    hits = []
    for rc in plan.get("resource_changes") or []:
        if rc.get("mode", "managed") != "managed" or rc.get("type") not in PROTECTED_TYPES:
            continue
        actions = (rc.get("change") or {}).get("actions") or []
        if "delete" not in actions:
            continue
        hits.append({
            "address": rc.get("address"),
            "type": rc.get("type"),
            "action": "replace" if "create" in actions else "destroy",
            "reasons": [".".join(str(p) for p in path) for path in
                        (rc.get("change") or {}).get("replace_paths") or []],
        })
    return hits


def format_report(hits: list[dict[str, Any]]) -> str:
    lines = []
    for h in hits:
        why = f"  (forced by: {', '.join(h['reasons'])})" if h["reasons"] else ""
        lines.append(f"  - {h['action']:7s} {h['address']}{why}")
    return "\n".join(lines)


def check(plan: dict[str, Any], *, allow_replace: bool = False) -> list[dict[str, Any]]:
    """Raise RuntimeError when the plan is destructive and not allowed; return the hits."""
    hits = destructive_changes(plan)
    if hits and not allow_replace:
        raise RuntimeError(
            "plan guard: this plan would destroy or replace protected infrastructure:\n"
            + format_report(hits)
            + "\nNothing was applied. If this is intended, re-run with --allow-replace. If not, "
              "check the environment config (`stratactl env show`) — a changed vpc_id/subnet "
              "is the usual cause."
        )
    return hits


def check_file(path: str, *, allow_replace: bool = False) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return check(json.load(f), allow_replace=allow_replace)
