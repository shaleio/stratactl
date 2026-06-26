"""Build a least-privilege IAM session policy from a module manifest (Decision A).

A session policy passed to sts:AssumeRole can only INTERSECT (narrow) the deploy
role's permissions. So the deploy role is broad, and this policy scopes the
effective permissions down to: (1) the tofu state backend (so init/plan/apply can
read+lock+write state), plus (2) exactly what the module declared.

Note the ~2048-char limit on an inline session policy; very large modules should
move to multiple managed policy ARNs (v2) — `policy_too_large` flags that.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from strata.artifacts.manifest import Manifest

SESSION_POLICY_MAX = 2048


def build_session_policy(manifest: Manifest, *, state_bucket: Optional[str] = None,
                         lock_table: Optional[str] = None) -> dict[str, Any]:
    statements: list[dict[str, Any]] = []

    # (1) mandatory tofu state-backend access (S3 state object + Dynamo lock)
    if state_bucket:
        statements.append({
            "Sid": "StateBackendS3",
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                       "s3:ListBucket", "s3:GetBucketLocation"],
            "Resource": [f"arn:aws:s3:::{state_bucket}", f"arn:aws:s3:::{state_bucket}/*"],
        })
    if lock_table:
        statements.append({
            "Sid": "StateBackendLock",
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:PutItem",
                       "dynamodb:DeleteItem", "dynamodb:DescribeTable"],
            "Resource": [f"arn:aws:dynamodb:*:*:table/{lock_table}"],
        })

    # (2) module-declared permissions
    for entry in manifest.required_permissions:
        if isinstance(entry, dict):
            statements.append({
                "Sid": _sid(entry["service"]),
                "Effect": "Allow",
                "Action": entry["actions"],
                "Resource": entry.get("resources", ["*"]),
            })
        elif isinstance(entry, str):
            # legacy/broad: whole-service wildcard (Shaleio-internal modules only)
            statements.append({
                "Sid": _sid(entry),
                "Effect": "Allow",
                "Action": [f"{entry}:*"],
                "Resource": "*",
            })

    return {"Version": "2012-10-17", "Statement": statements}


def policy_json(manifest: Manifest, **kw) -> str:
    return json.dumps(build_session_policy(manifest, **kw), separators=(",", ":"))


def policy_too_large(manifest: Manifest, **kw) -> bool:
    return len(policy_json(manifest, **kw)) > SESSION_POLICY_MAX


def _sid(service: str) -> str:
    return "".join(c for c in service.title() if c.isalnum()) or "Svc"
