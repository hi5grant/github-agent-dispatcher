# GitHub Agent Dispatcher

A local daemon that watches GitHub — **without webhooks or inbound connectivity** —
and hands work to a coding agent (OpenCode) running on your machine.

Instead of setting up a webhook + server, the dispatcher **polls** GitHub on a
schedule. When it finds something it owns — a new issue, a comment, or PR review
feedback that mentions `@agent` (or matches other rules you configure) — it:

1. Records the work as a **job** in a local SQLite database (deduplicated, never run twice).
2. Checks out the repository into a local workspace.
3. Invokes OpenCode non-interactively to do the work.
4. Runs **your** validation commands before anything is committed or pushed.
5. Commits and pushes the result, and posts status comments back on GitHub.

### What the agent does

When the coding agent picks up a `@agent` request, it is instructed to keep the
requester informed on the originating issue or PR by posting comments through
GitHub's REST API (the dispatcher passes the comment endpoint and `GITHUB_TOKEN`
is available in the agent's environment), in this order:

1. **Acknowledge receipt** — a short `🤖` acknowledgment
   (e.g. `🤖 Acknowledged, investigating now.`) posted immediately on receipt.
2. **Analysis** — what the current code does, the root cause or required change,
   and the plan to address it.
3. **Implement** — make the fix in the working directory (no comment at this step).
4. **Results** — a final comment reporting whether the change was applied and
   passed validation, saying **PASS** or **FAIL** explicitly, with failure
   details if applicable. This comment is posted regardless of the outcome.

If the machine is off when work arrives, nothing is lost — polling resumes on
the next run. You stay in control: nothing runs unless it matches your rules,
and validation commands come only from your config, never from GitHub text.

---

## Why polling instead of webhooks?

| | Webhooks | Polling (this project) |
|---|---|---|
| Requires public URL / tunnel | Yes | **No** |
| Requires inbound port open | Yes | **No** |
| Missed events while asleep/offline | Lost (retries are best-effort) | **Safe** — picked up on next poll |
| Setup complexity | ngrok/firewall/TLS, secret management | Just run a daemon |
| Determinism | Queueing once is tricky | SQLite dedup **guarantees** once |
| Latency | Instant | Configurable (default 5 min) |

---

## Architecture

```
 GitHub ──(poll every POLL_INTERVAL_SECONDS)──> Scanner
                                                │  finds @agent mentions / assigned / label / any
                                                ▼
                                         SQLite Queue (dedup + state)
                                                │
                                                ▼
                                    Worker (per-repo lock)
     ┌──────────────────────────────┐           │
     │ clone → branch → OpenCode ──▶│◀──────────┘
     │ (agent/issue-N or PR branch) │
     │ validation commands (YOURS)  │
     │ commit → push → comment      │
     └──────────────────────────────┘
```

Modules under `src/github_agent_dispatcher/`:

- `polling/scanner.py` — discovers candidate issues/comments/review comments.
- `security/authorization.py` — who may trigger work; trigger matching is
  word-boundary (`@agent` will not match `myagent`).
- `storage/database.py` — SQLite (WAL): `processed_items` for dedup, `jobs` for
  state. Interrupted RUNNING jobs are reset to INTERRUPTED on restart.
- `jobs/queue.py`, `jobs/worker.py`, `jobs/models.py` — job lifecycle and the
  clone → agent → validate → commit → push → comment pipeline.
- `agents/opencode.py` — summons OpenCode via subprocess.
- `git/repository.py`, `git/lock.py` — git operations and cross-platform
  per-repository locking (no `fcntl`, works on Windows via atomic lock files).
- `workspace/manager.py` — safe workspace paths (nested `owner/repo`
  directories under `WORKSPACE_ROOT`, no path traversal).
- `github/` — thin GitHub REST client (pagination, rate-limit handling with
  `Retry-After`, transient retry).
- `cli.py` / `service.py` — command line interface and the polling loop.
- `config.py` — environment/`.env`/JSON configuration and validation.

---

## Requirements

