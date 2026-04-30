from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClaudeCodeClientOptions:
    cwd: str
    model: str
    add_dirs: list[str] = field(default_factory=list)


class ClaudeCodeClient:
    def __init__(self, options: ClaudeCodeClientOptions) -> None:
        self._options = options

    async def prompt_structured(
        self,
        *,
        system_prompt: str,
        prompt: str,
        schema: dict[str, Any],
        max_turns: int = 4,
    ) -> Any:
        args = [
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False),
            "--model",
            self._options.model,
            "--allowedTools",
            "",
            "--permission-mode",
            "default",
            "--system-prompt",
            system_prompt,
            "--max-turns",
            str(max_turns),
        ]

        for directory in self._options.add_dirs:
            args.extend(["--add-dir", directory])

        try:
            process = await asyncio.create_subprocess_exec(
                "claude",
                *args,
                cwd=Path(self._options.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Claude Code CLI is not installed or not available in PATH.") from exc

        stdout, stderr = await process.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise _normalize_claude_error(stdout_text, stderr_text)

        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude Code returned invalid JSON:\n{stdout_text}") from exc

        structured_output = parsed.get("structured_output")
        if structured_output is None:
            result = parsed.get("result") or "Claude Code returned no structured output"
            raise RuntimeError(str(result))

        return structured_output


def _normalize_claude_error(stdout: str, stderr: str) -> RuntimeError:
    combined = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)

    if "Not logged in" in combined:
        return RuntimeError(
            "Claude Code is not logged in. Run `claude` and complete login with your Claude Pro account."
        )

    if not combined:
        combined = "Claude Code execution failed"

    return RuntimeError(combined)
