/* Case Interviewer UI: hash-routed single page, no build step. Style: editorial-greeting (see styles.css). */
"use strict";

const TYPES = [
  ["rca", "RCA"], ["product_design", "Product Design"], ["product_strategy", "Product Strategy"],
  ["situational", "Situational"], ["metrics", "Metrics"], ["guesstimate", "Guesstimate"],
];
const TYPE_NAME = Object.fromEntries(TYPES);
const LEVEL_NAME = { apm: "APM", pm: "PM", senior: "Senior PM" };
const LEVEL_SHIFT = { apm: -0.25, pm: 0, senior: 0.25 };       // matches rubrics.LEVELS
const BANDS = ["No hire", "Lean no hire", "Lean hire", "Hire", "Strong hire"];

const $app = document.getElementById("app");
const store = {
  get(k, d) { try { const v = localStorage.getItem("ci." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ci." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------- helpers ----------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = (strings, ...vals) => strings.reduce((a, s, i) => a + s + (i < vals.length ? vals[i] : ""), "");
const sim = () => (document.getElementById("includeSim").checked ? "include_sim=true" : "");
const level = () => store.get("level", "pm");
const fmtDate = t => t ? new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const fmtDay = t => {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  const days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return days === 0 ? `Today, ${time}` : days === 1 ? `Yesterday, ${time}` : d.toLocaleDateString([], { day: "numeric", month: "short" }) + `, ${time}`;
};
const fmtScore = s => (s === null || s === undefined) ? "–" : Number(s).toFixed(2);
const dash = `<span class="dash">–</span>`;
const vclass = v => "v-" + (v ? v.toLowerCase().replace(/\s+/g, "-") : "none");
const pill = v => `<span class="pill ${vclass(v)}">${esc(v || "Not graded")}</span>`;
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
/** Band edges for a level: [1, no|lean-no, lean-no|lean, lean|hire, hire|strong, 4] */
const edges = lv => { const s = LEVEL_SHIFT[lv] ?? 0; return [1, 2 + s, 2.5 + s, 3 + s, 3.5 + s, 4]; };
function band(s, lv = level()) {
  if (s === null || s === undefined) return null;
  const e = edges(lv);
  return s >= e[4] ? "Strong hire" : s >= e[3] ? "Hire" : s >= e[2] ? "Lean hire" : s >= e[1] ? "Lean no hire" : "No hire";
}
const pos = s => Math.max(0, Math.min(100, (s - 1) / 3 * 100));   // score 1..4 -> % along a track

async function api(url, opts = {}) {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}
const post = (url, body = {}) => api(url, { method: "POST", body });

async function streamEvents(url, body, onEvent) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

function toast(msg, ms = 3200) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.hidden = false;
  requestAnimationFrame(() => t.classList.add("show"));
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

/** Render an exhibit body: lines starting with "|" become a table, the rest paragraphs. */
function renderExhibit(body) {
  const lines = String(body).split("\n");
  let out = "", rows = [];
  const flush = () => {
    if (!rows.length) return;
    const cells = rows.map(r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
    out += "<table>" + cells.map((r, i) => "<tr>" + r.map(c => i === 0 ? `<th>${esc(c)}</th>` : `<td>${esc(c)}</td>`).join("") + "</tr>").join("") + "</table>";
    rows = [];
  };
  for (const raw of lines) {
    const l = raw.trim().replace(/^Exhibit \d+\s*[-–:]?\s*[^|]*?(?:\):|:)\s*$/, "");   // the heading already names it
    if (l.startsWith("|")) { rows.push(l); continue; }
    flush();
    if (l) out += `<p>${esc(l)}</p>`;
  }
  flush();
  return out;
}

const segHtml = (id, opts, on) => `<div class="seg" id="${id}" role="group">${opts.map(([k, n]) =>
  `<button type="button" data-k="${k}" class="${k === on ? "on" : ""}" aria-pressed="${k === on}">${esc(n)}</button>`).join("")}</div>`;
function bindSeg(id, onPick) {
  document.querySelectorAll(`#${id} button`).forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(`#${id} button`).forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
    onPick(b.dataset.k);
  }));
}

// ---------- practice set (persisted) ----------
const PracticeSet = {
  get() { return store.get("set", null); },
  start(ids, lv, voice) { store.set("set", { ids, i: 0, level: lv, voice, sessions: [] }); },
  clear() { store.set("set", null); },
  advance(sessionId) {
    const s = this.get(); if (!s) return null;
    if (sessionId) s.sessions.push(sessionId);
    s.i += 1; store.set("set", s);
    return s.i < s.ids.length ? s.ids[s.i] : null;
  },
};

// ---------- shell: grading queue, level, menu ----------
async function pollJobs() {
  try {
    const jobs = await api("/api/jobs");
    const active = jobs.filter(j => ["queued", "waiting", "grading"].includes(j.status));
    const el = document.getElementById("queue");
    if (active.length) {
      const g = active.filter(j => j.status === "grading").length;
      const w = active.length - g;
      el.innerHTML = (g ? `<b>Grading</b> ${esc(active.find(j => j.status === "grading").id.split("_").pop())}` : "") +
        (g && w ? " · " : "") + (w ? `${w} waiting` : "") +
        `<div class="muted">${w && !g ? "Starts when no interview is running" : "About 5 to 9 minutes each"}</div>`;
      el.hidden = false;
      el.onclick = () => (location.hash = "#/report/" + active[0].id);
    } else el.hidden = true;
    pollJobs.active = active.length;
  } catch { /* server restarting */ }
}
setInterval(pollJobs, 4000);

async function checkHealth() {
  const b = document.getElementById("banner");
  try {
    const hs = await api("/api/health");
    if (!hs.ollama) { b.textContent = "Ollama isn't running. Start the Ollama app, then reload."; b.hidden = false; }
    else if (hs.missing_models.length) { b.textContent = "Missing Ollama models: " + hs.missing_models.join(", ") + ". Pull them before practising."; b.hidden = false; }
    else b.hidden = true;
  } catch { b.textContent = "Can't reach the server."; b.hidden = false; }
}

const levelPick = document.getElementById("levelPick");
levelPick.value = level();
levelPick.addEventListener("change", () => { store.set("level", levelPick.value); if (!live) route(); });
const menuBtn = document.getElementById("menuBtn");
const sidebar = document.getElementById("sidebar");
menuBtn.addEventListener("click", () => {
  const open = !sidebar.classList.contains("open");
  sidebar.classList.toggle("open", open);
  menuBtn.setAttribute("aria-expanded", open);
  menuBtn.textContent = open ? "Close" : "Menu";
});

// ---------- router ----------
let live = null;          // the running interview, if any
let cleanup = [];

function route() {
  cleanup.forEach(f => f()); cleanup = [];
  sidebar.classList.remove("open"); menuBtn.setAttribute("aria-expanded", "false"); menuBtn.textContent = "Menu";
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(query || "");
  const nav = parts[0] || "home";
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active",
    a.dataset.nav === nav || (nav === "case" && a.dataset.nav === "cases") || (nav === "report" && a.dataset.nav === "history")));
  window.scrollTo(0, 0);
  const views = { home: viewHome, cases: viewCases, case: viewCase, interview: viewInterview,
                  report: viewReport, history: viewHistory, add: viewAdd, models: viewModels };
  (views[nav] || viewHome)(parts[1], params).catch(err => {
    $app.innerHTML = `<div class="column"><div class="empty" style="margin-top:48px">Something went wrong: ${esc(err.message)}</div></div>`;
  });
}
let ignoreNextHash = false;
window.addEventListener("hashchange", () => {
  if (ignoreNextHash) { ignoreNextHash = false; return; }
  if (live && !live.ended && !location.hash.startsWith("#/interview")) {
    if (!confirm("Leave this interview? It won't be saved or graded.")) {
      ignoreNextHash = true;          // going back to #/interview must not re-render and wipe the transcript
      history.back(); return;
    }
    live = null;
  }
  route();
});
window.addEventListener("beforeunload", e => { if (live && !live.ended) { e.preventDefault(); e.returnValue = ""; } });
document.getElementById("includeSim").checked = store.get("includeSim", false);
document.getElementById("includeSim").addEventListener("change", e => { store.set("includeSim", e.target.checked); route(); });

/** First case of a type you haven't tried (casebook order), else the one tried longest ago. */
function nextCaseOf(cases, typeKey) {
  const pool = cases.filter(c => !typeKey || c.type_key === typeKey);
  return pool.find(c => !c.attempts) || pool[0];
}

// ---------- home ----------

