from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from github_agent_dispatcher.agents.base import AgentBackend, AgentResult, build_prompt

logger = logging.getLogger(__name__)


class OpenCodeBackend(AgentBackend):
    """Invokes the local ``opencode`` CLI in non-interactive ``run`` mode.

    The command line is assembled from ``AGENT_COMMAND`` (default ``opencode run``)
    plus optional ``AGENT_EXTRA_ARGS``.  The full prompt is passed as the final
    positional argument(s), matching ``opencode run [message..]``.
    """

    def __init__(self, command: str | None, extra_args: list[str] | None = None):
        self.prefix = shlex.split(command) if command else ["opencode", "run"]
        self.extra_args = list(extra_args or [])

    def run(self, request_text: str, context: dict, cwd: Path) -> AgentResult:
        job = context.get("_job")
        prompt = build_prompt(job) if job else request_text
        argv = self.prefix + self.extra_args + [prompt]
        logger.info("[agent] running %s", " ".join(shlex.quote(str(a)) for a in self.prefix))
        logger.info("[agent] working directory %s", cwd)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=None,
            )
        except FileNotFoundError:
            logger.error("[agent] could not find executable %s", self.prefix[0])
            return AgentResult(success=False, error=f"executable not found: {self.prefix[0]}")
        except OSError as exc:
            return AgentResult(success=False, error=f"failed to launch agent: {exc}")
        except subprocess.TimeoutExpired as exc:
            return AgentResult(success=False, error=f"agent timed out: {exc}")

        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            logger.warning("[agent] exited with code %s", proc.returncode)
            return AgentResult(success=False, output=output, error=f"exit code {proc.returncode}")
        return AgentResult(success=True, output=output)


class RefusalBackend(AgentBackend):
    """Rejects jobs without an installed agent backend."""

    def run(self, request_text: str, context: dict, cwd: Path) -> AgentResult:
        return AgentResult(
            success=False,
            error="no agent backend is configured or installed; set AGENT_COMMAND or install opencode",
        )
