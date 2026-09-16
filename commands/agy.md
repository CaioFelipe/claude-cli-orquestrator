---
description: Delega uma tarefa pontual ao Antigravity CLI (agy / Gemini) via cli-orquestrator, em vez do Claude gastar tokens fazendo o trabalho diretamente
argument-hint: "[instrucao da tarefa]"
allowed-tools: Bash, Read
---

Delegue a seguinte tarefa ao `agy` rodando `${CLAUDE_PLUGIN_ROOT}/scripts/agy_bridge.py`:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/agy_bridge.py" --profile agy --prompt "$ARGUMENTS" --cwd "$(pwd)"
```

Depois de rodar:
1. Leia o JSON de resumo impresso no stdout.
2. Se `status` for `"success"`, mostre ao usuario o `result_preview` (ou abra o `report_file`
   com a tool Read se o preview estiver truncado e o usuario pedir o texto completo).
3. Se `status` for `"recommend_claude_takeover"`, `"requires_claude_takeover"`, `"timeout"` ou
   `"error"`, explique o motivo (`reason` ou `error_message`) e ofereca-se para executar a
   tarefa "$ARGUMENTS" voce mesmo agora.
