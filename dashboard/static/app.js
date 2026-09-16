const state = {
  sessions: new Map(), // session_id -> { meta, ws, toolCards: Map, el: {chatEl} }
  activeId: null,
  browsePath: null,
};

const el = (sel) => document.querySelector(sel);
const create = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

// --------------------------------------------------------------------------
// Projetos
// --------------------------------------------------------------------------

async function loadProjects() {
  const res = await fetch("/api/projects");
  const data = await res.json();
  const list = el("#project-list");
  list.innerHTML = "";
  for (const p of data.projects) {
    const item = create("div", "project-item");
    item.innerHTML = `<div class="name">${escapeHtml(p.name)}</div><div class="path">${escapeHtml(p.path)}</div>`;
    item.onclick = () => startSession(p.path, p.name);
    list.appendChild(item);
  }
}

async function loadGlobalAgents() {
  try {
    const res = await fetch("/api/system/agents");
    const data = await res.json();
    const list = el("#global-agents-list");
    list.innerHTML = "";
    for (const a of data.agents || []) {
      const item = create("div", "agent-item");
      item.innerHTML = `<div>${escapeHtml(a.name || a.sessionId.slice(0, 8))}</div><div class="cwd">${escapeHtml(a.cwd)}</div>`;
      list.appendChild(item);
    }
    if (!data.agents || data.agents.length === 0) {
      list.innerHTML = '<div style="padding:4px">Nenhuma sessao ativa.</div>';
    }
  } catch (e) { /* silencioso */ }
}

// --------------------------------------------------------------------------
// Modal de navegacao de pastas
// --------------------------------------------------------------------------

async function openBrowse(path) {
  const res = await fetch("/api/browse" + (path ? `?path=${encodeURIComponent(path)}` : ""));
  const data = await res.json();
  state.browsePath = data.path || data.default_root || null;
  el("#browse-path").textContent = state.browsePath || "Selecionar pasta";
  const list = el("#browse-list");
  list.innerHTML = "";

  if (data.parent) {
    const up = create("div", "browse-item");
    up.textContent = ".. (voltar)";
    up.onclick = () => openBrowse(data.parent);
    list.appendChild(up);
  } else if (data.default_root && !path) {
    const root = create("div", "browse-item");
    root.textContent = `📁 ${data.default_root}`;
    root.onclick = () => openBrowse(data.default_root);
    list.appendChild(root);
  }

  for (const entry of data.entries) {
    const item = create("div", "browse-item");
    item.textContent = "📁 " + entry.name;
    item.onclick = () => openBrowse(entry.path);
    list.appendChild(item);
  }
  el("#browse-modal").style.display = "flex";
}

el("#btn-browse").onclick = () => openBrowse(null);
el("#browse-close").onclick = () => { el("#browse-modal").style.display = "none"; };
el("#browse-choose").onclick = async () => {
  if (!state.browsePath) return;
  await fetch("/api/projects", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: state.browsePath }),
  });
  el("#browse-modal").style.display = "none";
  await loadProjects();
  startSession(state.browsePath);
};

// --------------------------------------------------------------------------
// Sessoes
// --------------------------------------------------------------------------

async function startSession(cwd, name) {
  const res = await fetch("/api/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cwd, name }),
  });
  const meta = await res.json();
  if (meta.error) { alert(meta.error); return; }
  attachSession(meta);
}

function attachSession(meta) {
  const chatEl = create("div", "chat-area");
  chatEl.style.display = "none";
  el(".main").insertBefore(chatEl, el("#composer"));

  const entry = { meta, ws: null, toolCards: new Map(), chatEl, activity: [] };
  state.sessions.set(meta.session_id, entry);

  const ws = new WebSocket(`ws://${location.host}/ws/sessions/${meta.session_id}`);
  ws.onmessage = (ev) => {
    const payload = JSON.parse(ev.data);
    if (payload.type === "error") {
      appendSystemNote(entry, payload.message, true);
      return;
    }
    entry.meta.status = payload.status || entry.meta.status;
    handleEvent(entry, payload.data);
    renderTabs();
    if (state.activeId === meta.session_id) updateComposerState();
  };
  ws.onclose = () => {
    entry.meta.status = "ended";
    renderTabs();
    if (state.activeId === meta.session_id) updateComposerState();
  };
  entry.ws = ws;

  renderTabs();
  selectSession(meta.session_id);
}

function renderTabs() {
  const tabsEl = el("#session-tabs");
  tabsEl.innerHTML = "";
  for (const [id, entry] of state.sessions) {
    const tab = create("div", "tab" + (id === state.activeId ? " active" : ""));
    const dot = create("span", "dot " + (entry.meta.status || ""));
    tab.appendChild(dot);
    const label = create("span");
    label.textContent = entry.meta.name || id.slice(0, 8);
    tab.appendChild(label);
    const close = create("span", "close");
    close.textContent = "x";
    close.onclick = (e) => { e.stopPropagation(); closeSession(id); };
    tab.appendChild(close);
    tab.onclick = () => selectSession(id);
    tabsEl.appendChild(tab);
  }
}

