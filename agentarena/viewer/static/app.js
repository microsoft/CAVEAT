"use strict";
/* ===================== shared helpers ===================== */
const $ = (id) => document.getElementById(id);
const J = (u) => fetch(u).then((r) => r.json());
const uniq = (a) => [...new Set(a)];
const esc = (s) => String(s == null ? "" : s).replace(/[<>&"]/g,
  (m) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[m]));
const SCEN_PREFIX = { laptop: "lap", office_chair: "oc", mattress: "mat", backpack: "bp", tent: "tent" };

/* ---- human-readable labels for code-like names ---- */
const PROD_NAME = { lap: "Laptop", oc: "Office chair", mat: "Mattress", bp: "Backpack", tent: "Tent" };
const SWEEP_NAME = {
  g55: "gpt-5.5", g41: "gpt-4.1", exp: "model sweep", hid: "hidden-spec",
  gN_g55: "gpt-5.5", gN_g55b: "gpt-5.5", gN3_g41: "gpt-4.1",
  vintage5: "vintage line", vintage5b: "vintage line", scale54: "model scale",
  effort55: "reasoning effort", effort55b: "reasoning effort",
  xfam: "cross-family", xfam2: "cross-family", xfam_ds: "cross-family", xfam_ds2: "cross-family",
  hidden: "hidden-spec",
};
const PRETTY_MODEL = {
  "gpt-5.5-low": "gpt-5.5 · low effort", "gpt-5.5-medium": "gpt-5.5 · med effort", "gpt-5.5-high": "gpt-5.5 · high effort",
  "gpt-5.4-mini": "gpt-5.4 mini", "gpt-5.4-nano": "gpt-5.4 nano",
  "grok-4-1-fast-non-reasoning": "Grok 4.1 (fast)", "gpt-oss-120b": "GPT-OSS 120B", "DeepSeek-V4-Pro": "DeepSeek-V4 Pro",
};
const COND_NAME = { clean: "clean", combined: "steered", steered: "steered" };
const VARIANT_NAME = { thresholded: "L0 · absolute", mixed: "L1 · mixed", graded: "L2 · graded",
                       graded3: "L3 · graded-3", graded4: "L4 · fully graded" };
const prettyModel = (m) => PRETTY_MODEL[m] || m;
const prettyCond = (c) => COND_NAME[c] || c;
function prettyTask(t) {
  const m = String(t).match(/-(thresholded|mixed|graded3|graded4|graded)$/);
  return m ? VARIANT_NAME[m[1]] : String(t).replace(/_/g, " ");
}
const SCAFFOLD_NAME = { browseruse: "browser-use", "playwright-mcp": "playwright-mcp", simple: "simple", stagehand: "stagehand" };
function prettyDim(d, v) {
  return d === "model" ? prettyModel(v) : d === "condition" ? prettyCond(v)
    : d === "task_id" ? prettyTask(v) : d === "scaffold" ? (SCAFFOLD_NAME[v] || v) : v;
}
const SCAFFOLD_RUN = { pwmcp: "Playwright-MCP" };
function expProduct(name) {
  if (SCAFFOLD_RUN[name.split("_")[0]]) return "Scaffold runs (all products)";
  return PROD_NAME[name.split("_")[0]] || "Other";
}
function prettyExp(name) {
  const rm = name.match(/_r(\d+)$/);
  const rep = rm ? +rm[1] : null;
  const base = rm ? name.slice(0, rm.index) : name;
  const sc = SCAFFOLD_RUN[base.split("_")[0]];
  if (sc) {
    const model = base.split("_")[1] || "";
    return `${sc} · ${SWEEP_NAME[model] || model}${rep != null ? ` · rep ${rep}` : ""}`;
  }
  const toks = base.split("_");
  const prod = PROD_NAME[toks[0]], sweep = SWEEP_NAME[toks.slice(1).join("_")];
  if (prod && sweep) return `${prod} · ${sweep}${rep != null ? ` · rep ${rep}` : ""}`;
  return base.replace(/_/g, " ") + (rep != null ? ` · rep ${rep}` : "");
}

/* ===================== theme ===================== */
(function () {
  const KEY = "aa-theme", root = document.documentElement, btn = $("themeBtn");
  if (localStorage.getItem(KEY) === "dark") root.dataset.theme = "dark";
  const sync = () => { btn.textContent = root.dataset.theme === "dark" ? "☀" : "🌙"; };
  btn.onclick = () => {
    if (root.dataset.theme === "dark") { delete root.dataset.theme; localStorage.setItem(KEY, "light"); }
    else { root.dataset.theme = "dark"; localStorage.setItem(KEY, "dark"); }
    sync();
  };
  sync();
})();