async function viewHome() {
  const [types, sessions, cases] = await Promise.all([
    api("/api/types?" + sim()), api("/api/sessions?" + sim()), api("/api/cases?" + sim())]);
  document.getElementById("navCases").textContent = cases.length;
  const lv = level();
  const graded = sessions.filter(s => s.score !== null);
  const avg = graded.length ? graded.reduce((a, s) => a + s.score, 0) / graded.length : null;
  const tried = new Set(sessions.map(s => s.case_id)).size;
  const waiting = sessions.filter(s => ["queued", "waiting", "grading"].includes(s.status)).length;
  const gradedTypes = types.filter(t => t.avg !== null);
  const worst = gradedTypes.length ? [...gradedTypes].sort((a, b) => a.avg - b.avg)[0] : null;
  const hireEdge = edges(lv)[3];
  const set = PracticeSet.get();
  const setActive = set && set.i < set.ids.length;

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning." : hour < 17 ? "Good afternoon." : "Good evening.";
  let finding;
  if (setActive) finding = `You're ${set.i} of ${plural(set.ids.length, "case")} into a practice set. Next up: <b>${esc(set.ids[set.i])}</b>.`;
  else if (worst) {
    const wd = worst.weakest[0];
    finding = `<b>${esc(worst.name)}</b> is furthest from the ${LEVEL_NAME[lv]} hire bar, averaging ${fmtScore(worst.avg)} against ${hireEdge.toFixed(2)}.` +
      (wd ? ` Its weakest dimension is ${esc(wd.name.toLowerCase())}, at ${fmtScore(wd.avg)}.` : "");
  } else finding = `No graded sessions yet. ${cases.length} cases are ready, across six types; each type lands on the hire bar below once it's graded.`;
  const primary = setActive
    ? `<button class="btn primary" id="resumeSet">Continue set</button><button class="btn" id="dropSet">Discard set</button>`
    : worst ? `<button class="btn primary" data-practise="${worst.type_key}">Practise ${esc(worst.name)}</button><a class="btn" href="#/cases">Choose cases</a>`
            : `<a class="btn primary" href="#/cases">Choose cases</a>`;

  const scale = edges(lv);
  const bandW = i => ((scale[i + 1] - scale[i]) / 3 * 100).toFixed(3);
  const bandMid = i => pos((scale[i] + scale[i + 1]) / 2);
  const ladderRows = types.map(t => {
    const last = t.trend.length ? t.trend.at(-1).score : null;
    return h`<div class="lrow">
      <div class="lname"><b>${esc(t.name)}</b><span>${t.graded ? plural(t.graded, "session") : "Not graded yet"} · ${t.cases} cases</span></div>
      <div class="trackcell"><div class="track" role="img" aria-label="${esc(t.name)}: ${t.avg !== null ? "average " + fmtScore(t.avg) + ", " + band(t.avg, lv) : "no graded sessions"}">
        ${BANDS.map((_, i) => `<i class="b${i}" style="width:${bandW(i)}%"></i>`).join("")}
        ${last !== null && t.graded > 1 ? `<span class="tick" style="left:${pos(last)}%"></span>` : ""}
        ${t.avg !== null ? `<span class="dot" style="left:${pos(t.avg)}%"></span>` : ""}
      </div></div>
      <div class="ladder-value${t.avg === null ? " none" : ""}">${t.avg !== null ? fmtScore(t.avg) : "–"}${t.avg !== null ? `<small class="bandword">${band(t.avg, lv)}</small>` : ""}</div>
      <div class="go"><button class="textlink" data-practise="${t.type_key}">Practise</button></div>
    </div>`;
  }).join("");

  const next = nextCaseOf(cases, worst ? worst.type_key : null);
  const nextPanel = setActive ? h`
      <div class="panel-head"><h2>Practice set <span class="chip">${set.i + 1} of ${set.ids.length}</span></h2></div>
      <div class="rows">${set.ids.slice(set.i, set.i + 4).map((id, k) => {
        const c = cases.find(x => x.id === id) || { id, title: "", case_type: "" };
        return `<div class="rowcard" data-case="${esc(id)}" tabindex="0"><div class="who"><b>${esc(id)} · ${esc(c.title)}</b><span>${k === 0 ? "Next" : esc(c.case_type)}</span></div></div>`;
      }).join("")}</div>`
    : h`
      <div class="panel-head"><h2>Practise next</h2></div>
      ${next ? h`<div class="rowcard" data-case="${next.id}" tabindex="0"><div class="who"><b>${esc(next.id)} · ${esc(next.title)}</b>
        <span>${esc(next.case_type)} · ${esc(next.company)}</span></div></div>
        <p class="nextprompt">${esc(next.prompt)}</p>
        <p class="small muted" style="margin-top:12px">${worst ? `The first ${esc(worst.name)} case you haven't tried.` : "The first case in the casebook you haven't tried."} About ${next.time_box} minutes, ${esc(next.difficulty.toLowerCase())}.</p>
        <div class="facts" style="margin-top:12px"><span><b>${cases.filter(x => x.type_key === next.type_key && x.attempts).length}</b> of ${cases.filter(x => x.type_key === next.type_key).length} ${esc(next.case_type)} cases tried</span></div>` : ""}`;

  const recent = graded.slice(0, 5);
  const allDims = types.flatMap(t => t.weakest.map(d => ({ ...d, type: t.name }))).sort((a, b) => a.avg - b.avg).slice(0, 5);

  $app.innerHTML = h`<div class="column">
    <header class="greet">
      <div><h1>${greeting}</h1><p class="finding">${finding}</p></div>
      <div class="actions">${primary}</div>
    </header>

    <div class="grid12">
      <div class="tile span-3"><div class="label">Sessions graded</div><div class="kpi-value">${graded.length}</div>
        <div class="caption">${sessions.length > graded.length ? `${sessions.length - graded.length} more saved, not graded` : "Practice sessions with a report"}</div></div>
      <div class="tile span-3"><div class="label">Average score</div><div class="kpi-value">${avg !== null ? `${fmtScore(avg)}<small> / 4</small>` : `<small>–</small>`}</div>
        <div class="caption">${avg !== null ? `${band(avg, lv)} on the ${LEVEL_NAME[lv]} bar` : "Weighted mean across reports"}</div></div>
      <div class="tile span-3"><div class="label">Cases tried</div><div class="kpi-value">${tried}<small> / ${cases.length}</small></div>
        <div class="caption">${cases.length - tried} still untried</div></div>
      <div class="tile span-3"><div class="label">Waiting to grade</div><div class="kpi-value">${waiting}</div>
        <div class="caption">${waiting ? "Grading runs between cases" : "Nothing in the queue"}</div></div>
    </div>

    <div class="grid12 stretch gapy">
      <section class="panel span-9">
        <div class="panel-head">
          <h2>Against the hire bar</h2>
          ${segHtml("ladderLevel", Object.entries(LEVEL_NAME), lv)}
        </div>
        <div class="ladder">
          <div class="lhead hide-sm">Case type</div>
          <div class="lhead scalecell"><div class="bandscale">${BANDS.map((b, i) => `<span style="left:${bandMid(i)}%">${b}</span>`).join("")}</div></div>
          <div class="lhead r hide-sm">Average</div><div class="lhead hide-sm"></div>
          ${ladderRows}
        </div>
        <div class="row-legend" style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:12px">
          <div class="legend"><span><i class="ldot"></i>Average</span><span><i class="ltick"></i>Latest session</span></div>
          <span class="small muted">Band edges ${scale.slice(1, 5).map(x => x.toFixed(2)).join(" · ")} at the ${LEVEL_NAME[lv]} bar</span>
        </div>
      </section>
      <section class="panel span-3" style="display:flex;flex-direction:column">${nextPanel}
        <div style="margin-top:auto;padding-top:16px">${setActive ? "" : next ? `<button class="btn primary" style="width:100%" data-start="${next.id}">Start ${esc(next.id)}</button>` : ""}</div>
      </section>
    </div>

    <div class="grid12 stretch gapy">
      <section class="panel span-6">
        <div class="panel-head"><h2>Recent reports <span class="chip">${graded.length}</span></h2><a class="textlink" href="#/history">View all</a></div>
        ${recent.length ? `<div class="rows">${recent.map(s => h`<div class="rowcard" data-session="${esc(s.id)}" tabindex="0">
            <div class="who"><b>${esc(s.case_id)} · ${esc(s.title)}</b><span>${esc(s.case_type)} · ${fmtDay(s.started)} · ${LEVEL_NAME[s.level] || s.level} bar</span></div>
            <div class="fig">${fmtScore(s.score)}<small>${esc(s.verdict)}</small></div></div>`).join("")}</div>`
          : `<div class="empty">No graded sessions yet. Reports land here about 5 to 9 minutes after each case.</div>`}
      </section>
      <section class="panel span-6">
        <div class="panel-head"><h2>Weakest dimensions</h2><span class="small muted">Average across reports</span></div>
        ${allDims.length ? `<div class="rows">${allDims.map(d => h`<div class="rowcard" data-type="${esc(types.find(t => t.name === d.type)?.type_key || "")}" tabindex="0">
            <div class="who"><b>${esc(d.name)}</b><span>${esc(d.type)} · ${plural(d.n, "report")}</span></div>
            <div class="fig">${fmtScore(d.avg)}<small>${band(d.avg, lv)}</small></div></div>`).join("")}</div>`
          : `<div class="empty">Dimension scores appear after your first graded session.</div>`}
      </section>
    </div>
  </div>`;

  bindSeg("ladderLevel", k => { store.set("level", k); levelPick.value = k; route(); });
  $app.querySelectorAll("[data-practise]").forEach(b => b.addEventListener("click", () => {
    const c = nextCaseOf(cases, b.dataset.practise);
    if (c) location.hash = "#/case/" + c.id;
  }));
  $app.querySelectorAll("[data-start]").forEach(b => b.addEventListener("click", () => { PracticeSet.clear(); startInterview(b.dataset.start, lv, store.get("voice", false)); }));
  bindRowLinks();
  if (setActive) {
    document.getElementById("resumeSet").onclick = () => startInterview(set.ids[set.i], set.level, set.voice);
    document.getElementById("dropSet").onclick = () => { PracticeSet.clear(); route(); };
  }
}

