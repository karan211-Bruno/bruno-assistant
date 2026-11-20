/**
 * REST + SSE client for Bruno AI backend with dynamic server support for native apps.
 */

export const DEFAULT_REMOTE_URL = "https://bruno-sony.tail617e7a.ts.net";
const SERVER_STORAGE_KEY = "bruno_server_url";

export function isNativeApp() {
  return Boolean(
    window.brunoNative?.isNative ||
      window.Capacitor?.isNativePlatform?.() ||
      window.location.protocol === "file:" ||
      window.location.protocol === "capacitor:"
  );
}

export function normalizeServerUrl(url) {
  const trimmed = String(url || "").trim();
  if (!trimmed) return "";
  return trimmed.replace(/\/+$/, "");
}

export function getServerBaseUrl() {
  try {
    const saved = localStorage.getItem(SERVER_STORAGE_KEY);
    if (saved !== null && saved.trim() !== "") {
      return normalizeServerUrl(saved);
    }
  } catch {
    // ignore storage access issues
  }
  if (isNativeApp()) {
    return normalizeServerUrl(window.brunoNative?.defaultServerUrl || DEFAULT_REMOTE_URL);
  }
  return "";
}

export function setServerBaseUrl(url) {
  const clean = normalizeServerUrl(url);
  try {
    if (clean) {
      localStorage.setItem(SERVER_STORAGE_KEY, clean);
    } else {
      localStorage.removeItem(SERVER_STORAGE_KEY);
    }
  } catch {
    // ignore storage errors
  }
  return getServerBaseUrl();
}

export function apiUrl(path) {
  const p = String(path || "");
  if (!p) return getServerBaseUrl();
  if (/^https?:\/\//i.test(p)) return p;
  const base = getServerBaseUrl();
  if (!base) return p;
  return base + (p.startsWith("/") ? p : `/${p}`);
}

async function request(path, options = {}) {
  const res = await fetch(apiUrl(path), {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const errData = await res.json();
      detail = errData.detail || errData.error || detail;
    } catch {
      // ignore json parse failure
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => request("/api/health"),
  async testConnection(baseUrl) {
    const clean = normalizeServerUrl(baseUrl);
    const target = clean ? `${clean}/api/health` : "/api/health";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const res = await fetch(target, { signal: controller.signal });
      if (!res.ok) {
        return { ok: false, error: `HTTP ${res.status}` };
      }
      const data = await res.json();
      return { ok: Boolean(data?.ok), data };
    } catch (err) {
      return {
        ok: false,
        error: err?.name === "AbortError" ? "연결 시간 초과 (5초)" : (err?.message || "연결 실패"),
      };
    } finally {
      clearTimeout(timer);
    }
  },
  getSettings: () => request("/api/settings"),
  saveSettings: (payload) =>
    request("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),

  listConversations: () => request("/api/conversations"),
  createConversation: (title) =>
    request("/api/conversations", { method: "POST", body: JSON.stringify({ title }) }),
  deleteConversation: (id) =>
    request(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),

  getMessages: (convId) => request(`/api/conversations/${encodeURIComponent(convId)}/messages`),
  sendChat: (conversationId, message) =>
    request("/api/chat", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, message }),
    }),

  listApprovals: () => request("/api/approvals"),
  decideApproval: (id, decision, note = "") =>
    request(`/api/approvals/${encodeURIComponent(id)}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision, note }),
    }),

  listCalls: () => request("/api/calls"),
  createCall: (payload) =>
    request("/api/calls", { method: "POST", body: JSON.stringify(payload) }),

  listMemories: () => request("/api/memories"),
  createMemory: (payload) =>
    request("/api/memories", { method: "POST", body: JSON.stringify(payload) }),
  deleteMemory: (id) => request(`/api/memories/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listContacts: () => request("/api/contacts"),
  createContact: (payload) =>
    request("/api/contacts", { method: "POST", body: JSON.stringify(payload) }),
  deleteContact: (id) => request(`/api/contacts/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listTasks: () => request("/api/tasks"),
  createTask: (payload) =>
    request("/api/tasks", { method: "POST", body: JSON.stringify({ ...payload }) }),
  patchTask: (id, payload) =>
    request(`/api/tasks/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteTask: (id) => request(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),

  browserState: () => request("/api/browser/state"),
  browserNavigate: (url) =>
    request("/api/browser/navigate", { method: "POST", body: JSON.stringify({ url }) }),
  browserClickPoint: (x, y) =>
    request("/api/browser/click", { method: "POST", body: JSON.stringify({ x, y }) }),
  browserType: (text, pressEnter = false) =>
    request("/api/browser/type", {
      method: "POST",
      body: JSON.stringify({ selector: "", text, press_enter: pressEnter }),
    }),
  browserScreenshot: () => request("/api/browser/screenshot", { method: "POST", body: "{}" }),
};

export function connectEvents(handlers) {
  const source = new EventSource(apiUrl("/api/events"));
  const eventNames = [
    "connected",
    "message_created",
    "agent_status",
    "approval_created",
    "approval_updated",
    "call_updated",
    "task_updated",
    "reminder_due",
    "memory_updated",
    "contact_updated",
    "browser_updated",
  ];
  for (const name of eventNames) {
    source.addEventListener(name, (evt) => {
      if (!handlers[name]) return;
      try {
        const parsed = evt.data ? JSON.parse(evt.data) : {};
        handlers[name](parsed);
      } catch {
        handlers[name]({});
      }
    });
  }
  source.onopen = () => handlers.onOpen?.();
  source.onerror = () => handlers.onError?.();
  return source;
}
