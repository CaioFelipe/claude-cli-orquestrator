# claude-cli-orquestrator

Plugin do Claude Code que inverte a ideia do projeto
[`antigravity-cli-orchestrator`](https://github.com/CaioFelipe/antigravity-cli-orchestrator):
em vez do Antigravity IDE delegar planejamento ao Claude, aqui e o **Claude Code quem
pensa e planeja**, e delega tarefas mecanicas (analise de arquivos, edicoes, pesquisa)
para o **Antigravity CLI (`agy`, modelos Gemini)** rodando localmente como subagente
headless - inclusive varios em paralelo.

Conta unica do usuario. Sem hot-swap de credenciais, sem multiplas contas e sem tentar
burlar limites de cota do agy ou de qualquer outro CLI - isso foi removido de proposito
em relacao ao projeto original. O que sobrou (e vale a pena) foi: a ponte de execucao
generica, o guardrail de cota (agora so informativo) e a filosofia de failover -> se o
agy nao puder terminar, o Claude assume.

## Estrutura

```
claude-cli-orquestrator/
├── .claude-plugin/
│   └── plugin.json          # manifesto do plugin
├── agents/
│   └── agy-worker.md         # subagente "proxy fino": chama o agy e relata o resultado
├── skills/
│   └── agy-delegation/
│       └── SKILL.md          # quando/como delegar, paralelismo, guardrail, self-healing
├── commands/
│   └── agy.md                # /agy "<instrucao>" para delegacao pontual manual
├── scripts/
│   ├── agy_bridge.py          # ponte de execucao (subprocess -> agy.exe)
│   ├── quota_guard.py         # checagem best-effort da cota da conta local
│   └── config.json            # perfis (agy, mock) e parametros default
└── README.md
```

## Pre-requisitos

- [Claude Code](https://claude.com/claude-code) instalado.
- [Antigravity CLI](https://antigravity.google/) instalado e autenticado (`agy` no PATH -
  por padrao em `%LOCALAPPDATA%\agy\bin\agy.exe` no Windows).
- Python 3.10+ no PATH (usado apenas pelos scripts do plugin, nao pelo agy em si).

## Instalacao

### Localmente (desenvolvimento)

No `settings.json` do Claude Code (`~/.claude/settings.json` ou do projeto):

```json
{
  "plugins": ["C:/Users/Admin/Desktop/PROGRAMACAO/claude-cli-orquestrator"]
}
```

Ou, para uma sessao pontual:

```powershell
claude --plugin-dir "C:/Users/Admin/Desktop/PROGRAMACAO/claude-cli-orquestrator"
```

### Via GitHub (depois de publicado)

```
/plugin marketplace add CaioFelipe/claude-cli-orquestrator
/plugin install claude-cli-orquestrator@claude-cli-orquestrator
```

## Uso

**Delegacao pontual:**
```
/agy "Analise src/auth.py e liste vulnerabilidades de seguranca"
```

**Delegacao paralela (dentro de uma tarefa maior):** peca ao Claude Code para "usar
subagentes agy" para N tarefas independentes - ele vai invocar varios `agy-worker` de
uma vez via tool Agent. Veja `skills/agy-delegation/SKILL.md` para o comportamento
completo, incluindo o que acontece quando o agy nao consegue terminar (o Claude assume).

## Seguranca

- `agy_bridge.py` chama `agy.exe` com `--dangerously-skip-permissions` (necessario para
  rodar sem prompts interativos em modo headless). Isso equivale a rodar um agente
  autonomo sem supervisao no diretorio informado - so use `--cwd` apontando para
  diretorios onde isso e aceitavel.
- Nenhuma credencial e manipulada por este plugin; ele usa a sessao do `agy` ja
  autenticada na maquina do usuario.
- `.agy-tasks/` (specs e resultados das tarefas) fica de fora do git via `.gitignore`.