function bindRowLinks() {
  $app.querySelectorAll("[data-session]").forEach(r => {
    const go = () => (location.hash = "#/report/" + r.dataset.session);
    r.addEventListener("click", go);
    r.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  });
  $app.querySelectorAll(".rowcard[data-case]").forEach(r => {
    const go = () => (location.hash = "#/case/" + r.dataset.case);
    r.addEventListener("click", go);
    r.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  });
  $app.querySelectorAll(".rowcard[data-type]").forEach(r => {
    const go = () => (location.hash = "#/cases?type=" + r.dataset.type);
    r.addEventListener("click", go);
    r.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  });
}

// ---------- shared session table ----------

const statusLabel = s => ({ queued: "Queued", waiting: "Waiting to grade", grading: "Grading", error: "Grading failed", ungraded: "Not graded" }[s] || s);
function sessionTable(sessions, showCase) {
  return h`<div class="tablewrap"><table class="list"><thead><tr>
      <th>When</th>${showCase ? "<th>Case</th>" : ""}<th class="opt">Bar</th><th>Verdict</th><th class="num">Score</th><th class="num opt">Minutes</th>
    </tr></thead><tbody>
    ${sessions.map(s => h`<tr class="click" data-session="${esc(s.id)}">
      <td class="num" style="text-align:left;white-space:nowrap">${fmtDay(s.started)}${s.source !== "practice" ? ` <span class="tag">test run</span>` : ""}</td>
      ${showCase ? `<td><div class="casetitle">${esc(s.case_id)} · ${esc(s.title)}</div><div class="casesub">${esc(s.case_type)}</div></td>` : ""}
      <td class="opt">${esc(LEVEL_NAME[s.level] || s.level)}</td>
      <td>${s.status === "graded" ? pill(s.verdict) : `<span class="pill">${esc(statusLabel(s.status))}</span>`}</td>
      <td class="num">${s.score !== null ? fmtScore(s.score) : dash}</td>
      <td class="num opt">${s.minutes ?? "–"}</td></tr>`).join("")}
    </tbody></table></div>`;
}

// ---------- cases ----------

