from __future__ import annotations

import argparse
import logging
import shutil
import sys
import textwrap
from datetime import datetime, timezone

from github_agent_dispatcher import __version__
from github_agent_dispatcher.agents.opencode import OpenCodeBackend, RefusalBackend
from github_agent_dispatcher.config import AppConfig, load_config, validate_config
from github_agent_dispatcher.github.client import GitHubAPIError, GitHubClient
from github_agent_dispatcher.github.repositories import resolve_repositories
from github_agent_dispatcher.jobs.models import JobStatus
from github_agent_dispatcher.jobs.queue import JobQueue
from github_agent_dispatcher.service import Service
from github_agent_dispatcher.storage.database import Database
from github_agent_dispatcher.workspace.manager import WorkspaceManager


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _build(config: AppConfig) -> tuple[GitHubClient, Database]:
    client = GitHubClient(config.token, config.api_url)
    db = Database(config.database_path)
    return client, db


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=None, help="path to a .env file to load")


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _setup_logging(config.log_level)
    problems = validate_config(config)
    for p in problems:
        logging.getLogger(__name__).error("[config] %s", p)
    if any("is not set" in p for p in problems):
        return 1
    if not config.token:
        print("error: GITHUB_TOKEN is not set; run `agent-listener doctor` for help")
        return 1

    client, db = _build(config)
    backend = _make_backend(config)
    service = Service(config, db, client, backend)
    try:
        service.run_forever()
    except KeyboardInterrupt:
        service.stop()
    finally:
        db.close()
    return 0


def _make_backend(config: AppConfig):
    command = config.agent_command_for("__default__")
    if command:
        return OpenCodeBackend(command, config.agent_extra_args)
    return RefusalBackend()


def cmd_scan(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _setup_logging(config.log_level)
    client, db = _build(config)
    service = Service(config, db, client, _make_backend(config))
    result = service.scan_now()
    print(
        f"scan complete: discovered {result['discovered']} new jobs across {result['repositories']} repos "
        f"({result['elapsed_seconds']}s)"
    )
    db.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    client, db = _build(config)
    try:
        user = config.token_owner or client.authenticated_user()
    except GitHubAPIError as exc:
        print(f"GitHub: NOT connected ({exc})")
        user = config.token_owner or "unknown"
    print(f"Version:       {__version__}")
    try:
        rate = client.rate_limit()
        print(f"GitHub:        connected as {user} ({rate.summary})")
    except GitHubAPIError as exc:
        print(f"GitHub:        error ({exc})")
    repos = resolve_repositories(config, client)
    print(f"Repositories:  {len(repos)}")
    print(f"Polling:       every {config.poll_interval_seconds} seconds")
    last = db.get_meta("last_scan_at")
    if last:
        delta = seconds_since_utc(last)
        print(f"Last scan:     {delta} ago ({last})")
    else:
        print("Last scan:     never")
    counts = db.count_by_status()
    for status in [
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.BLOCKED,
        JobStatus.FAILED,
        JobStatus.SUCCEEDED,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
    ]:
        print(f"{status:<12} {counts.get(status, 0)}")
    db.close()
    return 0


def seconds_since_utc(iso: str) -> str:
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - parsed).total_seconds()
        if delta < 0:
            return "just now"
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta / 60)}m"
        return f"{int(delta / 3600)}h"
    except ValueError:
        return iso