/* ===================== router ===================== */
const VIEWS = ["runs", "browse", "figures"];
let curView = null;
const _inited = {};
function showView(v, opts) {
  if (!VIEWS.includes(v)) v = "runs";
  curView = v;
  VIEWS.forEach((x) => { $("view-" + x).hidden = (x !== v); });
  document.querySelectorAll("#tabs .tab").forEach((t) => t.classList.toggle("on", t.dataset.view === v));
  localStorage.setItem("aa-view", v);
  if (location.hash.slice(1) !== v) history.replaceState(null, "", "#" + v);
  if (v === "runs" && !_inited.runs) { _inited.runs = true; runsInit(); }
  if (v === "runs") runsPoll(true);
  if (v === "browse" && !_inited.browse) { _inited.browse = true; browseInit(); }
  if (v === "figures") figuresInit(opts && opts.product);
}
document.querySelectorAll("#tabs .tab").forEach((t) => t.onclick = () => showView(t.dataset.view));
window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (v && v !== curView) showView(v); });

/* ===================================================================
   RUNS — agent-run results (pivot of cells + trajectory player)
   =================================================================== */
const DIMS = ["env", "scaffold", "model", "task_id", "condition"];
const DIM_LABEL = { env: "env", scaffold: "scaffold", model: "model", task_id: "task", condition: "condition" };
const GLYPH = { compliant: "✓ faithful", decoy: "⚠ took the bait", violation: "○ violation",
                none: "– no purchase", error: "✗ error", skipped: "· skipped" };
const CLS = { compliant: "c-compliant", decoy: "c-decoy", violation: "c-violation",
              none: "c-none", error: "c-error", skipped: "c-skipped", success: "c-compliant" };
const S = { exp: null, data: null, filters: {}, seen: {}, sig: null, rowDim: "model",
            colDim: "scaffold", split: true, player: null, compareFrom: null, pollTimer: null };

function runsInit() {
  $("rowDim").innerHTML = $("colDim").innerHTML = DIMS.map((d) => `<option value="${d}">${DIM_LABEL[d]}</option>`).join("");
  $("rowDim").value = S.rowDim; $("colDim").value = S.colDim;
  $("rowDim").onchange = () => { S.rowDim = $("rowDim").value; render(); };
  $("colDim").onchange = () => { S.colDim = $("colDim").value; render(); };
  $("splitCond").checked = S.split;
  $("splitCond").onchange = () => { S.split = $("splitCond").checked; render(); };
  $("exp").onchange = () => loadExp($("exp").value, true);
  $("refresh").onclick = () => runsPoll(true);
  $("figLinkRuns").onclick = () => { const p = $("figLinkRuns").dataset.prefix; if (p) showView("figures", { product: p }); };
  bindPlayer();
  if (!S.pollTimer) S.pollTimer = setInterval(() => { if (curView === "runs") runsPoll(false); }, 4000);
}

