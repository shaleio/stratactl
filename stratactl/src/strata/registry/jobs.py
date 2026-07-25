"""Job status store (DynamoDB `strataci_jobs`). PK = job_id.

A job records one action (install/update/destroy) and its lifecycle, plus a
pointer to where the runner's logs live.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from strata.errors import RegistryError

PENDING = "PENDING"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
TERMINAL = {SUCCEEDED, FAILED}


@dataclass
class Job:
    job_id: str
    action: str
    module_id: str
    version: str
    status: str
    build_id: Optional[str] = None
    log_location: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None


def _table(session, table_name: str):
    return session.resource("dynamodb").Table(table_name)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def create(session, table_name: str, *, action: str, module_id: str, version: str) -> Job:
    job = Job(
        job_id=f"job-{uuid.uuid4().hex[:12]}",
        action=action,
        module_id=module_id,
        version=version,
        status=PENDING,
        started_at=_now(),
    )
    try:
        _table(session, table_name).put_item(Item={
            "job_id": job.job_id,
            "action": action,
            "module_id": module_id,
            "version": version,
            "status": PENDING,
            "started_at": job.started_at,
        })
    except Exception as exc:
        raise RegistryError(f"could not create job record: {exc}") from exc
    return job


def attach_build(session, table_name: str, job_id: str, build_id: str,
                 log_location: Optional[str] = None) -> None:
    expr = "SET build_id = :b, #s = :s"
    vals: dict[str, Any] = {":b": build_id, ":s": RUNNING}
    if log_location:
        expr += ", log_location = :l"
        vals[":l"] = log_location
    _update(session, table_name, job_id, expr, vals, names={"#s": "status"})


def set_status(session, table_name: str, job_id: str, status: str,
               *, exit_code: Optional[int] = None, error: Optional[str] = None) -> None:
    expr = "SET #s = :s"
    vals: dict[str, Any] = {":s": status}
    names = {"#s": "status"}
    if status in TERMINAL:
        expr += ", ended_at = :e"
        vals[":e"] = _now()
    if exit_code is not None:
        expr += ", exit_code = :c"
        vals[":c"] = exit_code
    if error:
        expr += ", #err = :err"
        vals[":err"] = error
        names["#err"] = "error"
    _update(session, table_name, job_id, expr, vals, names=names)


def get(session, table_name: str, job_id: str) -> Optional[dict[str, Any]]:
    try:
        return _table(session, table_name).get_item(Key={"job_id": job_id}).get("Item")
    except Exception as exc:
        raise RegistryError(f"could not read job {job_id}: {exc}") from exc


def _update(session, table_name, job_id, expr, vals, names=None) -> None:
    kwargs: dict[str, Any] = {
        "Key": {"job_id": job_id},
        "UpdateExpression": expr,
        "ExpressionAttributeValues": vals,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names
    try:
        _table(session, table_name).update_item(**kwargs)
    except Exception as exc:
        raise RegistryError(f"could not update job {job_id}: {exc}") from exc
