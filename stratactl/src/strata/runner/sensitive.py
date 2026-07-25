"""Sensitive-env contract for the runner job spec.

Some of the env vars injected into the ephemeral CodeBuild runner are bearer
credentials — chiefly ``STRATA_ARTIFACT_URL``, the short-lived presigned URL to the
licensed bundle. CodeBuild plaintext overrides have no per-variable redaction, so
these values are visible in the build's environment metadata to in-account
principals. We accept that residual exposure because the value is ephemeral (<= the
presign TTL), single-object and read-only, and confined to the customer's own
account.

As defense-in-depth, the runner treats these as sensitive: they are never printed,
and they are scrubbed from any error text before it reaches the runner log or the
jobs table. This module is the single place that declares which keys are sensitive.
"""

from __future__ import annotations

import os

SENSITIVE_ENV = frozenset({
    "STRATA_ARTIFACT_URL",   # presigned bearer URL to the licensed bundle
    "STRATA_HQ_TOKEN",       # license token (not normally passed to the runner)
})

MASK = "***REDACTED***"


def present_values() -> list[str]:
    """Non-empty sensitive values currently set in the environment."""
    return [v for k in SENSITIVE_ENV if (v := os.environ.get(k))]


def redact_text(text: str, secrets: list[str] | None = None) -> str:
    """Replace every occurrence of a sensitive value in ``text`` with a mask.

    Defaults to the values currently in the environment, so callers can simply do
    ``redact_text(str(exc))`` at an error boundary.
    """
    if not text:
        return text
    for s in (present_values() if secrets is None else secrets):
        if s:
            text = text.replace(s, MASK)
    return text


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """A copy of ``env`` safe to log: sensitive keys masked."""
    return {k: (MASK if (k in SENSITIVE_ENV and v) else v) for k, v in env.items()}
