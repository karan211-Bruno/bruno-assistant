/**
 * Dual-mode API client:
 * - "standalone": Runs 100% on-device via static/js/standalone.js (no Tailscale/server required)
 * - "remote": Syncs with FastAPI backend over HTTPS / Cloudflare Tunnel / Render / LAN / Tailscale
 */

import { createStandaloneBackend } from "./standalone.js";

export const DEFAULT_REMOTE_URL = "https://bruno-sony.tail617e7a.ts.net";
const SERVER_STORAGE_KEY = "bruno_server_url";
const MODE_STORAGE_KEY = "bruno_connection_mode";
const TOKEN_STORAGE_KEY = "bruno_access_token";

const standalone = createStandaloneBackend();

export function isNativeApp() {
  return Boolean(
    window.brunoNative?.isNative ||
      window.Capacitor?.isNativePlatform?.() ||
      window.location.protocol === "app:" ||
      window.location.protocol === "file:" ||
      window.location.protocol === "capacitor:"
  );
}

export function getConnectionMode() {
  try {
    const saved = localStorage.getItem(MODE_STORAGE_KEY);
    if (saved === "standalone" || saved === "remote") {
      return saved;
    }
  } catch {
    // ignore storage errors
  }
  return isNativeApp() ? "standalone" : "remote";
}

