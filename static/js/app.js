import {
  api,
  apiUrl,
  connectEvents,
  getAccessToken,
  getConnectionMode,
  getServerBaseUrl,
  isNativeApp,
  setAccessToken,
  setConnectionMode,
  setServerBaseUrl,
} from "./api.js";
import { formatShortTime, renderSafeMarkdown, setupDialogs, showToast } from "./ui.js";
import { createVoiceRecognizer, isVoiceInputSupported, speakKorean } from "./voice.js";

const state = {
  activeTab: "chat",
  activeConversationId: "default",
  conversations: [],
  messages: [],
  approvals: [],
  calls: [],
  tasks: [],
  memories: [],
  contacts: [],
  settings: {},
  health: {},
  browser: {},
  sseSource: null,
  bannerDismissed: false,
};

const CALL_STATUS_LABELS = {
  queued: "대기 중",
  dialing: "발신 중",
  ringing: "신호 연결 중",
  in_progress: "통화 진행 중",
  completed: "통화 완료",
  failed: "통화 실패",
};

const PROVIDER_LABELS = {
  gemini: "Gemini",
  openai: "OpenAI",
  anthropic: "Claude",
  ollama: "Ollama",
  demo: "로컬 데모",
};

function byId(id) {
  return document.getElementById(id);
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  for (const btn of document.querySelectorAll("[data-tab]")) {
    btn.classList.toggle("is-active", btn.dataset.tab === tabName);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.hidden = panel.id !== `tab-${tabName}`;
  }
  if (tabName === "browser") {
    refreshBrowser();
  }
}

function renderHeaderPills() {
  const provider = state.health.active_llm_provider || "demo";
  const tel = state.health.telephony_provider || "simulation";
  const llmPill = byId("llm-mode-pill");
  if (llmPill) {
    llmPill.textContent = `${PROVIDER_LABELS[provider] || provider} · 전화: ${tel === "simulation" ? "시뮬레이션" : tel}`;
  }
  const hostBadge = byId("sidebar-host-badge");
  if (hostBadge) {
    hostBadge.textContent = `${state.health.host || "localhost"} 온라인`;
  }
}

function renderConversations() {
  const list = byId("conv-list");
  if (!list) return;
  list.replaceChildren();

  for (const conv of state.conversations) {
    const row = document.createElement("div");
    row.className = "conv-item" + (conv.id === state.activeConversationId ? " is-active" : "");

    const selBtn = document.createElement("button");
    selBtn.type = "button";
    selBtn.className = "conv-select-btn";
    selBtn.textContent = conv.title;
    selBtn.addEventListener("click", () => selectConversation(conv.id));
    row.appendChild(selBtn);

    if (state.conversations.length > 1) {
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn-ghost btn-sm";
      delBtn.setAttribute("aria-label", `${conv.title} 삭제`);
      delBtn.textContent = "×";
      delBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await api.deleteConversation(conv.id);
        await loadConversations();
      });
      row.appendChild(delBtn);
    }

    list.appendChild(row);
  }

  const active = state.conversations.find((c) => c.id === state.activeConversationId);
  const titleEl = byId("page-title");
  if (titleEl && active) {
    titleEl.textContent = active.title;
  }
}

