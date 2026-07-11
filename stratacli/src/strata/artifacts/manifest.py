"""stratabi-module.json parsing + validation.

`required_permissions` drives least-privilege deploys (Decision A): the runner
builds an IAM session policy from it and assumes a deploy role scoped DOWN to
exactly what the module declares. Two accepted forms:

  Structured (preferred — enables tight scoping; module authors MUST use this for
  third-party modules):
      "required_permissions": [
        {"service": "s3",
         "actions": ["s3:CreateBucket", "s3:PutObject", "s3:DeleteObject"],
         "resources": ["arn:aws:s3:::acme-iceberg-*", "arn:aws:s3:::acme-iceberg-*/*"]}
      ]

  Simple (legacy / Shaleio-internal only — results in a BROAD service:* grant):
      "required_permissions": ["s3", "lambda", "dynamodb"]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strata.errors import ManifestError

VALID_ENTRYPOINTS = {"tofu"}

# Delivery mode governs whether the tofu bundle is persisted into the customer's
# environment. "local": the runner caches the verified bundle to the customer
# bucket, so teardown works offline/airgapped and without HQ. "managed": the
# bundle is never persisted; uninstall re-fetches it from HQ (destroy is always
# permitted, even for a lapsed/retired grant). Default local.
VALID_DELIVERY = {"local", "managed"}


@dataclass
class Manifest:
    module_id: str
    version: str
    entrypoint: str
    state_key: str
    delivery: str = "local"
    required_permissions: list[Any] = field(default_factory=list)
    supports_destroy: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def permissions_are_scoped(self) -> bool:
        """True when every permission entry is structured (service/actions/resources)."""
        if not self.required_permissions:
            return False
        return all(isinstance(p, dict) for p in self.required_permissions)


def load_and_validate(path: Path) -> Manifest:
    if not path.is_file():
        raise ManifestError(f"manifest not found: {path}")
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    return validate(data)


def validate(data: dict[str, Any]) -> Manifest:
    for required in ("module_id", "version", "entrypoint", "state_key"):
        if not data.get(required):
            raise ManifestError(f"manifest missing required field '{required}'")

    entrypoint = data["entrypoint"]
    if entrypoint not in VALID_ENTRYPOINTS:
        raise ManifestError(
            f"unsupported entrypoint '{entrypoint}' (expected one of {sorted(VALID_ENTRYPOINTS)})"
        )

    state_key = data["state_key"]
    if state_key.startswith("/") or ".." in state_key:
        raise ManifestError(f"unsafe state_key '{state_key}'")

    delivery = data.get("delivery", "local")
    if delivery not in VALID_DELIVERY:
        raise ManifestError(
            f"delivery must be one of {sorted(VALID_DELIVERY)}, got '{delivery}'"
        )

    perms = data.get("required_permissions", [])
    if not isinstance(perms, list):
        raise ManifestError("required_permissions must be a list")
    for entry in perms:
        _validate_perm_entry(entry)

    return Manifest(
        module_id=data["module_id"],
        version=str(data["version"]),
        entrypoint=entrypoint,
        state_key=state_key,
        delivery=delivery,
        required_permissions=perms,
        supports_destroy=bool(data.get("supports_destroy", False)),
        raw=data,
    )


def _validate_perm_entry(entry: Any) -> None:
    if isinstance(entry, str):
        if not entry:
            raise ManifestError("empty permission entry")
        return
    if isinstance(entry, dict):
        if not entry.get("service"):
            raise ManifestError("structured permission entry missing 'service'")
        actions = entry.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ManifestError(
                f"permission entry for '{entry.get('service')}' needs a non-empty 'actions' list"
            )
        resources = entry.get("resources", ["*"])
        if not isinstance(resources, list) or not resources:
            raise ManifestError(
                f"permission entry for '{entry.get('service')}' needs a non-empty 'resources' list"
            )
        return
    raise ManifestError(f"invalid permission entry: {entry!r}")
