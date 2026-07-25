"""Developer Edition source resolution must be HQ-free and always resolve stratabi_dev.

These tests pin the behavior that `install-stratabi-dev` / `stratactl dev install`
never require StrataHQ: a public URL, an s3:// bundle, and the default customer_s3
path all avoid the entitlement call, and the module id is always stratabi_dev.
"""

from strata.artifacts.source import (
    KIND_CUSTOMER, KIND_PUBLIC, KIND_STRATAHQ, resolve_source,
)


class _S:
    source = "customer_s3"


def test_public_url_is_public_kind():
    r = resolve_source(
        "https://github.com/shaleio/stratabi-dev/releases/download/v1.0.0/stratabi_dev.zip",
        _S(), None)
    assert r.kind == KIND_PUBLIC
    assert r.public_url and r.public_url.endswith("stratabi_dev.zip")
    assert r.module_id == "stratabi_dev"


def test_query_string_url_is_public_and_preserved():
    r = resolve_source("https://example.com/a/mod.zip?token=abc", _S(), None)
    assert r.kind == KIND_PUBLIC
    assert r.public_url == "https://example.com/a/mod.zip?token=abc"


def test_s3_uri_is_customer_kind():
    r = resolve_source("s3://my-bucket/artifacts/stratabi_dev/1.0.0/module.zip", _S(), None)
    assert r.kind == KIND_CUSTOMER
    assert r.customer_s3_uri.startswith("s3://")


def test_default_dev_is_customer_s3_not_hq():
    r = resolve_source("stratabi_dev", _S(), "customer_s3")
    assert r.kind == KIND_CUSTOMER
    assert r.kind != KIND_STRATAHQ


def test_public_prefix_forces_public_kind():
    r = resolve_source("public/stratabi-dev", _S(), None)
    assert r.kind == KIND_PUBLIC
    assert r.module_id == "stratabi_dev"  # dashes normalized