function selectSession(id) {
  state.activeId = id;
  el("#empty-state").style.display = "none";
  for (const [sid, entry] of state.sessions) {
    entry.chatEl.style.display = sid === id ? "flex" : "none";
  }
  el("#composer").style.display = "flex";
  renderTabs();
  updateComposerState();
  renderActivity(id);
}

async function closeSession(id) {
  const entry = state.sessions.get(id);
  if (!entry) return;
  try { await fetch(`/api/sessions/${id}/stop`, { method: "POST" }); } catch (e) {}
  entry.ws && entry.ws.close();
  entry.chatEl.remove();
  state.sessions.delete(id);
  if (state.activeId === id) {
    state.activeId = null;
    el("#composer").style.display = "none";
    el("#empty-state").style.display = "flex";
  }
  renderTabs();
}

function updateComposerState() {
  const entry = state.sessions.get(state.activeId);
  const busy = !entry || entry.meta.status === "busy" || entry.meta.status === "ended" || entry.meta.status === "error";
  el("#composer-send").disabled = busy;
}

// --------------------------------------------------------------------------
// Renderizacao de eventos do protocolo stream-json
// --------------------------------------------------------------------------

function handleEvent(entry, event) {
  if (!event) return;
  switch (event.type) {
    case "bridge_user_echo":
      appendBubble(entry, "user", event.text);
      break;
    case "assistant":
      renderAssistantMessage(entry, event);
      break;
    case "user":
      renderToolResults(entry, event);
      break;
    case "bridge_stderr":
      logActivity(entry, { kind: "stderr", text: event.text, ts: Date.now() });
      break;
    case "bridge_ended":
      appendSystemNote(entry, "Sessao encerrada (codigo " + event.exit_code + ").", event.exit_code !== 0);
      break;
    case "bridge_error":
      appendSystemNote(entry, event.message, true);
      break;
    case "result":
      logActivity(entry, {
        kind: "result", ts: Date.now(),
        text: `Turno concluido em ${event.duration_ms}ms - custo $${(event.total_cost_usd || 0).toFixed(4)}`,
      });
      break;
    default:
      break;
  }
  if (state.activeId) renderActivity(state.activeId);
}

function renderAssistantMessage(entry, event) {
  const parentId = event.parent_tool_use_id;
  const blocks = (event.message && event.message.content) || [];
  for (const block of blocks) {
    if (block.type === "text" && block.text) {
      if (parentId && entry.toolCards.has(parentId)) {
        appendSubagentText(entry, parentId, block.text);
      } else {
        appendBubble(entry, "assistant", block.text);
      }
    } else if (block.type === "tool_use") {
      renderToolUse(entry, block, parentId);
    }
  }
}

function renderToolUse(entry, block, parentId) {
  const card = create("div", "tool-card" + (parentId ? " subagent" : ""));
  const head = create("div", "head");
  head.innerHTML = `<span>🔧 ${escapeHtml(block.name)}</span><span class="status running">rodando</span>`;
  card.appendChild(head);
  const inputPre = create("pre");
  inputPre.textContent = truncate(JSON.stringify(block.input || {}, null, 2), 600);
  card.appendChild(inputPre);

  const host = parentId && entry.toolCards.has(parentId)
    ? entry.toolCards.get(parentId).el
    : entry.chatEl;
  host.appendChild(card);
  entry.chatEl.scrollTop = entry.chatEl.scrollHeight;

  entry.toolCards.set(block.id, { el: card, name: block.name, input: block.input, startedAt: Date.now() });
  logActivity(entry, { kind: "tool_use", tool: block.name, input: block.input, ts: Date.now(), id: block.id });
}

function appendSubagentText(entry, parentId, text) {
  const parent = entry.toolCards.get(parentId);
  if (!parent) return;
  const bubble = create("div", "tool-card subagent");
  bubble.innerHTML = `<div class="head"><span>💬 subagente</span></div><pre>${escapeHtml(truncate(text, 800))}</pre>`;
  parent.el.appendChild(bubble);
  entry.chatEl.scrollTop = entry.chatEl.scrollHeight;
}

