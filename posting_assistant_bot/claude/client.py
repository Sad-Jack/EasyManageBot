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


@dataclass(frozen=True)
class ClaudeStructuredResponse:
    structured_output: Any
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_cost_usd: float | None = None


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
        model: str | None = None,
    ) -> Any:
        response = await self.prompt_structured_with_meta(
            system_prompt=system_prompt,
            prompt=prompt,
            schema=schema,
            max_turns=max_turns,
            model=model,
        )
        return response.structured_output

    async def prompt_structured_with_meta(
        self,
        *,
        system_prompt: str,
        prompt: str,
        schema: dict[str, Any],
        max_turns: int = 4,
        model: str | None = None,
    ) -> ClaudeStructuredResponse:
        args = [
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False),
            "--model",
            model or self._options.model,
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

        usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = _as_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        output_tokens = _as_int(usage.get("output_tokens") or usage.get("completion_tokens"))
        total_cost_usd = _as_float(parsed.get("total_cost_usd")) if isinstance(parsed, dict) else None
        return ClaudeStructuredResponse(
            structured_output=structured_output,
            model=str(parsed.get("model") or (model or self._options.model)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost_usd=total_cost_usd,
        )


def _normalize_claude_error(stdout: str, stderr: str) -> RuntimeError:
    combined = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)

    if "Not logged in" in combined:
        return RuntimeError(
            "Claude Code is not logged in. Run `claude` and complete login with your Claude Pro account."
        )

    if not combined:
        combined = "Claude Code execution failed"

    return RuntimeError(combined)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
