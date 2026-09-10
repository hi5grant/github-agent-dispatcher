from __future__ import annotations

import logging
from pathlib import Path

from github_agent_dispatcher.agents.base import AgentBackend
from github_agent_dispatcher.config import AppConfig
from github_agent_dispatcher.git.lock import RepoLock
from github_agent_dispatcher.git.repository import GitError, Repository
from github_agent_dispatcher.github.client import GitHubAPIError, GitHubClient
from github_agent_dispatcher.jobs.models import AgentOutcome, Job, JobStatus
from github_agent_dispatcher.jobs.queue import JobQueue
from github_agent_dispatcher.reconciliation import marker_lines

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    def __init__(self, message: str, output: str = ""):
        super().__init__(message)
        self.output = output


def _run_validation_command(command: str, cwd: Path) -> ValidationError | None:
    """Run one validation command; returns an error describing the failure or None."""
    import subprocess

    logger.info("[validation] running %s", command)
    try:
        proc = subprocess.run(
            command.split(),
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return ValidationError(f"validation command could not run: {exc}")
    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return ValidationError(f"validation failed (exit {proc.returncode}): {command}", output)
    logger.info("[validation] passed %s", command)
    return None


class ValidationRunner:
    def __init__(self, commands: list[str] | None = None):
        self.commands = commands or []

    def run(self, cwd: Path) -> list[ValidationError]:
        errors: list[ValidationError] = []
        for command in self.commands:
            err = _run_validation_command(command, cwd)
            if err:
                errors.append(err)
                break
        return errors


class JobWorker:
    def __init__(
        self,
        config: AppConfig,
        queue: JobQueue,
        github: GitHubClient,
        backend: AgentBackend,
        data_dir: Path | None = None,
    ):
        self.config = config
        self.queue = queue
        self.github = github
        self.backend = backend
        self.data_dir = data_dir or config.data_dir
        self._lock_dir = Path(self.data_dir) / "locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- execution
    def try_process(self, job: Job) -> bool:
        """Process one job; returns False if the repository lock is busy."""
        lock_manager = RepoLock(self.lock_path_for(job.repo))
        if not lock_manager.acquire(timeout=0):
            logger.info("[lock] repository %s busy; job %s stays queued", job.repo, job.id)
            return False
        try:
            self._process_locked(job)
        except KeyboardInterrupt:  # pragma: no cover
            self.queue.save(job)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("[job] unexpected failure for %s", job.id)
            self._finish(job, AgentOutcome.FAILED, error=str(exc))
        finally:
            lock_manager.release()
        return True

    def lock_path_for(self, repo: str) -> Path:
        safe = repo.replace("/", "--").replace(":", "") or "repo"
        return self._lock_dir / f"{safe}.lock"

    def _process_locked(self, job: Job) -> None:
        job.tries += 1
        job.status = JobStatus.RUNNING
        job.started_at = Job.now()
        self.queue.save(job)
        logger.info("[job] starting id=%s repo=%s branch=%s", job.id, job.repo, job.branch or "-")

        try:
            self._prepare_and_run(job)
        except (GitError, GitHubAPIError) as exc:
            logger.error("[job] git/github error for %s: %s", job.id, exc)
            self._finish(job, AgentOutcome.FAILED, error=str(exc))
        except ValidationError as exc:
            logger.error("[job] validation failed for %s: %s", job.id, exc)
            self._finish(job, AgentOutcome.VALIDATION_FAILED, error=str(exc)[:4000])

    def _prepare_and_run(self, job: Job) -> None:
        repo = Repository(self._resolve_path(job))
        if not repo.exists():
            if not self.config.auto_clone:
                self._finish(
                    job,
                    AgentOutcome.BLOCKED,
                    error="repository is not cloned locally and AUTO_CLONE is disabled",
                )
                return
            info = self.github.get_repository(job.repo)
            repo.clone(info.clone_url, depth=None)
            logger.info("[git] cloned %s -> %s", job.repo, repo.path)

        ok, message = repo.ensure_clean_for_work()
        if not ok:
            self._finish(job, AgentOutcome.BLOCKED, error=message)
            return

        if job.pull_number is not None:
            ok, message = self._prepare_pr_branch(job, repo)
            if not ok:
                self._finish(job, AgentOutcome.BLOCKED, error=message)
                return
        else:
            repo.fetch(force=False)
            requested_branch = job.branch or f"agent/issue-{job.issue_number or 'x'}"
            if repo.current_branch() != requested_branch:
                repo.checkout(requested_branch, create=True)
            logger.info(
                "[git] working on branch %s",
                repo.current_branch() or "(detached)",
            )
        job.branch = repo.current_branch()
        self.queue.save(job)

        self._execute_agent(job, repo)

    def _prepare_pr_branch(self, job: Job, repo: Repository) -> tuple[bool, str]:
        pr_number = job.pull_number
        if pr_number is None:
            return False, "cannot determine the PR number"
        pr = self.github.get_pull_request(job.repo, pr_number)
        if not pr.head_ref:
            return False, "could not determine the PR head branch"
        if pr.head_repo_full_name and pr.head_repo_full_name.split("/")[0] != self.config.token_owner:
            logger.warning("[git] PR %s head is in fork %s", pr.number, pr.head_repo_full_name)
        head_repo_same = pr.head_repo_full_name and pr.head_repo_full_name.lower() == job.repo.lower()
        remote_name = "origin" if head_repo_same else f"dispatch-{pr.owner}"
        if repo.origin_url() and remote_name == "origin":
            self._verify_origin_matches(job, repo)
        repo.ensure_remote(remote_name, pr.head_clone_url or f"https://github.com/{job.repo}.git")
        repo.fetch(remote_name, force=True)
        repo.checkout(pr.head_ref, create=True, start_point=f"{remote_name}/{pr.head_ref}")
        current = repo.current_branch()
        if current != pr.head_ref:
            return False, f"expected branch {pr.head_ref} but got {current}"
        return True, pr.head_ref

    def _verify_origin_matches(self, job: Job, repo: Repository) -> None:
        url = repo.origin_url() or ""
        expected = f"github.com/{job.repo.lower()}"
        if expected not in url.lower().replace("git@", "github.com/"):
            logger.warning("[git] origin %r does not obviously match %s; continuing anyway", url, job.repo)

    def _execute_agent(self, job: Job, repo: Repository) -> None:
        context = dict(job.context)
        context["_job"] = job
        logger.info("[agent] starting %s", type(self.backend).__name__)
        result = self.backend.run(job.request_text, context, repo.path)

        if not result.success:
            detail = result.error or "agent reported failure"
            logger.warning("[agent] failed for %s: %s", job.id, detail)
            self._finish(job, AgentOutcome.FAILED, error=detail)
            return

        logger.info("[agent] completed for %s", job.id)

        if not repo.status_porcelain().strip():
            self._finish(
                job,
                AgentOutcome.NO_CHANGES,
                error="agent produced no changes",
                output=result.output,
            )
            return

        validation_errors = ValidationRunner(self.config.commands_for(job.repo)).run(repo.path)
        if validation_errors:
            details = "\n".join(f"- {e}" for e in validation_errors)
            self._finish(
                job,
                AgentOutcome.VALIDATION_FAILED,
                error=details[:4000],
                output=result.output,
            )
            return

        self._commit_and_push(job, repo, result.output)

    def _commit_and_push(self, job: Job, repo: Repository, agent_output: str) -> None:
        branch = job.branch or repo.current_branch() or ""
        remote = self._push_remote(job)
        logger.info("[git] committing on branch %s", branch)
        repo.stage_all()
        message = self._commit_message(job)
        sha = repo.commit(message)
        logger.info("[git] committed %s on %s", sha, branch)
        repo.push(remote, branch)
        logger.info("[git] pushed %s to %s/%s", sha, remote, branch)

        job.commit_sha = sha[:12]
        job.status = JobStatus.SUCCEEDED
        job.result = AgentOutcome.SUCCESS
        self.queue.save(job)

        if job.pull_number is None and self.config.auto_create_pr:
            self._maybe_create_pr(job)

        self.post_feedback(job, repo)
        self._finish(job, AgentOutcome.SUCCESS)

    def _push_remote(self, job: Job) -> str:
        if job.pull_number is None:
            return "origin"
        pr = self.github.get_pull_request(job.repo, job.pull_number)
        if pr.head_repo_full_name and pr.head_repo_full_name.lower() == job.repo.lower():
            return "origin"
        if pr.head_repo_full_name:
            logger.warning(
                "[git] pushing to fork %s; requires credentials for that fork",
                pr.head_repo_full_name,
            )
        return "origin"

    def _maybe_create_pr(self, job: Job) -> None:
        repo = Repository(self._resolve_path(job))
        branch = job.branch or repo.current_branch()
        if not branch:
            return
        try:
            info = self.github.get_repository(job.repo)
            title = f"agent: address issue #{job.issue_number}"
            body = f"Automated PR from the dispatcher job `{job.id}` for issue #{job.issue_number}."
            pr = self.github.create_pull_request(job.repo, branch, info.default_branch, title, body)
            job.pr_url = pr.get("html_url")
            self.queue.save(job)
            logger.info("[github] created PR %s", job.pr_url)
        except GitHubAPIError as exc:
            logger.warning("[github] could not create PR for %s: %s", job.id, exc)

    def _commit_message(self, job: Job) -> str:
        if job.pull_number is not None:
            return f"agent: address PR #{job.pull_number} feedback"
        if job.issue_number is not None:
            return f"agent: address issue #{job.issue_number}"
        return "agent: address GitHub request"

    # ------------------------------------------------------------------ calls
    def _resolve_path(self, job: Job) -> Path:
        from github_agent_dispatcher.workspace.manager import WorkspaceManager

        return WorkspaceManager(self.config.workspace_root).resolve(job.repo)

    def post_feedback(self, job: Job, repo: Repository | None = None) -> None:
        if self.queue.is_processed("feedback", job.repo, job.id):
            return
        body = self._feedback_body(job)
        if not body:
            return
        target = job.pull_number or job.issue_number
        if not target:
            return
        try:
            self.github.post_comment(job.repo, int(target), body)
            self.queue.mark_processed("feedback", job.repo, job.id)
            logger.info("[github] posted result for %s", job.id)
        except GitHubAPIError as exc:
            logger.warning("[github] could not post feedback for %s: %s", job.id, exc)

    def _feedback_body(self, job: Job) -> str:
        human, marker = marker_lines(job)
        footer = f"\n\n{human}\n{marker}"
        if job.status == JobStatus.SUCCEEDED:
            lines = [
                "🤖 Agent completed this request.",
                "",
                f"- Repository: `{job.repo}`",
                f"- Job: `{job.id}`",
                f"- Commit: `{job.commit_sha or 'unknown'}`",
                "- Tests: passed on the configured validation commands",
            ]
            if job.pr_url:
                lines.append(f"- Pull request: {job.pr_url}")
            return "\n".join(lines) + footer
        if job.result == AgentOutcome.VALIDATION_FAILED:
            return (
                "🤖 Agent attempted this request but validation failed.\n\n"
                "No changes were pushed.\n\n"
                f"Failure details:\n```\n{job.error or 'unknown'}\n```" + footer
            )
        if job.status == JobStatus.BLOCKED:
            return (
                "🤖 Agent could not start this request because the local repository "
                "contains pre-existing uncommitted changes.\n\n"
                f"Reason: {job.error or 'workspace not clean'}" + footer
            )
        if job.status == JobStatus.FAILED:
            return (
                f"🤖 Agent attempted this request but failed.\n\nError:\n```\n{job.error or 'unknown'}\n```"
                + footer
            )
        return ""

    # ---------------------------------------------------------- state updates
    def _finish(
        self,
        job: Job,
        outcome: str,
        error: str | None = None,
        output: str = "",
    ) -> None:
        job.completed_at = Job.now()
        job.error = error
        job.result = outcome
        if outcome == AgentOutcome.SUCCESS:
            job.status = JobStatus.SUCCEEDED
        elif outcome == AgentOutcome.BLOCKED:
            job.status = JobStatus.BLOCKED
        elif outcome == AgentOutcome.VALIDATION_FAILED:
            job.status = JobStatus.FAILED
        elif outcome == AgentOutcome.NO_CHANGES:
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.FAILED
        feedback_outcomes = {
            AgentOutcome.BLOCKED,
            AgentOutcome.VALIDATION_FAILED,
            AgentOutcome.FAILED,
            AgentOutcome.NO_CHANGES,
        }
        if outcome in feedback_outcomes:
            self.post_feedback(job)
        self.queue.save(job)
        logger.info("[job] finished id=%s status=%s", job.id, job.status)
