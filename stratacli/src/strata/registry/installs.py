"""Install registry (DynamoDB `strataci_install_registry`). PK = module_id.

Tracks what is installed in this account: version, status, tofu state_key, and
the last tofu outputs. Uninstall reads this to find the state to destroy.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from strata.errors import RegistryError

INSTALLED = "INSTALLED"
REMOVED = "REMOVED"


def _table(session, table_name: str):
    return session.resource("dynamodb").Table(table_name)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def upsert(session, table_name: str, *, module_id: str, version: str, state_key: str,
           status: str = INSTALLED, outputs: Optional[dict] = None,
           job_id: Optional[str] = None, previous_version: Optional[str] = None) -> None:
    item: dict[str, Any] = {
        "module_id": module_id,
        "version": version,
        "state_key": state_key,
        "status": status,
        "updated_at": _now(),
        "outputs": outputs or {},
    }
    if job_id:
        item["last_job"] = job_id
    if previous_version:
        item["previous_version"] = previous_version
    try:
        _table(session, table_name).put_item(Item=item)
    except Exception as exc:
        raise RegistryError(f"could not write install record for {module_id}: {exc}") from exc


def get(session, table_name: str, module_id: str) -> Optional[dict[str, Any]]:
    try:
        return _table(session, table_name).get_item(Key={"module_id": module_id}).get("Item")
    except Exception as exc:
        raise RegistryError(f"could not read install record {module_id}: {exc}") from exc


def list_all(session, table_name: str) -> list[dict[str, Any]]:
    try:
        return _table(session, table_name).scan().get("Items", [])
    except Exception as exc:
        raise RegistryError(f"could not list installs: {exc}") from exc


def mark_removed(session, table_name: str, module_id: str, job_id: Optional[str] = None) -> None:
    expr = "SET #s = :s, updated_at = :u"
    vals: dict[str, Any] = {":s": REMOVED, ":u": _now()}
    if job_id:
        expr += ", last_job = :j"
        vals[":j"] = job_id
    try:
        _table(session, table_name).update_item(
            Key={"module_id": module_id},
            UpdateExpression=expr,
            ExpressionAttributeValues=vals,
            ExpressionAttributeNames={"#s": "status"},
        )
    except Exception as exc:
        raise RegistryError(f"could not mark {module_id} removed: {exc}") from exc
