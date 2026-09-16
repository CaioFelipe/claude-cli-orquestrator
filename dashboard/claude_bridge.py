"""
Claude Bridge - gerencia UMA sessao do Claude Code como subprocesso headless,
falando o protocolo `--input-format stream-json --output-format stream-json`.

Cada linha escrita no stdin do processo e um turno do usuario:
    {"type": "user", "message": {"role": "user", "content": "<texto>"}}

Cada linha lida do stdout e um evento (system/init, assistant, user com
tool_result, rate_limit_event, result). Isso e exatamente o que a CLI
interativa do Claude Code usa por baixo dos panos - aqui so estamos
pilotando o mesmo protocolo programaticamente.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

CLAUDE_EXE = shutil.which("claude") or "claude"


def _build_argv(args: list[str]) -> list[str]:
    """No Windows, `claude` costuma ser um shim .CMD do npm - precisa passar por cmd /c."""
    if os.name == "nt" and CLAUDE_EXE.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", CLAUDE_EXE, *args]
    return [CLAUDE_EXE, *args]


EventListener = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ClaudeSession:
    session_id: str
    cwd: str
    name: str
    permission_mode: str = "default"
    model: Optional[str] = None
    effort: Optional[str] = None
    resumed: bool = False

    status: str = "starting"  # starting | ready | busy | ended | error
    created_at: float = field(default_factory=time.time)
    error_message: Optional[str] = None

    _proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    _listeners: list[EventListener] = field(default_factory=list, repr=False)
    _log_path: Optional[Path] = field(default=None, repr=False)
    _reader_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _awaiting_result: bool = field(default=False, repr=False)

    def add_listener(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: EventListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._log_path is not None:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:
                pass
        for listener in list(self._listeners):
            try:
                await listener(event)
            except Exception:
                pass

    async def start(self, sessions_dir: Path) -> None:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = sessions_dir / f"{self.session_id}.jsonl"

        args = [
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--forward-subagent-text",
            "--permission-mode", self.permission_mode,
        ]
        if self.resumed:
            args += ["--resume", self.session_id]
        else:
            args += ["--session-id", self.session_id]
        if self.model:
            args += ["--model", self.model]
        if self.effort:
            args += ["--effort", self.effort]

        argv = _build_argv(args)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            self.status = "error"
            self.error_message = f"Executavel 'claude' nao encontrado: {exc}"
            await self._emit({"type": "bridge_error", "message": self.error_message})
            return

        self.status = "ready"
        self._reader_task = asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for raw_line in self._proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"type": "raw_output", "text": line}
                if event.get("type") == "result":
                    self._awaiting_result = False
                    self.status = "ready"
                await self._emit(event)
        finally:
            exit_code = await self._proc.wait() if self._proc else -1
            if self.status != "error":
                self.status = "ended"
            await self._emit({"type": "bridge_ended", "exit_code": exit_code})

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for raw_line in self._proc.stderr:
            text = raw_line.decode("utf-8", errors="replace").rstrip()
            if text:
                await self._emit({"type": "bridge_stderr", "text": text})

    async def send_message(self, text: str) -> bool:
        """Envia um turno de usuario. Retorna False se a sessao estiver ocupada/encerrada."""
        if self.status in ("ended", "error", "starting"):
            return False
        if self._awaiting_result:
            return False
        if not self._proc or not self._proc.stdin:
            return False

        payload = {"type": "user", "message": {"role": "user", "content": text}}
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._awaiting_result = True
            self.status = "busy"
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        await self._emit({"type": "bridge_user_echo", "text": text})
        return True

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
        self.status = "ended"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "name": self.name,
            "permission_mode": self.permission_mode,
            "model": self.model,
            "effort": self.effort,
            "status": self.status,
            "created_at": self.created_at,
            "error_message": self.error_message,
        }


def new_session_id() -> str:
    return str(uuid.uuid4())