- Python 3.10+
- Git
- [OpenCode](https://opencode.ai) on `PATH` (or `AGENT_COMMAND` adjusted)
- A GitHub Personal Access Token

---

## Setup

### 1. Install

```bash
git clone https://github.com/hi5grant/github-agent-dispatcher.git
cd github-agent-dispatcher
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Create a GitHub token

Create a Personal Access Token (classic or fine-grained) at
https://github.com/settings/tokens with these scopes so the dispatcher can read
work, push branches, and post comments:

- `repo` (classic) — or for fine-grained tokens, **Contents: Read and write**,
  **Issues: Read and write**, **Pull requests: Read and write**, plus
  **Metadata: Read** (mandatory for fine-grained).
- `workflow` only if the agent will edit GitHub Actions files.

> **Security:** the token is used to clone/push and to post comments. Grant it
> only to repositories you actually want the agent to touch
> (`GITHUB_ALLOWED_REPOS`). Consider a token scoped to a personal or org that
> holds only agent-managed repos.

### 3. Configure via `.env`

Copy the template and fill it in:

```bash
cp .env.example .env
```

Minimal working `.env`:

```dotenv
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxx
GITHUB_ALLOWED_REPOS=you/your-repo
WORKSPACE_ROOT=/Users/you/Codebase
```

Verify everything wired up:

```bash
agent-listener doctor
agent-listener repos        # shows the repositories you can work on
agent-listener scan         # one manual scan of GitHub right now
agent-listener status
```

### 4. Run once (manual)

```bash
agent-listener scan
```

and inspect results:

```bash
agent-listener jobs
agent-listener show <job-id>
agent-listener retry <job-id>    # re-run a failed job
agent-listener cancel <job-id>   # abandon a job
agent-listener unblock <job-id>  # workspace was dirty; resolved
```

### 5. Run continuously (daemon)

#### macOS — launchd

Save this as `~/Library/LaunchAgents/com.github.agent-dispatcher.plist`
(substitute real paths; `/usr/local/bin` may be `/opt/homebrew/bin` on Apple
Silicon):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.github.agent-dispatcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/Codebase/github-agent-dispatcher/.venv/bin/agent-listener</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/you/Codebase/github-agent-dispatcher</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/usr/local/var/log/agent-dispatcher/out.log</string>
  <key>StandardErrorPath</key>
  <string>/usr/local/var/log/agent-dispatcher/err.log</string>
</dict>
</plist>
```

Load and run:

```bash
mkdir -p /usr/local/var/log/agent-dispatcher
launchctl load ~/Library/LaunchAgents/com.github.agent-dispatcher.plist
# or: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.github.agent-dispatcher.plist
launchctl list | grep agent-dispatcher   # check it is running
```

> Because `load_config()` reads `.env`, the daemon needs to run with the
> dispatcher repo as its working directory (set above), or you can point
> `GAD_ENV_FILE` at your `.env`. Prefer putting secrets in the `.env` with
> mode `600` over baking them into the plist.

Reload after config changes:

```bash
launchctl unload ~/Library/LaunchAgents/com.github.agent-dispatcher.plist
launchctl load ~/Library/LaunchAgents/com.github.agent-dispatcher.plist
```

#### Windows — Task Scheduler

1. Put `agent-listener.exe` on your `PATH` (or use `python -m
   github_agent_dispatcher.cli serve`). For an exe launcher:

   ```powershell
   pip install pyinstaller
   pyinstaller --onefile --name agent-listener src/github_agent_dispatcher/cli.py
   ```

   (or simply run it under the venv's python).

2. Open **Task Scheduler** → **Create Task**:
   - **General**: run only when user is logged on (required for the agent to
     use your interactive session) — check **Run with highest privileges** if the
     agent needs admin tools.
   - **Triggers**: enabled **At startup** (and optionally **On an event** /
     at a fixed time).
   - **Actions**: `Start a program`
     - Program/script: `C:\Codebase\github-agent-dispatcher\.venv\Scripts\agent-listener.exe`
     - Arguments: `serve`
     - Start in: `C:\Codebase\github-agent-dispatcher`
   - **Settings**: check **Restart on failure**; uncheck **Stop the task if it
     runs longer than**.
3. Save. The task now starts at login/startup and keeps polling.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub PAT (required). |
| `GITHUB_ALLOWED_REPOS` | — | Comma-separated `owner/repo` allowlist. |
| `GITHUB_ALLOWED_USERS` | *empty* | Comma-separated logins allowed to trigger work. |
| `GITHUB_ALLOW_SELF` | `true` | Token owner may trigger work even when not in `GITHUB_ALLOWED_USERS`. |
| `REPOSITORY_DISCOVERY` | `configured` | `configured` (only `GITHUB_ALLOWED_REPOS`) or `owned` (auto-discover repos owned by the token user, still intersected with the allowlist). |
| `ISSUE_DISCOVERY` | `mention` | Comma-separated: `mention`, `assigned`, `label:agent`, `any`, `none`. |
| `AGENT_TRIGGER` | `@agent` | Text that flags a comment/issue as work. Word-boundary match. |
| `AGENT_COMMAND` | `opencode run` | How to summon OpenCode. Prompt is passed as trailing arg(s). |
| `AGENT_EXTRA_ARGS` | *empty* | Extra flags before the prompt (e.g. `--model claude`). |
| `WORKSPACE_ROOT` | `~/Codebase` | Root for local checkouts. |
| `AUTO_CLONE` | `true` | Clone approved repos automatically if missing locally. |
| `AUTO_CREATE_PR` | `false` | After a successful issue job, open a PR from the agent branch. |
| `POLL_INTERVAL_SECONDS` | `300` | Poll cadence (min sensible ~60; `doctor` warns below 10). |
| `VALIDATION_COMMANDS` | *empty* | Comma/newline-separated commands to run before commit/push. **Your list only — never from GitHub input.** |
| `DATABASE_PATH` | `.state/state.db` | SQLite job store (in the project, gitignored). |
| `MAX_CONCURRENT_JOBS` | `2` | Worker threads. Per-repo locking prevents concurrent edits to the same checkout. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `GAD_DATA_DIR` | `.state/` in project root | Data directory (gitignored): holds `state.db`, `locks/`, and `config.json` by default. |
| `GAD_CONFIG_FILE` | `$GAD_DATA_DIR/config.json` | Optional per-repo JSON overrides. |
| `GAD_ENV_FILE` | `.env` in repo root | Path to the environment file. |
| `GITHUB_API_URL` / `GITHUB_WEB_URL` | github.com | Change `GITHUB_API_URL` for GitHub Enterprise. |

### Per-repository overrides (`config.json`)

```json
{
  "repositories": {
    "you/repo": {
      "validation_commands": ["pytest", "ruff check ."],
      "agent_command": "opencode run --model codex-mini"
    }
  }
}
```

---

## How work is discovered

Each poll:

- **Issue comments** and **PR comments**: any comment containing `@agent`
  (word boundary) from an authorized user becomes a job.
- **Review comments** (line discussions on PRs): same trigger.
- **New issues**: matched by the `ISSUE_DISCOVERY` mode (default `mention`).
  With `assigned` or `any`, only issues created by an authorized user, or
  assigned to one, are eligible.

### Workflows

**Issue → fix on a branch**

1. Someone opens `#42: "Backup step @agent is flaky — investigate and fix."`
2. Dispatcher checks out the repo to branch `agent/issue-42` and runs:
   `opencode run Backup step @agent is flaky — investigate and fix. Do not commit...`
3. On success it commits, pushes `agent/issue-42`, and posts on the issue:
   `🤖 Agent completed this request. … Commit: <sha> · Tests: …`
4. (Optional, `AUTO_CREATE_PR=true`) it converts the push into a PR you can review.

**PR review feedback**

1. A reviewer comments `@agent address this feedback` on your PR (or on a diff
   line).
2. The dispatcher checks out the PR's head branch (including forks, if you can
   write to them), runs the agent, commits `agent: address PR #7 feedback`,
   pushes, and replies on the PR.

### What a job result means

| Outcome | Job status | Meaning |
|---|---|---|
| agent succeeded + changes | `SUCCEEDED` | Committed, pushed, commented. |
| agent succeeded, no diff | `FAILED` | Nothing to commit (branch still pushed if it did change; see logs). |
| validation failed | `FAILED` | Changes kept locally, **not** pushed. |
| workspace dirty | `BLOCKED` | Manual changes are in the way — dispatcher never resets or cleans. |
| agent crashed / errored | `FAILED` | Check the job output. |

---

## Troubleshooting

```bash
agent-listener doctor          # fastest diagnosis (token, repos, required tools)
agent-listener status
agent-listener jobs            # recent jobs, statuses, error strings
agent-listener show <job-id>
```

- **`GITHUB_TOKEN is not set`** → the token must be in the environment of the
  daemon process, or in a `.env` in the working directory of the daemon, or
  point `GAD_ENV_FILE` at it.
- **`REPOSITORY_DISCOVERY=configured but GITHUB_ALLOWED_REPOS is empty`** → set
  at least one `owner/repo`.
- **Job stays `BLOCKED`** → the local workspace has uncommitted/changed files;
  commit, stash, or move them aside (by design the dispatcher never wipes work).
- **"agent produced no changes"** → the agent finished but `git status` shows
  nothing new; add a `VALIDATION_COMMAND` guard or check the agent output.
- **Ratellite / `RateLimitedError`** → polls back off per GitHub's
  `Retry-After` and try again next interval.
- **Agent output looks mangled** → confirm `opencode run <message>` works in the
  repo's workspace manually; check `AGENT_COMMAND`/`AGENT_EXTRA_ARGS`.
- **Pushes to a fork fail** → the dispatcher can only push if the token has
  write access to that fork (e.g. you are a maintainer); otherwise the job fails
  here and you're told why.

### Security model (summary)

- Allowlist: repos (`GITHUB_ALLOWED_REPOS`) **and** users
  (`GITHUB_ALLOWED_USERS` + `GITHUB_ALLOW_SELF`). Authorization is enforced in
  the scanner, not just at the UI.
- Commands run on your machine come only from `VALIDATION_COMMANDS` /
  per-repo overrides — GitHub text is **never** executed.
- Workspace paths are the nested `owner/repo` layout under `WORKSPACE_ROOT`
  and traversal-checked; jobs are per-repo locked so concurrent workers can't
  edit one checkout at once.
- The agent is prompted to leave git operations (commit/push) to the
  dispatcher; the dispatcher commits with an explicit `agent:` author, and
  never force-pushes, resets, or cleans the workspace.

---

## Development

```bash
.venv/bin/ruff check src/ tests/     # lint
.venv/bin/ruff format src/ tests/    # format
.venv/bin/mypy src/github_agent_dispatcher
.venv/bin/python -m pytest -q        # test suite (uses local temp git repos)
```

CI (GitHub Actions) runs ruff, mypy, and pytest on Ubuntu, macOS, and Windows.