function createApprovalCard(approval) {
  const card = document.createElement("article");
  card.className = "card stack-gap approval-card";

  const head = document.createElement("div");
  head.className = "card-row-head";
  const title = document.createElement("strong");
  title.textContent = approval.title;
  const badge = document.createElement("span");
  badge.className = "pill";
  badge.textContent = approval.kind === "phone_call" ? "전화 승인 대기" : "직접 조작 요청";
  head.append(title, badge);
  card.appendChild(head);

  const desc = document.createElement("p");
  desc.textContent = approval.description;
  card.appendChild(desc);

  const payload = approval.payload || {};
  if (payload.provider) {
    const meta = document.createElement("p");
    meta.className = "muted";
    meta.textContent = `방식: ${payload.provider === "simulation" ? "시뮬레이션 (테스트)" : payload.provider}`;
    card.appendChild(meta);
  }

  const actions = document.createElement("div");
  actions.className = "toolbar";

  if (approval.kind === "browser_takeover") {
    const openBrowserBtn = document.createElement("button");
    openBrowserBtn.type = "button";
    openBrowserBtn.className = "btn-secondary btn-sm";
    openBrowserBtn.textContent = "🌐 브라우저 탭 열기";
    openBrowserBtn.addEventListener("click", () => setActiveTab("browser"));
    actions.appendChild(openBrowserBtn);
  }

  const approveBtn = document.createElement("button");
  approveBtn.type = "button";
  approveBtn.className = "btn-primary btn-sm";
  approveBtn.textContent =
    approval.kind === "phone_call" ? "📞 승인 후 전화 걸기" : "✅ 직접 처리 완료 · 계속 진행";
  approveBtn.addEventListener("click", async () => {
    approveBtn.disabled = true;
    try {
      const res = await api.decideApproval(approval.id, "approved");
      if (res.call) {
        showToast("AI 전화 발신을 시작했습니다.");
      }
      await Promise.all([loadApprovals(), loadCalls()]);
    } catch (err) {
      showToast(`승인 처리 실패: ${err.message}`);
      approveBtn.disabled = false;
    }
  });

  const rejectBtn = document.createElement("button");
  rejectBtn.type = "button";
  rejectBtn.className = "btn-secondary btn-sm";
  rejectBtn.textContent = "거절";
  rejectBtn.addEventListener("click", async () => {
    rejectBtn.disabled = true;
    await api.decideApproval(approval.id, "rejected");
    await loadApprovals();
  });

  actions.append(approveBtn, rejectBtn);
  card.appendChild(actions);
  return card;
}

function renderApprovals() {
  const pending = state.approvals.filter((a) => a.status === "pending");

  const badge = byId("pending-approvals-badge");
  if (badge) {
    badge.hidden = pending.length === 0;
    badge.textContent = String(pending.length);
  }

  const chatTray = byId("approvals-tray");
  if (chatTray) {
    chatTray.replaceChildren(...pending.map((a) => createApprovalCard(a)));
  }

  const callsTray = byId("calls-approvals-list");
  if (callsTray) {
    callsTray.replaceChildren(
      ...pending.filter((a) => a.kind === "phone_call").map((a) => createApprovalCard(a))
    );
  }

  const takeoverBanner = byId("browser-takeover-banner");
  if (takeoverBanner) {
    takeoverBanner.replaceChildren(
      ...pending.filter((a) => a.kind === "browser_takeover").map((a) => createApprovalCard(a))
    );
  }
}

function renderMessages() {
  const list = byId("messages-list");
  if (!list) return;
  list.replaceChildren();

  for (const m of state.messages) {
    const bubble = document.createElement("article");
    bubble.className = "message-bubble " + (m.role === "user" ? "is-user" : "is-assistant");

    const meta = document.createElement("div");
    meta.className = "message-meta";
    const who = document.createElement("strong");
    who.textContent = m.role === "user" ? state.settings.user_name || "나" : "Bruno AI";
    const time = document.createElement("span");
    time.textContent = formatShortTime(m.created_at);
    meta.append(who, time);

    bubble.append(meta, renderSafeMarkdown(m.content));
    list.appendChild(bubble);
  }

  const scroll = byId("chat-scroll");
  if (scroll) {
    scroll.scrollTop = scroll.scrollHeight;
  }
}

