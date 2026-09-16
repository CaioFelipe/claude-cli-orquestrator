"""
Painel de Controle - claude-cli-orquestrator

Servidor local (FastAPI) que:
- abre pastas de projeto e inicia sessoes de chat com o Claude Code (via
  claude_bridge.ClaudeSession), com varias sessoes em paralelo;
- transmite a conversa (e os tool_use/subagentes) em tempo real por WebSocket;
- mostra o historico de tarefas delegadas ao agy (.agy-tasks) de cada projeto;
- mostra tambem as sessoes "globais" do Claude Code na maquina (via
  `claude agents --json --all`), mesmo as que nao foram abertas por aqui.

Rodar: `python dashboard/server.py` (ou uvicorn dashboard.server:app) e abrir
http://localhost:5151
"""

from __future__ import annotations

import asyncio
import json
import string
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from claude_bridge import CLAUDE_EXE, ClaudeSession, new_session_id

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SESSIONS_DIR = BASE_DIR / "sessions"
STATE_PATH = BASE_DIR / "state.json"
DEFAULT_BROWSE_ROOT = Path.home() / "Desktop" / "PROGRAMACAO"

app = FastAPI(title="Claude CLI Orquestrador - Painel")

SESSIONS: dict[str, ClaudeSession] = {}
CONNECTIONS: dict[str, set[WebSocket]] = {}


# ---------------------------------------------------------------------------
# Estado persistido (lista de projetos conhecidos)
# ---------------------------------------------------------------------------

def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"projects": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _remember_project(path: str, name: Optional[str] = None) -> None:
    state = _load_state()
    projects = state.setdefault("projects", [])
    for p in projects:
        if p["path"] == path:
            p["last_opened"] = time.time()
            _save_state(state)
            return
    projects.append({
        "path": path,
        "name": name or Path(path).name,
        "added_at": time.time(),
        "last_opened": time.time(),
    })
    _save_state(state)


# ---------------------------------------------------------------------------
# Navegador de pastas
# ---------------------------------------------------------------------------

@app.get("/api/browse")
def browse(path: Optional[str] = None):
    if not path:
        entries = []
        if hasattr(Path, "drive"):
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if Path(drive).exists():
                    entries.append({"name": drive, "path": drive, "is_dir": True})
        default_root = str(DEFAULT_BROWSE_ROOT) if DEFAULT_BROWSE_ROOT.exists() else str(Path.home())
        return {"path": None, "parent": None, "entries": entries, "default_root": default_root}

    p = Path(path)
    if not p.exists() or not p.is_dir():
        return JSONResponse({"error": f"Diretorio nao encontrado: {path}"}, status_code=404)

    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child), "is_dir": True})
            except PermissionError:
                continue
    except PermissionError:
        return JSONResponse({"error": f"Sem permissao para ler: {path}"}, status_code=403)

    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "entries": entries, "default_root": None}


# ---------------------------------------------------------------------------
# Projetos conhecidos
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def list_projects():
    state = _load_state()
    projects = sorted(state.get("projects", []), key=lambda p: p.get("last_opened", 0), reverse=True)
    return {"projects": projects}


class AddProjectBody(BaseModel):
    path: str
    name: Optional[str] = None


@app.post("/api/projects")
def add_project(body: AddProjectBody):
    p = Path(body.path)
    if not p.exists() or not p.is_dir():
        return JSONResponse({"error": "Caminho invalido"}, status_code=400)
    _remember_project(str(p), body.name)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sessoes de chat (bridge para `claude -p --input-format stream-json`)
# ---------------------------------------------------------------------------

class CreateSessionBody(BaseModel):
    cwd: str
    name: Optional[str] = None
    permission_mode: str = "default"
    model: Optional[str] = None
    effort: Optional[str] = None


async def _broadcast(session_id: str, event: dict[str, Any]) -> None:
    dead = []
    for ws in CONNECTIONS.get(session_id, set()):
        try:
            await ws.send_json({"type": "event", "data": event, "status": SESSIONS[session_id].status})
        except Exception:
            dead.append(ws)
    for ws in dead:
        CONNECTIONS.get(session_id, set()).discard(ws)