function renderToolResults(entry, event) {
  const blocks = (event.message && event.message.content) || [];
  for (const block of blocks) {
    if (block.type !== "tool_result") continue;
    const card = entry.toolCards.get(block.tool_use_id);
    if (!card) continue;
    const statusEl = card.el.querySelector(".status");
    if (statusEl) { statusEl.textContent = "concluido"; statusEl.classList.remove("running"); statusEl.classList.add("done"); }
    let text = "";
    if (typeof block.content === "string") text = block.content;
    else if (Array.isArray(block.content)) text = block.content.map((c) => c.text || "").join("\n");
    if (text) {
      const pre = create("pre");
      pre.textContent = truncate(text, 800);
      card.el.appendChild(pre);
    }
    logActivity(entry, { kind: "tool_result", tool: card.name, ts: Date.now(), id: block.tool_use_id });
  }
}

function appendBubble(entry, role, text) {
  const msg = create("div", "msg " + role);
  const bubble = create("div", "bubble");
  bubble.textContent = text;
  msg.appendChild(bubble);
  entry.chatEl.appendChild(msg);
  entry.chatEl.scrollTop = entry.chatEl.scrollHeight;
}

function appendSystemNote(entry, text, isError) {
  const note = create("div", "msg assistant");
  note.innerHTML = `<div class="bubble" style="color:${isError ? "var(--danger)" : "var(--text-muted)"}">${escapeHtml(text)}</div>`;
  entry.chatEl.appendChild(note);
  entry.chatEl.scrollTop = entry.chatEl.scrollHeight;
}

// --------------------------------------------------------------------------
// Painel de atividade
// --------------------------------------------------------------------------

function logActivity(entry, item) {
  entry.activity.unshift(item);
  entry.activity = entry.activity.slice(0, 100);
}

function renderActivity(sessionId) {
  const entry = state.sessions.get(sessionId);
  const host = el("#activity-live");
  host.innerHTML = "";
  if (!entry) return;
  for (const item of entry.activity) {
    const card = create("div", "task-card");
    if (item.kind === "tool_use") {
      card.innerHTML = `<div class="row1"><span>🔧 ${escapeHtml(item.tool)}</span></div><div class="meta">${new Date(item.ts).toLocaleTimeString()}</div>`;
    } else if (item.kind === "tool_result") {
      card.innerHTML = `<div class="row1"><span>✔ ${escapeHtml(item.tool)} concluido</span></div><div class="meta">${new Date(item.ts).toLocaleTimeString()}</div>`;
    } else if (item.kind === "result") {
      card.innerHTML = `<div class="row1"><span>⏱ ${escapeHtml(item.text)}</span></div>`;
    } else {
      card.innerHTML = `<div class="row1"><span>${escapeHtml(item.text || "")}</span></div>`;
    }
    host.appendChild(card);
  }
}

async function loadAgyTasks() {
  const res = await fetch("/api/agy-tasks");
  const data = await res.json();
  const host = el("#activity-agy");
  host.innerHTML = "";
  for (const t of data.tasks) {
    const card = create("div", "task-card");
    card.innerHTML = `
      <div class="row1"><span>${escapeHtml(t.profile)} · ${escapeHtml(t.project_name)}</span><span class="status-${t.status}">${escapeHtml(t.status)}</span></div>
      <div class="meta">${t.task_size || ""} · ${t.duration_seconds || 0}s · ${new Date(t.mtime * 1000).toLocaleString()}</div>
    `;
    host.appendChild(card);
  }
  if (data.tasks.length === 0) host.innerHTML = '<div class="task-card">Nenhuma tarefa do agy registrada ainda.</div>';
}

for (const tabBtn of document.querySelectorAll(".activity-tab")) {
  tabBtn.onclick = () => {
    document.querySelectorAll(".activity-tab").forEach((b) => b.classList.remove("active"));
    tabBtn.classList.add("active");
    const target = tabBtn.dataset.tab;
    el("#activity-live").style.display = target === "live" ? "flex" : "none";
    el("#activity-agy").style.display = target === "agy" ? "flex" : "none";
    if (target === "agy") loadAgyTasks();
  };
}

// --------------------------------------------------------------------------
// Composer
// --------------------------------------------------------------------------

el("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  sendActiveMessage();
});
el("#composer-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendActiveMessage();
  }
});

function sendActiveMessage() {
  const entry = state.sessions.get(state.activeId);
  const input = el("#composer-input");
  const text = input.value.trim();
  if (!entry || !text) return;
  entry.ws.send(JSON.stringify({ type: "message", text }));
  input.value = "";
  entry.meta.status = "busy";
  renderTabs();
  updateComposerState();
}

// --------------------------------------------------------------------------
// Utils
// --------------------------------------------------------------------------

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function truncate(str, n) { return str.length > n ? str.slice(0, n) + "\n... (truncado)" : str; }

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------

loadProjects();
loadGlobalAgents();
setInterval(loadGlobalAgents, 10000);
setInterval(() => { if (el("#activity-agy").style.display !== "none") loadAgyTasks(); }, 8000);