async function runsPoll(force) {
  let exps;
  try { exps = await J("/api/experiments"); } catch (e) { return; }
  const sel = $("exp"), cur = sel.value;
  // group the experiment list by product, with human-readable labels
  const groups = {};
  for (const e of exps) (groups[expProduct(e.name)] ||= []).push(e);
  const order = ["Scaffold runs (all products)", "Laptop", "Office chair", "Mattress", "Backpack", "Tent", "Other"];
  sel.innerHTML = order.filter((g) => groups[g]).map((g) =>
    `<optgroup label="${g}">` + groups[g].map((e) =>
      `<option value="${esc(e.name)}">${esc(prettyExp(e.name))} · ${e.kpis.n} runs</option>`).join("") + "</optgroup>"
  ).join("");
  if (!exps.length) { showEmpty(); S.exp = null; S.data = null; S.sig = null; return; }
  const names = exps.map((e) => e.name);
  const target = (S.exp && names.includes(S.exp)) ? S.exp : (names.includes(cur) ? cur : names[0]);
  sel.value = target;
  await loadExp(target, force || target !== S.exp);
}
function showEmpty() {
  $("kpis").innerHTML = ""; $("taskcard").className = "taskcard"; $("count").textContent = "";
  $("pivot").innerHTML = '<div class="empty-state">No runs yet — waiting for results…<br>' +
    '<span class="muted" style="font-size:12px">This view auto-refreshes as cells finish. ' +
    'Start a run with <code>agentarena run …</code> pointing <code>--results</code> here.</span></div>';
}
async function loadExp(name, isNew) {
  let data;
  try { data = await J("/api/experiments/" + encodeURIComponent(name)); } catch (e) { return; }
  const sig = data.cells.length + "|" + data.cells.map((c) => c.cell + ":" + c.outcome + ":" + c.num_steps).join(",");
  if (!isNew && sig === S.sig) return;
  const reallyNew = isNew || S.exp !== name;
  S.exp = name; S.data = data; S.sig = sig;
  if (reallyNew) { S.filters = {}; S.seen = {}; }
  for (const d of DIMS) {
    S.filters[d] = S.filters[d] || new Set(); S.seen[d] = S.seen[d] || new Set();
    for (const v of uniq(data.cells.map((c) => String(c[d]))))
      if (!S.seen[d].has(v)) { S.seen[d].add(v); S.filters[d].add(v); }
  }
  if (reallyNew) setDefaultAxes();
  buildSidebar();
  render();
}
function setDefaultAxes() {
  const nval = (d) => uniq(S.data.cells.map((c) => c[d])).length;
  const cand = DIMS.filter((d) => d !== "condition").sort((a, b) => nval(b) - nval(a));
  S.rowDim = nval("model") > 1 ? "model" : (cand[0] || "model");
  S.colDim = (nval("scaffold") > 1 && S.rowDim !== "scaffold") ? "scaffold"
    : (cand.find((d) => d !== S.rowDim) || (S.rowDim === "model" ? "scaffold" : "model"));
  S.split = nval("condition") > 1;
  $("rowDim").value = S.rowDim; $("colDim").value = S.colDim; $("splitCond").checked = S.split;
}
function buildSidebar() {
  const sb = $("sidebar"); sb.innerHTML = "";
  for (const d of DIMS) {
    const vals = uniq(S.data.cells.map((c) => String(c[d]))).sort();
    if (vals.length <= 1 && d !== "condition") continue;
    const f = document.createElement("div"); f.className = "facet";
    f.innerHTML = `<h4>${DIM_LABEL[d]}</h4>` + vals.map((v) =>
      `<label><input type="checkbox" data-d="${d}" value="${esc(v)}" ${S.filters[d].has(v) ? "checked" : ""}>
       <span class="${d === "scaffold" ? "mono" : ""}">${esc(prettyDim(d, v))}</span></label>`).join("");
    sb.appendChild(f);
  }
  sb.querySelectorAll("input").forEach((cb) => cb.onchange = () => {
    const set = S.filters[cb.dataset.d];
    cb.checked ? set.add(cb.value) : set.delete(cb.value);
    render();
  });
}
const filtered = () => S.data.cells.filter((c) => DIMS.every((d) => S.filters[d].has(String(c[d]))));

