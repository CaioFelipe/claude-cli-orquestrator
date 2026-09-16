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

Inclui tambem um **painel web local** (`dashboard/`) para abrir pastas de projeto,
iniciar/gerenciar varias conversas com o Claude Code em paralelo e visualizar em tempo
real os subagentes/processos (incluindo as tarefas delegadas ao agy).

## Estrutura

```
claude-cli-orquestrator/
├── .claude-plugin/
│   ├── plugin.json            # manifesto do plugin
│   └── marketplace.json       # permite `claude plugin marketplace add` local
├── agents/
│   └── agy-worker.md           # subagente "proxy fino": chama o agy e relata o resultado
├── skills/
│   └── agy-delegation/
│       └── SKILL.md            # quando/como delegar, paralelismo, guardrail, self-healing
├── commands/
│   └── agy.md                  # /agy "<instrucao>" para delegacao pontual manual
├── scripts/
│   ├── agy_bridge.py            # ponte de execucao (subprocess -> agy.exe)
│   ├── quota_guard.py           # checagem best-effort da cota da conta local
│   └── config.json              # perfis (agy, mock) e parametros default
├── dashboard/                   # painel web local (sessoes de chat + atividade)
│   ├── server.py                  # FastAPI: sessoes, websocket, historico agy, browser de pastas
│   ├── claude_bridge.py            # wrapper de UMA sessao `claude -p --input-format stream-json`
│   ├── static/                     # frontend (HTML/CSS/JS puro, sem build)
│   └── requirements.txt
├── abrir_painel.bat             # atalho de 1 clique para o painel (Windows)
└── README.md
```

## Pre-requisitos

- [Claude Code](https://claude.com/claude-code) instalado.
- [Antigravity CLI](https://antigravity.google/) instalado e autenticado (`agy` no PATH -
  por padrao em `%LOCALAPPDATA%\agy\bin\agy.exe` no Windows).
- Python 3.10+ no PATH (usado pelos scripts do plugin e pelo painel).

## Instalacao do plugin

### Localmente (ja feito nesta maquina)

```powershell
claude plugin marketplace add "C:\Users\Admin\Desktop\PROGRAMACAO\claude-cli-orquestrator"
claude plugin install claude-cli-orquestrator@claude-cli-orquestrator
```

Reinicie a sessao do Claude Code para carregar o comando `/agy`, o agente `agy-worker`
e a skill `agy-delegation`.

### Via GitHub (outra maquina)

```
/plugin marketplace add CaioFelipe/claude-cli-orquestrator
/plugin install claude-cli-orquestrator@claude-cli-orquestrator
```

## Uso do plugin

**Delegacao pontual:**
```
/agy "Analise src/auth.py e liste vulnerabilidades de seguranca"
```

**Delegacao paralela (dentro de uma tarefa maior):** peca ao Claude Code para "usar
subagentes agy" para N tarefas independentes - ele vai invocar varios `agy-worker` de
uma vez via tool Agent. Veja `skills/agy-delegation/SKILL.md` para o comportamento
completo, incluindo o que acontece quando o agy nao consegue terminar (o Claude assume).

## Painel web (dashboard)

O painel roda 100% local (`http://127.0.0.1:5151`) e fala com o Claude Code pelo mesmo
protocolo que a CLI interativa usa por baixo dos panos
(`claude -p --input-format stream-json --output-format stream-json`), entao cada "sessao"
no painel e uma conversa real com voce mesmo (Claude Code), com o mesmo acesso a
ferramentas, plugins e skills - inclusive o `claude-cli-orquestrator`.

### Instalar dependencias (uma vez)

O Python global desta maquina tinha um `starlette` incompativel instalado, entao o
painel usa seu proprio virtualenv:

```powershell
cd dashboard
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### Rodar

```powershell
.\abrir_painel.bat
```

Isso abre `http://127.0.0.1:5151` no navegador e sobe o servidor. Na interface:

1. Clique em **+** em "Projetos" para navegar ate uma pasta e abrir uma sessao nela.
2. Cada pasta aberta vira uma aba de conversa - pode abrir varias em paralelo.
3. O painel direito mostra, ao vivo, cada ferramenta chamada nessa conversa (Bash, Edit,
   Read, subagentes via Task/`agy-worker`, etc.) com status rodando/concluido, e uma aba
   separada com o historico de tarefas delegadas ao `agy` (`.agy-tasks`) em todos os
   projetos ja abertos.
4. "Sessoes do sistema" na barra lateral mostra (somente leitura) todas as sessoes do
   Claude Code ativas na maquina agora, mesmo as abertas pelo terminal (via
   `claude agents --json`).

### Limitacoes conhecidas (v1)

- O modo de permissao padrao das sessoes criadas pelo painel e `default`: ferramentas que
  pedem aprovacao interativa sao automaticamente negadas (nao ha canal de aprovacao pelo
  protocolo headless ainda). Para tarefas que precisam editar arquivos livremente, crie a
  sessao com um `permission_mode` mais permissivo (`acceptEdits` ou `bypassPermissions`) -
  isso ja e aceito pela API (`POST /api/sessions`), so falta expor o seletor na UI.
  Trate `bypassPermissions` com o mesmo cuidado que trataria rodar um agente autonomo sem
  supervisao.
- Sem streaming token-a-token (mostra a resposta completa de cada turno, nao caractere por
  caractere) - possivel evolucao futura via `--include-partial-messages`.
- Historico do painel some se o servidor reiniciar no meio de uma sessao (a conversa em si
  continua existindo no Claude Code e pode ser retomada com `claude --resume <session_id>`
  ou pelo endpoint `POST /api/sessions/{id}/resume`).

## Seguranca

- `agy_bridge.py` chama `agy.exe` com `--dangerously-skip-permissions` (necessario para
  rodar sem prompts interativos em modo headless). Isso equivale a rodar um agente
  autonomo sem supervisao no diretorio informado - so use `--cwd` apontando para
  diretorios onde isso e aceitavel.
- Nenhuma credencial e manipulada por este plugin; ele usa a sessao do `agy` ja
  autenticada na maquina do usuario.
- `.agy-tasks/` (specs e resultados das tarefas) e `dashboard/state.json`/`dashboard/sessions/`
  ficam de fora do git via `.gitignore`.
