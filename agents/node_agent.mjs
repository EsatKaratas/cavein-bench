// Reference agent #3: a different language entirely (Node.js, no dependencies).
// Same CaveIn protocol, same Bifrost endpoint. Proves the harness does not care
// what an agent is built with.
import readline from "node:readline";

const send = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
let cfg = null;
let messages = [];
let pending = [];
let telemetry = [];

async function llm() {
  const t0 = Date.now();
  const r = await fetch(cfg.bifrost_url + "/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-bf-lh-run_id": cfg.run_id,
      "x-bf-lh-agent": cfg.agent_name || "node",
    },
    body: JSON.stringify({ model: cfg.model, fallbacks: cfg.fallbacks || [], messages, tools: cfg.tools, temperature: 0.3 }),
  });
  if (!r.ok) throw new Error(`bifrost ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const data = await r.json();
  const u = data.usage || {};
  telemetry.push({ model: data.model, extra: data.extra_fields, tokens_in: u.prompt_tokens || 0,
                   tokens_out: u.completion_tokens || 0, latency_ms: Date.now() - t0 });
  return data.choices[0].message;
}

const flush = () => { const t = telemetry; telemetry = []; return t; };

function emitNextTool() {
  const tc = pending.shift();
  let args = {};
  try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}
  send({ type: "tool_call", call_id: tc.id, name: tc.function.name, arguments: args, llm: flush() });
}

async function act() {
  const msg = await llm();
  const entry = { role: "assistant", content: msg.content || "" };
  if (msg.tool_calls && msg.tool_calls.length) {
    entry.tool_calls = msg.tool_calls;
    messages.push(entry);
    pending = [...msg.tool_calls];
    emitNextTool();
  } else {
    messages.push(entry);
    send({ type: "reply", content: msg.content || "", llm: flush() });
  }
}

const rl = readline.createInterface({ input: process.stdin });
const queue = [];
let busy = false;

async function handle(ev) {
  if (ev.type === "reset") {
    cfg = ev;
    messages = [{ role: "system", content: ev.policy }];
  } else if (ev.type === "customer") {
    messages.push({ role: "user", content: ev.content });
    await act();
  } else if (ev.type === "tool_result") {
    messages.push({ role: "tool", tool_call_id: ev.call_id, content: ev.content });
    if (pending.length) emitNextTool(); else await act();
  } else if (ev.type === "end") {
    process.exit(0);
  }
}

async function pump() {
  if (busy) return;
  busy = true;
  while (queue.length) {
    const ev = queue.shift();
    try { await handle(ev); }
    catch (e) { send({ type: "error", message: String(e).slice(0, 500), llm: flush() }); }
  }
  busy = false;
}

rl.on("line", (line) => { queue.push(JSON.parse(line)); pump(); });