function renderCalls() {
  const container = byId("calls-list");
  if (!container) return;
  container.replaceChildren();

  if (state.calls.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card muted";
    empty.textContent = "아직 AI 통화 기록이 없습니다. 우측 상단 '+ 새 AI 전화 걸기' 또는 채팅으로 전화를 요청해 보세요.";
    container.appendChild(empty);
    return;
  }

  for (const call of state.calls) {
    const card = document.createElement("article");
    card.className = "card stack-gap";

    const head = document.createElement("div");
    head.className = "card-row-head";
    const title = document.createElement("strong");
    title.textContent = `${call.recipient_name || "상대방"} (${call.phone_number})`;
    const statusPill = document.createElement("span");
    statusPill.className = "pill";
    statusPill.textContent = CALL_STATUS_LABELS[call.status] || call.status;
    head.append(title, statusPill);
    card.appendChild(head);

    const goal = document.createElement("p");
    goal.textContent = `목적: ${call.goal}`;
    card.appendChild(goal);

    const meta = document.createElement("p");
    meta.className = "muted";
    meta.textContent = `방식: ${call.provider} · ${formatShortTime(call.created_at)}`;
    card.appendChild(meta);

    if (call.transcript && call.transcript.length > 0) {
      const box = document.createElement("div");
      box.className = "transcript-box";
      for (const turn of call.transcript) {
        const line = document.createElement("p");
        const sp = document.createElement("strong");
        sp.textContent = `${turn.speaker}: `;
        line.append(sp, document.createTextNode(turn.text));
        box.appendChild(line);
      }
      card.appendChild(box);
    }

    if (call.summary) {
      const sum = document.createElement("p");
      const strong = document.createElement("strong");
      strong.textContent = "요약: ";
      sum.append(strong, document.createTextNode(call.summary));
      card.appendChild(sum);
    }

    container.appendChild(card);
  }
}

function renderTasks() {
  const container = byId("tasks-list");
  if (!container) return;
  container.replaceChildren();

  if (state.tasks.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card muted";
    empty.textContent = "등록된 일정이나 할 일이 없습니다.";
    container.appendChild(empty);
    return;
  }

  for (const t of state.tasks) {
    const card = document.createElement("div");
    card.className = "card card-row-head";

    const label = document.createElement("label");
    label.className = "checkbox-row";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = t.status === "done";
    cb.addEventListener("change", async () => {
      await api.patchTask(t.id, { status: cb.checked ? "done" : "open" });
      await loadTasks();
    });

    const textWrap = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = t.title;
    if (t.status === "done") {
      title.style.textDecoration = "line-through";
      title.style.opacity = "0.65";
    }
    textWrap.appendChild(title);
    if (t.due_at) {
      const due = document.createElement("p");
      due.className = "muted";
      due.textContent = `⏰ ${formatShortTime(t.due_at)}`;
      textWrap.appendChild(due);
    }

    label.append(cb, textWrap);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-ghost btn-sm";
    delBtn.textContent = "삭제";
    delBtn.addEventListener("click", async () => {
      await api.deleteTask(t.id);
      await loadTasks();
    });

    card.append(label, delBtn);
    container.appendChild(card);
  }
}

function renderMemoriesAndContacts() {
  const memList = byId("memories-list");
  if (memList) {
    memList.replaceChildren();
    if (state.memories.length === 0) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "저장된 기억 버블이 없습니다.";
      memList.appendChild(empty);
    }
    for (const mem of state.memories) {
      const item = document.createElement("div");
      item.className = "card stack-gap";
      const head = document.createElement("div");
      head.className = "card-row-head";
      const cat = document.createElement("span");
      cat.className = "pill";
      cat.textContent = mem.category;
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-ghost btn-sm";
      del.textContent = "삭제";
      del.addEventListener("click", async () => {
        await api.deleteMemory(mem.id);
        await loadMemories();
      });
      head.append(cat, del);

      const body = document.createElement("p");
      body.textContent = mem.content;
      item.append(head, body);
      memList.appendChild(item);
    }
  }

  const ctList = byId("contacts-list");
  if (ctList) {
    ctList.replaceChildren();
    if (state.contacts.length === 0) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "등록된 연락처가 없습니다.";
      ctList.appendChild(empty);
    }
    for (const ct of state.contacts) {
      const item = document.createElement("div");
      item.className = "card card-row-head";
      const info = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = ct.name + (ct.relation ? ` (${ct.relation})` : "");
      const phone = document.createElement("p");
      phone.className = "muted";
      phone.textContent = ct.phone + (ct.notes ? ` · ${ct.notes}` : "");
      info.append(name, phone);

      const actions = document.createElement("div");
      actions.className = "toolbar";

      const telDigits = String(ct.phone || "").replace(/[^+\d*#,;]/g, "");
      if (telDigits) {
        const directCallLink = document.createElement("a");
        directCallLink.href = `tel:${telDigits}`;
        directCallLink.className = "btn-secondary btn-sm";
        directCallLink.textContent = "📱";
        directCallLink.style.textDecoration = "none";
        directCallLink.setAttribute("aria-label", `${ct.name}에게 직접 전화 걸기`);
        directCallLink.title = "기기 전화 앱으로 바로 걸기";
        actions.appendChild(directCallLink);
      }

      const callBtn = document.createElement("button");
      callBtn.type = "button";
      callBtn.className = "btn-secondary btn-sm";
      callBtn.textContent = "📞";
      callBtn.title = "AI 비서가 대신 전화 걸기";
      callBtn.setAttribute("aria-label", `${ct.name}에게 AI 전화 걸기`);
      callBtn.addEventListener("click", () => openCallDialog(ct));

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btn-ghost btn-sm";
      delBtn.textContent = "삭제";
      delBtn.addEventListener("click", async () => {
        await api.deleteContact(ct.id);
        await loadContacts();
      });

      actions.append(callBtn, delBtn);
      item.append(info, actions);
      ctList.appendChild(item);
    }
  }
}

