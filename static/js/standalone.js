/**
 * On-device standalone Bruno AI backend (zero Tailscale / zero server required).
 * Persists state in localStorage and performs LLM, web search, and Vapi/Retell calls
 * directly from the device using Electron IPC / CapacitorHttp / fetch.
 */

const STORE_KEY = "bruno_local_store_v1";

const SECRET_KEYS = new Set([
  "gemini_api_key",
  "openai_api_key",
  "anthropic_api_key",
  "vapi_api_key",
  "retell_api_key",
  "twilio_auth_token",
]);

const BOOL_KEYS = new Set(["require_call_approval", "auto_speak"]);

const DEFAULT_SETTINGS = {
  llm_provider: "auto",
  gemini_api_key: "",
  gemini_model: "gemini-2.5-flash",
  openai_api_key: "",
  openai_model: "gpt-4.1-mini",
  anthropic_api_key: "",
  anthropic_model: "claude-sonnet-4-20250514",
  ollama_base_url: "http://127.0.0.1:11434",
  ollama_model: "qwen2.5:7b",
  telephony_provider: "simulation",
  vapi_api_key: "",
  vapi_phone_number_id: "",
  retell_api_key: "",
  retell_from_number: "",
  retell_agent_id: "",
  twilio_account_sid: "",
  twilio_auth_token: "",
  twilio_from_number: "",
  public_base_url: "",
  user_name: "브루노",
  user_phone: "",
  user_timezone: "Asia/Seoul",
  require_call_approval: true,
  auto_speak: false,
};

const TOOL_SPECS = [
  {
    name: "remember_fact",
    description: "사용자의 취향, 자주 가는 곳, 중요한 개인 정보를 장기 메모리 버블에 저장합니다.",
    parameters: {
      type: "object",
      properties: {
        category: { type: "string" },
        content: { type: "string" },
        tags: { type: "string" },
      },
      required: ["content"],
    },
  },
  {
    name: "search_memories",
    description: "저장된 장기 메모리에서 키워드로 기억을 검색합니다.",
    parameters: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
    },
  },
  {
    name: "save_contact",
    description: "이름과 전화번호를 연락처에 저장합니다.",
    parameters: {
      type: "object",
      properties: {
        name: { type: "string" },
        phone: { type: "string" },
        relation: { type: "string" },
        notes: { type: "string" },
      },
      required: ["name", "phone"],
    },
  },
  {
    name: "find_contact",
    description: "이름, 관계, 번호로 연락처를 조회합니다.",
    parameters: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
    },
  },
  {
    name: "create_task",
    description: "할 일 또는 지정된 시간의 알림(리마인더)을 생성합니다.",
    parameters: {
      type: "object",
      properties: {
        title: { type: "string" },
        due_at: { type: "string" },
        kind: { type: "string" },
        notes: { type: "string" },
      },
      required: ["title"],
    },
  },
  {
    name: "list_tasks",
    description: "등록된 할 일과 알림 목록을 조회합니다.",
    parameters: {
      type: "object",
      properties: { status: { type: "string" } },
    },
  },
  {
    name: "complete_task",
    description: "할 일을 완료 상태로 변경합니다.",
    parameters: {
      type: "object",
      properties: { task_id: { type: "string" } },
      required: ["task_id"],
    },
  },
  {
    name: "web_search",
    description: "인터넷에서 최신 정보, 가게 전화번호, 영업시간, 뉴스 등을 검색합니다.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string" },
        max_results: { type: "integer" },
      },
      required: ["query"],
    },
  },
  {
    name: "make_phone_call",
    description: "상대방이나 매장에 AI가 대신 전화를 걸어 예약, 문의, 확인을 처리합니다.",
    parameters: {
      type: "object",
      properties: {
        recipient_name: { type: "string" },
        phone_number: { type: "string" },
        goal: { type: "string" },
        script: { type: "string" },
      },
      required: ["goal"],
    },
  },
];

const TOOL_STATUS_LABELS = {
  remember_fact: "기억 버블 저장 중...",
  search_memories: "기억 검색 중...",
  save_contact: "연락처 저장 중...",
  find_contact: "연락처 조회 중...",
  create_task: "일정/알림 등록 중...",
  list_tasks: "할 일 목록 확인 중...",
  complete_task: "할 일 완료 처리 중...",
  web_search: "웹 검색 중...",
  make_phone_call: "전화 발신 준비 중...",
};

function nowIso() {
  return new Date().toISOString();
}

function newId(prefix = "id") {
  const rand = Math.random().toString(16).slice(2, 10);
  const ts = Date.now().toString(36).slice(-4);
  return `${prefix}_${ts}${rand}`;
}

function maskSecret(val) {
  const s = String(val || "").trim();
  if (!s) return "";
  if (s.length <= 4) return "****";
  return "****" + s.slice(-4);
}

export function normalizeKrPhone(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  const hasPlus = text.startsWith("+");
  const digits = text.replace(/\D/g, "");
  if (!digits) return text;
  if (hasPlus) return `+${digits}`;
  if (digits.startsWith("00")) return `+${digits.slice(2)}`;
  if (digits.startsWith("0") && digits.length >= 9) return `+82${digits.slice(1)}`;
  if (digits.startsWith("82") && digits.length >= 10) return `+${digits}`;
  return digits;
}

export async function defaultCorsFreeFetch(url, opts = {}) {
  const method = String(opts.method || "GET").toUpperCase();
  const headers = opts.headers || {};
  const body = opts.body;

  if (typeof window !== "undefined" && typeof window.brunoNative?.httpRequest === "function") {
    return window.brunoNative.httpRequest({ url, method, headers, body });
  }

  const capHttp =
    typeof window !== "undefined" &&
    window.Capacitor?.isNativePlatform?.() &&
    window.Capacitor?.Plugins?.CapacitorHttp;
  if (capHttp && typeof capHttp.request === "function") {
    let parsedData = body;
    if (typeof body === "string" && headers["Content-Type"]?.includes("application/json")) {
      try {
        parsedData = JSON.parse(body);
      } catch {
        parsedData = body;
      }
    }
    const res = await capHttp.request({
      url,
      method,
      headers,
      data: parsedData,
      connectTimeout: 20000,
      readTimeout: 45000,
    });
    const text =
      typeof res.data === "string" ? res.data : JSON.stringify(res.data ?? {});
    return {
      ok: res.status >= 200 && res.status < 300,
      status: res.status,
      text,
    };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  try {
    const resp = await globalThis.fetch(url, {
      method,
      headers,
      body,
      signal: controller.signal,
    });
    const text = await resp.text();
    return { ok: resp.ok, status: resp.status, text };
  } finally {
    clearTimeout(timer);
  }
}

function decodeHtmlEntities(str) {
  return String(str || "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseDuckDuckGoHtml(html, limit = 5) {
  const results = [];
  const linkRe = /<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = linkRe.exec(html)) !== null && results.length < limit) {
    let href = match[1] || "";
    if (href.startsWith("//")) href = "https:" + href;
    const uddg = /[?&]uddg=([^&]+)/.exec(href);
    if (uddg) {
      try {
        href = decodeURIComponent(uddg[1]);
      } catch {
        // keep raw href
      }
    }
    const title = decodeHtmlEntities(match[2]);
    const tail = html.slice(match.index, match.index + 1200);
    const snipMatch = /class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/(?:a|div|span)>/i.exec(tail);
    const snippet = snipMatch ? decodeHtmlEntities(snipMatch[1]) : "";
    if (title && href) {
      results.push({ title, url: href, snippet });
    }
  }
  return results;
}

