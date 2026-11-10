"""Pure input validators for the installation wizard.

Each validator takes a single (already string-trimmed) value and raises
``ValueError`` with a human message when the value is invalid; otherwise it
returns ``None``. They are referenced by name from schema fields, so prompting
and validation stay decoupled — the prompt engine looks a validator up here and
calls it, and the same functions are unit-tested directly.
"""

from __future__ import annotations

import re
from typing import Callable

_REGION = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
_VPC = re.compile(r"^vpc-[0-9a-f]{8,}$")
_SUBNET = re.compile(r"^subnet-[0-9a-f]{8,}$")
_CIDR = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,20}$")
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def nonempty(value: str) -> None:
    if not str(value).strip():
        raise ValueError("a value is required")


def aws_region(value: str) -> None:
    if not _REGION.match(str(value)):
        raise ValueError("not a valid AWS region (e.g. us-east-1)")


def vpc_id(value: str) -> None:
    if not _VPC.match(str(value)):
        raise ValueError("must look like vpc-0123abcd...")


def subnet_id(value: str) -> None:
    if not _SUBNET.match(str(value)):
        raise ValueError("must look like subnet-0123abcd...")


def cidr(value: str) -> None:
    if not _CIDR.match(str(value)):
        raise ValueError("must be a CIDR block, e.g. 203.0.113.0/24")
    octets = str(value).split("/")[0].split(".")
    if any(int(o) > 255 for o in octets):
        raise ValueError("CIDR octets must be 0-255")
    if int(str(value).split("/")[1]) > 32:
        raise ValueError("CIDR prefix must be 0-32")


def name_prefix(value: str) -> None:
    if not _NAME.match(str(value)):
        raise ValueError("lowercase, start with a letter, digits/hyphens, <= 21 chars")


def ecr_image(value: str) -> None:
    v = str(value).strip()
    if not v:
        return  # optional override; empty means "build in-account" (E2)
    if "/" not in v or ":" not in v.rsplit("/", 1)[-1]:
        raise ValueError(
            "expected an image ref like "
            "<acct>.dkr.ecr.<region>.amazonaws.com/stratabi:latest"
        )


def domain(value: str) -> None:
    v = str(value).strip().lower()
    if v and not _DOMAIN.match(v):
        raise ValueError("not a valid domain name (e.g. hq.example.com)")


def acm_arn(value: str) -> None:
    v = str(value).strip()
    if v and not v.startswith("arn:aws:acm:"):
        raise ValueError("expected an ACM certificate ARN (arn:aws:acm:...)")


# Name -> validator. Schema fields reference these by string so the schema stays
# declarative and serializable.
REGISTRY: dict[str, Callable[[str], None]] = {
    "nonempty": nonempty,
    "aws_region": aws_region,
    "vpc_id": vpc_id,
    "subnet_id": subnet_id,
    "cidr": cidr,
    "name_prefix": name_prefix,
    "ecr_image": ecr_image,
    "domain": domain,
    "acm_arn": acm_arn,
}


def get(name: str | None) -> Callable[[str], None] | None:
    if not name:
        return None
    return REGISTRY.get(name)
