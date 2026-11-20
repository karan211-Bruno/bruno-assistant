import assert from "node:assert/strict";
import test from "node:test";
import { createStandaloneBackend } from "../static/js/standalone.js";

function makeMemoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
}

test("standalone health, settings masking, and secret preservation", async () => {
  const backend = createStandaloneBackend(makeMemoryStorage(), async () => ({ ok: true, status: 200, text: "{}" }), {
    enableTimer: false,
    simDelayMs: 10,
  });

  const health = await backend.health();
  assert.equal(health.ok, true);
  assert.equal(health.mode, "standalone");
  assert.equal(health.version, "0.2.0");

  const saved = await backend.saveSettings({
    user_name: "테스터",
    gemini_api_key: "AIzaStandaloneTest9999",
  });
  assert.equal(saved.user_name, "테스터");
  assert.equal(saved.gemini_api_key, "****9999");
  assert.equal(saved.has_gemini_api_key, true);

  // Saving unrelated settings with masked placeholder preserves existing key
  const saved2 = await backend.saveSettings({
    user_name: "브루노",
    gemini_api_key: "****9999",
    llm_provider: "demo",
  });
  assert.equal(saved2.user_name, "브루노");
  assert.equal(saved2.gemini_api_key, "****9999");
  assert.equal(saved2.has_gemini_api_key, true);
});

test("standalone CRUD and demo chat tool execution with simulated call", async () => {
  const storage = makeMemoryStorage();
  const backend = createStandaloneBackend(storage, async () => ({ ok: true, status: 200, text: "{}" }), {
    enableTimer: false,
    simDelayMs: 10,
  });
  await backend.saveSettings({ llm_provider: "demo", telephony_provider: "simulation" });

  // CRUD check
  const mem = await backend.createMemory({ category: "preference", content: "차가운 커피 선호" });
  assert.equal(mem.ok, true);
  assert.equal((await backend.listMemories()).memories.length, 1);

  const ct = await backend.createContact({ name: "단골식당", phone: "02-111-2222", relation: "식당" });
  assert.equal(ct.ok, true);
  assert.equal((await backend.listContacts()).contacts.length, 1);

  const tsk = await backend.createTask({ title: "산책하기" });
  assert.equal(tsk.ok, true);
  await backend.patchTask(tsk.task.id, { status: "done" });
  assert.equal((await backend.listTasks()).tasks[0].status, "done");

  // Chat demo tools: remember_fact & create_task
  const chatMem = await backend.sendChat("default", "나는 견과류를 안 먹는다고 기억해줘");
  assert.equal(chatMem.tool_trace[0].tool, "remember_fact");
  assert.equal((await backend.listMemories()).memories.length, 2);

  const chatTask = await backend.sendChat("default", "5분 뒤 물 마시기 알림 등록해줘");
  assert.equal(chatTask.tool_trace[0].tool, "create_task");
  assert.equal((await backend.listTasks()).tasks.length, 2);

  // Chat phone call -> creates approval card -> approve -> simulated call finishes
  const chatCall = await backend.sendChat("default", "단골식당에 오늘 저녁 7시 2명 예약 전화해줘");
  assert.equal(chatCall.tool_trace[0].tool, "make_phone_call");
  const approvalId = chatCall.tool_trace[0].result.approval_id;
  assert.ok(approvalId);

  const decided = await backend.decideApproval(approvalId, "approved");
  assert.equal(decided.ok, true);
  const callId = decided.call.id;

  let completed = null;
  for (let i = 0; i < 40; i += 1) {
    const calls = (await backend.listCalls()).calls;
    const found = calls.find((c) => c.id === callId);
    if (found?.status === "completed") {
      completed = found;
      break;
    }
    await new Promise((r) => setTimeout(r, 15));
  }
  assert.ok(completed, "simulated call should complete");
  assert.ok(completed.transcript.length >= 4);
  assert.ok(completed.transcript[0].text.includes("[시뮬레이션]"));
  assert.ok(completed.summary);
});
