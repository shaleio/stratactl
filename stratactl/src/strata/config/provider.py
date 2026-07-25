"""Five-layer configuration resolver.

Precedence (highest wins):
    1. CLI flag overrides
    2. Environment variables (STRATA_*)
    3. Local config file (./.stratactl.toml, then ~/.stratactl/config.toml)
    4. AWS SSM Parameter Store / Secrets Manager (/strataci/hq/*)
    5. Version-controlled packaged defaults (config/defaults.toml)

The StrataHQ location appears as a literal ONLY in defaults.toml. Call
`load_settings()` once at startup and pass the resulting Settings object down.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any


def _tomllib():
    """Lazy import so the package imports on <3.11; toml is only needed when
    actually reading a defaults/config file (Python 3.11+ ships tomllib)."""
    try:
        import tomllib
        return tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli  # type: ignore
        return tomli

from strata.config.models import CustomerConfig, HQConfig, Settings
from strata.errors import ConfigError

SSM_PREFIX = "/strataci/hq/"


def load_settings(cli_overrides: dict[str, Any] | None = None) -> Settings:
    """Resolve configuration across all layers and return a typed Settings."""
    merged: dict[str, Any] = {}

    # lowest -> highest: later overlays win. Discovery (a CDN-hosted HQ-location
    # doc) sits just above packaged defaults so HQ can migrate without a client
    # update, while SSM/file/env/flag still override it.
    _deep_merge(merged, _from_defaults())
    _deep_merge(merged, _from_discovery(merged))
    _deep_merge(merged, _from_ssm(merged))
    _deep_merge(merged, _from_local_file())
    _deep_merge(merged, _from_env())
    _deep_merge(merged, _normalize(cli_overrides or {}))

    return _to_settings(merged)


def _from_discovery(partial: dict[str, Any]) -> dict[str, Any]:
    """Fetch the StrataHQ discovery doc from a stable CDN URL, if configured, and
    overlay the HQ endpoint/bucket/region. Best-effort — never fatal offline."""
    if os.environ.get("STRATA_DISABLE_DISCOVERY") == "1":
        return {}
    url = os.environ.get("STRATA_DISCOVERY_URL") or partial.get("hq", {}).get("discovery_url")
    if not url:
        return {}
    try:
        import json
        import urllib.request

        with urllib.request.urlopen(url, timeout=5) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        out: dict[str, Any] = {"hq": {}}
        for key in ("endpoint", "artifact_bucket", "region"):
            if doc.get(key):
                out["hq"][key] = doc[key]
        return out
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Layer 5: packaged defaults
# --------------------------------------------------------------------------- #
def _from_defaults() -> dict[str, Any]:
    try:
        raw = resources.files("strata.config").joinpath("defaults.toml").read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:  # pragma: no cover
        raise ConfigError(f"packaged defaults.toml missing: {exc}") from exc
    return _normalize(_tomllib().loads(raw.decode("utf-8")))


# --------------------------------------------------------------------------- #
# Layer 4: SSM / Secrets Manager (best-effort, never fatal offline)
# --------------------------------------------------------------------------- #
def _from_ssm(partial: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("STRATA_DISABLE_SSM") == "1":
        return {}
    try:
        import boto3

        region = (partial.get("region")
                  or partial.get("hq", {}).get("region")
                  or os.environ.get("STRATA_REGION"))
        profile = partial.get("profile") or os.environ.get("STRATA_PROFILE")
        session = boto3.Session(profile_name=profile, region_name=region)
        ssm = session.client("ssm")

        out: dict[str, Any] = {"hq": {}}
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=SSM_PREFIX, WithDecryption=True):
            for p in page.get("Parameters", []):
                key = p["Name"].rsplit("/", 1)[-1]  # e.g. "endpoint"
                if key == "artifact_bucket":
                    out["hq"]["artifact_bucket"] = p["Value"]
                elif key in ("endpoint", "region", "auth", "token"):
                    out["hq"][key] = p["Value"]
        return out
    except Exception:
        # Offline / no creds / no params: SSM is optional, fall through.
        return {}


# --------------------------------------------------------------------------- #
# Layer 3: local config file
# --------------------------------------------------------------------------- #
def _from_local_file() -> dict[str, Any]:
    for candidate in (Path.cwd() / ".stratactl.toml",
                      Path.home() / ".stratactl" / "config.toml"):
        if candidate.is_file():
            tomllib = _tomllib()
            try:
                return _normalize(tomllib.loads(candidate.read_text("utf-8")))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"invalid config file {candidate}: {exc}") from exc
    return {}


# --------------------------------------------------------------------------- #
# Layer 2: environment variables
# --------------------------------------------------------------------------- #
_ENV_MAP = {
    "STRATA_HQ_ENDPOINT": ("hq", "endpoint"),
    "STRATA_HQ_BUCKET": ("hq", "artifact_bucket"),
    "STRATA_HQ_REGION": ("hq", "region"),
    "STRATA_HQ_AUTH": ("hq", "auth"),
    "STRATA_HQ_TOKEN": ("hq", "token"),
    "STRATA_HQ_PUBKEY": ("hq", "public_key"),
    "STRATA_REQUIRE_SIGNATURE": ("hq", "require_signature"),
    "STRATA_SOURCE": ("source",),
    "STRATA_PROFILE": ("profile",),
    "STRATA_REGION": ("region",),
    "STRATA_CUSTOMER_BUCKET": ("customer", "bucket"),
    "STRATA_DASH_BUCKET": ("dashboards", "bucket"),
    "STRATA_DASH_GIT_TABLE": ("dashboards", "git_registry_table"),
    "STRATA_DASH_PINNED_TABLE": ("dashboards", "pinned_table"),
    "STRATA_DASH_PREFIX": ("dashboards", "prefix"),
    "STRATA_DASH_LOCAL_DIR": ("dashboards", "local_dir"),
    "STRATA_DISCOVERY_URL": ("hq", "discovery_url"),
    "STRATA_RUNTIME_MODULE_ID": ("runtime", "module_id"),
    "STRATA_RUNTIME_DIST": ("runtime", "dist_dir"),
}


def _from_env() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for env_key, path in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is None or val == "":
            continue
        _set_path(out, path, val)
    return out


# --------------------------------------------------------------------------- #
# Normalization + merge helpers
# --------------------------------------------------------------------------- #
def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Map raw TOML/dict shapes into the canonical Settings dict shape."""
    out: dict[str, Any] = {}
    if "hq" in raw and isinstance(raw["hq"], dict):
        out["hq"] = dict(raw["hq"])
    if "customer" in raw and isinstance(raw["customer"], dict):
        out["customer"] = dict(raw["customer"])
    if "dashboards" in raw and isinstance(raw["dashboards"], dict):
        out["dashboards"] = dict(raw["dashboards"])
    if "runtime" in raw and isinstance(raw["runtime"], dict):
        out["runtime"] = dict(raw["runtime"])
    # [defaults].source  ->  top-level "source"
    if isinstance(raw.get("defaults"), dict) and "source" in raw["defaults"]:
        out["source"] = raw["defaults"]["source"]
    for top in ("source", "profile", "region"):
        if top in raw and not isinstance(raw[top], dict):
            out[top] = raw[top]
    return out


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        elif v is not None and v != "":
            base[k] = v


def _set_path(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur = d
    for key in path[:-1]:
        cur = cur.setdefault(key, {})
    cur[path[-1]] = value


def _to_settings(m: dict[str, Any]) -> Settings:
    from strata.config.models import DashboardsConfig, RuntimeConfig

    hq = HQConfig(**{k: v for k, v in m.get("hq", {}).items()
                     if k in HQConfig.__dataclass_fields__})
    customer = CustomerConfig(**{k: v for k, v in m.get("customer", {}).items()
                                 if k in CustomerConfig.__dataclass_fields__})
    dashboards = DashboardsConfig(**{k: v for k, v in m.get("dashboards", {}).items()
                                     if k in DashboardsConfig.__dataclass_fields__})
    runtime = RuntimeConfig(**{k: v for k, v in m.get("runtime", {}).items()
                               if k in RuntimeConfig.__dataclass_fields__})
    return Settings(
        hq=hq,
        customer=customer,
        dashboards=dashboards,
        runtime=runtime,
        source=m.get("source", "stratahq"),
        profile=m.get("profile"),
        region=m.get("region", hq.region or "us-east-1"),
    )
