---
name: agy-delegation
description: >-
  Orquestra subagentes locais baseados no Antigravity CLI (agy / modelos Gemini) para
  analise de arquivos, edicoes e pesquisa, enquanto o Claude Code foca em pensar, planejar
  e revisar. Use quando houver tarefas mecanicas, paralelizaveis ou grandes o suficiente
  para valer a pena tirar do contexto/tokens do Claude.
---

# CLI Orquestrador Claude -> Agy

Este skill inverte o papel de um projeto anterior do usuario (onde o Antigravity IDE
delegava planejamento ao Claude). Aqui e o **Claude Code quem pensa e planeja**, e o
**`agy` (Antigravity CLI, modelos Gemini) quem executa** o trabalho mecanico, como
subagente local rodando via processo headless (`agy -p "<prompt>" --dangerously-skip-permissions`).

Conta unica do usuario, sem hot-swap de credenciais, sem multiplas contas e sem tentar
burlar limites de cota - o objetivo e produtividade e paralelismo, nao evasao de limites.

---

## Quando delegar ao agy

Delegue quando a tarefa for:
1. **Mecanica e bem definida:** analisar um arquivo/modulo, escrever testes, buscar um
   bug em um trecho especifico, traduzir/documentar, refatoracoes localizadas.
2. **Paralelizavel:** N tarefas independentes (ex.: analisar 5 modulos diferentes,
   revisar 3 arquivos separados) - dispare varios `agy-worker` de uma vez em vez de
   processar um por um.
3. **Pesada em contexto:** arquivos grandes ou repetitivos que consumiriam muito da
   janela de contexto do Claude sem agregar valor de raciocinio.

NAO delegue quando a tarefa exigir:
- Decisao de arquitetura, trade-offs ou julgamento de produto (isso e seu trabalho).
- Coordenacao entre varias partes que precisam ficar consistentes entre si (faca voce,
  ou quebre em subtarefas independentes e integre o resultado depois).
- Algo sensivel/destrutivo que normalmente pediria confirmacao do usuario.

## Como delegar

### Uma tarefa, via subagente (preferido para paralelismo)

Use a tool Agent com `subagent_type: "agy-worker"`. De ao worker uma descricao
autocontida (ele nao ve o resto desta conversa): objetivo, arquivos relevantes,
diretorio de trabalho, e o caminho do plugin (`${CLAUDE_PLUGIN_ROOT}`).

Para tarefas independentes, dispare varios `agy-worker` na mesma resposta (chamadas
paralelas da tool Agent) - isso e literalmente "invocar quantos subagentes usando o
agy quiser" para fazer o trabalho render mais rapido.

### Uma tarefa pontual, direto via Bash

Para uma unica chamada rapida sem overhead de subagente:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/agy_bridge.py" --profile agy --prompt "<instrucao>" --cwd "<diretorio>" [--files arq1.py arq2.py]
```

O comando `/agy <instrucao>` faz exatamente isso.

## Lendo o resultado

O script grava em `<cwd>/.agy-tasks/`:
- `<task_id>_spec.json` - o que foi pedido.
- `<task_id>_result.json` - resultado estruturado (`status`, `result_text`, `duration_seconds`, etc).
- `<task_id>_result.md` - relatorio legivel.

O JSON de resumo impresso em stdout ja traz `result_preview` (ate ~800 chars). Se precisar
do texto completo, leia o `report_file` com a tool Read.

## Guardrail de cota (informativo, conta unica)

Antes de cada chamada real ao `agy`, o bridge tenta medir a cota via `agy -p "/usage"`
(best-effort - se nao conseguir parsear, so segue em frente). Se a cota estiver critica
(>=90%) e a tarefa for classificada como `HEAVY`, o bridge **nao roda o agy** e devolve
`status: "recommend_claude_takeover"`.

## Self-healing: quando o agy nao pode terminar

Se o resultado vier com `status` em `recommend_claude_takeover`, `requires_claude_takeover`,
`timeout` ou `error`, e `fallback_to_claude: true`:

**Regra de conduta:** assuma a tarefa diretamente agora, com suas proprias ferramentas
(Read/Edit/Grep/etc), sem bloquear o usuario esperando o agy. Informe brevemente que o
agy nao conseguiu concluir e que voce esta terminando por conta propria.

## Efeito colateral esperado na primeira execucao

`agy_bridge.py` chama o `agy.exe` com `--dangerously-skip-permissions`, necessario porque
o `agy` e um agente completo e, sem essa flag, ficaria esperando aprovacoes interativas que
ninguem responde em um subprocesso headless. Isso e equivalente a rodar um agente autonomo
sem supervisao no diretorio de trabalho informado - o Claude Code deve pedir aprovacao do
usuario na primeira chamada de Bash a este script (comportamento normal e esperado), e o
`--cwd` passado deve sempre ser um diretorio em que o usuario aceita que o agy edite arquivos
sem confirmacao adicional.