function extractPhone(text) {
  const m = /(\+?\d{2,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4})/.exec(text);
  return m ? m[1].trim() : "";
}

function parseKoreanDue(text) {
  const now = new Date();
  const relMin = /(\d+)\s*분\s*(?:뒤|후)/.exec(text);
  if (relMin) {
    return new Date(now.getTime() + Number(relMin[1]) * 60000).toISOString();
  }
  const relHr = /(\d+)\s*시간\s*(?:뒤|후)/.exec(text);
  if (relHr) {
    return new Date(now.getTime() + Number(relHr[1]) * 3600000).toISOString();
  }
  const ampm = /(오전|오후)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?/.exec(text);
  if (ampm) {
    let hour = Number(ampm[2]);
    const minute = Number(ampm[3] || 0);
    if (ampm[1] === "오후" && hour < 12) hour += 12;
    else if (ampm[1] === "오전" && hour === 12) hour = 0;
    if (hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59) {
      const target = new Date(now);
      target.setHours(hour, minute, 0, 0);
      if (text.includes("내일") || target < now) {
        target.setDate(target.getDate() + 1);
      }
      return target.toISOString();
    }
  }
  return "";
}

function guessRecipient(text, phone) {
  const cleaned = text.replace(phone, " ").trim();
  const m = /([가-힣A-Za-z0-9_]{1,20}?)(?:에게|한테|에|으로|로)\s/.exec(cleaned);
  if (m) {
    const c = m[1].trim();
    if (!["전화", "통화", "예약", "내일", "오늘", "지금", "나"].includes(c)) {
      return c;
    }
  }
  return "";
}

