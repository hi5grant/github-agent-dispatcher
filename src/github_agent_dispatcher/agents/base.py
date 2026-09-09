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

GITHUB COMMUNICATION
--------------------
You MUST reply to the requester on the originating issue or pull request by
posting comments at STAGES through the GitHub API. The comment posting URL is:

    {comments_api_url}

Use the following command pattern (the environment variable GITHUB_TOKEN is
available to you). Always build the JSON body carefully and escape it properly
(single-quote it and use a temp file if the text is long):

    curl -sS -X POST \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      -d '{{"body": "YOUR COMMENT TEXT"}}' \
      "{comments_api_url}"

In this order:

1. ACKNOWLEDGMENT - Immediately, before doing anything else, post a single
   comment consisting of the robot emoji "🤖" plus a one-line acknowledgment
   that you received the request (e.g. "🤖 Acknowledged, investigating now.").
2. ANALYSIS - Next, post a comment describing your analysis of the request:
   what the current code does, the root cause or required change, and your
   plan to address it.
3. IMPLEMENT - Do the fix in the working directory. Do NOT respond to the
   comment here; just make the change.
4. RESULTS - After implementing and running validation, post a final comment
   reporting clearly whether the change was applied AND passed validation or
   failed. If validation failed, include the failure details. Say "PASS" or
   "FAIL" explicitly. This comment is required regardless of the outcome.

If any step above fails (for example the comment cannot be posted), continue the
work and note the failure in your final output so the dispatcher can report it.
"""


def build_prompt(job: Job) -> str:
    context = json.dumps({"request": job.request_text, "job": _job_context(job)}, indent=2)
    return INSTRUCTIONS.format(
        repo=job.repo,
        context=context,
        request=job.request_text,
        comments_api_url=_comments_api_url(job),
    )


def _comments_api_url(job: Job) -> str:
    target = job.pull_number or job.issue_number
    return f"https://api.github.com/repos/{job.repo}/issues/{target}/comments"


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
