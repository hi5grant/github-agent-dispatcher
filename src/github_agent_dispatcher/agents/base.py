from __future__ import annotations

import abc
import json
from pathlib import Path

from github_agent_dispatcher.jobs.models import Job


class AgentResult:
    def __init__(self, success: bool, output: str = "", error: str = ""):
        self.success = success
        self.output = output
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AgentResult(success={self.success}, error={self.error!r})"


class AgentBackend(abc.ABC):
    """Abstraction over a local coding agent CLI.

    Implementations receive the carefully sanitized request text plus an
    ``AgentContext`` payload describing the GitHub request.  They are expected to
    modify the working directory they are launched from and nothing else.
    """

    telemetry_file: str | None = None

    @abc.abstractmethod
    def run(self, request_text: str, context: dict, cwd: Path) -> AgentResult: ...


INSTRUCTIONS = """You are operating on a local repository ({repo}) in response to a GitHub request.

CONTEXT
------
{context}

REQUEST
-------
{request}

RULES
-----
- Inspect the repository before making any changes. Understand the existing code,
  tests, and conventions first.
- Implement the requested change. Prefer the smallest change that satisfies the request.
- Do not modify unrelated functionality.
- Run the relevant tests, linting, formatting, type checks, and any other validation
  that the repository uses. Fix any failures you introduce.
- Do not commit and do not push. The dispatcher owns all git commit and push
  operations and will review your changes first.
- Do not modify files outside this repository's working directory.
"""


def build_prompt(job: Job) -> str:
    context = json.dumps({"request": job.request_text, "job": _job_context(job)}, indent=2)
    return INSTRUCTIONS.format(repo=job.repo, context=context, request=job.request_text)


def _job_context(job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "repository": job.repo,
        "issue_number": job.issue_number,
        "pull_number": job.pull_number,
        "branch": job.branch,
        "requester": job.requester,
        "comment_id": job.comment_id,
        "url": job.url(),
        "extra": job.context,
    }