function render() {
  const cells = filtered();
  renderKPIs(cells);
  $("count").textContent = `${cells.length} runs shown`;
  renderTaskCard(cells);
  // figures cross-link: when the shown cells are all one generated product
  const scen = uniq(cells.map((c) => String(c.task_id).replace(/-(thresholded|mixed|graded\d?|graded)$/, "")));
  const link = $("figLinkRuns");
  if (scen.length === 1 && SCEN_PREFIX[scen[0]]) {
    link.dataset.prefix = SCEN_PREFIX[scen[0]];
    link.textContent = `📈 ${scen[0].replace("_", " ")} figures →`;
    link.hidden = false;
  } else link.hidden = true;
  renderPivot(cells);
}
function renderKPIs(cells) {
  const n = cells.length || 1;
  const pct = (f) => Math.round(100 * cells.filter(f).length / n);
  const done = cells.filter((c) => !["error", "skipped"].includes(c.outcome));
  const withP = cells.filter((c) => typeof c.preservation === "number");
  let html = "";
  if (withP.length) {
    const meanP = withP.reduce((s, c) => s + c.preservation, 0) / withP.length;
    const cl = meanP >= 0.97 ? "c-compliant" : (meanP >= 0.85 ? "" : "c-decoy");
    html += `<span class="kpi">preservation P <b class="${cl}">${meanP.toFixed(3)}</b>` +
            ` <span class="muted" style="font-size:11px">(${withP.length}/${n})</span></span>`;
  }
  const kpis = [
    ["faithful", pct((c) => c.success), "c-compliant"],
    ["took bait", pct((c) => c.took_bait), "c-decoy"],
    ["completed", Math.round(100 * done.filter((c) => c.outcome !== "none").length / n), ""],
    ["runs", cells.length, ""],
  ];
  html += kpis.map(([k, v, cl]) =>
    `<span class="kpi">${k} <b class="${cl}">${v}${k === "runs" ? "" : "%"}</b></span>`).join("");
  $("kpis").innerHTML = html;
}
function renderTaskCard(cells) {
  const tc = $("taskcard");
  const tasks = uniq(cells.map((c) => c.task_id));
  const t = tasks.length === 1 && (S.data.manifest.tasks || []).find((x) => x.task_id === tasks[0]);
  if (!t) { tc.className = "taskcard"; return; }
  tc.className = "taskcard show";
  tc.innerHTML = `<div class="ti">${esc(t.env)} · ${esc(t.task_id)} — user instruction</div>
    <div class="instr">${esc(t.instruction)}</div>
    <div class="prefs">${Object.entries(t.preferences || {}).map(([k, v]) =>
      `<span class="pref">${esc(k)} = ${esc(JSON.stringify(v))}</span>`).join("")}</div>`;
}
function colKeys(cells) {
  const base = uniq(cells.map((c) => String(c[S.colDim]))).sort();
  if (!S.split || S.colDim === "condition") return base.map((b) => ({ label: b, val: b, cond: null }));
  const conds = uniq(cells.map((c) => String(c.condition))).sort();
  const out = [];
  for (const b of base) for (const cd of conds) out.push({ label: b, val: b, cond: cd, grp: b, sub: cd });
  return out;
}
function renderPivot(cells) {
  const rows = uniq(cells.map((c) => String(c[S.rowDim]))).sort();
  const cols = colKeys(cells);
  const grouped = S.split && S.colDim !== "condition";
  let html = "<table class='pivot-grid'>";
  if (grouped) {
    const groups = uniq(cols.map((c) => c.grp));
    html += "<tr><th class='rowhead'></th>" + groups.map((g) => {
      const span = cols.filter((c) => c.grp === g).length;
      return `<th class='grp' colspan='${span}'>${esc(prettyDim(S.colDim, g))}</th>`;
    }).join("") + "</tr>";
    html += "<tr><th class='rowhead'>" + DIM_LABEL[S.rowDim] + "</th>" +
      cols.map((c) => `<th>${esc(prettyCond(c.sub))}</th>`).join("") + "</tr>";
  } else {
    html += "<tr><th class='rowhead'>" + DIM_LABEL[S.rowDim] + " \\ " + DIM_LABEL[S.colDim] + "</th>" +
      cols.map((c) => `<th>${esc(prettyDim(S.colDim, c.label))}</th>`).join("") + "</tr>";
  }
  for (const r of rows) {
    html += `<tr><td class='rowhead'>${esc(prettyDim(S.rowDim, r))}</td>`;
    for (const c of cols) {
      const match = cells.filter((x) => String(x[S.rowDim]) === r && String(x[S.colDim]) === c.val &&
        (c.cond === null || String(x.condition) === c.cond));
      html += "<td class='cellwrap'>" + cellHTML(match) + "</td>";
    }
    html += "</tr>";
  }
  html += "</table>";
  const p = $("pivot"); p.innerHTML = html;
  p.querySelectorAll(".cell[data-cell]").forEach((el) => el.onclick = () => onCellClick(el.dataset.cell.split("||")));
}
function cellHTML(match) {
  if (!match.length) return "<div class='cell empty'>·</div>";
  if (match.length === 1) {
    const c = match[0];
    const pStr = typeof c.preservation === "number" ? ` · P=${c.preservation.toFixed(2)}` : "";
    const cfgStr = c.chosen_config ? ` · cfg:${c.chosen_config}` : "";
    const sub = (c.chosen_label || c.chosen || "") + cfgStr + pStr + (c.num_steps != null ? ` · ${c.num_steps} steps` : "");
    return `<div class='cell' data-cell='${esc(c.cell)}'><div class='oc ${CLS[c.outcome] || ""}'>${GLYPH[c.outcome] || esc(c.outcome)}</div>
      <div class='sub' title='${esc(sub)}'>${esc(sub)}</div></div>`;
  }
  const ok = match.filter((c) => c.success).length;
  const ids = match.map((c) => c.cell).join("||");
  const withP = match.filter((c) => typeof c.preservation === "number");
  const meanP = withP.length ? withP.reduce((s, c) => s + c.preservation, 0) / withP.length : null;
  const cl = meanP == null ? "c-none" : (meanP >= 0.97 ? "c-compliant" : (meanP >= 0.85 ? "" : "c-decoy"));
  const head = meanP == null ? "– no P" : `P ${meanP.toFixed(2)}`;
  const barW = meanP == null ? 0 : 100 * meanP;
  return `<div class='cell' data-cell='${esc(ids)}'><div class='oc ${cl}'>${head}</div>
    <div class='sub'>✓ ${ok}/${match.length} faithful · ${match.length} runs · click to drill</div>
    <div class='bar'><i style='width:${Math.round(barW)}%'></i></div></div>`;
}
function onCellClick(cellDirs) {
  if (S.compareFrom) { openPlayer([S.compareFrom, cellDirs[0]]); S.compareFrom = null; return; }
  if (cellDirs.length === 1) openPlayer([cellDirs[0]]); else drill(cellDirs);
}
function drill(cellDirs) {
  const card = $("drillCard");
  const cells = cellDirs.map((d) => S.data.cells.find((c) => c.cell === d)).filter(Boolean);
  card.innerHTML = cells.map((c) =>
    `<div class='drill-row' data-cell='${esc(c.cell)}'><span class='dot bg-${c.outcome}'></span>
     <span style='flex:1'>${esc(prettyModel(c.model))} · ${esc(prettyCond(c.condition))}</span>
     <span class='${CLS[c.outcome]}'>${GLYPH[c.outcome] || esc(c.outcome)}</span></div>`).join("");
  card.querySelectorAll(".drill-row").forEach((el) => el.onclick = () => { $("drill").hidden = true; openPlayer([el.dataset.cell]); });
  $("drill").hidden = false;
}
$("drill").onclick = (e) => { if (e.target.id === "drill") $("drill").hidden = true; };