async function viewCases(_, params) {
  const all = await api("/api/cases?" + sim());
  document.getElementById("navCases").textContent = all.length;
  let type = params.get("type") || "";
  let q = "";
  let freshOnly = false;
  const selected = new Set(store.get("selected", []).filter(id => all.some(c => c.id === id)));
  const voice = store.get("voice", false);
  const triedN = all.filter(c => c.attempts).length;

  $app.innerHTML = h`<div class="column">
    <div class="titlerow">
      <div><h1>Cases</h1><p class="sub">${all.length} cases across six types · ${triedN} tried · ${all.filter(c => c.custom).length} added by you</p></div>
      <div class="actions"><a class="btn" href="#/add">Add a case</a></div>
    </div>
    <div class="filters">
      ${segHtml("typeSeg", [["", "All"], ...TYPES], type)}
      <div class="right">
        <label class="switch" style="font-size:13px"><input type="checkbox" id="fresh"> Not tried</label>
        <input type="text" id="search" placeholder="Search title, company, prompt" aria-label="Search cases">
      </div>
    </div>
    <div id="caseTable"></div>
    <div class="selectbar" id="selectbar" aria-hidden="true">
      <b id="selCount"></b>
      <button class="btn small" id="selClear">Clear</button>
      <span class="spacer"></span>
      <label class="switch"><input type="checkbox" id="selVoice" ${voice ? "checked" : ""}> Voice</label>
      <button class="btn small" id="selRandom">Shuffle</button>
      <button class="btn primary" id="selStart">Start practice set</button>
    </div>
  </div>`;

  const draw = () => {
    const ql = q.toLowerCase();
    const rows = all.filter(c => (!type || c.type_key === type) && (!freshOnly || !c.attempts) &&
      (!ql || (c.title + " " + c.company + " " + c.prompt + " " + c.id).toLowerCase().includes(ql)));
    document.getElementById("caseTable").innerHTML = rows.length ? h`
      <div class="tablewrap"><table class="list"><thead><tr>
        <th style="width:28px"><input type="checkbox" id="selAll" aria-label="Select all shown"></th>
        <th>Case</th><th class="opt">Type</th><th class="opt">Difficulty</th><th class="num opt">Minutes</th><th class="num">Tries</th><th class="num">Best</th><th class="opt">Last verdict</th>
      </tr></thead><tbody>
      ${rows.map(c => h`<tr class="click" data-case="${c.id}">
        <td><input type="checkbox" data-sel="${c.id}" aria-label="Select ${esc(c.id)}" ${selected.has(c.id) ? "checked" : ""}></td>
        <td><div class="casetitle">${esc(c.id)} · ${esc(c.title)} ${c.custom ? `<span class="tag">added by you</span>` : ""}</div>
            <div class="casesub">${esc(c.company)}</div><div class="caseprompt">${esc(c.prompt)}</div></td>
        <td class="opt">${esc(c.case_type)}</td><td class="opt">${esc(c.difficulty)}</td>
        <td class="num opt">${c.time_box}</td><td class="num">${c.attempts || dash}</td>
        <td class="num">${c.best !== null ? fmtScore(c.best) : dash}</td><td class="opt">${c.last_verdict ? pill(c.last_verdict) : ""}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No cases match.</div>`;
    const selAll = document.getElementById("selAll");
    if (selAll) {
      selAll.checked = rows.length && rows.every(c => selected.has(c.id));
      selAll.onchange = () => { rows.forEach(c => selAll.checked ? selected.add(c.id) : selected.delete(c.id)); sync(); draw(); };
    }
    document.querySelectorAll("[data-sel]").forEach(cb => cb.addEventListener("change", () => {
      cb.checked ? selected.add(cb.dataset.sel) : selected.delete(cb.dataset.sel); sync();
    }));
    document.querySelectorAll("tr[data-case]").forEach(r => r.addEventListener("click", e => {
      if (e.target.closest("input")) return;
      location.hash = "#/case/" + r.dataset.case;
    }));
  };
  const sync = () => {
    store.set("selected", [...selected]);
    const bar = document.getElementById("selectbar");
    bar.classList.toggle("show", selected.size > 0);
    bar.setAttribute("aria-hidden", selected.size ? "false" : "true");
    document.getElementById("selCount").textContent = `${plural(selected.size, "case")} · ${LEVEL_NAME[level()]} bar`;
  };
  bindSeg("typeSeg", k => { type = k; history.replaceState(null, "", "#/cases" + (type ? "?type=" + type : "")); draw(); });
  document.getElementById("search").addEventListener("input", e => { q = e.target.value; draw(); });
  document.getElementById("fresh").addEventListener("change", e => { freshOnly = e.target.checked; draw(); });
  document.getElementById("selClear").onclick = () => { selected.clear(); sync(); draw(); };
  document.getElementById("selRandom").onclick = () => {
    const ids = [...selected].sort(() => Math.random() - .5); selected.clear(); ids.forEach(i => selected.add(i)); sync();
    toast("Order shuffled: " + ids.join(", "));
  };
  document.getElementById("selVoice").onchange = e => store.set("voice", e.target.checked);
  document.getElementById("selStart").onclick = () => {
    const ids = [...selected];
    const vo = document.getElementById("selVoice").checked;
    PracticeSet.start(ids, level(), vo);
    selected.clear(); sync();
    startInterview(ids[0], level(), vo);
  };
  draw(); sync();
}

async function viewCase(id) {
  const c = await api(`/api/cases/${id}?` + sim());
  const voice = store.get("voice", false);
  const lv = level();
  const graded = c.sessions.filter(s => s.score !== null);
  const best = graded.length ? Math.max(...graded.map(s => s.score)) : null;
  $app.innerHTML = h`<div class="column">
    <div class="titlerow">
      <div><nav class="crumb"><a href="#/cases">Cases</a><span>/</span><a href="#/cases?type=${c.type_key}">${esc(c.case_type)}</a></nav>
        <h1>${esc(c.id)} · ${esc(c.title)} ${c.custom ? `<span class="tag">added by you</span>` : ""}</h1>
        <p class="sub">${esc(c.case_type)} · ${esc(c.company)} · ${esc(c.difficulty)} · about ${c.time_box} minutes</p></div>
      <div class="actions">
        <label class="switch" style="font-size:13px"><input type="checkbox" id="vo" ${voice ? "checked" : ""}> Voice</label>
        <button class="btn primary" id="go">Start at the ${LEVEL_NAME[lv]} bar</button>
      </div>
    </div>
    <div class="grid12 stretch">
      <section class="panel span-8"><div class="panel-head"><h2>The prompt</h2></div>
        <p class="prompt">${esc(c.prompt)}</p>
        <div class="facts"><span><b>${c.n_clarifications}</b> prepared ${c.n_clarifications === 1 ? "answer" : "answers"}</span>
          <span><b>${c.n_exhibits}</b> ${c.n_exhibits === 1 ? "exhibit" : "exhibits"}</span>
          <span><b>${c.n_twists}</b> follow-up ${c.n_twists === 1 ? "twist" : "twists"}</span>
          <span>Data is handed over only when you ask for it</span></div>
      </section>
      <section class="panel span-4"><div class="panel-head"><h2>Your record</h2>${graded.length ? pill(band(graded[0].score, lv)) : ""}</div>
        <div class="kpis">
          <div class="kpi lead"><div class="kpi-label">Best</div><div class="kpi-value">${best !== null ? `${fmtScore(best)}<small> / 4</small>` : `<small>–</small>`}</div></div>
          <div class="kpi"><div class="kpi-label">Last</div><div class="kpi-value">${graded.length ? fmtScore(graded[0].score) : `<small>–</small>`}</div></div>
          <div class="kpi"><div class="kpi-label">Graded</div><div class="kpi-value">${graded.length}</div></div>
        </div>
        ${graded.length ? `<div class="track" style="margin-top:18px">${BANDS.map((_, i) => `<i class="b${i}" style="width:${((edges(lv)[i + 1] - edges(lv)[i]) / 3 * 100).toFixed(3)}%"></i>`).join("")}
          <span class="dot" style="left:${pos(best)}%"></span></div><p class="small muted" style="margin-top:8px">Best score on the ${LEVEL_NAME[lv]} hire bar</p>`
          : `<p class="small muted" style="margin-top:12px">Not graded yet.</p>`}
      </section>
    </div>
    <section class="gapy" style="margin-top:32px"><div class="panel-head"><h2>Sessions <span class="chip">${c.sessions.length}</span></h2></div>
      ${c.sessions.length ? sessionTable(c.sessions, false) : `<div class="empty">No sessions on this case yet.</div>`}
    </section>
    ${c.custom ? `<p style="margin-top:32px"><button class="btn danger small" id="del">Delete this case</button></p>` : ""}
  </div>`;
  document.getElementById("go").onclick = () => {
    const vo = document.getElementById("vo").checked;
    store.set("voice", vo);
    PracticeSet.clear();
    startInterview(c.id, level(), vo);
  };
  bindTableRows();
  const del = document.getElementById("del");
  if (del) del.onclick = async () => {
    if (!confirm(`Delete ${c.id} "${c.title}"? Its past sessions are kept.`)) return;
    await api(`/api/cases/${c.id}`, { method: "DELETE" });
    toast("Case deleted"); location.hash = "#/cases";
  };
}
function bindTableRows() {
  $app.querySelectorAll("tr[data-session]").forEach(r => r.addEventListener("click", () => (location.hash = "#/report/" + r.dataset.session)));
}

// ---------- interview ----------

const MIC = `<svg viewBox="0 0 18 18"><rect x="6.5" y="2" width="5" height="9" rx="2.5"/><path d="M3.5 8.5a5.5 5.5 0 0 0 11 0M9 14v2.5"/></svg>`;

async function startInterview(caseId, lv, voice) {
  $app.innerHTML = `<div class="centre-note"><h1>Setting up ${esc(caseId)}</h1><p class="muted">${voice ? "Loading the speech models and the interviewer." : "Loading the interviewer."}</p>
    <div style="margin-top:16px;color:var(--muted)"><span class="typing" aria-label="Loading"><i></i><i></i><i></i></span></div></div>`;
  try {
    const r = await post("/api/interviews", { case_id: caseId, level: lv, voice });
    live = { ...r, level: lv, startedAt: Date.now(), exhibits: [], ended: false, busy: false };
    if (voice && r.voice_error) toast("Voice unavailable, using text: " + r.voice_error, 6000);
    if (location.hash === "#/interview") route();
    else location.hash = "#/interview";
  } catch (e) {
    $app.innerHTML = `<div class="column"><div class="empty" style="margin-top:48px">Couldn't start the interview: ${esc(e.message)}
      <div class="actions" style="margin-top:16px;justify-content:center"><a class="btn" href="#/models">Open Models</a></div></div></div>`;
  }
}

async function viewInterview() {
  if (!live) { location.hash = "#/cases"; return; }
  const c = live.case;
  const set = PracticeSet.get();
  const inSet = set && set.ids[set.i] === c.id;
  $app.innerHTML = h`
    <div class="interview">
      <section class="panel chat">
        <div class="chat-head"><div><h1>${esc(c.id)} · ${esc(c.title)}</h1>
          <p class="small muted">${esc(c.case_type)} · ${esc(c.company)} · ${LEVEL_NAME[live.level]} bar${live.model ? ` · ${esc(live.model.label)}` : ""}${inSet ? ` · case ${set.i + 1} of ${set.ids.length}` : ""}</p></div>
          ${live.voice ? `<span class="tag">Voice on</span>` : ""}</div>
        <div class="transcript" id="tx" aria-live="polite"></div>
        <div class="composer">
          ${live.voice ? `<button class="mic" id="mic" aria-label="Talk">${MIC}</button>` : ""}
          <textarea id="say" aria-label="Your answer" placeholder="${live.voice ? "Click the mic to talk, or type." : "Type your answer."} Enter sends, Shift+Enter adds a line."></textarea>
          <button class="btn primary" id="send">Send</button>
        </div>
      </section>
      <aside class="side">
        <section class="panel">
          <div class="panel-head" style="margin-bottom:4px"><h2>Time</h2><span class="small muted">box ${c.time_box} min</span></div>
          <div class="timer" id="timer">0:00</div>
          <div class="bar" id="tbar"><i></i></div>
        </section>
        <section class="panel stack">
          <button class="btn" id="hint">Ask for a hint</button>
          <button class="btn primary" id="endGrade">End and grade</button>
          <button class="btn ghost" id="endSkip">End without grading</button>
          <p class="hint">Hints are logged and cost a little. After your recommendation the interviewer asks one follow-up twist, then closes the case.</p>
        </section>
        <section class="panel"><div class="panel-head"><h2>Exhibits <span class="chip" id="exCount">0</span></h2></div>
          <div id="exh"><p class="small muted">Data appears here when you ask for it.</p></div></section>
        <details class="panel"><summary>Case prompt</summary><p style="margin-top:10px;font-size:13px;line-height:19px">${esc(c.prompt)}</p></details>
      </aside>
    </div>`;

  const tx = document.getElementById("tx");
  const input = document.getElementById("say");
  const add = (cls, text, who) => {
    const el = document.createElement("div");
    el.className = "msg " + cls + (cls === "me" ? "" : " enter");
    el.innerHTML = (who ? `<div class="who">${esc(who)}</div>` : "") + `<span class="body">${esc(text)}</span>`;
    tx.appendChild(el); tx.scrollTop = tx.scrollHeight;
    return el;
  };
  add("iv", live.opening, "Interviewer");
  live.exhibits.forEach(e => addExhibit(e, false));
  function addExhibit(e, flash = true) {
    const box = document.getElementById("exh");
    if (!live.exhibits.some(x => x.exhibit_id === e.exhibit_id)) live.exhibits.push(e);
    if (box.querySelector("p.muted")) box.innerHTML = "";
    const el = document.createElement("div");
    el.className = "exhibit" + (flash ? " enter flash" : "");
    if (flash) setTimeout(() => el.classList.remove("flash"), 900);
    el.id = "ex-" + e.exhibit_id;
    el.innerHTML = `<h4>${esc(e.exhibit_id)} · ${esc(e.title)}</h4>` + renderExhibit(e.text);
    box.prepend(el);
    document.getElementById("exCount").textContent = live.exhibits.length;
    if (flash) {
      const chip = add("exh", `${e.exhibit_id} shared: ${e.title}`);
      chip.onclick = () => el.scrollIntoView({ behavior: "smooth" });
    }
  }

  const tick = () => {
    const secs = Math.floor((Date.now() - live.startedAt) / 1000);
    document.getElementById("timer").textContent = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`; // taste-skill-ignore: a clock
    const frac = secs / 60 / c.time_box;
    const bar = document.getElementById("tbar");
    bar.firstElementChild.style.transform = `scaleX(${Math.min(1, frac)})`;
    bar.className = "bar" + (frac >= 1 ? " over" : frac >= .8 ? " late" : "");
  };
  tick();
  const tId = setInterval(tick, 1000);
  cleanup.push(() => clearInterval(tId));

  const setBusy = b => {
    live.busy = b;
    ["send", "hint", "endGrade", "endSkip", "mic"].forEach(id => { const el = document.getElementById(id); if (el) el.disabled = b; });
  };

  async function run(url, body, mine) {
    if (live.busy || live.ended || live.micOpen) return;
    if (mine) add("me", mine, "You");
    setBusy(true);
    const bubble = add("iv", "", "Interviewer");
    const bodyEl = bubble.querySelector(".body");
    bodyEl.innerHTML = '<span class="typing" aria-label="Interviewer is typing"><i></i><i></i><i></i></span>';
    let started = false, done = false;
    try {
      await streamEvents(url, body, ev => {
        if (ev.kind === "exhibit") addExhibit(ev);
        else if (ev.kind === "say") {
          if (!started) { bodyEl.textContent = ""; started = true; }
          bodyEl.textContent += ev.text; tx.scrollTop = tx.scrollHeight;
        }
        else if (ev.kind === "error") add("sys", "Error: " + ev.text);
        else if (ev.kind === "status") done = ev.done;
      });
    } catch (e) { add("sys", "Error: " + e.message); }
    if (!started || !bodyEl.textContent.trim()) bubble.remove();
    else tx.appendChild(bubble);                // keep the reply after any exhibit chips
    tx.scrollTop = tx.scrollHeight;
    setBusy(false);
    if (done) { add("sys", "The interviewer has closed the case."); finish(true); }
  }
  const send = () => {
    const text = input.value.trim();
    if (!text || live.micOpen) return;      // typed text joins the spoken answer instead
    input.value = "";
    run(`/api/interviews/${live.iid}/say`, { text }, text);
  };
  document.getElementById("send").onclick = send;
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  document.getElementById("hint").onclick = () => run(`/api/interviews/${live.iid}/hint`, {}, null);
  document.getElementById("endGrade").onclick = () => finish(true);
  document.getElementById("endSkip").onclick = () => { if (confirm("End without grading? The transcript is still saved.")) finish(false); };

  const mic = document.getElementById("mic");
  if (mic) {
    let recording = false, pending = false;
    const idleHint = input.placeholder;
    // While the mic is open or Whisper is working, nothing else may send or speak.
    const micState = (state) => {
      recording = state === "rec";
      live.micOpen = state !== "idle";
      mic.classList.toggle("rec", recording);
      mic.setAttribute("aria-label", recording ? "Stop and send" : "Talk");
      mic.disabled = state === "wait";
      ["send", "hint"].forEach(id => { document.getElementById(id).disabled = state !== "idle"; });
      input.placeholder = recording ? "Listening… click the mic again when you're done." : state === "wait" ? "Transcribing…" : idleHint;
    };
    mic.onclick = async () => {
      if (pending || live.ended || (live.busy && !recording)) return;
      pending = true;                         // ignore double clicks while a request is in flight
      try {
        if (!recording) {
          await post(`/api/interviews/${live.iid}/listen/start`);
          micState("rec");
        } else {
          micState("wait");
          const r = await post(`/api/interviews/${live.iid}/listen/stop`);
          micState("idle");
          if (!r.text) { toast("Didn't catch that. Try again, a little closer to the mic."); return; }
          const text = (input.value.trim() + " " + r.text).trim();
          input.value = "";
          run(`/api/interviews/${live.iid}/say`, { text }, text);
        }
      } catch (e) { micState("idle"); toast(e.message); }
      finally { pending = false; }
    };
  }
  input.focus();

  async function finish(grade) {
    if (live.ended) return;
    live.ended = true;
    setBusy(true);
    let r;
    try { r = await post(`/api/interviews/${live.iid}/end`, { grade }); }
    catch (e) { toast("Couldn't end: " + e.message); live.ended = false; setBusy(false); return; }
    const caseId = c.id;
    live = null;
    const s = PracticeSet.get();
    if (s && s.ids[s.i] === caseId) {
      const next = PracticeSet.advance(r.session_id);
      if (next) { showBetween(r, next); return; }
      showSetDone(PracticeSet.get());
      PracticeSet.clear();
      return;
    }
    if (r.reason) toast(r.reason);
    location.hash = "#/report/" + r.session_id;
  }
}

