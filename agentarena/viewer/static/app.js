"use strict";
const DIMS = ["env", "scaffold", "model", "task_id", "condition"];
const DIM_LABEL = {env: "env", scaffold: "scaffold", model: "model", task_id: "task", condition: "condition"};
const GLYPH = {compliant: "✓ faithful", decoy: "⚠ took the bait", violation: "○ violation",
               none: "– no purchase", error: "✗ error", skipped: "· skipped"};
const CLS = {compliant: "c-compliant", decoy: "c-decoy", violation: "c-violation",
             none: "c-none", error: "c-error", skipped: "c-skipped", success: "c-compliant"};

const S = {exp: null, data: null, filters: {}, rowDim: "model", colDim: "scaffold",
           split: true, player: null, compareFrom: null};
const $ = (id) => document.getElementById(id);
const uniq = (a) => [...new Set(a)];
const J = (u) => fetch(u).then((r) => r.json());

async function init() {
  const exps = await J("/api/experiments");
  const sel = $("exp");
  sel.innerHTML = exps.map((e) => `<option value="${e.name}">${e.name}  ·  ${e.kpis.n} runs</option>`).join("");
  sel.onchange = () => loadExp(sel.value);
  $("rowDim").innerHTML = $("colDim").innerHTML = DIMS.map((d) => `<option value="${d}">${DIM_LABEL[d]}</option>`).join("");
  $("rowDim").value = S.rowDim; $("colDim").value = S.colDim;
  $("rowDim").onchange = () => { S.rowDim = $("rowDim").value; render(); };
  $("colDim").onchange = () => { S.colDim = $("colDim").value; render(); };
  $("splitCond").checked = S.split;
  $("splitCond").onchange = () => { S.split = $("splitCond").checked; render(); };
  bindPlayer();
  if (exps.length) loadExp(exps[0].name);
  else $("pivot").innerHTML = '<div class="empty-state">No experiments under results/ yet.<br>Run one with <code>agentarena run …</code></div>';
}

async function loadExp(name) {
  S.exp = name;
  S.data = await J("/api/experiments/" + encodeURIComponent(name));
  S.filters = {};
  for (const d of DIMS) S.filters[d] = new Set(uniq(S.data.cells.map((c) => String(c[d]))));
  // sensible default axes: condition becomes the column split, so keep it off the
  // row/col axes; prefer model on rows and scaffold on columns.
  const nval = (d) => uniq(S.data.cells.map((c) => c[d])).length;
  const cand = DIMS.filter((d) => d !== "condition").sort((a, b) => nval(b) - nval(a));
  S.rowDim = nval("model") > 1 ? "model" : (cand[0] || "model");
  S.colDim = (nval("scaffold") > 1 && S.rowDim !== "scaffold") ? "scaffold"
    : (cand.find((d) => d !== S.rowDim) || (S.rowDim === "model" ? "scaffold" : "model"));
  S.split = nval("condition") > 1;
  $("rowDim").value = S.rowDim; $("colDim").value = S.colDim; $("splitCond").checked = S.split;
  buildSidebar();
  render();
}

function buildSidebar() {
  const sb = $("sidebar"); sb.innerHTML = "";
  for (const d of DIMS) {
    const vals = uniq(S.data.cells.map((c) => String(c[d]))).sort();
    if (vals.length <= 1 && d !== "condition") continue;
    const f = document.createElement("div"); f.className = "facet";
    f.innerHTML = `<h4>${DIM_LABEL[d]}</h4>` + vals.map((v) =>
      `<label><input type="checkbox" data-d="${d}" value="${v}" ${S.filters[d].has(v) ? "checked" : ""}>
       <span class="${d === "model" || d === "scaffold" ? "mono" : ""}">${v}</span></label>`).join("");
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
  renderPivot(cells);
}

function renderKPIs(cells) {
  const n = cells.length || 1;
  const pct = (f) => Math.round(100 * cells.filter(f).length / n);
  const done = cells.filter((c) => !["error", "skipped"].includes(c.outcome));
  $("kpis").innerHTML = [
    ["faithful", pct((c) => c.success), "c-compliant"],
    ["took bait", pct((c) => c.took_bait), "c-decoy"],
    ["completed", Math.round(100 * done.filter((c) => c.outcome !== "none").length / n), ""],
    ["runs", cells.length, ""],
  ].map(([k, v, cl]) => `<span class="kpi">${k} <b class="${cl}">${v}${k === "runs" ? "" : "%"}</b></span>`).join("");
}

function renderTaskCard(cells) {
  const tc = $("taskcard");
  const tasks = uniq(cells.map((c) => c.task_id));
  const t = tasks.length === 1 && (S.data.manifest.tasks || []).find((x) => x.task_id === tasks[0]);
  if (!t) { tc.className = "taskcard"; return; }
  tc.className = "taskcard show";
  tc.innerHTML = `<div class="ti">${t.env} · ${t.task_id} — user instruction</div>
    <div class="instr">${t.instruction}</div>
    <div class="prefs">${Object.entries(t.preferences || {}).map(([k, v]) =>
      `<span class="pref">${k} = ${JSON.stringify(v)}</span>`).join("")}</div>`;
}

function colKeys(cells) {
  const base = uniq(cells.map((c) => String(c[S.colDim]))).sort();
  if (!S.split || S.colDim === "condition") return base.map((b) => ({label: b, val: b, cond: null}));
  const conds = uniq(cells.map((c) => String(c.condition))).sort();
  const out = [];
  for (const b of base) for (const cd of conds) out.push({label: b, val: b, cond: cd, grp: b, sub: cd});
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
      return `<th class='grp' colspan='${span}'>${g}</th>`;
    }).join("") + "</tr>";
    html += "<tr><th class='rowhead'>" + DIM_LABEL[S.rowDim] + "</th>" +
      cols.map((c) => `<th>${c.sub}</th>`).join("") + "</tr>";
  } else {
    html += "<tr><th class='rowhead'>" + DIM_LABEL[S.rowDim] + " \\ " + DIM_LABEL[S.colDim] + "</th>" +
      cols.map((c) => `<th>${c.label}</th>`).join("") + "</tr>";
  }
  for (const r of rows) {
    html += `<tr><td class='rowhead'>${r}</td>`;
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
    const sub = (c.chosen_label || c.chosen || "") + (c.num_steps != null ? ` · ${c.num_steps} steps` : "");
    return `<div class='cell' data-cell='${c.cell}'><div class='oc ${CLS[c.outcome] || ""}'>${GLYPH[c.outcome] || c.outcome}</div>
      <div class='sub' title='${esc(sub)}'>${esc(sub)}</div></div>`;
  }
  const ok = match.filter((c) => c.success).length;
  const ids = match.map((c) => c.cell).join("||");
  return `<div class='cell' data-cell='${ids}'><div class='oc c-success'>✓ ${ok}/${match.length}</div>
    <div class='sub'>${match.length} runs · click to drill</div>
    <div class='bar'><i style='width:${Math.round(100 * ok / match.length)}%'></i></div></div>`;
}

