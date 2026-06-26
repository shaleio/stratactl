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