function showBetween(r, nextId) {
  const s = PracticeSet.get();
  $app.innerHTML = h`<div class="centre-note">
      <h1>Case ${s.i} of ${s.ids.length} done.</h1>
      <p class="muted">${r.queued ? "It's queued for grading, which waits until you're between cases, so it won't slow the next interview." : esc(r.reason || "")}</p>
      <div class="actions">
        <button class="btn primary" id="next">Start ${esc(nextId)}</button>
        <a class="btn" href="#/report/${esc(r.session_id)}">See this report</a>
        <button class="btn ghost" id="stop">Stop the set</button>
      </div>
    </div>`;
  document.getElementById("next").onclick = () => startInterview(nextId, s.level, s.voice);
  document.getElementById("stop").onclick = () => { PracticeSet.clear(); location.hash = "#/history"; };
}

function showSetDone(s) {
  $app.innerHTML = h`<div class="centre-note">
      <h1>Practice set finished.</h1>
      <p class="muted">${plural(s.ids.length, "case")}. Each report appears as it finishes grading, about 5 to 9 minutes apiece.</p>
      <div class="rows" style="margin-top:20px;text-align:left">${s.sessions.map(id => `<div class="rowcard" data-session="${esc(id)}" tabindex="0"><div class="who"><b>${esc(id.split("_").pop())}</b><span>${esc(fmtDay(parseStamp(id)))}</span></div></div>`).join("")}</div>
      <div class="actions"><a class="btn primary" href="#/">Back home</a></div>
    </div>`;
  bindRowLinks();
}
function parseStamp(id) {
  const m = id.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() / 1000 : null;
}

// ---------- report ----------