function renderBrowserState() {
  const b = state.browser || {};
  const statusText = byId("browser-status-text");
  const img = byId("browser-screenshot-img");
  const urlInput = byId("browser-url-input");
  if (urlInput && b.url && document.activeElement !== urlInput) {
    urlInput.value = b.url;
  }
  if (!statusText || !img) return;

  if (b.standalone || getConnectionMode() === "standalone") {
    statusText.textContent =
      "현재 '기기 단독 모드'입니다. 샌드박스 원격 브라우저 화면 공유는 ⚙️ 설정에서 '원격 서버 모드'(로컬 PC / Cloudflare Tunnel / Render 등)로 전환할 때 사용할 수 있습니다. 채팅·AI 전화·기억·연락처·일정은 단독 모드에서도 바로 동작합니다.";
    img.hidden = true;
    return;
  }

  if (!b.available) {
    statusText.textContent =
      "Playwright Chromium이 설치되어 있지 않습니다. bruno-sony 배포 시 `--with-browser` 옵션을 사용하면 라이브 브라우저 조작이 활성화됩니다.";
    img.hidden = true;
    return;
  }

  if (b.screenshot_url) {
    statusText.textContent = `${b.title || "페이지"} (${b.url || ""}) — 화면을 탭/클릭하면 서버 브라우저가 해당 위치를 클릭합니다.`;
    img.src = apiUrl(b.screenshot_url);
    img.hidden = false;
  } else {
    statusText.textContent = "위 입력창에 URL을 입력해 샌드박스 브라우저를 시작하세요.";
    img.hidden = true;
  }
}

async function refreshBrowser() {
  try {
    state.browser = await api.browserState();
    renderBrowserState();
  } catch {
    // ignore
  }
}

async function loadConversations() {
  const data = await api.listConversations();
  state.conversations = data.conversations || [];
  if (
    state.conversations.length > 0 &&
    !state.conversations.some((c) => c.id === state.activeConversationId)
  ) {
    state.activeConversationId = state.conversations[0].id;
  }
  renderConversations();
  await loadMessages();
}

async function selectConversation(convId) {
  state.activeConversationId = convId;
  renderConversations();
  await loadMessages();
  setActiveTab("chat");
}

async function loadMessages() {
  const data = await api.getMessages(state.activeConversationId);
  state.messages = data.messages || [];
  renderMessages();
}

async function loadApprovals() {
  const data = await api.listApprovals();
  state.approvals = data.approvals || [];
  renderApprovals();
}

async function loadCalls() {
  const data = await api.listCalls();
  state.calls = data.calls || [];
  renderCalls();
}

async function loadTasks() {
  const data = await api.listTasks();
  state.tasks = data.tasks || [];
  renderTasks();
}

async function loadMemories() {
  const data = await api.listMemories();
  state.memories = data.memories || [];
  renderMemoriesAndContacts();
}

async function loadContacts() {
  const data = await api.listContacts();
  state.contacts = data.contacts || [];
  renderMemoriesAndContacts();
}