/* ---------- trajectory player ---------- */
async function openPlayer(cellDirs) {
  const trajs = await Promise.all(cellDirs.map((d) =>
    J(`/api/trajectory/${encodeURIComponent(S.exp)}/${encodeURIComponent(d)}`).then((t) => ({ dir: d, t }))));
  S.player = { panes: trajs, i: 0, playing: false, timer: null };
  $("player").hidden = false;
  renderPlayer();
}
function renderPlayer() {
  const P = S.player; const maxN = Math.max(...P.panes.map((p) => p.t.steps.length), 1);
  $("pSlider").max = Math.max(0, maxN - 1);
  const p0 = P.panes[0].t;
  $("pHead").innerHTML = crumbs(p0) + verdict(p0);
  $("pPanes").innerHTML = P.panes.map((p, idx) => {
    const v = p.t.evaluation || {};
    return `<div class='pane'><div class='phh'>${esc(p.t.scaffold)} · <b class='mono'>${esc(prettyModel(p.t.model))}</b> · ${esc(prettyCond(p.t.condition))}
       <span class='${CLS[v.outcome]}'>${GLYPH[v.outcome] || ""}</span></div>
      <div class='shot'><img id='shot${idx}'></div>
      <div class='stepinfo'>
        <div class='lbl'>step</div><div id='stp${idx}'></div>
        <div class='lbl'>action</div><div class='act' id='act${idx}'></div>
        <div class='lbl'>reasoning</div><div class='rsn' id='rsn${idx}'></div>
        <div class='lbl'>url</div><div class='url' id='url${idx}'></div>
      </div></div>`;
  }).join("");
  $("pFilm").innerHTML = p0.steps.map((s, k) =>
    `<img data-k='${k}' src='/api/image/${encodeURIComponent(S.exp)}/${encodeURIComponent(P.panes[0].dir)}/${s.index}' loading='lazy'>`).join("");
  $("pFilm").querySelectorAll("img").forEach((im) => im.onclick = () => { P.i = +im.dataset.k; showStep(); });
  showStep();
}
function crumbs(t) {
  return `<span class='crumbs'><b>${esc(t.env)}</b><span class='sep'>›</span>${esc(t.scaffold)}
    <span class='sep'>›</span><b class='mono'>${esc(prettyModel(t.model))}</b><span class='sep'>›</span>${esc(prettyTask(t.task_id))}
    <span class='sep'>›</span>${esc(prettyCond(t.condition))}</span>`;
}
function verdict(t) {
  const v = t.evaluation || {}; const cl = CLS[v.outcome] || "c-none";
  const det = v.details || {};
  const extra = det.price_paid != null ? ` · paid $${det.price_paid}` : (det.total_price != null ? ` · $${det.total_price}` : "");
  return `<span class='verdict ${cl}'>${GLYPH[v.outcome] || esc(v.outcome)}</span>
    <span class="muted" style='font-size:13px'>${v.chosen_label ? "chose " + esc(v.chosen_label) : ""}${extra}</span>`;
}
function showStep() {
  const P = S.player; if (!P) return;
  P.panes.forEach((p, idx) => {
    const i = Math.min(P.i, p.t.steps.length - 1); const s = p.t.steps[i] || {};
    const img = $("shot" + idx);
    if (img) img.src = s.has_image ? `/api/image/${encodeURIComponent(S.exp)}/${encodeURIComponent(p.dir)}/${s.index}` : "";
    setTxt("stp" + idx, `${(i + 1)} / ${p.t.steps.length}`);
    setTxt("act" + idx, s.action || ""); setTxt("rsn" + idx, s.reasoning || ""); setTxt("url" + idx, s.url || "");
  });
  const n = Math.max(...P.panes.map((p) => p.t.steps.length), 1);
  $("pSlider").value = P.i; $("pPos").textContent = `${P.i + 1} / ${n}`;
  $("pFilm").querySelectorAll("img").forEach((im) => im.classList.toggle("on", +im.dataset.k === P.i));
}
function setTxt(id, t) { const e = $(id); if (e) e.textContent = t; }
function maxSteps() { return Math.max(...S.player.panes.map((p) => p.t.steps.length), 1); }
function go(d) { S.player.i = Math.max(0, Math.min(maxSteps() - 1, S.player.i + d)); showStep(); }
function stopPlay() { const P = S.player; if (P && P.timer) { clearInterval(P.timer); P.timer = null; $("pPlay").textContent = "▶ play"; } }
function bindPlayer() {
  $("pClose").onclick = () => { stopPlay(); $("player").hidden = true; S.player = null; };
  $("pNext").onclick = () => go(1); $("pPrev").onclick = () => go(-1);
  $("pFirst").onclick = () => { S.player.i = 0; showStep(); };
  $("pLast").onclick = () => { S.player.i = maxSteps() - 1; showStep(); };
  $("pSlider").oninput = (e) => { S.player.i = +e.target.value; showStep(); };
  $("pPlay").onclick = function () {
    const P = S.player; if (P.timer) { stopPlay(); return; }
    this.textContent = "⏸ pause";
    P.timer = setInterval(() => { if (P.i >= maxSteps() - 1) { stopPlay(); return; } go(1); }, 1100);
  };
  $("pCompare").onclick = () => {
    if (!S.player) return;
    S.compareFrom = S.player.panes[0].dir;
    $("pClose").click();
    $("count").textContent = "↳ click another cell to compare side-by-side";
  };
  document.onkeydown = (e) => {
    if (!$("lightbox").hidden) { lbKey(e); return; }
    if ($("player").hidden) return;
    if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1);
    else if (e.key === "Escape") $("pClose").click(); else if (e.key === " ") { e.preventDefault(); $("pPlay").click(); }
  };
}

