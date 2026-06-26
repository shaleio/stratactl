"""Typed configuration objects. Constructed once by strata.config.provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HQConfig:
    endpoint: str = ""
    artifact_bucket: str = ""
    region: str = "us-east-1"
    auth: str = "license_token"
    token: Optional[str] = None  # resolved from env/file/SSM, never from defaults.toml
    public_key: Optional[str] = None  # base64-DER signing public key (Decision C, offline verify)
    require_signature: bool = False   # enforce bundle signature verification in the runner
    discovery_url: str = ""           # stable CDN URL of the StrataHQ discovery doc (migration-resilient)


@dataclass
class CustomerConfig:
    """Names of the customer-account StrataCI resources (from the bootstrap infra)."""

    bucket: str = ""  # "" => derived at runtime: strataci-<account_id>-<region>
    install_table: str = "strataci_install_registry"
    jobs_table: str = "strataci_jobs"
    lock_table: str = "strataci_tf_locks"
    log_group: str = "/strataci/runner"
    codebuild_project: str = "strataci-runner"


@dataclass
class DashboardsConfig:
    """StrataBI dashboard git-sync targets (the runtime deployment's resources)."""

    bucket: str = ""                                  # StrataBI system bucket holding dashboards
    git_registry_table: str = ""                      # e.g. stratabi-dashboard-git-registry
    pinned_table: str = ""                            # optional; for --pinned
    prefix: str = "analyst/dashboards"
    local_dir: str = "dashboards"                     # local git working dir


@dataclass
class RuntimeConfig:
    """How `stratacli fetch-runtime` pulls the account-entitled StrataBI runtime."""

    module_id: str = "stratabi-runtime"   # catalog id of the runtime artifact
    dist_dir: str = "dist"                # where the fetched wheel is written (docker build context)


@dataclass
class Settings:
    hq: HQConfig = field(default_factory=HQConfig)
    customer: CustomerConfig = field(default_factory=CustomerConfig)
    dashboards: DashboardsConfig = field(default_factory=DashboardsConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    source: str = "stratahq"          # default artifact source
    profile: Optional[str] = None     # AWS profile for the target account
    region: str = "us-east-1"         # target-account region

    def resolved_bucket(self, account_id: str) -> str:
        """Customer bucket, deriving the conventional name if unset."""
        if self.customer.bucket:
            return self.customer.bucket
        return f"strataci-{account_id}-{self.region}"