async function loadMeta() {
  const [health, settings] = await Promise.all([api.health(), api.getSettings()]);
  state.health = health;
  state.settings = settings;
  renderHeaderPills();
}

function showConnectionBanner(errorDetail = "") {
  const banner = byId("connection-banner");
  const desc = byId("connection-banner-desc");
  if (!banner || state.bannerDismissed) return;
  const current = getServerBaseUrl() || window.location.origin;
  if (desc) {
    desc.textContent = `대상: ${current}${errorDetail ? ` (${errorDetail})` : ""}. Tailscale이 켜져 있는지 확인하거나 다른 서버 주소를 선택하세요.`;
  }
  banner.hidden = false;
}

function hideConnectionBanner() {
  const banner = byId("connection-banner");
  if (banner) banner.hidden = true;
}

async function loadAllData() {
  try {
    await Promise.all([
      loadMeta(),
      loadConversations(),
      loadApprovals(),
      loadCalls(),
      loadTasks(),
      loadMemories(),
      loadContacts(),
      refreshBrowser(),
    ]);
    hideConnectionBanner();
  } catch (err) {
    showConnectionBanner(err?.message || "연결 실패");
  }
}

async function openSettingsDialog() {
  const dialog = byId("settings-dialog");
  const form = byId("settings-form");
  if (!dialog || !form) return;

  const modeSelect = byId("connection-mode-select");
  const remoteFields = byId("remote-server-fields");
  const serverInput = byId("server-url-input");
  const tokenInput = byId("server-token-input");
  const testResult = byId("server-test-result");
  const currentMode = getConnectionMode();
  if (modeSelect) {
    modeSelect.value = currentMode;
  }
  if (remoteFields) {
    remoteFields.hidden = currentMode === "standalone";
  }
  if (serverInput) {
    serverInput.value = getServerBaseUrl();
  }
  if (tokenInput) {
    tokenInput.value = getAccessToken();
  }
  if (testResult) {
    testResult.textContent = "";
  }

  try {
    await loadMeta();
  } catch {
    // allow opening settings even when remote server is offline so user can change URL
  }

  const s = state.settings;
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") {
      el.checked = Boolean(s[el.name]);
    } else if (el.type === "password") {
      el.value = "";
      const hint = byId(`hint-${el.name}`);
      if (hint) {
        hint.textContent = s[`has_${el.name}`] ? `(저장됨: ${s[el.name]})` : "(미설정)";
      }
    } else if (s[el.name] !== undefined) {
      el.value = String(s[el.name]);
    }
  }

  const healthBox = byId("settings-health-info");
  if (healthBox) {
    const activeServer =
      currentMode === "standalone"
        ? "이 기기 (Tailscale·서버 없이 단독 실행)"
        : getServerBaseUrl() || window.location.origin;
    healthBox.textContent =
      `실행 모드: ${currentMode === "standalone" ? "기기 단독 모드" : "원격 서버 모드"}\n` +
      `현재 연결 대상: ${activeServer}\n` +
      `응답 호스트: ${state.health.host || "미연결"}\n` +
      `활성 LLM 모드: ${state.health.active_llm_provider || "demo"}\n` +
      `전화 발신 모드: ${state.health.telephony_provider || "simulation"}\n` +
      `Playwright 브라우저: ${state.health.playwright_available ? "사용 가능" : "미설치 (원격 서버 전용)"}`;
  }

  dialog.showModal();
}

function openCallDialog(prefillContact = null) {
  const dialog = byId("call-dialog");
  const select = byId("call-contact-select");
  if (!dialog || !select) return;

  select.replaceChildren();
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "직접 입력";
  select.appendChild(defaultOpt);

  for (const ct of state.contacts) {
    const opt = document.createElement("option");
    opt.value = ct.id;
    opt.textContent = `${ct.name} (${ct.phone})`;
    select.appendChild(opt);
  }

  byId("call-recipient-input").value = prefillContact?.name || "";
  byId("call-phone-input").value = prefillContact?.phone || "";
  byId("call-goal-input").value = "";
  byId("call-script-input").value = "";
  byId("call-template-select").value = "";

  dialog.showModal();
}

