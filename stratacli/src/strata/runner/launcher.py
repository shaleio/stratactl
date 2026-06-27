"""Launch the ephemeral runner inside the target account.

MVP execution surface: AWS CodeBuild (a project + an IAM service role — far less
to operate than ECS/Fargate for "run a tofu script and stream logs"). The launcher
is a Protocol so a FargateRunner can drop in later without touching callers.
"""

from __future__ import annotations

from typing import Protocol

from strata.config.models import Settings
from strata.errors import RunnerError


class Runner(Protocol):
    def start(self, session, *, job_id: str, env: dict[str, str]) -> dict:
        """Launch the runner with the given job spec env. Returns a launch handle."""
        ...


class CodeBuildRunner:
    def __init__(self, project_name: str):
        self.project_name = project_name

    def start(self, session, *, job_id: str, env: dict[str, str]) -> dict:
        cb = session.client("codebuild")
        # Some of these are sensitive (see strata.runner.sensitive.SENSITIVE_ENV) —
        # chiefly the short-lived presigned artifact URL. CodeBuild has no per-variable
        # redaction for PLAINTEXT overrides, so the value is visible in the build's
        # environment metadata to in-account principals; we accept that because it is
        # ephemeral (<= the presign TTL), single-object and read-only. The launcher never
        # logs override values, and the runner scrubs them from any error output.
        overrides = [{"name": k, "value": str(v), "type": "PLAINTEXT"}
                     for k, v in env.items()]
        try:
            resp = cb.start_build(
                projectName=self.project_name,
                environmentVariablesOverride=overrides,
            )
        except Exception as exc:
            raise RunnerError(f"could not start CodeBuild runner: {exc}") from exc

        build = resp["build"]
        return {
            "build_id": build["id"],
            "log_group": build.get("logs", {}).get("groupName"),
            "log_stream": build.get("logs", {}).get("streamName"),
        }


def for_settings(settings: Settings) -> Runner:
    # Only CodeBuild for MVP; swap on a future settings.runner field.
    return CodeBuildRunner(project_name=settings.customer.codebuild_project)