async function viewReport(sid) {
  const s = await api("/api/sessions/" + encodeURIComponent(sid));
  const r = s.report;
  const job = s.job;
  const crumb = `<nav class="crumb"><a href="#/history">History</a><span>/</span><a href="#/case/${esc(s.case_id)}">${esc(s.case_id)} · ${esc(s.title)}</a></nav>`;
  if (!r) {
    const status = job ? job.status : "ungraded";
    $app.innerHTML = h`<div class="column">
      <div class="titlerow"><div>${crumb}<h1>${esc(s.title)}</h1><p class="sub">${fmtDay(s.started)} · ${LEVEL_NAME[s.level] || s.level} bar</p></div></div>
      <section class="panel">
        <div class="panel-head"><h2>${esc(statusLabel(status))}</h2>
          <span class="small muted">${status === "waiting" ? "Starts once no interview is running" : status === "grading" ? "Scoring one dimension at a time, about 5 to 9 minutes" : ""}</span></div>
        ${job && job.messages.length ? `<div class="progresslog">${esc(job.messages.join("\n"))}</div>` : ""}
        ${job && job.error ? `<p class="cap" style="margin-top:8px">${esc(job.error)}</p>` : ""}
        ${["ungraded", "error"].includes(status) ? `<div style="margin-top:8px">${gradeControls(s.level)}</div>` : ""}
      </section>
      ${transcriptBlock(s)}
    </div>`;
    bindGrade(s);
    if (job && ["queued", "waiting", "grading"].includes(job.status)) {
      const t = setInterval(async () => {
        const fresh = await api("/api/sessions/" + encodeURIComponent(sid)).catch(() => null);
        if (!fresh) return;
        if (fresh.report || (fresh.job && fresh.job.status !== status) ||
            (fresh.job && fresh.job.messages.length !== job.messages.length)) { clearInterval(t); route(); }
      }, 3000);
      cleanup.push(() => clearInterval(t));
    }
    return;
  }

  const co = r.coaching || {}, ct = r.content || {};
  const fired = (r.gates || []).filter(g => g.triggered);
  const disputed = (r.gates || []).filter(g => g.disputed);
  const badMath = (r.arithmetic || []).filter(a => a.error);
  const e = edges(r.level);
  $app.innerHTML = h`<div class="column">
    <div class="titlerow">
      <div>${crumb}<h1>${esc(s.title)}</h1>
        <p class="sub">${co.one_line_summary ? esc(co.one_line_summary) : `${esc(s.case_type)} · ${fmtDay(s.started)}`}</p></div>
      <div class="actions"><button class="btn primary" id="again">Practise again</button></div>
    </div>

    <div class="grid12 stretch">
      <section class="panel span-8">
        <div class="panel-head"><h2>Verdict</h2><span class="small muted">${esc(s.case_type)} · ${fmtDay(s.started)} · ${s.minutes} of ${s.time_box} min · ${LEVEL_NAME[r.level] || r.level} bar${s.source !== "practice" ? " · test run" : ""}${r.grader_model && !String(r.grader_model).startsWith("local:") ? " · graded by " + esc(String(r.grader_model).replace(/^or:/, "").split("/").pop()) : ""}${s.interviewer_model && !String(s.interviewer_model).startsWith("local:") ? " · interviewer " + esc(String(s.interviewer_model).replace(/^or:/, "").split("/").pop()) : ""}</span></div>
        <div class="verdict-row">
          <div class="kpi lead"><div class="kpi-label">Weighted score</div><div class="kpi-value">${fmtScore(r.weighted_mean)}<small> / 4</small></div></div>
          <div>${pill(r.verdict)}</div>
          <div class="kpi"><div class="kpi-label">Vs casebook answer</div><div class="kpi-value">${r.answer_quality ?? "–"}<small> / 4</small></div></div>
          <div class="kpi"><div class="kpi-label">Hints</div><div class="kpi-value">${s.hints ?? 0}</div></div>
          <div class="kpi"><div class="kpi-label">Answers unlocked</div><div class="kpi-value">${esc(r.stats?.clarifications_unlocked ?? "–")}</div></div>
          <div class="kpi"><div class="kpi-label">Exhibits</div><div class="kpi-value">${esc(r.stats?.exhibits_unlocked ?? "–")}</div></div>
        </div>
        <div class="track" style="margin-top:20px" role="img" aria-label="Weighted score ${fmtScore(r.weighted_mean)} on the hire bar">
          ${BANDS.map((_, i) => `<i class="b${i}" style="width:${((e[i + 1] - e[i]) / 3 * 100).toFixed(3)}%"></i>`).join("")}
          <span class="dot" style="left:${pos(r.weighted_mean)}%"></span></div>
      </section>
      <section class="panel span-4">
        <div class="panel-head"><h2>What capped it</h2><span class="chip">${fired.length + badMath.length}</span></div>
        ${fired.length || badMath.length ? `<div class="stack" style="display:flex;flex-direction:column;gap:10px">
          ${fired.map(g => `<p class="cap"><b>${esc(g.id.replace(/_/g, " "))}</b>, max ${esc(g.cap)}. ${esc(g.reason)}</p>`).join("")}
          ${badMath.map(a => `<p class="cap">Arithmetic: ${esc(a.text)} is off by ${esc(a.ratio)}x${a.corrected ? " (you corrected it)" : ""}.</p>`).join("")}</div>`
          : `<p class="muted">No gate fired and the arithmetic checked out, so the verdict comes straight from the weighted score.</p>`}
        <div class="reasons"><b style="color:var(--text);font-weight:500">How it was set</b>${(r.rollup?.reasons || []).map(x => `<div>${esc(x)}</div>`).join("")}
          ${r.rollup?.interventions ? `<div>Interviewer help: ${Object.entries(r.rollup.interventions).filter(([, n]) => n).map(([k, n]) => `${n} ${esc(k)}`).join(", ") || "none"}</div>` : ""}</div>
      </section>
    </div>

    <div class="grid12 stretch gapy">
      <section class="panel span-6"><div class="panel-head"><h2>Fix these first</h2></div>
        ${(co.fixes || []).map((f, i) => h`<div class="fix"><b>${i + 1}. ${esc(f.issue)}</b>
          <div class="small muted" style="margin-top:2px">${esc(f.why_it_matters)}</div>
          <div class="say">“${esc(f.say_this_instead)}”</div></div>`).join("") || `<p class="muted">No fixes listed.</p>`}
        ${co.practice_next ? `<p class="coach" style="margin-top:14px"><b>Practise next:</b> ${esc(co.practice_next)}</p>` : ""}
      </section>
      <section class="panel span-6"><div class="panel-head"><h2>Against the casebook answer</h2><span class="chip">${(ct.key_points_hit || []).length} of ${(ct.key_points_hit || []).length + (ct.key_points_missed || []).length}</span></div>
        ${ct.answer_comment ? `<p class="small muted" style="margin-bottom:8px">${esc(ct.answer_comment)}</p>` : ""}
        <ul class="clean">${(ct.key_points_hit || []).map(p => `<li class="hit">${esc(p)}</li>`).join("")}
          ${(ct.key_points_missed || []).map(p => `<li class="miss">${esc(p)}</li>`).join("")}</ul>
        ${(ct.pitfalls_fallen_into || []).length ? `<p class="cap" style="margin-top:10px">Pitfalls: ${esc(ct.pitfalls_fallen_into.join("; "))}</p>` : ""}
        ${ct.twist_score ? `<p class="coach" style="margin-top:10px"><b>Twist ${ct.twist_score}/4.</b> ${esc(ct.twist_comment || "")}</p>` : ""}
        ${(co.strengths || []).length ? `<div class="panel-head" style="margin:16px 0 6px"><h2>Strengths</h2></div><ul class="clean">${co.strengths.map(x => `<li class="hit">${esc(x)}</li>`).join("")}</ul>` : ""}
      </section>
    </div>

    <section class="panel gapy">
      <div class="panel-head"><h2>Dimensions${r.tradeoff_profile ? ` <span class="chip">trade-off weights</span>` : ""}</h2>
        <div class="legend"><span><i class="ldot" style="border-radius:3px;width:14px;height:6px"></i>Score</span><span><i class="ltick"></i>Hire bar, ${e[3].toFixed(2)}</span></div></div>
      ${(r.dimensions || []).map(d => dimRow(d, e)).join("")}
    </section>

    ${disputed.length ? h`<details class="panel gapy"><summary>Disputed gates, not applied (${disputed.length})</summary>
      ${disputed.map(g => `<p class="small muted" style="margin-top:8px">${esc(g.id.replace(/_/g, " "))}: ${esc(g.reason)}</p>`).join("")}</details>` : ""}
    ${transcriptBlock(s)}
    <div class="formfoot" style="margin-top:24px;justify-content:space-between">${gradeControls(r.level, "Re-grade")}
      <button class="btn danger small" id="delSession">Delete session</button></div>
  </div>`;
  bindGrade(s);
  document.getElementById("again").onclick = () => { PracticeSet.clear(); startInterview(s.case_id, r.level, store.get("voice", false)); };
  document.getElementById("delSession").onclick = async () => {
    if (!confirm("Delete this session and its report? This can't be undone.")) return;
    await api("/api/sessions/" + encodeURIComponent(s.id), { method: "DELETE" });
    toast("Session deleted"); location.hash = "#/case/" + s.case_id;
  };
}

