from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


class JobType(str):
    ISSUE = "issue"
    ISSUE_COMMENT = "issue_comment"
    PULL_COMMENT = "pull_comment"
    REVIEW_COMMENT = "review_comment"


class JobStatus(str):
    DISCOVERED = "DISCOVERED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class AgentOutcome(str):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NO_CHANGES = "NO_CHANGES"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


@dataclass
class Job:
    id: str
    type: str
    repo: str
    issue_number: int | None
    pull_number: int | None
    branch: str | None
    comment_id: int | None
    requester: str
    request_text: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    commit_sha: str | None = None
    pr_url: str | None = None
    context: dict = field(default_factory=dict)
    result: str | None = None
    error: str | None = None
    tries: int = 0

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def dedup_topic(self) -> str:
        if self.type == JobType.ISSUE:
            return "issue"
        if self.type == JobType.ISSUE_COMMENT:
            return "issue_comment"
        if self.type == JobType.PULL_COMMENT:
            return "pull_comment"
        if self.type == JobType.REVIEW_COMMENT:
            return "review_comment"
        return "issue"

    @property
    def dedup_id(self) -> str:
        if self.type in {JobType.ISSUE_COMMENT, JobType.PULL_COMMENT, JobType.REVIEW_COMMENT}:
            return f"comment:{self.comment_id}"
        return f"issue:#{self.issue_number}"

    def url(self) -> str:
        ctx = self.context
        if ctx.get("pr_url"):
            return ctx["pr_url"]
        if ctx.get("issue_url"):
            return ctx["issue_url"]
        return ""

    def display_name(self) -> str:
        if self.pull_number is not None:
            return f"{self.repo}#PR{self.pull_number}"
        if self.issue_number is not None:
            return f"{self.repo}#{self.issue_number}"
        return self.repo
