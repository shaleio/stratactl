"""Declarative installation schema.

A schema is a flat, ordered list of ``Field`` objects grouped into config-file
``section``s. Each field knows where it lives in the YAML config (``section`` +
``key``) and, optionally, the Terraform variable name it maps to (``tfvar``).
Fields with ``tfvar=None`` are configuration-only (e.g. an organization label)
and are never written to ``.tfvars``.

This is the only place the StrataBI variable set is described; the prompt engine,
the YAML store, and the tfvars generator all read from it. Modules supply their
own schema the same way (see ``modules.py``), so no module name is hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Field:
    section: str                       # YAML config section (installation, network, ...)
    key: str                           # YAML key within the section (unique across schema)
    prompt: str                        # question shown to the user
    tfvar: Optional[str] = None        # Terraform variable name; None = config-only
    type: str = "string"               # string | bool | int | list | enum
    default: Any = None
    required: bool = False
    secret: bool = False
    choices: Optional[list] = None      # for type == "enum"
    validator: Optional[str] = None     # validators.REGISTRY name (per scalar / per list item)
    help: str = ""
    when: Optional[tuple] = None        # (other_key, expected_value): only prompt when it matches


@dataclass
class InstallSchema:
    name: str
    title: str
    fields: list[Field] = field(default_factory=list)

    def by_key(self, key: str) -> Optional[Field]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def sections(self) -> list[str]:
        seen: list[str] = []
        for f in self.fields:
            if f.section not in seen:
                seen.append(f.section)
        return seen

    def tfvar_fields(self) -> list[Field]:
        return [f for f in self.fields if f.tfvar]


# --------------------------------------------------------------------------- #
# StrataBI Enterprise / Developer core installation schema.
#
# Covers the four Terraform variables that have no default (vpc_id,
# public_subnet_ids, private_subnet_ids, stratabi_image) plus the values a new
# customer most commonly sets. Deep-tuning variables (cpu/memory, themes, athena
# sharding, ...) intentionally keep their Terraform defaults and are not prompted;
# they can still be set by editing strata.yaml and re-running.
# --------------------------------------------------------------------------- #
STRATABI_SCHEMA = InstallSchema(
    name="stratabi",
    title="StrataBI deployment",
    fields=[
        # installation
        Field("installation", "name", "Installation name (also the Terraform name_prefix)",
              tfvar="name_prefix", default="stratabi", required=True, validator="name_prefix",
              help="lowercase, <=21 chars; isolates resources when running >1 install per account"),
        Field("installation", "aws_region", "AWS region",
              tfvar="region", default="us-east-1", required=True, validator="aws_region"),
        Field("installation", "organization", "Organization name (label only)",
              tfvar=None, default="", help="recorded in the config; not a Terraform variable"),
        Field("installation", "edition", "Edition",
              tfvar=None, type="enum", choices=["enterprise", "developer"], default="enterprise",
              help="enterprise = licensed; developer = source-available community edition"),
        Field("installation", "domain", "Public domain (optional, informational)",
              tfvar="domain_name", default="", validator="domain",
              help="DNS is managed outside this module; leave blank if none"),

        # runtime image — OPTIONAL override only (E2). By default the platform module
        # builds the entitled runtime in-account and pushes it to the ECR repo it creates,
        # so the customer does NOT supply an image. Set this only to run your own registry.
        Field("runtime", "stratabi_image", "StrataBI container image (ECR URL) — optional override; leave blank to build in-account",
              tfvar="stratabi_image", required=False, default="", validator="ecr_image",
              help="Leave blank: the install builds + pushes the image for you. "
                   "Override example: <acct>.dkr.ecr.us-east-1.amazonaws.com/my-stratabi:1.7.1"),

        # network
        Field("network", "vpc_id", "VPC id",
              tfvar="vpc_id", required=True, validator="vpc_id"),
        Field("network", "public_subnet_ids", "Public subnet ids (for the ALB)",
              tfvar="public_subnet_ids", type="list", required=True, validator="subnet_id"),
        # Ask whether to use private subnets BEFORE prompting for them, and only prompt
        # for the ids when the answer is yes (otherwise it demanded private subnet ids the
        # user explicitly didn't want, and looped on "a value is required").
        Field("network", "enable_private_subnets", "Place ECS tasks in private subnets?",
              tfvar="enable_private_subnets", type="bool", default=False),
        Field("network", "private_subnet_ids", "Private subnet ids (for ECS tasks)",
              tfvar="private_subnet_ids", type="list", required=False, default=[],
              validator="subnet_id", when=("enable_private_subnets", True)),
        Field("network", "allowed_ip_cidrs", "Allowed inbound CIDRs (empty = open 0.0.0.0/0)",
              tfvar="allowed_ip_cidrs", type="list", default=[], validator="cidr",
              help="lock this down for anything beyond a private demo"),

        # tls
        Field("tls", "enable_https", "Enable HTTPS/TLS on the load balancer?",
              tfvar="enable_https", type="bool", default=False),
        Field("tls", "acm_certificate_arn", "ACM certificate ARN",
              tfvar="acm_certificate_arn", default="", validator="acm_arn",
              when=("enable_https", True)),

        # features
        Field("features", "enable_rbac", "Enable role-based access control (deny-by-default)?",
              tfvar="enable_rbac", type="bool", default=False),
        Field("features", "rbac_bootstrap_admin", "Bootstrap admin user id (always-admin while RBAC is on)",
              tfvar="rbac_bootstrap_admin", default="", when=("enable_rbac", True)),
        Field("features", "enable_ai", "Enable the AI dashboard builder (needs Bedrock access)?",
              tfvar="enable_ai", type="bool", default=True),

        # security (the opt-in AWS-native integrations)
        Field("security", "enable_guardduty", "Enable Amazon GuardDuty? (AWS bills this)",
              tfvar="enable_guardduty", type="bool", default=False),
        Field("security", "enable_inspector", "Enable Amazon Inspector? (AWS bills this)",
              tfvar="enable_inspector", type="bool", default=False),
        Field("security", "enable_securityhub", "Enable AWS Security Hub? (AWS bills this)",
              tfvar="enable_securityhub", type="bool", default=False),
    ],
)


def stratabi_schema() -> InstallSchema:
    return STRATABI_SCHEMA
