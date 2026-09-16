#!/usr/bin/env python3
"""
Quota Guard - avisa quando a cota do agy (conta local) esta perto do limite.

Uso: consultivo e de conta unica. Nao existe troca de conta nem redirecionamento
para outra assinatura - se o agy estiver critico, a recomendacao e o proprio
Claude Code assumir a tarefa diretamente em vez de gastar a cota do agy.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

CACHE_TTL_SECONDS = 120
HEAVY_KEYWORDS = [
    "refatore", "refactor", "reescreva", "rewrite", "crie um", "crie uma",
    "implemente", "implement", "arquitetura", "architecture", "completo",
    "todos os arquivos", "all files", "stress test", "benchmark", "full test",
    "migre", "migrate", "reorganize", "reorganizar",
]
MAX_MICRO_PROMPT_CHARS = 280


def _cache_path(tasks_dir: Path) -> Path:
    return tasks_dir / "usage_cache.json"


def _load_cache(tasks_dir: Path) -> Dict[str, Any]:
    path = _cache_path(tasks_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(tasks_dir: Path, data: Dict[str, Any]) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    try:
        _cache_path(tasks_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _parse_consumption_percent(raw_text: str) -> float:
    """
    Best-effort: procura por algo como '87%' ou '"consumption_percent": 87.5'
    na saida do 'agy -p "/usage"'. O formato exato do agy pode mudar entre
    versoes, entao isso e apenas uma estimativa - nunca trate como exato.
    """
    match = re.search(r'"consumption_percent"\s*:\s*([0-9.]+)', raw_text)
    if match:
        return float(match.group(1))
    match = re.search(r'(\d{1,3}(?:\.\d+)?)\s*%', raw_text)
    if match:
        return float(match.group(1))
    return -1.0


def get_agy_usage(agy_command: str, tasks_dir: Path, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Retorna {consumption_percent, status, parse_ok, stale, raw_text}.
    consumption_percent == -1 significa "nao foi possivel medir" (nunca bloqueia).
    """
    cache = _load_cache(tasks_dir)
    now = time.time()
    if not force_refresh and cache.get("checked_at") and (now - cache["checked_at"]) < CACHE_TTL_SECONDS:
        cache["stale"] = False
        return cache

    result = {
        "consumption_percent": -1.0,
        "status": "UNKNOWN",
        "parse_ok": False,
        "stale": False,
        "raw_text": "",
        "checked_at": now,
    }
    try:
        proc = subprocess.run(
            [agy_command, "-p", "/usage", "--output-format", "json", "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        raw = (proc.stdout or "") + (proc.stderr or "")
        result["raw_text"] = raw[:4000]
        pct = _parse_consumption_percent(raw)
        if pct >= 0:
            result["consumption_percent"] = pct
            result["parse_ok"] = True
            if pct >= 100:
                result["status"] = "EXHAUSTED"
            elif pct >= 90:
                result["status"] = "CRITICAL"
            elif pct >= 70:
                result["status"] = "WARNING"
            else:
                result["status"] = "HEALTHY"
    except Exception as exc:
        result["raw_text"] = f"quota check failed: {exc}"

    _save_cache(tasks_dir, result)
    return result


def classify_task_size(prompt: str, files: List[str] = None) -> str:
    prompt_clean = (prompt or "").strip()
    if files and len(files) > 1:
        return "HEAVY"
    if len(prompt_clean) > MAX_MICRO_PROMPT_CHARS:
        return "HEAVY"
    lower_prompt = prompt_clean.lower()
    for kw in HEAVY_KEYWORDS:
        if kw in lower_prompt:
            return "HEAVY"
    return "MICRO"


def advise(agy_command: str, tasks_dir: Path, prompt: str, files: List[str] = None) -> Tuple[bool, str, str]:
    """
    Retorna (should_delegate_to_agy, task_size, reason).
    Nunca lanca excecao - na duvida (medicao falhou), recomenda delegar normalmente.
    """
    task_size = classify_task_size(prompt, files)
    usage = get_agy_usage(agy_command, tasks_dir)

    if not usage.get("parse_ok"):
        return True, task_size, "Nao foi possivel medir a cota do agy; prosseguindo normalmente."

    pct = usage["consumption_percent"]
    if usage["status"] == "EXHAUSTED":
        return False, task_size, f"Cota do agy esgotada ({pct}%). Recomenda-se o Claude Code executar a tarefa diretamente."
    if usage["status"] == "CRITICAL" and task_size == "HEAVY":
        return False, task_size, (
            f"[GUARDRAIL 90%] Cota do agy em {pct}%. Tarefa classificada como HEAVY - "
            f"recomenda-se o Claude Code assumir diretamente para preservar a cota restante."
        )
    return True, task_size, f"Cota do agy em {pct if pct >= 0 else '??'}% - delegacao autorizada."


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "agy"
    test_prompt = sys.argv[2] if len(sys.argv) > 2 else "Responda se o sistema esta online"
    ok, size, reason = advise(cmd, Path(".agy-tasks"), test_prompt)
    print(f"Task Size: {size} | Should Delegate: {ok}")
    print(f"Reason: {reason}")