function bindEvents() {
  for (const btn of document.querySelectorAll("[data-tab]")) {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
  }

  byId("new-conv-btn")?.addEventListener("click", async () => {
    const res = await api.createConversation(`대화 ${state.conversations.length + 1}`);
    await loadConversations();
    if (res.conversation?.id) {
      await selectConversation(res.conversation.id);
    }
  });

  byId("open-settings-btn")?.addEventListener("click", openSettingsDialog);
  byId("open-settings-sidebar-btn")?.addEventListener("click", openSettingsDialog);
  byId("sse-status-pill")?.addEventListener("click", openSettingsDialog);
  byId("banner-open-settings-btn")?.addEventListener("click", openSettingsDialog);
  byId("banner-dismiss-btn")?.addEventListener("click", () => {
    state.bannerDismissed = true;
    hideConnectionBanner();
  });
  byId("settings-cancel-btn")?.addEventListener("click", () => byId("settings-dialog")?.close());

  byId("banner-standalone-btn")?.addEventListener("click", async () => {
    state.bannerDismissed = false;
    setConnectionMode("standalone");
    hideConnectionBanner();
    bindSSE();
    await loadAllData();
    showToast("기기 단독 모드로 전환했습니다 (Tailscale 불필요).");
  });

  byId("connection-mode-select")?.addEventListener("change", (e) => {
    const remoteFields = byId("remote-server-fields");
    if (remoteFields) {
      remoteFields.hidden = e.target.value === "standalone";
    }
  });

  for (const btn of document.querySelectorAll("[data-preset-url]")) {
    btn.addEventListener("click", async () => {
      state.bannerDismissed = false;
      setConnectionMode("remote");
      setServerBaseUrl(btn.dataset.presetUrl || "");
      bindSSE();
      await loadAllData();
      showToast(`서버 주소 전환: ${getServerBaseUrl() || "현재 origin"}`);
    });
  }

  for (const btn of document.querySelectorAll("[data-settings-preset]")) {
    btn.addEventListener("click", () => {
      const input = byId("server-url-input");
      if (input) input.value = btn.dataset.settingsPreset || "";
    });
  }

  byId("test-server-btn")?.addEventListener("click", async () => {
    const input = byId("server-url-input");
    const tokenInput = byId("server-token-input");
    const out = byId("server-test-result");
    if (!input || !out) return;
    out.textContent = "연결 확인 중...";
    const res = await api.testConnection(input.value, tokenInput?.value || "");
    if (res.ok) {
      out.textContent = `✅ 연결 성공! 호스트: ${res.data.host} (LLM: ${res.data.active_llm_provider})`;
    } else {
      out.textContent = `❌ 연결 실패: ${res.error}`;
    }
  });

  byId("settings-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const prevMode = getConnectionMode();
    const prevUrl = getServerBaseUrl();
    const prevToken = getAccessToken();

    const modeSelect = byId("connection-mode-select");
    const nextUrlInput = byId("server-url-input");
    const nextTokenInput = byId("server-token-input");

    if (modeSelect) {
      setConnectionMode(modeSelect.value);
    }
    if (nextUrlInput) {
      setServerBaseUrl(nextUrlInput.value);
    }
    if (nextTokenInput) {
      setAccessToken(nextTokenInput.value);
    }

    const payload = {};
    for (const el of form.elements) {
      if (!el.name) continue;
      if (el.type === "checkbox") {
        payload[el.name] = el.checked;
      } else if (el.type === "password") {
        if (el.value.trim()) payload[el.name] = el.value.trim();
      } else {
        payload[el.name] = el.value.trim();
      }
    }

    let savedRemote = false;
    try {
      await api.saveSettings(payload);
      savedRemote = true;
    } catch (err) {
      showToast(`서버 연결 전이라 연결 설정만 저장했습니다 (${err.message})`);
    }

    if (
      prevMode !== getConnectionMode() ||
      prevUrl !== getServerBaseUrl() ||
      prevToken !== getAccessToken()
    ) {
      state.bannerDismissed = false;
      bindSSE();
    }
    await loadAllData();
    byId("settings-dialog")?.close();
    if (savedRemote) {
      showToast("설정이 저장되었습니다.");
    }
  });

  // Chat submit
  const chatInput = byId("chat-input");
  const chatForm = byId("chat-form");
  chatInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      chatForm?.requestSubmit();
    }
  });

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    const sendBtn = byId("chat-send-btn");
    sendBtn.disabled = true;
    try {
      const res = await api.sendChat(state.activeConversationId, text);
      if (state.settings.auto_speak && res.assistant_message?.content) {
        speakKorean(res.assistant_message.content);
      }
      await Promise.all([
        loadMessages(),
        loadApprovals(),
        loadTasks(),
        loadMemories(),
        loadContacts(),
      ]);
    } catch (err) {
      showToast(`전송 오류: ${err.message}`);
    } finally {
      sendBtn.disabled = false;
      chatInput.focus();
    }
  });

  for (const chip of document.querySelectorAll(".chip-btn")) {
    chip.addEventListener("click", () => {
      if (!chatInput) return;
      chatInput.value = chip.dataset.prompt || "";
      chatInput.focus();
    });
  }

  // Voice mic
  const micBtn = byId("voice-mic-btn");
  if (!isVoiceInputSupported() && micBtn) {
    micBtn.hidden = true;
  } else if (micBtn) {
    const recognizer = createVoiceRecognizer({
      onResult(transcript) {
        chatInput.value = transcript;
        chatForm?.requestSubmit();
      },
      onStateChange(listening) {
        micBtn.textContent = listening ? "⏹️" : "🎙️";
        micBtn.setAttribute("aria-pressed", String(listening));
      },
      onError() {
        showToast("음성 인식을 사용할 수 없습니다. 마이크 권한을 확인하세요.");
      },
    });
    micBtn.addEventListener("click", () => recognizer?.toggle());
  }

  // Call composer
  byId("quick-call-btn")?.addEventListener("click", () => openCallDialog());
  byId("calls-new-btn")?.addEventListener("click", () => openCallDialog());
  byId("call-cancel-btn")?.addEventListener("click", () => byId("call-dialog")?.close());

  byId("call-contact-select")?.addEventListener("change", (e) => {
    const ct = state.contacts.find((c) => c.id === e.target.value);
    if (ct) {
      byId("call-recipient-input").value = ct.name;
      byId("call-phone-input").value = ct.phone;
    }
  });

  byId("call-template-select")?.addEventListener("change", (e) => {
    if (e.target.value) {
      byId("call-goal-input").value = e.target.value;
    }
  });

  byId("call-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.createCall({
        recipient_name: byId("call-recipient-input").value.trim(),
        phone_number: byId("call-phone-input").value.trim(),
        goal: byId("call-goal-input").value.trim(),
        script: byId("call-script-input").value.trim(),
        conversation_id: state.activeConversationId,
      });
      byId("call-dialog")?.close();
      setActiveTab("calls");
      await loadCalls();
      showToast("AI 전화를 발신했습니다.");
    } catch (err) {
      showToast(`발신 오류: ${err.message}`);
    }
  });

  // Tasks
  byId("task-quick-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const titleEl = byId("task-title-input");
    const dueEl = byId("task-due-input");
    const title = titleEl.value.trim();
    if (!title) return;
    await api.createTask({
      title,
      due_at: dueEl.value ? `${dueEl.value}:00+09:00` : "",
      kind: dueEl.value ? "reminder" : "task",
    });
    titleEl.value = "";
    dueEl.value = "";
    await loadTasks();
  });

  // Contacts & Memory modals
  byId("add-contact-btn")?.addEventListener("click", () => byId("contact-dialog")?.showModal());
  byId("contact-cancel-btn")?.addEventListener("click", () => byId("contact-dialog")?.close());
  byId("contact-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    await api.createContact({
      name: byId("contact-name-input").value.trim(),
      phone: byId("contact-phone-input").value.trim(),
      relation: byId("contact-relation-input").value.trim(),
      notes: byId("contact-notes-input").value.trim(),
    });
    e.target.reset();
    byId("contact-dialog")?.close();
    await loadContacts();
  });

  byId("add-memory-btn")?.addEventListener("click", () => byId("memory-dialog")?.showModal());
  byId("memory-cancel-btn")?.addEventListener("click", () => byId("memory-dialog")?.close());
  byId("memory-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    await api.createMemory({
      category: byId("memory-category-input").value,
      content: byId("memory-content-input").value.trim(),
      tags: byId("memory-tags-input").value.trim(),
    });
    e.target.reset();
    byId("memory-dialog")?.close();
    await loadMemories();
  });

  // Sandbox browser controls
  byId("browser-refresh-btn")?.addEventListener("click", async () => {
    const res = await api.browserScreenshot();
    state.browser = res;
    renderBrowserState();
  });

  byId("browser-nav-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = byId("browser-url-input").value.trim();
    const res = await api.browserNavigate(url);
    if (!res.ok && res.error) showToast(res.error);
    state.browser = res;
    renderBrowserState();
  });

  byId("browser-type-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = byId("browser-type-input");
    const res = await api.browserType(input.value, true);
    if (!res.ok && res.error) showToast(res.error);
    input.value = "";
    state.browser = res;
    renderBrowserState();
  });

  byId("browser-screenshot-img")?.addEventListener("click", async (e) => {
    const img = e.currentTarget;
    const rect = img.getBoundingClientRect();
    const scaleX = (img.naturalWidth || 1280) / rect.width;
    const scaleY = (img.naturalHeight || 800) / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    const res = await api.browserClickPoint(x, y);
    state.browser = res;
    renderBrowserState();
  });
}

