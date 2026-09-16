#!/usr/bin/env python3
"""
Agy Bridge - ponte de execucao para delegar uma tarefa ao Antigravity CLI (agy).

Filosofia: o Claude Code pensa e planeja; tarefas mecanicas de analise de
arquivos, edicao e pesquisa podem ser delegadas ao agy (modelos Gemini) rodando
localmente e de forma nao-interativa, para nao gastar tokens/contexto do
Claude com trabalho que o agy consegue fazer sozinho.

Conta unica, sem hot-swap de credenciais e sem redirecionamento entre contas:
se o agy estiver com a cota critica/esgotada, a resposta sinaliza
`fallback_to_claude: true` para que o proprio Claude execute a tarefa.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import subprocess  # noqa: E402
from quota_guard import advise, classify_task_size  # noqa: E402


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {"profiles": {}, "default_timeout_seconds": 600, "tasks_directory": ".agy-tasks"}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


QUOTA_ERROR_PATTERNS = [
    "resource_exhausted",
    "exhausted your capacity",
    "model_capacity_exhausted",
    "quota exceeded",
    "quota_exhausted",
    "rate limit exceeded",
    "rate_limited",
    "429 too many requests",
]


def looks_like_quota_error(text: str) -> bool:
    lower = (text or "").lower()
    return any(p in lower for p in QUOTA_ERROR_PATTERNS)


def extract_result_text(stdout: str) -> str:
    """
    Com --output-format json o agy deve responder um envelope JSON.
    Tenta extrair um campo de texto comum; se nao for JSON valido, devolve o stdout cru.
    """
    stdout = stdout or ""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout
    if isinstance(data, dict):
        for key in ("result", "response", "output", "text", "message"):
            if isinstance(data.get(key), str):
                return data[key]
    return stdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Agy Bridge - delega uma tarefa ao Antigravity CLI (agy)")
    parser.add_argument("--profile", default="agy", help="Perfil definido em config.json (agy, mock)")
    parser.add_argument("--prompt", required=True, help="Instrucao da tarefa para o subagente")
    parser.add_argument("--files", nargs="*", default=[], help="Arquivos de contexto a anexar ao prompt")
    parser.add_argument("--effort", default=None, choices=["low", "medium", "high"], help="Forca o esforco de raciocinio do agy")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--cwd", default=None, help="Diretorio de trabalho onde o agy deve rodar")
    parser.add_argument("--skip-quota-check", action="store_true", help="Ignora o guardrail de cota e forca a execucao")

    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    config_path = Path(args.config) if args.config else (base_dir / "config.json")
    config = load_config(config_path)

    profiles = config.get("profiles", {})
    if args.profile not in profiles:
        print(json.dumps({
            "status": "error",
            "message": f"Perfil '{args.profile}' nao encontrado. Perfis disponiveis: {list(profiles.keys())}",
        }, indent=2, ensure_ascii=False))
        sys.exit(1)

    profile = profiles[args.profile]
    timeout = args.timeout or config.get("default_timeout_seconds", 600)
    task_id = args.task_id or f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    working_dir = Path(args.cwd).resolve() if args.cwd else Path.cwd()

    tasks_dir = working_dir / config.get("tasks_directory", ".agy-tasks")
    tasks_dir.mkdir(parents=True, exist_ok=True)

    context_text = ""
    if args.files:
        blocks = []
        for file_rel in args.files:
            file_path = working_dir / file_rel
            if file_path.exists() and file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    blocks.append(f"--- FILE: {file_rel} ---\n{content}\n")
                except Exception as exc:
                    blocks.append(f"--- FILE: {file_rel} (erro ao ler: {exc}) ---")
        if blocks:
            context_text = "\n\nArquivos de contexto relevantes:\n" + "\n".join(blocks)

    full_prompt = args.prompt + context_text

    spec_path = tasks_dir / f"{task_id}_spec.json"
    spec_path.write_text(json.dumps({
        "task_id": task_id,
        "profile": args.profile,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt": args.prompt,
        "context_files": args.files,
        "timeout_seconds": timeout,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Guardrail de cota (apenas para o perfil real "agy", nao para "mock")
    if args.profile == "agy" and not args.skip_quota_check:
        agy_cmd = os.path.expandvars(profile.get("command", "agy"))
        should_delegate, task_size, reason = advise(agy_cmd, tasks_dir, args.prompt, args.files)
        if not should_delegate:
            response = {
                "status": "recommend_claude_takeover",
                "task_id": task_id,
                "profile": args.profile,
                "task_size": task_size,
                "reason": reason,
                "fallback_to_claude": True,
                "spec_file": str(spec_path.relative_to(working_dir)).replace("\\", "/"),
            }
            print(json.dumps(response, indent=2, ensure_ascii=False))
            return
    else:
        task_size = classify_task_size(args.prompt, args.files)

    effort = args.effort or (
        config.get("heavy_effort", "high") if task_size == "HEAVY" else config.get("micro_effort", "low")
    )

    cmd_str = os.path.expandvars(profile.get("command"))
    args_template = list(profile.get("args_template", ["{prompt}"]))
    if args.profile == "agy" and "--effort" not in args_template:
        args_template += ["--effort", effort]

    full_args = [cmd_str] + [a.replace("{prompt}", full_prompt) for a in args_template]
    env_vars = os.environ.copy()
    for key, value in profile.get("env", {}).items():
        env_vars[key] = os.path.expandvars(value) if isinstance(value, str) else value

    start = time.time()
    status, exit_code, stdout, stderr, error_message = "success", 0, "", "", ""
    try:
        proc = subprocess.run(
            full_args,
            cwd=str(working_dir),
            env=env_vars,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if exit_code != 0:
            status = "failed"
            error_message = f"Processo saiu com codigo {exit_code}"
    except subprocess.TimeoutExpired as exc:
        status, exit_code = "timeout", -1
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        error_message = f"Comando expirou apos {timeout}s"
    except FileNotFoundError:
        status, exit_code = "error", 127
        error_message = f"Executavel '{cmd_str}' nao encontrado no PATH."
    except Exception as exc:
        status, exit_code = "error", 1
        error_message = str(exc)

    duration = round(time.time() - start, 2)

    if looks_like_quota_error(stdout + " " + stderr):
        status = "requires_claude_takeover"
        error_message = "O agy sinalizou esgotamento de cota durante a execucao."

    result_text = extract_result_text(stdout)

    result_data = {
        "task_id": task_id,
        "profile": args.profile,
        "task_size": task_size,
        "effort": effort,
        "status": status,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "error_message": error_message,
        "fallback_to_claude": status in ("requires_claude_takeover", "timeout", "error"),
        "result_text": result_text,
        "stderr": stderr,
        "spec_file": str(spec_path.relative_to(working_dir)).replace("\\", "/"),
    }

    result_json_path = tasks_dir / f"{task_id}_result.json"
    result_json_path.write_text(json.dumps(result_data, indent=2, ensure_ascii=False), encoding="utf-8")

    result_md_path = tasks_dir / f"{task_id}_result.md"
    md_lines = [
        f"# Resultado da tarefa: {task_id}",
        "",
        f"- **Perfil:** `{args.profile}`",
        f"- **Tamanho:** `{task_size}` | **Esforco:** `{effort}`",
        f"- **Status:** `{status}`",
        f"- **Duracao:** {duration}s",
        "",
    ]
    if status == "requires_claude_takeover":
        md_lines.append("> **Auto-assuncao necessaria:** cota do agy esgotada. O Claude Code deve executar a tarefa diretamente.\n")
    if error_message:
        md_lines += ["### Erro", "```", error_message, "```", ""]
    md_lines += ["### Resultado", "", "```", result_text.strip(), "```"]
    result_md_path.write_text("\n".join(md_lines), encoding="utf-8")

    summary = {
        "status": status,
        "task_id": task_id,
        "profile": args.profile,
        "task_size": task_size,
        "duration_seconds": duration,
        "fallback_to_claude": result_data["fallback_to_claude"],
        "result_file": str(result_json_path.relative_to(working_dir)).replace("\\", "/"),
        "report_file": str(result_md_path.relative_to(working_dir)).replace("\\", "/"),
        "result_preview": (result_text[:800] + "...") if len(result_text) > 800 else result_text.strip(),
        "error": error_message or None,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