/* ===================================================================
   BROWSE — launch live envs + steering sandbox (navigate yourself)
   =================================================================== */
let BENCH = null;
const ENV_ICON = { amazon: "🛒", airbnb: "🏠", doordash: "🍔", ebay: "🏷️", etsy: "🧶",
                   fiverr: "💼", instacart: "🥕", nike: "👟", stockx: "📈", zillow: "🏡" };
const ENV_ACCENT = { amazon: "#ff9900", airbnb: "#ff5a5f", doordash: "#ff3008", ebay: "#0064d2",
                     etsy: "#f56400", fiverr: "#1dbf73", instacart: "#43b02a", nike: "#111111",
                     stockx: "#006340", zillow: "#1277e1" };
async function browseInit() {
  await Promise.all([renderSandbox(), renderEnvGrid()]);
}
async function launch(body, btn, status) {
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = "launching…"; if (status) status.textContent = "";
  try {
    const res = await fetch("/api/launch", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then((r) => r.ok ? r.json() : r.json().then((j) => Promise.reject(j)));
    if (res.url) { window.open(res.url, "_blank"); if (status) status.textContent = "running ✓"; }
  } catch (err) { if (status) status.textContent = "failed: " + ((err && err.detail) || "error"); }
  btn.disabled = false; btn.textContent = orig;
}
async function renderSandbox() {
  const box = $("sandbox");
  try { BENCH = await J("/api/benchmark"); } catch (e) { box.hidden = true; return; }
  if (!BENCH || !(BENCH.scenarios || []).length) { box.hidden = true; return; }
  box.hidden = false;
  const scSel = $("sbScenario"), vSel = $("sbVariant"), cSel = $("sbCondition");
  scSel.innerHTML = BENCH.scenarios.map((s) => `<option value="${esc(s.id)}">${esc(s.id.replace(/_/g, " "))}</option>`).join("");
  cSel.innerHTML = BENCH.conditions.map((c) => `<option value="${esc(c)}">${c === "combined" ? "steered" : esc(c)}</option>`).join("");
  const fillVariants = () => {
    const s = BENCH.scenarios.find((x) => x.id === scSel.value);
    vSel.innerHTML = Object.keys(s.variants).map((v) =>
      `<option value="${esc(v)}">${esc(VARIANT_NAME[v] || v)}</option>`).join("");
  };
  const look = () => {
    const s = BENCH.scenarios.find((x) => x.id === scSel.value);
    const instr = s.variants[vSel.value] || "";
    const note = (BENCH.notes || {})[cSel.value] || "";
    const titles = (arr) => (arr || []).map((p) => esc(p.title)).join(" · ") || "—";
    const traps = (s.satisfice || []).concat(s.decoy || []);
    $("sbLook").innerHTML =
      `<div class="sb-task"><span class="sb-k">your task (${esc(VARIANT_NAME[vSel.value] || vSel.value)})</span>${esc(instr)}</div>` +
      `<div class="sb-hint"><span class="c-compliant">✓ faithful (the genuine optimum)</span> ${titles(s.compliant)}</div>` +
      `<div class="sb-hint"><span class="c-decoy">⚠ promoted traps</span> ${titles(traps)}</div>` +
      `<div class="sb-note"><span class="sb-k">steering “${esc(cSel.value)}”</span>${esc(note)}</div>`;
    const pf = (BENCH.scenario_prefix || SCEN_PREFIX)[scSel.value];
    $("sbFigs").style.display = pf ? "" : "none";
    $("sbFigs").dataset.prefix = pf || "";
  };
  scSel.onchange = () => { fillVariants(); look(); };
  vSel.onchange = look; cSel.onchange = look;
  fillVariants(); look();
  $("sbOpen").onclick = (ev) =>
    launch({ env: "amazon", catalog: scSel.value, condition: cSel.value }, ev.target, $("sbStatus"));
  $("sbFigs").onclick = () => { const p = $("sbFigs").dataset.prefix; if (p) showView("figures", { product: p }); };
}
async function renderEnvGrid() {
  const grid = $("envGrid");
  grid.textContent = "loading…";
  let envs = [];
  try { envs = await J("/api/envs"); } catch (e) { grid.textContent = "could not load envs"; return; }
  grid.innerHTML = "";
  for (const e of envs) {
    const running = e.running && !e.catalog;
    const card = document.createElement("div");
    card.className = "env-card";
    card.style.setProperty("--accent", ENV_ACCENT[e.env] || "var(--blue)");
    card.innerHTML =
      `<div class="env-top"><span class="env-ico">${ENV_ICON[e.env] || "🌐"}</span>
        <span class="env-name">${esc(e.env)}</span>
        <span class="env-dot ${running ? "on" : ""}" title="${running ? "running" : "stopped"}"></span></div>
       <div class="env-actions">
         <div class="seg env-cond"><button class="on" data-c="clean">clean</button><button data-c="steered">steered</button></div>
         <button class="primary env-open">Open ↗</button>
       </div>
       <div class="status env-status">${running ? "running · " + esc(e.condition || "") : ""}</div>`;
    const seg = card.querySelector(".env-cond");
    seg.querySelectorAll("button").forEach((b) => b.onclick = () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.remove("on")); b.classList.add("on");
    });
    if (e.condition && running) {
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.c === e.condition));
    }
    card.querySelector(".env-open").onclick = (ev) => {
      const cond = seg.querySelector("button.on").dataset.c;
      launch({ env: e.env, condition: cond }, ev.target, card.querySelector(".env-status"));
    };
    grid.appendChild(card);
  }
}