export function createStandaloneBackend(
  storage = typeof globalThis.localStorage !== "undefined" ? globalThis.localStorage : null,
  httpFetch = defaultCorsFreeFetch,
  options = {}
) {
  const memFallback = new Map();
  const simDelayMs = typeof options.simDelayMs === "number" ? options.simDelayMs : 650;
  const listeners = new Set();
  let schedulerTimer = null;

  function readRaw(key) {
    if (storage && typeof storage.getItem === "function") {
      return storage.getItem(key);
    }
    return memFallback.get(key) ?? null;
  }

  function writeRaw(key, val) {
    if (storage && typeof storage.setItem === "function") {
      storage.setItem(key, val);
    } else {
      memFallback.set(key, val);
    }
  }

  function createSeedStore() {
    const ts = nowIso();
    const welcome =
      "안녕하세요! **Bruno AI** (기기 단독 모드)입니다.\n\n" +
      "지금 **Tailscale이나 외부 서버 없이도** 이 기기에서 바로 동작합니다:\n" +
      "- **AI 전화 대행:** 예) \"단골식당에 02-123-4567 오늘 저녁 7시 2명 예약 전화해줘\"\n" +
      "- **기억 & 연락처:** 예) \"엄마 010-1234-5678 연락처에 저장해줘\"\n" +
      "- **일정·할 일 알림:** 예) \"10분 뒤 약 먹기 알림 등록해줘\"\n" +
      "- **웹 검색:** 예) \"강남역 조용한 카페 검색해줘\"\n\n" +
      "우측 상단 **⚙️ 설정**에서 Gemini / OpenAI / Claude API 키나 Vapi · Retell 키를 넣으면 실전 AI 대화와 실제 전화 발신까지 단독으로 실행됩니다. (여러 기기를 동기화할 때만 원격 서버 모드를 켜면 됩니다.)";
    return {
      settings: { ...DEFAULT_SETTINGS },
      conversations: [{ id: "default", title: "메인 비서", created_at: ts, updated_at: ts }],
      messages: [
        {
          id: newId("msg"),
          conversation_id: "default",
          role: "assistant",
          content: welcome,
          meta: { provider: "system" },
          created_at: ts,
        },
      ],
      memories: [],
      contacts: [],
      tasks: [],
      calls: [],
      approvals: [],
    };
  }

  function loadStore() {
    const raw = readRaw(STORE_KEY);
    if (!raw) {
      const seed = createSeedStore();
      writeRaw(STORE_KEY, JSON.stringify(seed));
      return seed;
    }
    try {
      const parsed = JSON.parse(raw);
      return {
        ...createSeedStore(),
        ...parsed,
        settings: { ...DEFAULT_SETTINGS, ...(parsed.settings || {}) },
      };
    } catch {
      const seed = createSeedStore();
      writeRaw(STORE_KEY, JSON.stringify(seed));
      return seed;
    }
  }

  function saveStore(store) {
    writeRaw(STORE_KEY, JSON.stringify(store));
  }

  function publish(eventType, data = {}) {
    for (const h of Array.from(listeners)) {
      try {
        h[eventType]?.(data);
      } catch {
        // ignore listener errors
      }
    }
  }

  function getSettings(includeSecrets = false) {
    const store = loadStore();
    const s = { ...DEFAULT_SETTINGS, ...store.settings };
    if (includeSecrets) return s;
    const masked = {};
    for (const [k, v] of Object.entries(s)) {
      if (SECRET_KEYS.has(k)) {
        masked[k] = maskSecret(v);
        masked[`has_${k}`] = Boolean(String(v || "").trim());
      } else {
        masked[k] = v;
      }
    }
    return masked;
  }

  function saveSettings(updates = {}) {
    const store = loadStore();
    for (const sk of SECRET_KEYS) {
      if (updates[`clear_${sk}`] === true) {
        store.settings[sk] = "";
      }
    }
    for (const [k, v] of Object.entries(updates)) {
      if (!(k in DEFAULT_SETTINGS)) continue;
      if (SECRET_KEYS.has(k)) {
        if (v === null || v === undefined) continue;
        const sv = String(v).trim();
        if (!sv || sv.startsWith("****")) continue;
        store.settings[k] = sv;
      } else if (BOOL_KEYS.has(k)) {
        store.settings[k] = Boolean(v);
      } else {
        store.settings[k] = v === null || v === undefined ? "" : String(v).trim();
      }
    }
    saveStore(store);
    return getSettings(false);
  }

  function resolveActiveProvider(settings) {
    const cfg = String(settings.llm_provider || "auto").trim().toLowerCase();
    if (cfg !== "auto") return cfg;
    if (String(settings.gemini_api_key || "").trim()) return "gemini";
    if (String(settings.openai_api_key || "").trim()) return "openai";
    if (String(settings.anthropic_api_key || "").trim()) return "anthropic";
    return "demo";
  }

  function getDefaultConversationId(store) {
    if (!store.conversations.length) {
      const ts = nowIso();
      store.conversations.push({ id: "default", title: "메인 비서", created_at: ts, updated_at: ts });
    }
    return store.conversations[0].id;
  }

  function appendMessage(conversationId, role, content, meta = {}) {
    const store = loadStore();
    const ts = nowIso();
    let conv = store.conversations.find((c) => c.id === conversationId);
    if (!conv) {
      conv = { id: conversationId, title: "새 대화", created_at: ts, updated_at: ts };
      store.conversations.unshift(conv);
    } else {
      conv.updated_at = ts;
    }
    const msg = {
      id: newId("msg"),
      conversation_id: conversationId,
      role,
      content,
      meta,
      created_at: ts,
    };
    store.messages.push(msg);
    saveStore(store);
    publish("message_created", { conversation_id: conversationId, message: msg });
    return msg;
  }

  function postCallResultToChat(call) {
    const store = loadStore();
    const convId = call.conversation_id || getDefaultConversationId(store);
    const target = call.recipient_name || call.phone_number;
    const label = call.status === "completed" ? "통화 완료" : "통화 실패";
    const prefix = call.provider === "simulation" ? "[시뮬레이션] " : "";
    const body = `${prefix}**[${label}] ${target}** (\`${call.phone_number}\`)\n- 목적: ${call.goal}\n- 결과: ${call.summary || "요약 없음"}`;
    appendMessage(convId, "assistant", body, { call_id: call.id, provider: call.provider });
  }

  function updateCall(callId, patch = {}) {
    const store = loadStore();
    const idx = store.calls.findIndex((c) => c.id === callId);
    if (idx < 0) return null;
    const prevStatus = store.calls[idx].status;
    const updated = {
      ...store.calls[idx],
      ...patch,
      updated_at: nowIso(),
    };
    store.calls[idx] = updated;
    saveStore(store);
    publish("call_updated", { call: updated });
    if (prevStatus !== updated.status && (updated.status === "completed" || updated.status === "failed")) {
      postCallResultToChat(updated);
    }
    return updated;
  }

  function appendCallTranscript(callId, speaker, text) {
    const store = loadStore();
    const call = store.calls.find((c) => c.id === callId);
    if (!call || !String(text || "").trim()) return call || null;
    const transcript = [...(call.transcript || []), { speaker, text: String(text).trim(), at: nowIso() }];
    return updateCall(callId, { transcript });
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function runSimulationCall(callId, userName) {
    const store = loadStore();
    const call = store.calls.find((c) => c.id === callId);
    if (!call) return;
    const recipient = call.recipient_name || call.phone_number || "상대방";
    const goal = call.goal || "일정 확인";
    const script = call.script || "";

    updateCall(callId, { status: "dialing" });
    await sleep(simDelayMs);

    updateCall(callId, { status: "ringing" });
    await sleep(simDelayMs);

    updateCall(callId, { status: "in_progress" });
    const turns = [
      ["AI 비서", `[시뮬레이션] 안녕하세요, ${userName}님의 AI 비서입니다. ${recipient} 맞으실까요?`],
      [recipient, "[시뮬레이션] 네, 안녕하세요. 무슨 일이시죠?"],
      [
        "AI 비서",
        `[시뮬레이션] ${goal} 관련해서 확인차 연락드렸습니다.` + (script ? ` (${script})` : ""),
      ],
      [recipient, `[시뮬레이션] 네, 확인했습니다. '${goal}' 요청하신 대로 접수해 드릴게요.`],
      ["AI 비서", "[시뮬레이션] 감사합니다. 좋은 하루 보내세요!"],
    ];
    for (const [speaker, text] of turns) {
      await sleep(Math.max(10, Math.round(simDelayMs * 0.55)));
      appendCallTranscript(callId, speaker, text);
    }
    await sleep(Math.max(10, Math.round(simDelayMs * 0.3)));
    updateCall(callId, {
      status: "completed",
      summary: `[시뮬레이션] ${recipient} 측과 통화가 완료되어 '${goal}' 건이 확인·접수되었습니다. (실제 발신은 설정에서 Vapi 또는 Retell 키를 입력하면 동작합니다.)`,
    });
  }

  function mapExternalCallStatus(raw) {
    const low = String(raw || "").trim().toLowerCase();
    if (["queued", "scheduled", "initiated"].includes(low)) return "dialing";
    if (low === "ringing") return "ringing";
    if (["in-progress", "in_progress", "ongoing", "answered", "forwarding"].includes(low)) {
      return "in_progress";
    }
    if (["ended", "completed", "registered"].includes(low)) return "completed";
    if (["failed", "error", "busy", "no-answer", "canceled", "cancelled"].includes(low)) {
      return "failed";
    }
    return "in_progress";
  }

  async function pollSingleExternalCall(callId) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await sleep(5000);
      const store = loadStore();
      const call = store.calls.find((c) => c.id === callId);
      if (!call || ["completed", "failed"].includes(call.status) || !call.provider_call_id) {
        return;
      }
      const settings = getSettings(true);
      try {
        if (call.provider === "vapi" && settings.vapi_api_key) {
          const resp = await httpFetch(`https://api.vapi.ai/call/${call.provider_call_id}`, {
            method: "GET",
            headers: { Authorization: `Bearer ${settings.vapi_api_key}` },
          });
          if (!resp.ok) continue;
          const data = JSON.parse(resp.text || "{}");
          const mapped = mapExternalCallStatus(data.status);
          const msgs = data.messages || data.artifact?.messages || [];
          const transcript = msgs
            .filter((m) => m && ["assistant", "bot", "user"].includes(m.role) && (m.message || m.content))
            .map((m) => ({
              speaker: ["assistant", "bot"].includes(m.role) ? "AI 비서" : "상대방",
              text: String(m.message || m.content),
              at: nowIso(),
            }));
          const summary = data.summary || data.analysis?.summary || call.summary;
          updateCall(callId, {
            status: mapped,
            ...(transcript.length ? { transcript } : {}),
            ...(summary ? { summary } : {}),
          });
        } else if (call.provider === "retell" && settings.retell_api_key) {
          const resp = await httpFetch(`https://api.retellai.com/v2/get-call/${call.provider_call_id}`, {
            method: "GET",
            headers: { Authorization: `Bearer ${settings.retell_api_key}` },
          });
          if (!resp.ok) continue;
          const data = JSON.parse(resp.text || "{}");
          const mapped = mapExternalCallStatus(data.call_status);
          const transcript = (data.transcript_object || [])
            .filter((t) => t && t.content)
            .map((t) => ({
              speaker: t.role === "agent" ? "AI 비서" : "상대방",
              text: String(t.content),
              at: nowIso(),
            }));
          const summary = data.call_analysis?.call_summary || call.summary;
          updateCall(callId, {
            status: mapped,
            ...(transcript.length ? { transcript } : {}),
            ...(summary ? { summary } : {}),
          });
        }
      } catch {
        // continue polling
      }
    }
  }

  async function placeCall(callId) {
    const store = loadStore();
    const call = store.calls.find((c) => c.id === callId);
    if (!call) throw new Error(`Call not found: ${callId}`);

    const settings = getSettings(true);
    const provider = call.provider || settings.telephony_provider || "simulation";
    const userName = settings.user_name || "사용자";
    const recipient = call.recipient_name || call.phone_number || "상대방";
    const toNumber = normalizeKrPhone(call.phone_number);

    if (provider === "simulation") {
      void runSimulationCall(callId, userName);
      return loadStore().calls.find((c) => c.id === callId);
    }

    if (provider === "vapi") {
      if (!settings.vapi_api_key || !settings.vapi_phone_number_id) {
        return updateCall(callId, {
          status: "failed",
          summary: "Vapi API 키 또는 Phone Number ID가 설정되어 있지 않습니다.",
        });
      }
      const sysPrompt =
        `당신은 ${userName}님의 개인 AI 비서입니다. 상대방(${recipient})에게 정중하고 자연스러운 한국어로 통화하세요. ` +
        `통화 시작 시 자신이 AI 비서임을 밝히고 목적(${call.goal})을 명확히 확인하세요.` +
        (call.script ? ` 추가 조건: ${call.script}` : "");
      try {
        const resp = await httpFetch("https://api.vapi.ai/call/phone", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${settings.vapi_api_key}`,
          },
          body: JSON.stringify({
            phoneNumberId: settings.vapi_phone_number_id,
            customer: { number: toNumber, name: recipient },
            assistant: {
              firstMessage: `안녕하세요, ${userName}님의 AI 비서입니다. ${call.goal} 관련해서 연락드렸습니다.`,
              model: {
                provider: "openai",
                model: "gpt-4o-mini",
                messages: [{ role: "system", content: sysPrompt }],
              },
              transcriber: { provider: "deepgram", model: "nova-2", language: "ko" },
            },
          }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.text}`);
        const data = JSON.parse(resp.text || "{}");
        const updated = updateCall(callId, {
          status: "dialing",
          provider_call_id: String(data.id || ""),
        });
        void pollSingleExternalCall(callId);
        return updated;
      } catch (err) {
        return updateCall(callId, { status: "failed", summary: `Vapi 발신 실패: ${err.message}` });
      }
    }

    if (provider === "retell") {
      if (!settings.retell_api_key || !settings.retell_from_number || !settings.retell_agent_id) {
        return updateCall(callId, {
          status: "failed",
          summary: "Retell API 키, 발신번호 또는 Agent ID가 설정되어 있지 않습니다.",
        });
      }
      try {
        const resp = await httpFetch("https://api.retellai.com/v2/create-phone-call", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${settings.retell_api_key}`,
          },
          body: JSON.stringify({
            from_number: normalizeKrPhone(settings.retell_from_number),
            to_number: toNumber,
            override_agent_id: settings.retell_agent_id,
            retell_llm_dynamic_variables: {
              user_name: userName,
              recipient_name: recipient,
              goal: call.goal,
              script: call.script || "",
            },
          }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.text}`);
        const data = JSON.parse(resp.text || "{}");
        const updated = updateCall(callId, {
          status: "dialing",
          provider_call_id: String(data.call_id || ""),
        });
        void pollSingleExternalCall(callId);
        return updated;
      } catch (err) {
        return updateCall(callId, { status: "failed", summary: `Retell 발신 실패: ${err.message}` });
      }
    }

    if (provider === "twilio_realtime") {
      return updateCall(callId, {
        status: "failed",
        summary:
          "Twilio + OpenAI Realtime 음성 브리지는 공용 원격 서버(Render / Cloudflare Tunnel / bruno-sony)가 필요합니다. 기기 단독 모드에서는 Vapi 또는 Retell을 선택해 주세요.",
      });
    }

    return updateCall(callId, {
      status: "failed",
      summary: `지원하지 않는 전화 프로바이더입니다: ${provider}`,
    });
  }

  function createCallRecord({ phone_number, goal, recipient_name = "", script = "", provider = "", conversation_id = "" }) {
    const store = loadStore();
    const settings = getSettings(false);
    const ts = nowIso();
    const call = {
      id: newId("call"),
      conversation_id: conversation_id || getDefaultConversationId(store),
      recipient_name: String(recipient_name || "").trim(),
      phone_number: normalizeKrPhone(phone_number) || String(phone_number || "").trim(),
      goal: String(goal || "").trim(),
      script: String(script || "").trim(),
      provider: String(provider || settings.telephony_provider || "simulation").trim(),
      provider_call_id: null,
      status: "queued",
      transcript: [],
      summary: null,
      created_at: ts,
      updated_at: ts,
    };
    store.calls.unshift(call);
    saveStore(store);
    publish("call_updated", { call });
    return call;
  }

  async function executeTool(name, args = {}, ctx = {}) {
    const store = loadStore();
    const convId = ctx.conversation_id || getDefaultConversationId(store);

    if (name === "remember_fact") {
      const content = String(args.content || "").trim();
      if (!content) return { ok: false, error: "기억할 내용이 비어 있습니다." };
      const mem = {
        id: newId("mem"),
        category: String(args.category || "general").trim() || "general",
        content,
        tags: String(args.tags || "").trim(),
        created_at: nowIso(),
      };
      store.memories.unshift(mem);
      saveStore(store);
      publish("memory_updated", { action: "created", memory: mem });
      return { ok: true, memory: mem };
    }

    if (name === "search_memories") {
      const q = String(args.query || "").trim().toLowerCase();
      const found = store.memories.filter(
        (m) =>
          !q ||
          m.content.toLowerCase().includes(q) ||
          m.category.toLowerCase().includes(q) ||
          (m.tags || "").toLowerCase().includes(q)
      );
      return { ok: true, count: found.length, memories: found.slice(0, 20) };
    }

    if (name === "save_contact") {
      const cname = String(args.name || "").trim();
      const phone = String(args.phone || "").trim();
      if (!cname || !phone) return { ok: false, error: "이름과 전화번호가 모두 필요합니다." };
      const existing = store.contacts.find((c) => c.name === cname);
      let contact;
      if (existing) {
        existing.phone = phone;
        existing.relation = String(args.relation || existing.relation || "").trim();
        existing.notes = String(args.notes || existing.notes || "").trim();
        contact = existing;
      } else {
        contact = {
          id: newId("ct"),
          name: cname,
          phone,
          relation: String(args.relation || "").trim(),
          notes: String(args.notes || "").trim(),
          created_at: nowIso(),
        };
        store.contacts.push(contact);
      }
      saveStore(store);
      publish("contact_updated", { action: "saved", contact });
      return { ok: true, contact };
    }

    if (name === "find_contact") {
      const q = String(args.query || "").trim().toLowerCase();
      const found = store.contacts.filter(
        (c) =>
          !q ||
          c.name.toLowerCase().includes(q) ||
          c.phone.includes(q) ||
          (c.relation || "").toLowerCase().includes(q)
      );
      return { ok: true, count: found.length, contacts: found.slice(0, 10) };
    }

    if (name === "create_task") {
      const title = String(args.title || "").trim();
      if (!title) return { ok: false, error: "할 일 또는 알림 제목이 필요합니다." };
      const task = {
        id: newId("tsk"),
        title,
        due_at: String(args.due_at || "").trim() || null,
        kind: String(args.kind || "task").trim() || "task",
        status: "open",
        notified: 0,
        notes: String(args.notes || "").trim(),
        created_at: nowIso(),
      };
      store.tasks.unshift(task);
      saveStore(store);
      publish("task_updated", { action: "created", task });
      return { ok: true, task };
    }

    if (name === "list_tasks") {
      const st = String(args.status || "open").toLowerCase();
      const items =
        st === "all" ? store.tasks : store.tasks.filter((t) => t.status === st);
      return { ok: true, count: items.length, tasks: items };
    }

    if (name === "complete_task") {
      const task = store.tasks.find((t) => t.id === args.task_id);
      if (!task) return { ok: false, error: "작업을 찾을 수 없습니다." };
      task.status = "done";
      saveStore(store);
      publish("task_updated", { action: "updated", task });
      return { ok: true, task };
    }

    if (name === "web_search") {
      const q = String(args.query || "").trim();
      if (!q) return { ok: false, error: "검색어가 비어 있습니다." };
      const maxRes = Math.max(1, Math.min(Number(args.max_results || 5), 10));
      try {
        const resp = await httpFetch("https://html.duckduckgo.com/html/", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ q, kl: "kr-kr" }).toString(),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const results = parseDuckDuckGoHtml(resp.text, maxRes);
        return { ok: true, query: q, count: results.length, results };
      } catch (err) {
        return { ok: false, error: `웹 검색 실패: ${err.message}`, results: [] };
      }
    }

    if (name === "make_phone_call") {
      let recipient = String(args.recipient_name || "").trim();
      let phone = String(args.phone_number || "").trim();
      const goal = String(args.goal || "").trim() || "용건 확인";
      const script = String(args.script || "").trim();

      if (!phone && recipient) {
        const match = store.contacts.find((c) => c.name.includes(recipient) || recipient.includes(c.name));
        if (match) {
          phone = match.phone;
          recipient = recipient || match.name;
        }
      }
      if (!phone) {
        return {
          ok: false,
          error: `'${recipient || "수신자"}'의 전화번호를 찾지 못했습니다. 전화번호를 직접 알려주시거나 연락처에 먼저 등록해 주세요.`,
        };
      }

      const settings = getSettings(false);
      const provider = String(settings.telephony_provider || "simulation");
      const normalized = normalizeKrPhone(phone);

      if (settings.require_call_approval) {
        const approval = {
          id: newId("apr"),
          conversation_id: convId,
          kind: "phone_call",
          title: `AI 전화 발신 승인: ${recipient || phone}`,
          description: `번호: ${phone} (${normalized}) · 목적: ${goal}`,
          payload: {
            recipient_name: recipient,
            phone_number: phone,
            normalized_phone: normalized,
            goal,
            script,
            provider,
          },
          status: "pending",
          decision_note: "",
          created_at: nowIso(),
          decided_at: null,
        };
        store.approvals.unshift(approval);
        saveStore(store);
        publish("approval_created", { approval });
        return {
          ok: true,
          status: "awaiting_approval",
          approval_id: approval.id,
          recipient_name: recipient,
          phone_number: phone,
          provider,
          goal,
        };
      }

      const call = createCallRecord({
        phone_number: phone,
        goal,
        recipient_name: recipient,
        script,
        provider,
        conversation_id: convId,
      });
      const dispatched = await placeCall(call.id);
      return {
        ok: true,
        status: dispatched?.status || "dialing",
        call_id: call.id,
        recipient_name: recipient,
        phone_number: phone,
        provider,
      };
    }

    return { ok: false, error: `기기 단독 모드에서 지원되지 않는 도구입니다: ${name}` };
  }

  function demoSummarizeTools(toolMsgs) {
    const lines = [];
    for (const m of toolMsgs) {
      let res = {};
      try {
        res = JSON.parse(m.content || "{}");
      } catch {
        res = {};
      }
      if (res.ok === false && res.error) {
        lines.push(`⚠️ ${res.error}`);
        continue;
      }
      if (m.name === "make_phone_call") {
        if (res.status === "awaiting_approval") {
          const target = res.recipient_name || res.phone_number;
          lines.push(
            `📞 **${target}** (\`${res.phone_number}\`) 번호로 AI 전화 발신 승인 카드를 생성했습니다.\n` +
              `- 통화 목적: ${res.goal}\n` +
              `- 모드: \`${res.provider}\`\n` +
              "아래 카드에서 **승인 후 전화 걸기**를 눌러주시면 바로 발신합니다."
          );
        } else {
          lines.push(`📞 전화를 발신했습니다 (상태: \`${res.status}\`, ID: \`${res.call_id}\`).`);
        }
      } else if (m.name === "remember_fact") {
        lines.push(`🧠 기억 버블에 저장했습니다: "${res.memory?.content || ""}"`);
      } else if (m.name === "save_contact") {
        lines.push(`📒 연락처에 저장했습니다: **${res.contact?.name}** (\`${res.contact?.phone}\`)`);
      } else if (m.name === "create_task") {
        const due = res.task?.due_at ? ` (예정: \`${res.task.due_at}\`)` : "";
        lines.push(`✅ 등록했습니다: **${res.task?.title}**${due}`);
      } else if (m.name === "web_search") {
        const items = res.results || [];
        if (!items.length) {
          lines.push("🔍 검색 결과를 찾지 못했습니다.");
        } else {
          lines.push(`🔍 **'${res.query}'** 검색 결과:`);
          for (const item of items.slice(0, 4)) {
            lines.push(`- [${item.title}](${item.url}) — ${item.snippet || ""}`);
          }
        }
      } else {
        lines.push(`작업을 완료했습니다: \`${JSON.stringify(res)}\``);
      }
    }
    return lines.join("\n") || "요청하신 작업을 처리했습니다.";
  }

  function runDemoTurn(messages) {
    if (messages.length && messages[messages.length - 1].role === "tool") {
      const recent = [];
      for (let i = messages.length - 1; i >= 0; i -= 1) {
        if (messages[i].role === "tool") recent.unshift(messages[i]);
        else break;
      }
      return { text: demoSummarizeTools(recent), tool_calls: [], provider_used: "demo" };
    }

    let userText = "";
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "user") {
        userText = String(messages[i].content || "").trim();
        break;
      }
    }

    const phone = extractPhone(userText);
    if (phone && ["연락처", "번호 저장", "저장해"].some((k) => userText.includes(k))) {
      let name = userText.replace(phone, " ");
      for (const kw of ["연락처에", "연락처", "번호", "전화번호", "저장해줘", "저장해", "등록해줘", "등록해"]) {
        name = name.replaceAll(kw, " ");
      }
      name = name.replace(/\s+/g, " ").replace(/^[\s,.\-:]+|[\s,.\-:]+$/g, "") || "새 연락처";
      return {
        text: "",
        tool_calls: [{ id: newId("demo"), name: "save_contact", arguments: { name, phone } }],
        provider_used: "demo",
      };
    }

    if (["전화", "통화", "콜"].some((k) => userText.includes(k))) {
      return {
        text: "",
        tool_calls: [
          {
            id: newId("demo"),
            name: "make_phone_call",
            arguments: {
              recipient_name: guessRecipient(userText, phone),
              phone_number: phone,
              goal: userText,
            },
          },
        ],
        provider_used: "demo",
      };
    }

    if (["기억해", "기억 해", "메모해", "메모 해", "잊지마"].some((k) => userText.includes(k))) {
      let fact = userText;
      for (const kw of ["기억해줘", "기억해 줘", "기억해", "메모해줘", "메모해", "잊지마"]) {
        fact = fact.replaceAll(kw, "");
      }
      fact = fact.replace(/^[\s.,]+|[\s.,]+$/g, "") || userText;
      return {
        text: "",
        tool_calls: [
          { id: newId("demo"), name: "remember_fact", arguments: { category: "general", content: fact } },
        ],
        provider_used: "demo",
      };
    }

    if (["알림", "리마인더", "할 일", "할일", "일정"].some((k) => userText.includes(k))) {
      const due = parseKoreanDue(userText);
      const kind = userText.includes("알림") || userText.includes("리마인더") || due ? "reminder" : "task";
      let title = userText;
      for (const kw of ["알림 등록해줘", "알림 설정해줘", "알림 해줘", "할 일 추가해줘", "등록해줘", "추가해줘"]) {
        title = title.replaceAll(kw, "");
      }
      title = title.replace(/^[\s.,]+|[\s.,]+$/g, "") || userText;
      return {
        text: "",
        tool_calls: [{ id: newId("demo"), name: "create_task", arguments: { title, due_at: due, kind } }],
        provider_used: "demo",
      };
    }

    if (["검색", "찾아", "알아봐"].some((k) => userText.includes(k)) || userText.toLowerCase().includes("search")) {
      let q = userText;
      for (const kw of ["검색해줘", "검색해", "찾아줘", "알아봐줘", "웹에서"]) {
        q = q.replaceAll(kw, "");
      }
      q = q.replace(/^[\s.,]+|[\s.,]+$/g, "") || userText;
      return {
        text: "",
        tool_calls: [{ id: newId("demo"), name: "web_search", arguments: { query: q, max_results: 5 } }],
        provider_used: "demo",
      };
    }

    return {
      text:
        `현재 **기기 단독 데모 모드**로 동작 중입니다 (\`${userText}\`).\n\n` +
        "지금 바로 실행 가능한 명령:\n" +
        "- **연락처:** `홍길동 010-1234-5678 연락처에 저장해줘`\n" +
        "- **AI 전화:** `02-123-4567로 저녁 7시 2명 예약 전화해줘`\n" +
        "- **기억 버블:** `나는 매운 음식을 잘 못 먹는다고 기억해줘`\n" +
        "- **일정·알림:** `10분 뒤 스트레칭 하기 알림 등록해줘`\n" +
        "- **웹 검색:** `강남역 파스타 맛집 검색해줘`\n\n" +
        "상단 **⚙️ 설정**에서 Gemini / OpenAI / Anthropic API 키를 입력하면 자유로운 자연어 대화가 바로 활성화됩니다.",
      tool_calls: [],
      provider_used: "demo",
    };
  }

  async function callOpenAICompatible({ baseUrl, apiKey, model, label, messages, systemPrompt }) {
    const formatted = [{ role: "system", content: systemPrompt }];
    for (const m of messages) {
      if (m.role === "user") formatted.push({ role: "user", content: String(m.content || "") });
      else if (m.role === "assistant") {
        const item = { role: "assistant", content: m.content || (m.tool_calls?.length ? null : "") };
        if (m.tool_calls?.length) {
          item.tool_calls = m.tool_calls.map((tc) => ({
            id: tc.id,
            type: "function",
            function: { name: tc.name, arguments: JSON.stringify(tc.arguments || {}) },
          }));
        }
        formatted.push(item);
      } else if (m.role === "tool") {
        formatted.push({ role: "tool", tool_call_id: m.tool_call_id, content: String(m.content || "{}") });
      }
    }
    const headers = { "Content-Type": "application/json" };
    if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

    const resp = await httpFetch(`${baseUrl.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model,
        messages: formatted,
        tools: TOOL_SPECS.map((t) => ({
          type: "function",
          function: { name: t.name, description: t.description, parameters: t.parameters },
        })),
      }),
    });
    if (!resp.ok) {
      return { text: `${label} API 오류 (${resp.status}): ${resp.text}`, tool_calls: [], provider_used: label };
    }
    const data = JSON.parse(resp.text || "{}");
    const msg = data.choices?.[0]?.message || {};
    const toolCalls = (msg.tool_calls || []).map((tc) => {
      let args = {};
      try {
        args = typeof tc.function?.arguments === "string"
          ? JSON.parse(tc.function.arguments || "{}")
          : (tc.function?.arguments || {});
      } catch {
        args = {};
      }
      return { id: tc.id || newId("tc"), name: tc.function?.name || "", arguments: args };
    });
    return { text: msg.content || "", tool_calls: toolCalls, provider_used: label };
  }

  async function callGemini({ apiKey, model, messages, systemPrompt }) {
    const contents = [];
    let pendingTools = [];
    const flushTools = () => {
      if (pendingTools.length) {
        contents.push({ role: "user", parts: pendingTools });
        pendingTools = [];
      }
    };
    for (const m of messages) {
      if (m.role === "tool") {
        let obj = {};
        try {
          obj = JSON.parse(m.content || "{}");
        } catch {
          obj = { result: String(m.content || "") };
        }
        pendingTools.push({ functionResponse: { name: m.name || "tool", response: obj } });
        continue;
      }
      flushTools();
      if (m.role === "user") contents.push({ role: "user", parts: [{ text: String(m.content || "") }] });
      else if (m.role === "assistant") {
        const parts = [];
        if (m.content) parts.push({ text: String(m.content) });
        for (const tc of m.tool_calls || []) {
          parts.push({ functionCall: { name: tc.name, args: tc.arguments || {} } });
        }
        if (parts.length) contents.push({ role: "model", parts });
      }
    }
    flushTools();

    const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`;
    const resp = await httpFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: systemPrompt }] },
        contents,
        tools: [{ functionDeclarations: TOOL_SPECS }],
      }),
    });
    if (!resp.ok) {
      return { text: `Gemini API 오류 (${resp.status}): ${resp.text}`, tool_calls: [], provider_used: "gemini" };
    }
    const data = JSON.parse(resp.text || "{}");
    const parts = data.candidates?.[0]?.content?.parts || [];
    const textChunks = [];
    const toolCalls = [];
    for (const p of parts) {
      if (p.text) textChunks.push(p.text);
      if (p.functionCall) {
        toolCalls.push({
          id: newId("gem"),
          name: p.functionCall.name || "",
          arguments: p.functionCall.args || {},
        });
      }
    }
    return { text: textChunks.join("\n").trim(), tool_calls: toolCalls, provider_used: "gemini" };
  }

  async function callAnthropic({ apiKey, model, messages, systemPrompt }) {
    const formatted = [];
    let pendingResults = [];
    const flushResults = () => {
      if (pendingResults.length) {
        formatted.push({ role: "user", content: pendingResults });
        pendingResults = [];
      }
    };
    for (const m of messages) {
      if (m.role === "tool") {
        pendingResults.push({
          type: "tool_result",
          tool_use_id: m.tool_call_id || "",
          content: String(m.content || "{}"),
        });
        continue;
      }
      flushResults();
      if (m.role === "user") formatted.push({ role: "user", content: String(m.content || "") });
      else if (m.role === "assistant") {
        const blocks = [];
        if (m.content) blocks.push({ type: "text", text: String(m.content) });
        for (const tc of m.tool_calls || []) {
          blocks.push({ type: "tool_use", id: tc.id, name: tc.name, input: tc.arguments || {} });
        }
        if (blocks.length) formatted.push({ role: "assistant", content: blocks });
      }
    }
    flushResults();

    const resp = await httpFetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({
        model,
        max_tokens: 2048,
        system: systemPrompt,
        messages: formatted,
        tools: TOOL_SPECS.map((t) => ({
          name: t.name,
          description: t.description,
          input_schema: t.parameters,
        })),
      }),
    });
    if (!resp.ok) {
      return { text: `Anthropic API 오류 (${resp.status}): ${resp.text}`, tool_calls: [], provider_used: "anthropic" };
    }
    const data = JSON.parse(resp.text || "{}");
    const textChunks = [];
    const toolCalls = [];
    for (const block of data.content || []) {
      if (block.type === "text" && block.text) textChunks.push(block.text);
      else if (block.type === "tool_use") {
        toolCalls.push({ id: block.id || newId("ant"), name: block.name || "", arguments: block.input || {} });
      }
    }
    return { text: textChunks.join("\n").trim(), tool_calls: toolCalls, provider_used: "anthropic" };
  }

  async function chatWithTools(messages, systemPrompt, settings) {
    const provider = resolveActiveProvider(settings);
    if (provider === "demo") return runDemoTurn(messages);
    if (provider === "gemini") {
      if (!settings.gemini_api_key) {
        return { text: "Gemini API 키가 설정되지 않았습니다.", tool_calls: [], provider_used: "gemini" };
      }
      return callGemini({
        apiKey: settings.gemini_api_key,
        model: settings.gemini_model || "gemini-2.5-flash",
        messages,
        systemPrompt,
      });
    }
    if (provider === "openai") {
      if (!settings.openai_api_key) {
        return { text: "OpenAI API 키가 설정되지 않았습니다.", tool_calls: [], provider_used: "openai" };
      }
      return callOpenAICompatible({
        baseUrl: "https://api.openai.com/v1",
        apiKey: settings.openai_api_key,
        model: settings.openai_model || "gpt-4.1-mini",
        label: "openai",
        messages,
        systemPrompt,
      });
    }
    if (provider === "anthropic") {
      if (!settings.anthropic_api_key) {
        return { text: "Anthropic API 키가 설정되지 않았습니다.", tool_calls: [], provider_used: "anthropic" };
      }
      return callAnthropic({
        apiKey: settings.anthropic_api_key,
        model: settings.anthropic_model || "claude-sonnet-4-20250514",
        messages,
        systemPrompt,
      });
    }
    if (provider === "ollama") {
      const base = String(settings.ollama_base_url || "http://127.0.0.1:11434").replace(/\/+$/, "");
      return callOpenAICompatible({
        baseUrl: `${base}/v1`,
        apiKey: "ollama",
        model: settings.ollama_model || "qwen2.5:7b",
        label: "ollama",
        messages,
        systemPrompt,
      });
    }
    return runDemoTurn(messages);
  }

  function buildSystemPrompt(store, settings) {
    const userName = settings.user_name || "사용자";
    const memBlock =
      store.memories.slice(0, 12).map((m) => `- [${m.category}] ${m.content}`).join("\n") ||
      "(저장된 기억 없음)";
    const ctBlock =
      store.contacts.slice(0, 15).map((c) => `- ${c.name} (${c.relation || "기타"}): ${c.phone}`).join("\n") ||
      "(등록된 연락처 없음)";
    const taskBlock =
      store.tasks
        .filter((t) => t.status === "open")
        .slice(0, 15)
        .map((t) => `- [${t.id}] ${t.title}${t.due_at ? ` (기한: ${t.due_at})` : ""}`)
        .join("\n") || "(열린 할 일 없음)";

    return (
      `당신은 ${userName}님의 개인 AI 비서 'Bruno AI'입니다. 한국어로 명확하고 친절하며 간결하게 답하세요.\n` +
      `현재 시각: ${nowIso()}\n\n` +
      "원칙:\n" +
      "1. 전화를 걸어야 할 때는 `make_phone_call` 도구를 사용하세요.\n" +
      "2. 사용자가 취향/정보를 말하면 `remember_fact`로 기억해 두세요.\n" +
      "3. 존재하지 않는 실행 결과나 통화 결과를 지어내지 마세요.\n\n" +
      `[최근 기억 버블]\n${memBlock}\n\n` +
      `[등록된 연락처]\n${ctBlock}\n\n` +
      `[진행 중인 할 일/알림]\n${taskBlock}\n`
    );
  }

  function checkDueReminders() {
    const store = loadStore();
    const now = Date.now();
    const fired = [];
    for (const task of store.tasks) {
      if (task.status === "open" && !task.notified && task.due_at) {
        const dueMs = Date.parse(task.due_at);
        if (!Number.isNaN(dueMs) && dueMs <= now) {
          task.notified = 1;
          fired.push(task);
        }
      }
    }
    if (!fired.length) return [];
    saveStore(store);
    const convId = getDefaultConversationId(store);
    for (const task of fired) {
      publish("reminder_due", { task });
      publish("task_updated", { action: "updated", task });
      appendMessage(
        convId,
        "assistant",
        `⏰ **알림:** ${task.title}${task.due_at ? ` (\`${task.due_at}\`)` : ""}`,
        { reminder_task_id: task.id }
      );
    }
    return fired;
  }

  return {
    async health() {
      const settings = getSettings(true);
      return {
        ok: true,
        version: "0.2.0",
        mode: "standalone",
        host: "이 기기 (단독 실행)",
        tailscale_hint: "https://bruno-sony.tail617e7a.ts.net",
        active_llm_provider: resolveActiveProvider(settings),
        telephony_provider: settings.telephony_provider || "simulation",
        playwright_available: false,
      };
    },
    async getSettings() {
      return getSettings(false);
    },
    async saveSettings(payload) {
      return saveSettings(payload);
    },
    async listConversations() {
      const store = loadStore();
      return { conversations: store.conversations };
    },
    async createConversation(title) {
      const store = loadStore();
      const ts = nowIso();
      const conv = {
        id: newId("conv"),
        title: String(title || "새 대화").trim() || "새 대화",
        created_at: ts,
        updated_at: ts,
      };
      store.conversations.unshift(conv);
      saveStore(store);
      return { conversation: conv };
    },
    async deleteConversation(id) {
      const store = loadStore();
      store.conversations = store.conversations.filter((c) => c.id !== id);
      store.messages = store.messages.filter((m) => m.conversation_id !== id);
      if (!store.conversations.length) {
        const ts = nowIso();
        store.conversations.push({ id: "default", title: "메인 비서", created_at: ts, updated_at: ts });
      }
      saveStore(store);
      return { ok: true };
    },
    async getMessages(convId) {
      const store = loadStore();
      return { messages: store.messages.filter((m) => m.conversation_id === convId) };
    },
    async sendChat(conversationId, message) {
      const text = String(message || "").trim();
      if (!text) throw new Error("메시지가 비어 있습니다.");

      const userMsg = appendMessage(conversationId, "user", text);
      const settings = getSettings(true);
      const store = loadStore();
      const systemPrompt = buildSystemPrompt(store, settings);
      const working = store.messages
        .filter((m) => m.conversation_id === conversationId && (m.role === "user" || m.role === "assistant"))
        .slice(-16)
        .map((m) => ({ role: m.role, content: m.content }));

      const toolTrace = [];
      let finalText = "";
      let providerUsed = "demo";

      publish("agent_status", { conversation_id: conversationId, status: "생각하는 중..." });
      try {
        for (let step = 0; step < 6; step += 1) {
          const llmRes = await chatWithTools(working, systemPrompt, settings);
          providerUsed = llmRes.provider_used || "demo";
          if (!llmRes.tool_calls?.length) {
            finalText = String(llmRes.text || "").trim() || "요청을 처리했습니다.";
            break;
          }
          working.push({
            role: "assistant",
            content: llmRes.text || "",
            tool_calls: llmRes.tool_calls,
          });
          for (const tc of llmRes.tool_calls) {
            publish("agent_status", {
              conversation_id: conversationId,
              status: TOOL_STATUS_LABELS[tc.name] || `${tc.name} 실행 중...`,
              tool: tc.name,
            });
            const result = await executeTool(tc.name, tc.arguments, { conversation_id: conversationId });
            toolTrace.push({ tool: tc.name, args: tc.arguments, result });
            working.push({
              role: "tool",
              tool_call_id: tc.id,
              name: tc.name,
              content: JSON.stringify(result),
            });
          }
        }
        if (!finalText) finalText = "작업을 완료했습니다.";
      } finally {
        publish("agent_status", { conversation_id: conversationId, status: "" });
      }

      const assistantMsg = appendMessage(conversationId, "assistant", finalText, {
        provider: providerUsed,
        tool_trace: toolTrace,
      });
      return { user_message: userMsg, assistant_message: assistantMsg, tool_trace: toolTrace, provider: providerUsed };
    },
    async listApprovals() {
      const store = loadStore();
      const sorted = [...store.approvals].sort((a, b) => {
        if (a.status === "pending" && b.status !== "pending") return -1;
        if (a.status !== "pending" && b.status === "pending") return 1;
        return String(b.created_at).localeCompare(String(a.created_at));
      });
      return { approvals: sorted };
    },
    async decideApproval(id, decision, note = "") {
      const store = loadStore();
      const apr = store.approvals.find((a) => a.id === id);
      if (!apr) throw new Error("승인 요청을 찾을 수 없습니다.");
      if (apr.status !== "pending") {
        return { ok: true, approval: apr, already_decided: true };
      }
      apr.status = decision;
      apr.decision_note = String(note || "").trim();
      apr.decided_at = nowIso();
      saveStore(store);
      publish("approval_updated", { approval: apr });

      let createdCall = null;
      if (decision === "approved" && apr.kind === "phone_call") {
        const p = apr.payload || {};
        createdCall = createCallRecord({
          phone_number: p.phone_number || "",
          goal: p.goal || apr.description,
          recipient_name: p.recipient_name || "",
          script: p.script || "",
          provider: p.provider || "",
          conversation_id: apr.conversation_id || "",
        });
        createdCall = await placeCall(createdCall.id);
      }
      return { ok: true, approval: apr, call: createdCall };
    },
    async listCalls() {
      return { calls: loadStore().calls };
    },
    async createCall(payload) {
      const call = createCallRecord(payload);
      const dispatched = await placeCall(call.id);
      return { ok: true, call: dispatched };
    },
    async listMemories() {
      return { memories: loadStore().memories };
    },
    async createMemory(payload) {
      return executeTool("remember_fact", payload);
    },
    async deleteMemory(id) {
      const store = loadStore();
      const before = store.memories.length;
      store.memories = store.memories.filter((m) => m.id !== id);
      saveStore(store);
      const ok = store.memories.length < before;
      if (ok) publish("memory_updated", { action: "deleted", id });
      return { ok, id };
    },
    async listContacts() {
      return { contacts: loadStore().contacts };
    },
    async createContact(payload) {
      return executeTool("save_contact", payload);
    },
    async deleteContact(id) {
      const store = loadStore();
      const before = store.contacts.length;
      store.contacts = store.contacts.filter((c) => c.id !== id);
      saveStore(store);
      const ok = store.contacts.length < before;
      if (ok) publish("contact_updated", { action: "deleted", id });
      return { ok, id };
    },
    async listTasks() {
      return { tasks: loadStore().tasks };
    },
    async createTask(payload) {
      return executeTool("create_task", payload);
    },
    async patchTask(id, payload) {
      const store = loadStore();
      const task = store.tasks.find((t) => t.id === id);
      if (!task) throw new Error("작업을 찾을 수 없습니다.");
      Object.assign(task, payload);
      saveStore(store);
      publish("task_updated", { action: "updated", task });
      return { ok: true, task };
    },
    async deleteTask(id) {
      const store = loadStore();
      const before = store.tasks.length;
      store.tasks = store.tasks.filter((t) => t.id !== id);
      saveStore(store);
      const ok = store.tasks.length < before;
      if (ok) publish("task_updated", { action: "deleted", id });
      return { ok, id };
    },
    async browserState() {
      return { available: false, standalone: true, running: false };
    },
    connectEvents(handlers) {
      listeners.add(handlers);
      if (options.enableTimer !== false && !schedulerTimer) {
        schedulerTimer = setInterval(checkDueReminders, 15000);
      }
      queueMicrotask(() => {
        if (listeners.has(handlers)) {
          handlers.onOpen?.();
          handlers.connected?.({ ok: true, mode: "standalone" });
        }
      });
      return {
        close() {
          listeners.delete(handlers);
          if (listeners.size === 0 && schedulerTimer) {
            clearInterval(schedulerTimer);
            schedulerTimer = null;
          }
        },
      };
    },
    _checkDueReminders: checkDueReminders,
  };
}