function bindSSE() {
  if (state.sseSource) {
    try {
      state.sseSource.close();
    } catch {
      // ignore
    }
    state.sseSource = null;
  }
  const pill = byId("sse-status-pill");
  state.sseSource = connectEvents({
    onOpen() {
      if (pill) {
        pill.textContent =
          getConnectionMode() === "standalone" ? "기기 단독 모드" : "실시간 동기화 중";
      }
      hideConnectionBanner();
    },
    onError() {
      if (pill) pill.textContent = "재연결 중...";
    },
    message_created(data) {
      if (data.conversation_id === state.activeConversationId && data.message) {
        if (!state.messages.some((m) => m.id === data.message.id)) {
          state.messages.push(data.message);
          renderMessages();
        }
      }
    },
    agent_status(data) {
      const box = byId("agent-thinking");
      if (!box) return;
      if (data.status && data.conversation_id === state.activeConversationId) {
        box.hidden = false;
        box.textContent = `⏳ ${data.status}`;
      } else {
        box.hidden = true;
      }
    },
    approval_created() {
      loadApprovals();
    },
    approval_updated() {
      loadApprovals();
    },
    call_updated(data) {
      if (!data.call) {
        loadCalls();
        return;
      }
      const prev = state.calls.find((c) => c.id === data.call.id);
      if (
        prev?.status !== data.call.status &&
        data.call.status === "completed" &&
        window.brunoNative?.notify
      ) {
        const who = data.call.recipient_name || data.call.phone_number;
        window.brunoNative.notify("AI 통화 완료", `${who}: ${data.call.summary || "통화가 끝났습니다."}`);
      }
      const idx = state.calls.findIndex((c) => c.id === data.call.id);
      if (idx >= 0) {
        state.calls[idx] = data.call;
      } else {
        state.calls.unshift(data.call);
      }
      renderCalls();
    },
    task_updated() {
      loadTasks();
    },
    reminder_due(data) {
      if (data.task?.title) {
        showToast(`⏰ 알림: ${data.task.title}`);
        if (window.brunoNative?.notify) {
          window.brunoNative.notify("Bruno AI 알림", data.task.title);
        }
      }
      loadTasks();
    },
    memory_updated() {
      loadMemories();
    },
    contact_updated() {
      loadContacts();
    },
    browser_updated(data) {
      if (data.browser) {
        state.browser = data.browser;
        renderBrowserState();
      }
    },
  });
}

async function init() {
  setupDialogs();
  bindEvents();
  bindSSE();

  if (!isNativeApp() && "serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  await loadAllData();
}

init();
