"""Typed errors for StrataCLI. The CLI maps these to clean exit codes/messages."""


class StrataError(Exception):
    """Base class for all expected StrataCLI/runner errors."""


class ConfigError(StrataError):
    """Configuration could not be resolved or is invalid."""


class EntitlementDenied(StrataError):
    """StrataHQ denied entitlement for the requested module/version."""


class ManifestError(StrataError):
    """The module bundle manifest is missing or invalid."""


class ArtifactError(StrataError):
    """The artifact could not be fetched, verified, or extracted."""


class RunnerError(StrataError):
    """The runner could not be launched or failed during execution."""


class RegistryError(StrataError):
    """A registry/job-store operation failed."""