function onCellClick(cellDirs) {
  if (S.compareFrom) { openPlayer([S.compareFrom, cellDirs[0]]); S.compareFrom = null; return; }
  if (cellDirs.length === 1) openPlayer([cellDirs[0]]);
  else drill(cellDirs);
}

function esc(s) { return String(s).replace(/[<>&"]/g, (m) => ({"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;"}[m])); }

/* ---------- drill-down ---------- */
function drill(cellDirs) {
  const card = $("drillCard");
  const cells = cellDirs.map((d) => S.data.cells.find((c) => c.cell === d)).filter(Boolean);
  card.innerHTML = cells.map((c) =>
    `<div class='drill-row' data-cell='${c.cell}'><span class='dot bg-${c.outcome}'></span>
     <span class='mono' style='flex:1'>${c.scaffold} · ${c.model} · ${c.condition}</span>
     <span class='${CLS[c.outcome]}'>${GLYPH[c.outcome] || c.outcome}</span></div>`).join("");
  card.querySelectorAll(".drill-row").forEach((el) => el.onclick = () => { $("drill").hidden = true; openPlayer([el.dataset.cell]); });
  $("drill").hidden = false;
}
$("drill") && ($("drill").onclick = (e) => { if (e.target.id === "drill") $("drill").hidden = true; });

/* ---------- player ---------- */
async function openPlayer(cellDirs) {
  const trajs = await Promise.all(cellDirs.map((d) =>
    J(`/api/trajectory/${encodeURIComponent(S.exp)}/${encodeURIComponent(d)}`).then((t) => ({dir: d, t}))));
  S.player = {panes: trajs, i: 0, playing: false, timer: null};
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
    return `<div class='pane'><div class='phh'>${p.t.scaffold} · <b class='mono'>${p.t.model}</b> · ${p.t.condition}
       <span class='${CLS[v.outcome]}'>${GLYPH[v.outcome] || ""}</span></div>
      <div class='shot'><img id='shot${idx}'></div>
      <div class='stepinfo'>
        <div class='lbl'>step</div><div id='stp${idx}'></div>
        <div class='lbl'>action</div><div class='act' id='act${idx}'></div>
        <div class='lbl'>reasoning</div><div class='rsn' id='rsn${idx}'></div>
        <div class='lbl'>url</div><div class='url' id='url${idx}'></div>
      </div></div>`;
  }).join("");
  // filmstrip from pane 0
  $("pFilm").innerHTML = p0.steps.map((s, k) =>
    `<img data-k='${k}' src='/api/image/${encodeURIComponent(S.exp)}/${encodeURIComponent(P.panes[0].dir)}/${s.index}' loading='lazy'>`).join("");
  $("pFilm").querySelectorAll("img").forEach((im) => im.onclick = () => { P.i = +im.dataset.k; showStep(); });
  showStep();
}
function crumbs(t) {
  return `<span class='crumbs'><b>${t.env}</b><span class='sep'>›</span>${t.scaffold}
    <span class='sep'>›</span><b class='mono'>${t.model}</b><span class='sep'>›</span>${t.task_id}
    <span class='sep'>›</span>${t.condition}</span>`;
}
function verdict(t) {
  const v = t.evaluation || {}; const cl = CLS[v.outcome] || "c-none";
  const det = v.details || {};
  const extra = det.price_paid != null ? ` · paid $${det.price_paid}` : (det.total_price != null ? ` · $${det.total_price}` : "");
  return `<span class='verdict ${cl}'>${GLYPH[v.outcome] || v.outcome}</span>
    <span style='color:var(--dim);font-size:13px'>${v.chosen_label ? "chose " + esc(v.chosen_label) : ""}${extra}</span>`;
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
    if ($("player").hidden) return;
    if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1);
    else if (e.key === "Escape") $("pClose").click(); else if (e.key === " ") { e.preventDefault(); $("pPlay").click(); }
  };
}

init();