function dimRow(d, e) {
  const low = d.score < e[2];
  return h`<div class="dim">
    <div class="dname">${esc(d.name)}<span>weight ${d.weight}</span></div>
    <div class="bullet" role="img" aria-label="${esc(d.name)} ${Number(d.score).toFixed(1)} of 4"><i style="width:${Math.max(2, pos(d.score))}%"></i><b style="left:${pos(e[3])}%"></b></div>
    <div class="dscore${low ? " low" : ""}">${Number(d.score).toFixed(1)}</div>
    <div class="dbody">${esc(d.reason)}
      ${(d.quotes || []).slice(0, 2).map(q => `<div class="quote">“${esc(q)}”</div>`).join("")}
      ${(d.caps || []).map(c => `<div class="cap">${esc(c)}</div>`).join("")}
      ${d.score < 3 && d.coach ? `<div class="coach"><b>Next time:</b> ${esc(d.coach)}</div>` : ""}</div>
  </div>`;
}

function transcriptBlock(s) {
  const turns = s.turns || [];
  const ex = s.exhibits || [];
  return h`<details class="panel gapy"><summary>Transcript, ${turns.length} turns${ex.length ? ` and ${plural(ex.length, "exhibit")}` : ""}</summary>
    <div class="transcript" style="margin-top:14px;padding:0;overflow:visible">
      ${turns.map(t => `<div class="msg ${t.speaker === "Candidate" ? "me" : "iv"}"><div class="who">${esc(t.speaker === "Candidate" ? "You" : "Interviewer")}</div>${esc(t.text)}</div>`).join("")}
    </div>
    ${ex.map(e => `<div class="exhibit" style="margin-top:10px"><h4>${esc(e.id)} · ${esc(e.title)}</h4>${renderExhibit(e.body)}</div>`).join("")}
  </details>`;
}

function gradeControls(lv, label = "Grade now") {
  return h`<div class="actions"><select id="gLevel" aria-label="Bar to grade against">${Object.entries(LEVEL_NAME).map(([k, n]) => `<option value="${k}" ${k === lv ? "selected" : ""}>${n} bar</option>`).join("")}</select>
    <button class="btn" id="gGo">${label}</button></div>`;
}
function bindGrade(s) {
  const b = document.getElementById("gGo");
  if (!b) return;
  b.onclick = async () => {
    await post(`/api/sessions/${encodeURIComponent(s.id)}/grade`, { level: document.getElementById("gLevel").value });
    toast("Queued for grading"); pollJobs(); route();
  };
}

// ---------- history ----------

async function viewHistory(_, params) {
  let type = params.get("type") || "";
  const [sessions, cases] = await Promise.all([api("/api/sessions?" + sim()), api("/api/cases")]);
  const typeOf = Object.fromEntries(cases.map(c => [c.id, c.type_key]));
  const graded = sessions.filter(s => s.score !== null).length;
  $app.innerHTML = h`<div class="column">
    <div class="titlerow"><div><h1>History</h1>
      <p class="sub">${plural(sessions.length, "session")} · ${graded} graded${sim() ? " · test runs included" : ""}</p></div></div>
    <div class="filters">${segHtml("hSeg", [["", "All"], ...TYPES], type)}</div>
    <div id="hTable"></div>
  </div>`;
  const draw = () => {
    const rows = sessions.filter(s => !type || typeOf[s.case_id] === type);
    document.getElementById("hTable").innerHTML = rows.length ? sessionTable(rows, true)
      : `<div class="empty">No sessions${type ? " of this type" : ""} yet.</div>`;
    bindTableRows();
  };
  bindSeg("hSeg", k => { type = k; draw(); });
  draw();
}

// ---------- add a case ----------

async function viewAdd() {
  $app.innerHTML = h`<div class="column">
    <div class="titlerow"><div><nav class="crumb"><a href="#/cases">Cases</a></nav><h1>Add a case</h1>
      <p class="sub">Saved to custom_cases/ with the next free ID for its type. The casebook files aren't changed.</p></div></div>
    <form id="f" novalidate>
      <div class="formrow"><div class="about"><h2>The case</h2><p>How the interviewer opens it, and what the case list shows.</p></div>
        <div class="fields">
          <div class="cols">
            <label class="field"><span>Title</span><input type="text" name="title" required placeholder="Cart Attack"></label>
            <label class="field"><span>Type</span><select name="type_key" style="width:100%">${TYPES.map(([k, n]) => `<option value="${k}">${n}</option>`).join("")}</select></label>
            <label class="field"><span>Company</span><input type="text" name="company" placeholder="Zepto"></label>
          </div>
          <label class="field"><span>Prompt</span><textarea name="prompt" rows="3" required placeholder="Zepto's cart abandonment rose 15% this week in Mumbai. Why?"></textarea></label>
          <div class="cols" style="grid-template-columns:1fr 1fr 2fr">
            <label class="field"><span>Difficulty</span><select name="difficulty" style="width:100%"><option>Easy</option><option selected>Medium</option><option>Hard</option></select></label>
            <label class="field"><span>Time box, minutes</span><input type="number" name="time_box" value="25" min="5" max="90"></label><span></span>
          </div>
        </div></div>
      <div class="formrow"><div class="about"><h2>Prepared answers</h2><p>What a strong candidate asks, and your answer as the interviewer. Answers are spoken word for word, so write them the way you'd say them. At least one.</p></div>
        <div class="fields"><div class="repeat" id="clar"></div><div><button type="button" class="btn small" data-addrow="clar">Add a question</button></div></div></div>
      <div class="formrow"><div class="about"><h2>Exhibits</h2><p>Data handed over when it's asked for. One per box; each table row on its own line starting with "|".</p></div>
        <div class="fields"><div class="repeat" id="exh"></div><div><button type="button" class="btn small" data-addrow="exh">Add an exhibit</button></div></div></div>
      <div class="formrow"><div class="about"><h2>Follow-up twists</h2><p>Asked after the recommendation. The strong response is what the grader scores against.</p></div>
        <div class="fields"><div class="repeat" id="tw"></div><div><button type="button" class="btn small" data-addrow="tw">Add a twist</button></div></div></div>
      <div class="formrow"><div class="about"><h2>Grader's key</h2><p>Only the grader sees these. The interviewer never does, so it can't leak them.</p></div>
        <div class="fields">
          <label class="field"><span>Model answer and recommendation</span><textarea name="answer" rows="5"></textarea></label>
          <label class="field"><span>Common pitfalls, one per line</span><textarea name="pitfalls" rows="4"></textarea></label>
          <label class="field"><span>Guardrail metrics</span><textarea name="guardrails" rows="3"></textarea></label>
          <label class="field"><span>Context the interviewer may confirm</span><textarea name="context" rows="2"></textarea></label>
        </div></div>
      <div class="formfoot"><span class="small" id="fmsg" role="alert"></span><a class="btn ghost" href="#/cases">Cancel</a><button class="btn primary" type="submit">Save case</button></div>
    </form>
  </div>`;
  const rowsHtml = {
    clar: () => `<div class="item"><input type="text" aria-label="Question" placeholder="Is it all of Mumbai, or some areas?"><input type="text" aria-label="Answer" placeholder="Mostly the western suburbs."><button type="button" class="btn small ghost" data-del aria-label="Remove">Remove</button></div>`,
    exh: () => `<div class="item one"><textarea rows="3" aria-label="Exhibit" placeholder="Funnel this week&#10;| Step | Last week | This week |&#10;| Cart to checkout | 62% | 51% |"></textarea><button type="button" class="btn small ghost" data-del aria-label="Remove">Remove</button></div>`,
    tw: () => `<div class="item"><input type="text" aria-label="Twist" placeholder="What if it were all of India?"><input type="text" aria-label="Strong response" placeholder="Then look for a national cause: an app release or a fee change."><button type="button" class="btn small ghost" data-del aria-label="Remove">Remove</button></div>`,
  };
  const addRow = k => {
    const box = document.getElementById(k);
    box.insertAdjacentHTML("beforeend", rowsHtml[k]());
    box.lastElementChild.querySelector("[data-del]").onclick = e => e.target.closest(".item").remove();
  };
  ["clar", "clar", "clar", "exh", "tw"].forEach(addRow);
  document.querySelectorAll("[data-addrow]").forEach(b => (b.onclick = () => addRow(b.dataset.addrow)));
  document.getElementById("f").onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const pairs = id => [...document.querySelectorAll(`#${id} .item`)].map(it => [...it.querySelectorAll("input,textarea")].map(x => x.value.trim()));
    const body = {
      title: f.get("title"), type_key: f.get("type_key"), company: f.get("company"), prompt: f.get("prompt"),
      difficulty: f.get("difficulty"), time_box: Number(f.get("time_box") || 25), context: f.get("context"),
      clarifications: pairs("clar").map(([q, a]) => ({ q, a })),
      exhibits: pairs("exh").map(([t]) => t).filter(Boolean),
      twists: pairs("tw").map(([twist, response]) => ({ twist, response })),
      answer: f.get("answer"), guardrails: f.get("guardrails"),
      pitfalls: String(f.get("pitfalls") || "").split("\n").map(s => s.trim()).filter(Boolean),
    };
    const msg = document.getElementById("fmsg");
    try {
      const r = await post("/api/cases", body);
      toast(`Saved as ${r.id}`);
      location.hash = "#/case/" + r.id;
    } catch (err) { msg.textContent = err.message; msg.className = "small cap"; }
  };
}

