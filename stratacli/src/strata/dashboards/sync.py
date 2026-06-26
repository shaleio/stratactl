"""git <-> S3 sync for StrataBI's git-registered dashboards.

The StrataBI runtime marks a dashboard for version control by writing it to the
`dashboard_git_registry` DynamoDB table (the "git" toggle in the UI). This module
is the operator side: it scans that table and moves the JSON artifacts between S3
and a local directory you manage with normal git.

  pull  : registry -> local dir (+ a manifest recording each object's S3 key + ETag)
  push  : local dir -> S3 (overwrite in place; --new mints a fresh artifact)
  diff  : show which local dashboards differ from S3
  list  : the registered set

Identity is stable because the registry stores each dashboard's S3 key; push
overwrites that exact key (artifact swap). `--new` is the explicit fork/bootstrap.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from strata.errors import StrataError

MANIFEST_NAME = ".stratacli-dashboards.json"


@dataclass
class DashboardRef:
    slug: str            # local filename stem
    s3_key: str          # the exact S3 object key (stable identity)
    dashboard_id: str
    etag: Optional[str] = None


# --------------------------------------------------------------------------- #
# Registry + S3 primitives
# --------------------------------------------------------------------------- #
def scan_registry(session, table_name: str) -> list[DashboardRef]:
    """Read the git-registered set from DynamoDB (pk = GLOBAL)."""
    from boto3.dynamodb.conditions import Key

    table = session.resource("dynamodb").Table(table_name)
    try:
        items = table.query(KeyConditionExpression=Key("pk").eq("GLOBAL")).get("Items", [])
    except Exception as exc:
        raise StrataError(f"could not scan git registry '{table_name}': {exc}") from exc

    refs = []
    for it in items:
        key = it.get("s3_key") or it.get("dashboard_key")
        if not key:
            continue
        refs.append(DashboardRef(
            slug=_slug_for_key(key, it.get("dashboard_id")),
            s3_key=key,
            dashboard_id=it.get("dashboard_id") or _id_from_key(key),
        ))
    return refs


def _put_registry(session, table_name: str, s3_key: str, dashboard_id: str, by: Optional[str]) -> None:
    table = session.resource("dynamodb").Table(table_name)
    item = {
        "pk": "GLOBAL", "sk": f"DASHBOARD#{s3_key}",
        "dashboard_key": s3_key, "s3_key": s3_key, "dashboard_id": dashboard_id,
        "registered_at": _now(),
    }
    if by:
        item["registered_by"] = by
    table.put_item(Item=item)


def _put_pinned(session, table_name: str, s3_key: str, dashboard_id: str) -> None:
    session.resource("dynamodb").Table(table_name).put_item(Item={
        "pk": "GLOBAL", "sk": f"DASHBOARD#{s3_key}",
        "dashboard_key": s3_key, "s3_key": s3_key, "dashboard_id": dashboard_id,
        "pinned_at": _now(),
    })


def _get_object(s3, bucket: str, key: str) -> tuple[bytes, str]:
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read(), resp.get("ETag", "").strip('"')


def _put_object(s3, bucket: str, key: str, body: bytes) -> str:
    resp = s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return resp.get("ETag", "").strip('"')


def _head_etag(s3, bucket: str, key: str) -> Optional[str]:
    try:
        return s3.head_object(Bucket=bucket, Key=key).get("ETag", "").strip('"')
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# pull / push / diff
# --------------------------------------------------------------------------- #
def pull(session, *, bucket: str, registry_table: str, local_dir: Path) -> list[str]:
    s3 = session.client("s3")
    local_dir.mkdir(parents=True, exist_ok=True)
    refs = scan_registry(session, registry_table)

    manifest: dict[str, Any] = {}
    written = []
    for ref in refs:
        body, etag = _get_object(s3, bucket, ref.s3_key)
        # pretty-print so git diffs are clean
        try:
            body = json.dumps(json.loads(body), indent=2).encode("utf-8")
        except Exception:
            pass
        (local_dir / f"{ref.slug}.json").write_bytes(body)
        manifest[ref.slug] = {"s3_key": ref.s3_key, "dashboard_id": ref.dashboard_id, "etag": etag}
        written.append(ref.slug)

    _save_manifest(local_dir, manifest)
    return written


def push(session, *, bucket: str, registry_table: str, pinned_table: Optional[str],
         local_dir: Path, files: list[Path], new: bool, git_register: bool,
         pinned: bool, prefix: str, force: bool) -> list[str]:
    s3 = session.client("s3")
    manifest = _load_manifest(local_dir)
    targets = files or [local_dir / f"{s}.json" for s in manifest]
    pushed = []

    for path in targets:
        if not path.is_file():
            raise StrataError(f"no such dashboard file: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        _validate(data, path)
        slug = path.stem
        entry = manifest.get(slug)
        body = json.dumps(data, indent=2).encode("utf-8")

        if new or entry is None:
            dashboard_id = str(data.get("id") or slug)
            key = _new_dashboard_key(prefix, dashboard_id)
            etag = _put_object(s3, bucket, key, body)
            if git_register or new:
                _put_registry(session, registry_table, key, dashboard_id, by="stratacli")
            if pinned and pinned_table:
                _put_pinned(session, pinned_table, key, dashboard_id)
            manifest[slug] = {"s3_key": key, "dashboard_id": dashboard_id, "etag": etag}
            pushed.append(f"{slug} -> NEW {key}")
        else:
            key = entry["s3_key"]
            if not force:
                remote = _head_etag(s3, bucket, key)
                if remote is not None and entry.get("etag") and remote != entry["etag"]:
                    raise StrataError(
                        f"{slug}: S3 object changed since pull (someone edited it). "
                        f"Re-pull or use --force to overwrite."
                    )
            etag = _put_object(s3, bucket, key, body)
            entry["etag"] = etag
            if pinned and pinned_table:
                _put_pinned(session, pinned_table, key, entry["dashboard_id"])
            pushed.append(f"{slug} -> {key}")

    _save_manifest(local_dir, manifest)
    return pushed


def diff(session, *, bucket: str, registry_table: str, local_dir: Path) -> list[tuple[str, str]]:
    s3 = session.client("s3")
    manifest = _load_manifest(local_dir)
    out: list[tuple[str, str]] = []

    local_slugs = {p.stem for p in local_dir.glob("*.json")}
    for slug in sorted(local_slugs | set(manifest)):
        entry = manifest.get(slug)
        local_path = local_dir / f"{slug}.json"
        if entry is None:
            out.append((slug, "local-only (not registered / never pushed)")); continue
        if not local_path.is_file():
            out.append((slug, "missing locally")); continue
        try:
            remote_body, _ = _get_object(s3, bucket, entry["s3_key"])
            same = json.loads(remote_body) == json.loads(local_path.read_text("utf-8"))
            out.append((slug, "unchanged" if same else "MODIFIED"))
        except Exception as exc:
            out.append((slug, f"error: {exc}"))
    return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _validate(data: dict, path: Path) -> None:
    """Validate against the bundled/published dashboard schema if jsonschema is
    available; a missing validator never blocks a push (warn-only)."""
    try:
        import jsonschema  # noqa: F401
    except ModuleNotFoundError:
        return
    schema = _load_schema()
    if not schema:
        return
    from jsonschema import Draft7Validator
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise StrataError(f"{path.name} fails dashboard schema: {first.message} (at {list(first.path)})")


def _load_schema() -> Optional[dict]:
    # Best effort: a schema file next to the package or under ./schemas.
    for cand in (Path(__file__).resolve().parents[3] / "schemas" / "dashboard.schema.json",
                 Path.cwd() / "dashboard.schema.json"):
        if cand.is_file():
            try:
                return json.loads(cand.read_text("utf-8"))
            except Exception:
                return None
    return None


def _new_dashboard_key(prefix: str, dashboard_id: str, scope: str = "g") -> str:
    """Mint a key following the StrataBI convention:
    <prefix>/<YYYY_MM>/global/<id>__<scope>__<timestamp>__<uid>.json"""
    now = dt.datetime.now(dt.timezone.utc)
    ident = f"{dashboard_id}__{scope}__{now.strftime('%Y%m%dT%H%M%SZ')}__{secrets.token_hex(4)}"
    return f"{prefix.strip('/')}/{now.strftime('%Y_%m')}/global/{ident}.json"


def _slug_for_key(key: str, dashboard_id: Optional[str]) -> str:
    base = dashboard_id or _id_from_key(key)
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in base)


def _id_from_key(key: str) -> str:
    stem = key.rsplit("/", 1)[-1].removesuffix(".json")
    return stem.split("__", 1)[0] or stem


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _manifest_path(local_dir: Path) -> Path:
    return local_dir / MANIFEST_NAME


def _load_manifest(local_dir: Path) -> dict[str, Any]:
    p = _manifest_path(local_dir)
    if p.is_file():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def _save_manifest(local_dir: Path, manifest: dict[str, Any]) -> None:
    _manifest_path(local_dir).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
