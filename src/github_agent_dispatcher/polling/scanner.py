from __future__ import annotations

import logging
import time

from github_agent_dispatcher.config import AppConfig
from github_agent_dispatcher.github.client import GitHubClient, Issue, PullRequest
from github_agent_dispatcher.github.issues import issue_trigger_candidates, pr_trigger_candidates
from github_agent_dispatcher.github.repositories import resolve_repositories
from github_agent_dispatcher.jobs.models import Job, JobStatus, JobType, new_job_id
from github_agent_dispatcher.jobs.queue import JobQueue
from github_agent_dispatcher.reconciliation import parse_resolution_markers
from github_agent_dispatcher.security.authorization import Authorization, contains_trigger

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, config: AppConfig, client: GitHubClient, queue: JobQueue):
        self.config = config
        self.client = client
        self.queue = queue
        if not config.token_owner:
            try:
                config.token_owner = client.authenticated_user()
            except Exception as exc:  # noqa: BLE001 - resolved later by doctor
                logger.warning("[scan] could not determine token owner: %s", exc)
        self.auth = Authorization(
            allowed_repos=config.allowed_repos,
            allowed_users=config.allowed_users,
            token_owner=config.token_owner,
            allow_self=config.allow_self,
        )

    # ------------------------------------------------------------------ repo
    def repositories(self) -> list:
        return resolve_repositories(self.config, self.client)

    def scan(self) -> dict:
        started = time.monotonic()
        repo_infos = self.repositories()
        discovered = 0
        for info in repo_infos:
            repo = f"{info.owner}/{info.name}"
            discovered += self._scan_repo(repo)
        elapsed = time.monotonic() - started
        logger.info(
            "[scan] finished discovered=%d repos=%d in %.1fs",
            discovered,
            len(repo_infos),
            elapsed,
        )
        return {
            "repositories": len(repo_infos),
            "discovered": discovered,
            "elapsed_seconds": round(elapsed, 1),
        }

    def _scan_repo(self, repo: str) -> int:
        total = 0
        for issue, comments in issue_trigger_candidates(self.client, repo):
            resolved = self._thread_resolved(repo, comments)
            if self._maybe_issue_job(issue, resolved):
                total += 1
            total += self._scan_comments(issue, comments, resolved)
        for pr, comments, reviews in pr_trigger_candidates(self.client, repo):
            resolved = self._thread_resolved(repo, [*comments, *reviews])
            total += self._scan_pr(repo, pr, comments, reviews, resolved)
        return total

    # -------------------------------------------------------------- reconcile
    def _thread_resolved(self, repo: str, thread_comments: list) -> set[tuple[str, str]]:
        """Collect ``(topic, item_id)`` items this thread marks as already handled."""
        resolved: set[tuple[str, str]] = set()
        for comment in thread_comments:
            for topic, marker_repo, item_id in parse_resolution_markers(comment.body or ""):
                if marker_repo == repo:
                    resolved.add((topic, item_id))
        return resolved

    def _reconcile(self, topic: str, repo: str, item_id: str) -> None:
        """Record a Github-sourced resolution in local SQLite so a fresh database
        does not re-enqueue the item on the next scan."""
        self.queue.mark_processed(topic, repo, item_id)
        logger.info("[scan] reconciled from GitHub marker topic=%s repo=%s item=%s", topic, repo, item_id)

    # ----------------------------------------------------------------- issues
    def _maybe_issue_job(self, issue: Issue, resolved: set[tuple[str, str]]) -> bool:
        repo = f"{issue.owner}/{issue.name}"
        item_id = f"issue:#{issue.number}"
        if ("issue", item_id) in resolved:
            self._reconcile("issue", repo, item_id)
            return False
        if self.queue.is_processed("issue", repo, item_id):
            return False
        if not self._issue_matches_mode(issue):
            return False
        # An authorized assignee is sufficient in assigned/any modes; otherwise the
        # issue creator must be an authorized user.
        creator_ok = self.auth.user_allowed(issue.user)
        assignee_ok = any(self.auth.user_allowed(a) for a in issue.assignees)
        modes = {m for m in self.config.issue_discovery.split(",") if m}
        assignee_suffices = bool(modes & {"assigned", "any"})
        if not creator_ok and not (assignee_suffices and assignee_ok):
            return False
        job = Job(
            id=new_job_id(),
            type=JobType.ISSUE,
            repo=repo,
            issue_number=issue.number,
            pull_number=None,
            branch=None,
            comment_id=None,
            requester=issue.user,
            request_text=issue.title + "\n\n" + (issue.body or ""),
            status=JobStatus.QUEUED,
            created_at=Job.now(),
            context={
                "issue_url": issue.url,
                "issue_title": issue.title,
                "issue_body": issue.body or "",
                "labels": issue.labels,
                "assignees": issue.assignees,
            },
        )
        self.queue.enqueue(job)
        logger.info("[scan] discovered issue job repo=%s id=%s", repo, job.id)
        return True

    def _issue_matches_mode(self, issue: Issue) -> bool:
        modes = {m for m in self.config.issue_discovery.split(",") if m}
        if "any" in modes:
            return True
        if "mention" in modes and contains_trigger(issue.title, self.config.trigger):
            return True
        if "mention" in modes and contains_trigger(issue.body or "", self.config.trigger):
            return True
        if "assigned" in modes and any(self.auth.user_allowed(a) for a in issue.assignees):
            return True
        if "label:agent" in modes and "agent" in {label.lower() for label in issue.labels}:
            return True
        return False

    # ------------------------------------------------------------- comments
    def _scan_comments(self, issue: Issue, comments: list, resolved: set[tuple[str, str]]) -> int:
        repo = f"{issue.owner}/{issue.name}"
        total = 0
        for comment in comments:
            item_id = f"comment:{comment.id}"
            if ("issue_comment", item_id) in resolved:
                self._reconcile("issue_comment", repo, item_id)
                continue
            if not contains_trigger(comment.body, self.config.trigger):
                continue
            if not self.auth.user_allowed(comment.user):
                continue
            if self.queue.is_processed("issue_comment", repo, item_id):
                continue
            job = Job(
                id=new_job_id(),
                type=JobType.ISSUE_COMMENT,
                repo=repo,
                issue_number=issue.number,
                pull_number=None,
                branch=None,
                comment_id=comment.id,
                requester=comment.user,
                request_text=comment.body,
                status=JobStatus.QUEUED,
                created_at=Job.now(),
                context={
                    "issue_url": issue.url,
                    "comment_url": comment.url,
                    "issue_title": issue.title,
                },
            )
            self.queue.enqueue(job)
            logger.info(
                "[scan] discovered issue comment job repo=%s issue=%s id=%s",
                repo,
                issue.number,
                job.id,
            )
            total += 1
        return total

    # -------------------------------------------------------------------- pr
    def _scan_pr(
        self,
        repo: str,
        pr: PullRequest,
        comments: list,
        reviews: list,
        resolved: set[tuple[str, str]],
    ) -> int:
        total = 0
        if pr.state != "open":
            return total
        for comment in comments:
            item_id = f"comment:{comment.id}"
            if ("pull_comment", item_id) in resolved:
                self._reconcile("pull_comment", repo, item_id)
                continue
            if not contains_trigger(comment.body, self.config.trigger):
                continue
            if not self.auth.user_allowed(comment.user):
                continue
            if self.queue.is_processed("pull_comment", repo, item_id):
                continue
            job = Job(
                id=new_job_id(),
                type=JobType.PULL_COMMENT,
                repo=repo,
                issue_number=None,
                pull_number=pr.number,
                branch=pr.head_ref,
                comment_id=comment.id,
                requester=comment.user,
                request_text=comment.body,
                status=JobStatus.QUEUED,
                created_at=Job.now(),
                context={
                    "pr_url": pr.url,
                    "pr_title": pr.title,
                    "pr_body": pr.body or "",
                    "head_ref": pr.head_ref,
                    "head_sha": pr.head_sha,
                    "base_ref": pr.base_ref,
                    "head_repo_full_name": pr.head_repo_full_name,
                    "head_clone_url": pr.head_clone_url,
                    "comment_url": comment.url,
                },
            )
            self.queue.enqueue(job)
            logger.info("[scan] discovered PR comment job repo=%s pr=%s id=%s", repo, pr.number, job.id)
            total += 1

        for review in reviews:
            item_id = f"comment:{review.id}"
            if ("review_comment", item_id) in resolved:
                self._reconcile("review_comment", repo, item_id)
                continue
            if not contains_trigger(review.body, self.config.trigger):
                continue
            if not self.auth.user_allowed(review.user):
                continue
            if self.queue.is_processed("review_comment", repo, item_id):
                continue
            job = Job(
                id=new_job_id(),
                type=JobType.REVIEW_COMMENT,
                repo=repo,
                issue_number=None,
                pull_number=pr.number,
                branch=pr.head_ref,
                comment_id=review.id,
                requester=review.user,
                request_text=review.body,
                status=JobStatus.QUEUED,
                created_at=Job.now(),
                context={
                    "pr_url": pr.url,
                    "pr_title": pr.title,
                    "pr_body": pr.body or "",
                    "head_ref": pr.head_ref,
                    "head_sha": pr.head_sha,
                    "base_ref": pr.base_ref,
                    "head_repo_full_name": pr.head_repo_full_name,
                    "head_clone_url": pr.head_clone_url,
                    "comment_url": review.url,
                    "file_path": review.path,
                },
            )
            self.queue.enqueue(job)
            logger.info("[scan] discovered review comment job repo=%s pr=%s id=%s", repo, pr.number, job.id)
            total += 1
        return total