def cmd_repos(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    client, _ = _build(config)
    for repo in resolve_repositories(config, client):
        visibility = "private" if repo.private else "public"
        print(f"{repo.owner}/{repo.name} ({visibility}, default={repo.default_branch})")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _, db = _build(config)
    status = args.status.upper() if args.status else None
    limit = args.limit or 20
    jobs = db.list_jobs(status=status, limit=limit)
    if not jobs:
        print("no jobs found")
    for job in jobs:
        print(f"{job.id}  {job.status:<12} {job.display_name():<32} {job.requester:<16} {job.created_at}")
    db.close()
    return 0


def cmd_job_show(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _, db = _build(config)
    job = db.get_job(args.job_id)
    if job is None:
        print(f"error: job {args.job_id!r} not found")
        return 1
    print(f"id:          {job.id}")
    print(f"repository:  {job.repo}")
    print(f"status:      {job.status}")
    print(f"type:        {job.type}")
    print(f"issue:       {job.issue_number}")
    print(f"pull:        {job.pull_number}")
    print(f"branch:      {job.branch}")
    print(f"requester:   {job.requester}")
    print(f"created:     {job.created_at}")
    print(f"started:     {job.started_at}")
    print(f"completed:   {job.completed_at}")
    print(f"commit:      {job.commit_sha}")
    print(f"result:      {job.result}")
    print(f"error:       {job.error}")
    print(f"url:         {job.url()}")
    print("request text:\n" + textwrap.indent(job.request_text, "    "))
    db.close()
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _, db = _build(config)
    queue = JobQueue(db)
    try:
        job = queue.retry(args.job_id)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    if job is None:
        print(f"error: job {args.job_id!r} not found")
        return 1
    print(f"retried {job.id}; status now QUEUED")
    db.close()
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _, db = _build(config)
    queue = JobQueue(db)
    try:
        job = queue.cancel(args.job_id)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    if job is None:
        print(f"error: job {args.job_id!r} not found")
        return 1
    print(f"cancelled {job.id}")
    db.close()
    return 0


def cmd_unblock(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    _, db = _build(config)
    queue = JobQueue(db)
    try:
        job = queue.unblock(args.job_id)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    if job is None:
        print(f"error: job {args.job_id!r} not found")
        return 1
    print(f"unblocked {job.id}; status now QUEUED")
    db.close()
    return 0


def _check_executable(name: str) -> tuple[bool, str]:
    path = shutil.which(name)
    if path:
        return True, f"{name} -> {path}"
    return False, f"{name} not found on PATH"


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.env_file)
    problems = validate_config(config)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", True, sys.version.split()[0]))
    checks.append(("config", not problems, "; ".join(problems) if problems else "configuration looks valid"))

    git_ok, git_msg = _check_executable("git")
    checks.append(("git", git_ok, git_msg))

    workspace = WorkspaceManager(config.workspace_root)
    try:
        workspace.ensure_root()
        checks.append(("workspace", True, f"workspace ready at {config.workspace_root}"))
    except OSError as exc:
        checks.append(("workspace", False, f"cannot create workspace: {exc}"))

    checks.append(("database", True, f"sqlite state db at {config.database_path}"))

    if config.token:
        checks.append(("github-token", True, "GITHUB_TOKEN is set (value hidden)"))
        try:
            client = GitHubClient(config.token, config.api_url)
            user = client.authenticated_user()
            if not config.token_owner:
                config.token_owner = user
            rate = client.rate_limit()
            checks.append(("github-auth", True, f"authenticated as {user}; {rate.summary}"))
            repos = resolve_repositories(config, client)
            if repos:
                checks.append(("github-repos", True, f"{len(repos)} monitored repositories"))
            else:
                checks.append(("github-repos", False, "no monitored repositories matched your configuration"))
        except GitHubAPIError as exc:
            checks.append(("github-auth", False, f"GitHub API error: {exc}"))
    else:
        checks.append(("github-token", False, "GITHUB_TOKEN is not set"))

    command = config.agent_command_for("__default__")
    if command:
        exe = command.split()[0]
        ok, msg = _check_executable(exe)
        checks.append(("agent-backend", ok, f"AGENT_COMMAND={command!r}; {msg}"))
    else:
        checks.append(("agent-backend", False, "no AGENT_COMMAND configured"))

    failed = 0
    for name, ok, message in checks:
        mark = "ok" if ok else "FAIL"
        print(f"[{mark}] {name:<16} {message}")
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-listener",
        description="Local GitHub agent dispatcher - polls GitHub and dispatches work to OpenCode.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the polling + worker service continuously")
    p_serve.add_argument("--interval", type=int, default=None, help="override POLL_INTERVAL_SECONDS")
    add_common_args(p_serve)
    p_serve.set_defaults(func=cmd_serve)

    p_scan = sub.add_parser("scan", help="force one immediate GitHub scan")
    add_common_args(p_scan)
    p_scan.set_defaults(func=cmd_scan)

    p_doctor = sub.add_parser("doctor", help="verify installation and configuration")
    add_common_args(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_status = sub.add_parser("status", help="show service status")
    add_common_args(p_status)
    p_status.set_defaults(func=cmd_status)

    p_repos = sub.add_parser("repos", help="list monitored repositories")
    add_common_args(p_repos)
    p_repos.set_defaults(func=cmd_repos)

    p_jobs = sub.add_parser("jobs", help="list jobs")
    p_jobs.add_argument("--status", default=None, help="filter by status (e.g. QUEUED, FAILED)")
    p_jobs.add_argument("--limit", type=int, default=20)
    add_common_args(p_jobs)
    p_jobs.set_defaults(func=cmd_jobs)

    p_show = sub.add_parser("show", help="show one job")
    p_show.add_argument("job_id")
    add_common_args(p_show)
    p_show.set_defaults(func=cmd_job_show)

    p_retry = sub.add_parser("retry", help="re-queue a failed/blocked job")
    p_retry.add_argument("job_id")
    add_common_args(p_retry)
    p_retry.set_defaults(func=cmd_retry)

    p_cancel = sub.add_parser("cancel", help="mark a queued/running job as cancelled")
    p_cancel.add_argument("job_id")
    add_common_args(p_cancel)
    p_cancel.set_defaults(func=cmd_cancel)

    p_unblock = sub.add_parser("unblock", help="unblock a BLOCKED job")
    p_unblock.add_argument("job_id")
    add_common_args(p_unblock)
    p_unblock.set_defaults(func=cmd_unblock)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
