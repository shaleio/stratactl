"""Stream CodeBuild runner logs to the local terminal — the 'remote terminal' feel.

Polls the build status and tails its CloudWatch log stream until the build
reaches a terminal state.
"""

from __future__ import annotations

import sys
import time
from typing import Optional


def stream_build(session, build_id: str, poll_seconds: int = 3) -> str:
    """Tail a CodeBuild build's logs to stdout. Returns the final build status."""
    cb = session.client("codebuild")
    logs = session.client("logs")

    group: Optional[str] = None
    stream: Optional[str] = None
    next_token: Optional[str] = None
    terminal = {"SUCCEEDED", "FAILED", "FAULT", "TIMED_OUT", "STOPPED"}

    while True:
        builds = cb.batch_get_builds(ids=[build_id]).get("builds", [])
        if not builds:
            return "UNKNOWN"
        build = builds[0]
        status = build.get("buildStatus", "IN_PROGRESS")

        log_info = build.get("logs", {})
        group = group or log_info.get("groupName")
        stream = stream or log_info.get("streamName")

        if group and stream:
            next_token = _drain(logs, group, stream, next_token)

        if status in terminal:
            # One final drain to catch the last lines.
            if group and stream:
                _drain(logs, group, stream, next_token)
            return status

        time.sleep(poll_seconds)


def _drain(logs, group: str, stream: str, token: Optional[str]) -> Optional[str]:
    kwargs = {
        "logGroupName": group,
        "logStreamName": stream,
        "startFromHead": True,
    }
    if token:
        kwargs["nextToken"] = token
    try:
        resp = logs.get_log_events(**kwargs)
    except logs.exceptions.ResourceNotFoundException:
        return token
    for event in resp.get("events", []):
        sys.stdout.write(event["message"])
        if not event["message"].endswith("\n"):
            sys.stdout.write("\n")
    sys.stdout.flush()
    return resp.get("nextForwardToken", token)


def replay(session, group: str, stream: str) -> None:
    """Replay an archived log stream (used by `stratactl logs <job-id>`)."""
    _drain(session.client("logs"), group, stream, None)