// ---------- models ----------

const money = n => "$" + Number(n || 0).toFixed(2);

async function viewModels() {
  const m = await api("/api/models");
  const groups = [...new Set(["Local", "OpenRouter", ...m.models.map(x => x.group)])].filter(g => m.models.some(x => x.group === g));
  const prov = Object.fromEntries(m.providers.map(p => [p.id, p]));
  const pct = m.monthly_budget > 0 ? Math.min(100, m.spent / m.monthly_budget * 100) : 100;
  const keyState = p => m.keys[p] === "environment" ? "Set from the environment" : m.keys[p] === "saved" ? "Saved on this computer" : "Not set";
  const keyRow = p => `
        <div class="keyrow"><label class="field"><span>${esc(p.label)} <em class="small muted">${keyState(p.id)}</em></span>
          ${p.key_problem ? `<span class="cap" style="display:block;margin:-4px 0 8px">The saved key doesn't look like an API key from ${esc(p.label)}, so calls will fail. Paste the key itself again.</span>` : ""}
          <input type="password" autocomplete="off" spellcheck="false" data-key="${p.id}" placeholder="${m.keys[p.id] ? "Paste a new key to replace it" : "Paste your API key"}"></label>
          ${p.base_url !== null ? `<label class="field"><span>Endpoint <em class="small muted">from your Model Studio workspace; blank uses the default</em></span>
            <input type="text" spellcheck="false" data-endpoint="${p.id}" value="${esc(p.base_url)}"></label>` : ""}
          <div class="actions"><button class="btn small" data-savekey="${p.id}">Save</button>${m.keys[p.id] === "saved" ? `<button class="btn small ghost" data-delkey="${p.id}">Remove</button>` : ""}</div></div>`;
  const row = x => {
    const cost = x.price ? `about ${money(x.per_interview)} an interview` : "Free";
    const left = x.price ? Math.floor(Math.max(0, m.monthly_budget - m.spent) / x.per_interview) : null;
    return `<label class="modelrow${x.available ? "" : " off"}">
      <input type="radio" name="model" value="${esc(x.id)}" ${x.id === m.interviewer_model ? "checked" : ""} ${x.available ? "" : "disabled"}>
      <div><b>${esc(x.label)}</b><span>${esc(x.note)}</span>
        ${x.available ? "" : `<span class="cap">Needs your ${esc((prov[x.provider] || {}).label || x.group)} API key (below).</span>`}${x.peak_now ? `<span class="cap">Peak pricing right now (double).</span>` : ""}</div>
      <div class="cost"><b>${cost}</b>${left !== null && x.available ? `<span>${left} left this month</span>` : ""}</div></label>`;
  };
  $app.innerHTML = h`<div class="column">
    <div class="titlerow"><div><h1>Models</h1>
      <p class="sub">Which model plays the interviewer, and how much the API models may spend.</p></div></div>
    <div class="formrow"><div class="about"><h2>Interviewer</h2><p>Local models are free and run offline, but they follow a scripted pipeline. API models follow the conversation like a real interviewer. None of them ever sees the answer key.</p></div>
      <div class="fields" id="modelPick">${groups.map(g => `<div class="modelgroup"><h3>${g}</h3>${m.models.filter(x => x.group === g).map(row).join("")}</div>`).join("")}</div></div>
    <div class="formrow"><div class="about"><h2>Monthly budget</h2><p>A hard cap across all API models. Every API call is checked before it's sent; when the cap is reached, the interview stops and is still graded locally.</p></div>
      <div class="fields">
        <div><div class="budgetline"><b>${money(m.spent)}</b><span>of ${money(m.monthly_budget)} spent in ${esc(m.month)}</span></div>
          <div class="bar ${pct >= 100 ? "over" : pct >= 80 ? "late" : ""}"><i style="transform:scaleX(${pct / 100})"></i></div>
          ${Object.keys(m.by_model).length ? `<p class="small muted" style="margin-top:8px">${Object.entries(m.by_model).map(([k, v]) => `${esc(k)} ${money(v)}`).join(" · ")}</p>` : ""}</div>
        <label class="field" style="max-width:220px"><span>Cap per month, USD</span><input type="number" id="budget" min="0" max="1000" step="1" value="${m.monthly_budget}"></label>
        <div><button class="btn small" id="saveBudget">Save cap</button></div>
      </div></div>
    <div class="formrow"><div class="about"><h2>API keys</h2><p>Stored only on this computer (~/.config/case_interviewer/keys.json, readable by you alone) and sent only to their own provider. A key set in the environment takes priority. Set a spending limit in each provider's console too, as a backstop.</p></div>
      <div class="fields">${m.providers.map(keyRow).join("")}
      </div></div>
    <div class="formrow"><div class="about"><h2>Grader</h2><p>Who writes your report after each case. Only models that return per-token probabilities are offered, since the scores are built on them. API grading counts against the same monthly cap, and falls back to local if the cap can't cover it.</p></div>
      <div class="fields" id="graderPick"><div class="modelgroup">${m.graders.map(g => `<label class="modelrow${g.available ? "" : " off"}">
        <input type="radio" name="grader" value="${esc(g.id)}" ${g.id === m.grader_model ? "checked" : ""} ${g.available ? "" : "disabled"}>
        <div><b>${esc(g.label)}</b><span>${esc(g.note)}</span>${g.available ? "" : `<span class="cap">Needs your API key (below).</span>`}</div>
        <div class="cost"><b>${g.per_report ? `about ${money(g.per_report)} a report` : "Free"}</b></div></label>`).join("")}</div></div></div>
  </div>`;
  document.querySelectorAll('#modelPick input[name="model"]').forEach(r => r.addEventListener("change", async () => {
    try { await post("/api/models", { interviewer_model: r.value }); toast("Interviewer set to " + m.models.find(x => x.id === r.value).label); }
    catch (e) { toast(e.message); }
    route();
  }));
  document.querySelectorAll('#graderPick input[name="grader"]').forEach(r => r.addEventListener("change", async () => {
    try { await post("/api/models", { grader_model: r.value }); toast("Grader set to " + m.graders.find(x => x.id === r.value).label); }
    catch (e) { toast(e.message); }
    route();
  }));
  document.getElementById("saveBudget").onclick = async () => {
    try { await post("/api/models", { monthly_budget: Number(document.getElementById("budget").value) }); toast("Budget saved"); route(); }
    catch (e) { toast(e.message); }
  };
  document.querySelectorAll("[data-savekey]").forEach(b => (b.onclick = async () => {
    const inp = document.querySelector(`[data-key="${b.dataset.savekey}"]`);
    const ep = document.querySelector(`[data-endpoint="${b.dataset.savekey}"]`);
    if (!inp.value.trim() && !ep) { toast("Paste a key first"); return; }
    try {
      if (ep) await post("/api/endpoint", { provider: b.dataset.savekey, base_url: ep.value.trim() });
      if (inp.value.trim()) await post("/api/keys", { provider: b.dataset.savekey, key: inp.value.trim() });
      inp.value = ""; toast("Saved"); route();
    }
    catch (e) { toast(e.message); }
  }));
  document.querySelectorAll("[data-delkey]").forEach(b => (b.onclick = async () => {
    if (!confirm("Remove this saved key?")) return;
    await post("/api/keys", { provider: b.dataset.delkey, key: "" }); toast("Key removed"); route();
  }));
}

// ---------- boot ----------
api("/api/cases").then(cs => (document.getElementById("navCases").textContent = cs.length)).catch(() => {});
checkHealth();
pollJobs();
route();