@app.post("/api/sessions")
async def create_session(body: CreateSessionBody):
    p = Path(body.cwd)
    if not p.exists() or not p.is_dir():
        return JSONResponse({"error": "Diretorio de trabalho invalido"}, status_code=400)

    session_id = new_session_id()
    session = ClaudeSession(
        session_id=session_id,
        cwd=str(p),
        name=body.name or p.name,
        permission_mode=body.permission_mode,
        model=body.model,
        effort=body.effort,
    )
    SESSIONS[session_id] = session
    CONNECTIONS[session_id] = set()
    session.add_listener(lambda event, sid=session_id: _broadcast(sid, event))
    _remember_project(str(p), body.name)

    await session.start(SESSIONS_DIR)
    return session.to_dict()


@app.post("/api/sessions/{session_id}/resume")
async def resume_session(session_id: str, body: CreateSessionBody):
    """Reabre uma sessao encerrada (ex.: apos reiniciar o painel) via `claude --resume`."""
    p = Path(body.cwd)
    if not p.exists() or not p.is_dir():
        return JSONResponse({"error": "Diretorio de trabalho invalido"}, status_code=400)

    session = ClaudeSession(
        session_id=session_id,
        cwd=str(p),
        name=body.name or p.name,
        permission_mode=body.permission_mode,
        model=body.model,
        effort=body.effort,
        resumed=True,
    )
    SESSIONS[session_id] = session
    CONNECTIONS.setdefault(session_id, set())
    session.add_listener(lambda event, sid=session_id: _broadcast(sid, event))

    await session.start(SESSIONS_DIR)
    return session.to_dict()


@app.get("/api/sessions")
def list_sessions():
    return {"sessions": [s.to_dict() for s in SESSIONS.values()]}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error": "Sessao nao encontrada"}, status_code=404)
    return session.to_dict()


@app.get("/api/sessions/{session_id}/history")
def session_history(session_id: str):
    log_path = SESSIONS_DIR / f"{session_id}.jsonl"
    events = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"events": events}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    session = SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error": "Sessao nao encontrada"}, status_code=404)
    await session.stop()
    return session.to_dict()


@app.websocket("/ws/sessions/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = SESSIONS.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Sessao nao encontrada"})
        await websocket.close()
        return

    CONNECTIONS.setdefault(session_id, set()).add(websocket)
    try:
        while True:
            raw = await websocket.receive_json()
            if raw.get("type") == "message":
                text = (raw.get("text") or "").strip()
                if text:
                    ok = await session.send_message(text)
                    if not ok:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Sessao ocupada ou encerrada - aguarde a resposta atual terminar.",
                        })
            elif raw.get("type") == "stop":
                await session.stop()
    except WebSocketDisconnect:
        pass
    finally:
        CONNECTIONS.get(session_id, set()).discard(websocket)


# ---------------------------------------------------------------------------
# Sessoes globais do Claude Code na maquina (claude agents --json)
# ---------------------------------------------------------------------------

@app.get("/api/system/agents")
def system_agents():
    try:
        proc = subprocess.run(
            ["cmd", "/c", CLAUDE_EXE, "agents", "--json", "--all"] if CLAUDE_EXE.lower().endswith(".cmd")
            else [CLAUDE_EXE, "agents", "--json", "--all"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(proc.stdout or "[]")
        return {"agents": data}
    except Exception as exc:
        return {"agents": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Historico de tarefas do agy (.agy-tasks) nos projetos conhecidos
# ---------------------------------------------------------------------------

@app.get("/api/agy-tasks")
def agy_tasks():
    state = _load_state()
    tasks: list[dict[str, Any]] = []
    for project in state.get("projects", []):
        tasks_dir = Path(project["path"]) / ".agy-tasks"
        if not tasks_dir.exists():
            continue
        for result_file in tasks_dir.glob("*_result.json"):
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            data["project_path"] = project["path"]
            data["project_name"] = project.get("name", Path(project["path"]).name)
            data["mtime"] = result_file.stat().st_mtime
            tasks.append(data)
    tasks.sort(key=lambda t: t.get("mtime", 0), reverse=True)
    return {"tasks": tasks[:200]}


@app.get("/api/agy-tasks/report")
def agy_task_report(project_path: str, report_file: str):
    full_path = Path(project_path) / report_file
    try:
        full_path.resolve().relative_to(Path(project_path).resolve())
    except ValueError:
        return JSONResponse({"error": "Caminho invalido"}, status_code=400)
    if not full_path.exists():
        return JSONResponse({"error": "Relatorio nao encontrado"}, status_code=404)
    return {"content": full_path.read_text(encoding="utf-8", errors="replace")}


# ---------------------------------------------------------------------------
# Estaticos
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=5151, reload=False)
