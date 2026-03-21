"""Soulboard-local shell state tools."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

from nanobot.agent.tools.base import Tool


class SoulExecTool(Tool):
    """Exec tool bound to soulboard-managed cwd and environment."""

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    def __init__(
        self,
        *,
        get_cwd: Callable[[], Path],
        get_env: Callable[[], dict[str, str]],
        timeout: int = 60,
    ):
        self.timeout = timeout
        self.get_cwd = get_cwd
        self.get_env = get_env

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60, max 600).",
                    "minimum": 1,
                    "maximum": 600,
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, timeout: int | None = None, **kwargs: Any) -> str:
        del kwargs
        effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.get_cwd()),
                env=self.get_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=effective_timeout)
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {effective_timeout} seconds"

            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            output_parts.append(f"\nExit code: {process.returncode}")
            result = "\n".join(output_parts) if output_parts else "(no output)"
            if len(result) > self._MAX_OUTPUT:
                half = self._MAX_OUTPUT // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - self._MAX_OUTPUT:,} chars truncated) ...\n\n"
                    + result[-half:]
                )
            return result
        except Exception as exc:
            return f"Error executing command: {str(exc)}"


class CdTool(Tool):
    """Change the session-scoped working directory."""

    def __init__(self, *, get_cwd: Callable[[], Path], set_cwd: Callable[[Path], None]):
        self.get_cwd = get_cwd
        self.set_cwd = set_cwd

    @property
    def name(self) -> str:
        return "cd"

    @property
    def description(self) -> str:
        return "Change the current working directory for this session."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute target directory.",
                    "minLength": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        del kwargs
        target = (self.get_cwd() / path).expanduser().resolve()
        if not target.exists():
            return f"Error: Directory does not exist: {target}"
        if not target.is_dir():
            return f"Error: Not a directory: {target}"
        self.set_cwd(target)
        return str(target)


class SetEnvTool(Tool):
    """Mutate the session-scoped environment."""

    def __init__(self, *, get_env: Callable[[], dict[str, str]], set_env: Callable[[dict[str, str]], None]):
        self.get_env = get_env
        self.set_env = set_env

    @property
    def name(self) -> str:
        return "set_env"

    @property
    def description(self) -> str:
        return "Set environment variables for this session."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "values": {
                    "type": "object",
                    "description": "Environment variable key/value pairs to merge into the current session environment.",
                },
            },
            "required": ["values"],
        }

    async def execute(self, values: dict[str, str], **kwargs: Any) -> str:
        del kwargs
        env = self.get_env()
        env.update({str(key): str(value) for key, value in values.items()})
        self.set_env(env)
        return f"Updated environment variables: {', '.join(sorted(values))}" if values else "No environment variables updated."


if os.name != "nt":
    class SourceTool(Tool):
        """Source a POSIX shell file and persist the resulting environment."""

        def __init__(
            self,
            *,
            get_cwd: Callable[[], Path],
            get_env: Callable[[], dict[str, str]],
            set_env: Callable[[dict[str, str]], None],
        ):
            self.get_cwd = get_cwd
            self.get_env = get_env
            self.set_env = set_env

        @property
        def name(self) -> str:
            return "source"

        @property
        def description(self) -> str:
            return "Source a POSIX shell file and persist the resulting environment for this session."

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute shell file to source.",
                        "minLength": 1,
                    },
                },
                "required": ["path"],
            }

        async def execute(self, path: str, **kwargs: Any) -> str:
            del kwargs
            target = (self.get_cwd() / path).expanduser().resolve()
            if not target.exists():
                return f"Error: File does not exist: {target}"
            if not target.is_file():
                return f"Error: Not a file: {target}"

            process = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                '. "$1" >/dev/null 2>&1; env',
                "sh",
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.get_cwd()),
                env=self.get_env(),
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                return f"Error: Failed to source {target}" + (f": {detail}" if detail else "")

            env: dict[str, str] = {}
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key] = value
            self.set_env(env)
            return f"Sourced environment from {target}"