export function setConnectionMode(mode) {
  const normalized = mode === "remote" ? "remote" : "standalone";
  try {
    localStorage.setItem(MODE_STORAGE_KEY, normalized);
  } catch {
    // ignore storage errors
  }
  return normalized;
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

export function getAccessToken() {
  try {
    return String(localStorage.getItem(TOKEN_STORAGE_KEY) || "").trim();
  } catch {
    return "";
  }
}

export function setAccessToken(token) {
  const clean = String(token || "").trim();
  try {
    if (clean) {
      localStorage.setItem(TOKEN_STORAGE_KEY, clean);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // ignore storage errors
  }
  return clean;
}

export function apiUrl(path) {
  const p = String(path || "");
  if (!p) return getServerBaseUrl();
  if (/^https?:\/\//i.test(p)) return p;
  const base = getServerBaseUrl();
  let full = base ? base + (p.startsWith("/") ? p : `/${p}`) : p;
  const token = getAccessToken();
  if (token && p.startsWith("/api/browser/screenshot/")) {
    full += (full.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
  }
  return full;
}

async function request(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  const token = getAccessToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(apiUrl(path), {
    ...options,
    headers,
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
  health: () => (getConnectionMode() === "standalone" ? standalone.health() : request("/api/health")),
  async testConnection(baseUrl, tokenOverride = getAccessToken()) {
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
      if (data?.auth_required) {
        const tok = String(tokenOverride || "").trim();
        const checkHeaders = tok ? { Authorization: `Bearer ${tok}` } : {};
        const authRes = await fetch(clean ? `${clean}/api/settings` : "/api/settings", {
          headers: checkHeaders,
          signal: controller.signal,
        });
        if (!authRes.ok) {
          return {
            ok: false,
            error: authRes.status === 401 ? "접근 토큰이 없거나 일치하지 않습니다 (401)" : `HTTP ${authRes.status}`,
          };
        }
      }
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
  getSettings: () =>
    getConnectionMode() === "standalone" ? standalone.getSettings() : request("/api/settings"),
  saveSettings: (payload) =>
    getConnectionMode() === "standalone"
      ? standalone.saveSettings(payload)
      : request("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),

  listConversations: () =>
    getConnectionMode() === "standalone"
      ? standalone.listConversations()
      : request("/api/conversations"),
  createConversation: (title) =>
    getConnectionMode() === "standalone"
      ? standalone.createConversation(title)
      : request("/api/conversations", { method: "POST", body: JSON.stringify({ title }) }),
  deleteConversation: (id) =>
    getConnectionMode() === "standalone"
      ? standalone.deleteConversation(id)
      : request(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),

  getMessages: (convId) =>
    getConnectionMode() === "standalone"
      ? standalone.getMessages(convId)
      : request(`/api/conversations/${encodeURIComponent(convId)}/messages`),
  sendChat: (conversationId, message) =>
    getConnectionMode() === "standalone"
      ? standalone.sendChat(conversationId, message)
      : request("/api/chat", {
          method: "POST",
          body: JSON.stringify({ conversation_id: conversationId, message }),
        }),

  listApprovals: () =>
    getConnectionMode() === "standalone" ? standalone.listApprovals() : request("/api/approvals"),
  decideApproval: (id, decision, note = "") =>
    getConnectionMode() === "standalone"
      ? standalone.decideApproval(id, decision, note)
      : request(`/api/approvals/${encodeURIComponent(id)}/decide`, {
          method: "POST",
          body: JSON.stringify({ decision, note }),
        }),

  listCalls: () =>
    getConnectionMode() === "standalone" ? standalone.listCalls() : request("/api/calls"),
  createCall: (payload) =>
    getConnectionMode() === "standalone"
      ? standalone.createCall(payload)
      : request("/api/calls", { method: "POST", body: JSON.stringify(payload) }),

  listMemories: () =>
    getConnectionMode() === "standalone" ? standalone.listMemories() : request("/api/memories"),
  createMemory: (payload) =>
    getConnectionMode() === "standalone"
      ? standalone.createMemory(payload)
      : request("/api/memories", { method: "POST", body: JSON.stringify(payload) }),
  deleteMemory: (id) =>
    getConnectionMode() === "standalone"
      ? standalone.deleteMemory(id)
      : request(`/api/memories/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listContacts: () =>
    getConnectionMode() === "standalone" ? standalone.listContacts() : request("/api/contacts"),
  createContact: (payload) =>
    getConnectionMode() === "standalone"
      ? standalone.createContact(payload)
      : request("/api/contacts", { method: "POST", body: JSON.stringify(payload) }),
  deleteContact: (id) =>
    getConnectionMode() === "standalone"
      ? standalone.deleteContact(id)
      : request(`/api/contacts/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listTasks: () =>
    getConnectionMode() === "standalone" ? standalone.listTasks() : request("/api/tasks"),
  createTask: (payload) =>
    getConnectionMode() === "standalone"
      ? standalone.createTask(payload)
      : request("/api/tasks", { method: "POST", body: JSON.stringify({ ...payload }) }),
  patchTask: (id, payload) =>
    getConnectionMode() === "standalone"
      ? standalone.patchTask(id, payload)
      : request(`/api/tasks/${encodeURIComponent(id)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        }),
  deleteTask: (id) =>
    getConnectionMode() === "standalone"
      ? standalone.deleteTask(id)
      : request(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),

  browserState: () =>
    getConnectionMode() === "standalone" ? standalone.browserState() : request("/api/browser/state"),
  browserNavigate: (url) =>
    getConnectionMode() === "standalone"
      ? Promise.resolve({ ok: false, available: false, error: "원격 서버 모드에서 사용할 수 있습니다." })
      : request("/api/browser/navigate", { method: "POST", body: JSON.stringify({ url }) }),
  browserClickPoint: (x, y) =>
    getConnectionMode() === "standalone"
      ? Promise.resolve({ ok: false, available: false })
      : request("/api/browser/click", { method: "POST", body: JSON.stringify({ x, y }) }),
  browserType: (text, pressEnter = false) =>
    getConnectionMode() === "standalone"
      ? Promise.resolve({ ok: false, available: false })
      : request("/api/browser/type", {
          method: "POST",
          body: JSON.stringify({ selector: "", text, press_enter: pressEnter }),
        }),
  browserScreenshot: () =>
    getConnectionMode() === "standalone"
      ? Promise.resolve({ ok: false, available: false })
      : request("/api/browser/screenshot", { method: "POST", body: "{}" }),
};

export function connectEvents(handlers) {
  if (getConnectionMode() === "standalone") {
    return standalone.connectEvents(handlers);
  }

  const token = getAccessToken();
  const baseEvents = apiUrl("/api/events");
  const eventsUrl = token
    ? baseEvents + (baseEvents.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token)
    : baseEvents;

  const source = new EventSource(eventsUrl);
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
