"""boto3 session/clients for the TARGET (customer) account.

The operator's own AWS credentials drive everything here — Shaleio never holds
customer-account credentials.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import boto3

from strata.config.models import Settings


def make_session(settings: Settings) -> boto3.Session:
    return boto3.Session(profile_name=settings.profile, region_name=settings.region)


@lru_cache(maxsize=1)
def account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def client(session: boto3.Session, service: str):
    return session.client(service)


def caller_arn(session: boto3.Session) -> Optional[str]:
    try:
        return session.client("sts").get_caller_identity()["Arn"]
    except Exception:
        return None
