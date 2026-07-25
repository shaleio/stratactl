"""Resolve where a module artifact comes from.

A module spec can be:
  * "iceberg-explorer"            -> use the default source from config
  * "stratahq/iceberg-explorer"   -> force StrataHQ (entitlement required)
  * "customer_s3/iceberg-explorer"-> force customer S3
  * "s3://bucket/key/module.zip"  -> explicit customer S3 object
  * "https://.../module.zip"      -> explicit PUBLIC artifact (no StrataHQ contact,
                                     no entitlement) — e.g. a GitHub release of the
                                     source-available Developer / Community edition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from strata.config.models import Settings

KIND_STRATAHQ = "stratahq"
KIND_CUSTOMER = "customer_s3"
KIND_PUBLIC = "public"


@dataclass
class SourceSpec:
    kind: str               # "stratahq" | "customer_s3" | "public"
    module_id: str
    customer_s3_uri: Optional[str] = None   # set when kind == customer_s3 and explicit
    public_url: Optional[str] = None        # set when kind == public (http/https)
    sha256: Optional[str] = None


def _norm_module_id(name: str) -> str:
    return name.replace("-", "_")


def resolve_source(module_spec: str, settings: Settings,
                   cli_source: Optional[str] = None) -> SourceSpec:
    # Explicit http(s) URL is a PUBLIC artifact — fetched directly, no StrataHQ,
    # no entitlement (used by the source-available dev/community edition).
    if module_spec.startswith(("http://", "https://")):
        tail = module_spec.split("/")[-1].split("?")[0]
        module_id = _norm_module_id(tail.removesuffix(".zip"))
        return SourceSpec(kind=KIND_PUBLIC, module_id=module_id, public_url=module_spec)

    # Explicit s3:// URI is unambiguously a customer artifact.
    if module_spec.startswith("s3://"):
        tail = module_spec.split("/")[-1]
        module_id = _norm_module_id(tail.removesuffix(".zip"))
        return SourceSpec(kind=KIND_CUSTOMER, module_id=module_id, customer_s3_uri=module_spec)

    kind = cli_source or settings.source
    module_name = module_spec
    if "/" in module_spec:
        prefix, module_name = module_spec.split("/", 1)
        if prefix in (KIND_STRATAHQ, KIND_CUSTOMER, KIND_PUBLIC):
            kind = prefix

    return SourceSpec(kind=kind, module_id=_norm_module_id(module_name))