/* ===================================================================
   FIGURES — results-figure gallery + lightbox
   =================================================================== */
let FIGS = null, figMode = "product", figSel = null, lbItems = [], lbI = 0;
async function figuresInit(forceProduct) {
  if (!FIGS) {
    try { FIGS = await J("/api/figures"); } catch (e) { $("figGrid").textContent = "could not load figures"; return; }
    // report link
    const rep = (FIGS.reports || []).includes("five_product_findings_FINAL.md")
      ? "five_product_findings_FINAL.md" : (FIGS.reports || [])[0];
    if (rep) {
      $("figReportLink").textContent = "📄 open full report";
      $("figReportLink").onclick = () => window.open("/api/report/" + encodeURIComponent(rep), "_blank");
    }
    $("figMode").querySelectorAll("button").forEach((b) => b.onclick = () => {
      figMode = b.dataset.mode; figSel = null;
      $("figMode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      renderFigPills(); renderFigGrid();
    });
    bindLightbox();
  }
  if (forceProduct) { figMode = "product"; figSel = forceProduct;
    $("figMode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.mode === "product")); }
  renderFigPills(); renderFigGrid();
}
function figProducts() { return FIGS.products; }
function renderFigPills() {
  const row = $("figPills");
  if (figMode === "product") {
    const prods = figProducts();
    if (!figSel || !prods.find((p) => p.prefix === figSel))
      figSel = (prods.find((p) => p.prefix === "agg") || prods[0] || {}).prefix;
    row.innerHTML = prods.map((p) =>
      `<button class="pill ${p.prefix === figSel ? "on" : ""}" data-v="${p.prefix}">${esc(p.name)}</button>`).join("");
  } else {
    if (!figSel || !FIGS.types.includes(figSel)) figSel = "headline";
    row.innerHTML = FIGS.types.map((t) =>
      `<button class="pill ${t === figSel ? "on" : ""}" data-v="${t}">${esc(t)}</button>`).join("");
  }
  row.querySelectorAll(".pill").forEach((b) => b.onclick = () => {
    figSel = b.dataset.v;
    row.querySelectorAll(".pill").forEach((x) => x.classList.toggle("on", x === b));
    renderFigGrid();
  });
}
function renderFigGrid() {
  const grid = $("figGrid"); const items = [];
  if (figMode === "product") {
    const p = figProducts().find((x) => x.prefix === figSel); if (!p) { grid.innerHTML = ""; return; }
    for (const t of p.types) items.push({ prefix: p.prefix, type: t, title: t, sub: FIGS.type_desc[t] || "" });
  } else {
    for (const p of figProducts()) if (p.types.includes(figSel))
      items.push({ prefix: p.prefix, type: figSel, title: p.name, sub: FIGS.type_desc[figSel] || "" });
  }
  lbItems = items;
  grid.innerHTML = items.map((it, i) =>
    `<figure class="fig-tile" data-i="${i}">
       <img loading="lazy" src="/api/figure/${it.prefix}/${it.type}">
       <figcaption><b>${esc(it.title)}</b><span>${esc(it.sub)}</span></figcaption>
     </figure>`).join("");
  grid.querySelectorAll(".fig-tile").forEach((el) => el.onclick = () => openLightbox(+el.dataset.i));
}
function openLightbox(i) {
  lbI = i; $("lightbox").hidden = false; showLb();
}
function showLb() {
  const it = lbItems[lbI]; if (!it) return;
  $("lbImg").src = `/api/figure/${it.prefix}/${it.type}`;
  $("lbCap").innerHTML = `<b>${esc(it.title)}</b> — ${esc(it.sub)} <span class="muted">(${lbI + 1}/${lbItems.length})</span>`;
}
function bindLightbox() {
  $("lbClose").onclick = () => { $("lightbox").hidden = true; };
  $("lbPrev").onclick = () => { lbI = (lbI - 1 + lbItems.length) % lbItems.length; showLb(); };
  $("lbNext").onclick = () => { lbI = (lbI + 1) % lbItems.length; showLb(); };
  $("lightbox").onclick = (e) => { if (e.target.id === "lightbox") $("lightbox").hidden = true; };
}
function lbKey(e) {
  if (e.key === "Escape") $("lightbox").hidden = true;
  else if (e.key === "ArrowRight") $("lbNext").click();
  else if (e.key === "ArrowLeft") $("lbPrev").click();
}

/* ===================== boot ===================== */
showView(location.hash.slice(1) || localStorage.getItem("aa-view") || "runs");
