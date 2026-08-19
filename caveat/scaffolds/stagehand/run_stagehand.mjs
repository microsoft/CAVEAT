// Stagehand (Browserbase) harness runner for the amazon laptop task.
// Driven by ../run_stagehand.py, which sets the env + normalizes the output.
//
// Uses Stagehand v3 in `dom` mode (its recommended mode for regular chat
// models — DOM/aria-tree based act/fillForm, works for every model incl.
// text-only ones, and completes multi-step flows). Stagehand's agent runs
// autonomously in one execute() call, so we poll page.screenshot() concurrently
// to reconstruct a per-step visual timeline for the replay viewer, then pair
// frames to the agent's actions.
import { Stagehand } from "@browserbasehq/stagehand";
import fs from "fs";
import path from "path";

// TRAPI's non-OpenAI deployments (Kimi, Llama) only accept message roles
// system/user/assistant/tool, but the Vercel AI SDK emits a "developer" role for
// the system prompt. Rewrite developer->system on outgoing chat requests
// (harmless for the OpenAI models, which also accept "system").
const _origFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (opts && typeof opts.body === "string" && String(url).includes("chat/completions")) {
    try {
      const b = JSON.parse(opts.body);
      let changed = false;
      if (Array.isArray(b.messages))
        for (const m of b.messages) if (m && m.role === "developer") { m.role = "system"; changed = true; }
      // Llama-3.3-70B rejects multiple tool calls; force single-call mode.
      if (b.tools && b.parallel_tool_calls !== false) { b.parallel_tool_calls = false; changed = true; }
      if (changed) opts = { ...opts, body: JSON.stringify(b) };
    } catch { /* not JSON */ }
  }
  return _origFetch(url, opts);
};

const OUT = process.env.SH_OUT;            // output dir (_sh)
const MODEL = process.env.SH_MODEL;        // TRAPI deployment id
const TASK = process.env.SH_TASK;
const START_URL = process.env.SH_START_URL;
const MAX_STEPS = parseInt(process.env.SH_MAX_STEPS || "30", 10);
fs.mkdirSync(OUT, { recursive: true });

const sh = new Stagehand({
  env: "LOCAL",
  modelName: MODEL,
  modelClientOptions: { apiKey: process.env.SH_KEY, baseURL: process.env.SH_BASEURL },
  localBrowserLaunchOptions: {
    executablePath: process.env.CHROME_BIN, headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
    viewport: { width: 1280, height: 900 },
  },
  verbose: 0,
});

const frames = [];           // {t, buf}
let polling = true;
const t0 = Date.now();
async function poll(page) {
  while (polling) {
    try { const buf = await page.screenshot({ type: "png" }); frames.push({ t: Date.now() - t0, buf }); }
    catch { /* page busy/navigating */ }
    await new Promise(r => setTimeout(r, 1100));
  }
}

let result = { success: false, completed: false, message: "", actions: [] };
try {
  await sh.init();
  const page = sh.context.pages()[0] || await sh.context.newPage();
  await page.goto(START_URL);
  const poller = poll(page);
  const agent = sh.agent({
    model: { modelName: "openai/" + MODEL, modelClientOptions: { apiKey: process.env.SH_KEY, baseURL: process.env.SH_BASEURL } },
    mode: "dom",
  });
  const r = await agent.execute({ instruction: TASK, maxSteps: MAX_STEPS });
  polling = false; await poller;
  result = { success: r.success, completed: r.completed, message: r.message || "",
             actions: (r.actions || []).map(a => ({ type: a.type, action: a.action || "",
               reasoning: a.reasoning || "", url: a.pageUrl || "", timeMs: a.timeMs })) };
} catch (e) {
  polling = false;
  result.message = "ERROR: " + (e?.message || String(e));
} finally {
  try { await sh.close(); } catch {}
}

// Pair each action with a polled frame (evenly sampled across the timeline).
const A = result.actions.length;
const steps = [];
for (let i = 0; i < A; i++) {
  const a = result.actions[i];
  let frameIdx = -1;
  if (frames.length) {
    if (a.timeMs != null) {
      let best = Infinity;
      frames.forEach((f, j) => { const d = Math.abs(f.t - a.timeMs); if (d < best) { best = d; frameIdx = j; } });
    } else {
      frameIdx = A > 1 ? Math.round(i * (frames.length - 1) / (A - 1)) : frames.length - 1;
    }
  }
  let img = null;
  if (frameIdx >= 0) { img = `step_${String(i + 1).padStart(2, "0")}.png`; fs.writeFileSync(path.join(OUT, img), frames[frameIdx].buf); }
  steps.push({ img, url: a.url, action: a.action || a.type, reasoning: a.reasoning, type: a.type });
}
fs.writeFileSync(path.join(OUT, "steps.json"), JSON.stringify({
  success: result.success, completed: result.completed, answer: result.message,
  steps,
}, null, 2));
console.log(`STAGEHAND_DONE actions=${A} frames=${frames.length} success=${result.success}`);
