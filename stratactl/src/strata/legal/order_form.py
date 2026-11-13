"""Official Module order-form terms: reference the published terms + capture
acceptance at extraction.

Division of responsibility:
  * This client module SHOWS the operator a short notice — which module/version is
    being installed, a link to the published Official Module Order Terms, and that
    proceeding (extraction under their license token) is acceptance — then captures
    an affirmative confirmation and returns acceptance metadata.
  * StrataHQ's entitlement Lambda writes the AUTHORITATIVE record. Crucially, the
    *authenticated extraction request itself* is the act of acceptance (Terms §7), so
    HQ records concurrence even when the CLI is modified, bypassed, or automated — the
    bearer-token API call is the signature.

The canonical terms text ships in order_form.md and is hashed (`terms_sha256`) so the
record proves which terms were in force; the published page is the human-readable copy.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
from importlib import resources

from strata import __version__ as _CLI_VERSION
from strata.errors import StrataError

# Bump when the terms language in order_form.md / the published page changes.
TERMS_VERSION = "1.0"
# Canonical, human-readable published terms (the CLI links here instead of dumping text).
TERMS_URL = "https://shaleio.com/legal/module-order-terms.html"

_PKG = "strata.legal"
_TEMPLATE = "order_form.md"

# The platform products carry the master Enterprise license / Standard Order Terms,
# not a per-module order form, so they are exempt from this flow.
PLATFORM_IDS = frozenset({"stratabi", "stratabi_dev"})


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def terms_text() -> str:
    """The canonical Official Module Order Terms text as shipped in the wheel."""
    return resources.files(_PKG).joinpath(_TEMPLATE).read_text(encoding="utf-8")


def terms_sha256() -> str:
    """sha256 of the canonical terms text — the content-hash of what is being accepted."""
    return hashlib.sha256(terms_text().encode("utf-8")).hexdigest()


def prompt_acceptance(*, module_id: str, version: str, account_id: str,
                      assume_yes: bool = False) -> dict:
    """Show the short order-form notice and capture acceptance. Returns acceptance
    metadata for StrataHQ. Raises StrataError if the operator declines.

    Non-interactive acceptance (CI/automation) is recorded when `--yes` is passed or
    STRATA_ACCEPT_ORDER_FORM is truthy. Either way, proceeding triggers an
    authenticated extraction, which is itself acceptance under the terms.
    """
    accepted_at = dt.datetime.now(dt.timezone.utc).isoformat()
    env_accept = _truthy(os.getenv("STRATA_ACCEPT_ORDER_FORM", ""))

    print()
    print(f"Installing module: {module_id}  v{version}  (account {account_id})")
    print(f"Subject to the StrataBI Official Module Order Terms (v{TERMS_VERSION}):")
    print(f"    {TERMS_URL}")
    print("Proceeding extracts the module under your license token. Terminal approval, "
          "or\nextraction via the entitlement pipeline, constitutes your acceptance of "
          "these terms.")

    if assume_yes or env_accept:
        method = "cli:--yes" if assume_yes else "cli:env"
        print(f"Accepted non-interactively ({method}).")
    else:
        if not sys.stdin.isatty():
            raise StrataError(
                "This install accepts the Official Module Order Terms by proceeding, but "
                "no interactive terminal is attached. Re-run with --yes, or set "
                "STRATA_ACCEPT_ORDER_FORM=1, to proceed non-interactively."
            )
        reply = input(f"Agree and proceed with {module_id}@{version}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            raise StrataError("Order terms not accepted — install aborted.")
        method = "cli:interactive"

    return {
        "accepted": True,
        "terms_version": TERMS_VERSION,
        "terms_url": TERMS_URL,
        "terms_sha256": terms_sha256(),
        "accepted_at": accepted_at,
        "accepted_by": method,
        "cli_version": _CLI_VERSION,
    }
