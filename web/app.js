/* Calling Analytics Dashboard — Dynamic Dashboard. Data: Supabase RPC ca_meta / ca_report / ca_daily / ca_caller / ca_unique_leads /
   ca_cdr / ca_stage_export. Numbers follow the Streamlit calling dashboard exactly (docs/definitions.md); the pipeline pre-computes each caller's day. */
const sb = supabase.createClient(CA_CONFIG.supabaseUrl, CA_CONFIG.supabaseKey);
const $ = (id) => document.getElementById(id);
const S = { meta: null, seq: 0, sel: { team: new Set(), vertical: new Set(), caller: new Set(), stage: new Set(), month: new Set() }, last: null, preset: null, charts: {},
  sort: { k: "raw_dur_sec", dir: -1 }, issue: null, page: "overall", stack: [], group: null, prow: [], grain: "day", dgrain: "day", rows: [], caller: null };

/* ---------- format (Python-identical) ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const num = (v) => (v == null || !isFinite(v) ? "–" : inr0.format(v));
function pyRound(x) {   // Python round(): halves go to the even number
  const f = Math.floor(x), d = x - f;
  if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}
function fmtHm(sec) { if (sec == null || !(sec > 0)) return "0h 0m"; const tm = pyRound(sec / 60); return `${Math.floor(tm / 60)}h ${tm % 60}m`; }
const pur = (a, t) => (t > 0 ? pyRound((a / t) * 100) : 0);
const change = (c, p) => (p ? (c - p) / Math.abs(p) : null);
const chHtml = (ch) => ch == null ? `<span class="muted">–</span>` :
  `<span class="${ch >= 0 ? "up" : "down"}">${ch >= 0 ? "▲" : "▼"} ${Math.abs(ch * 100).toFixed(1)}%</span>`;
const breaksTxt = (n, s) => (n > 0 ? `${num(n)} breaks · ${fmtHm(s)}` : "0");

/* ---------- dates (IST) ---------- */
const istToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const D = (s) => new Date(s + "T00:00:00Z");
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = D(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const daysIn = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();
const nice = (s) => D(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const short = (s) => D(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const ddmmyyyy = (s) => `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}`;
const spanDays = (a, b) => Math.round((D(b) - D(a)) / 864e5) + 1;
function preset(p) {
  const t = istToday(), ms = t.slice(0, 8) + "01";
  switch (p) {
    case "today": return [t, t];
    case "yesterday": { const y = addDays(t, -1); return [y, y]; }
    case "week": return [addDays(t, -((D(t).getUTCDay() + 6) % 7)), t];
    case "lastweek": { const mon = addDays(t, -((D(t).getUTCDay() + 6) % 7) - 7); return [mon, addDays(mon, 6)]; }
    case "month": return [ms, t];
    case "lastmonth": { const e = addDays(ms, -1); return [e.slice(0, 8) + "01", e]; }
  }
}
function shiftMonth(s, n) {
  const [y, m, d] = s.split("-").map(Number), t = new Date(Date.UTC(y, m - 1 + n, 1)), yy = t.getUTCFullYear(), mm = t.getUTCMonth() + 1;
  return `${yy}-${String(mm).padStart(2, "0")}-${String(Math.min(d, daysIn(yy, mm))).padStart(2, "0")}`;
}
/* the period compared with (same rule as the Revenue dashboard's date buttons; custom dates: the same number of days just before) */
function compareWith(p, from, to) {
  switch (p) {
    case "today": return [addDays(from, -1), addDays(to, -1), "yesterday"];
    case "yesterday": return [addDays(from, -1), addDays(to, -1), "the day before"];
    case "week": return [addDays(from, -7), addDays(to, -7), "the same days of last week"];
    case "lastweek": return [addDays(from, -7), addDays(to, -7), "the week before"];
    case "month": return [shiftMonth(from, -1), shiftMonth(to, -1), "the same days of last month"];
    case "lastmonth": return [shiftMonth(from, -1), addDays(from, -1), "the month before"];
    case "months": {   // Month filter: the same days, as many months earlier as were picked
      const n = S.sel.month.size || 1, endOfMonth = addDays(to, 1).slice(8) === "01";
      const pt = endOfMonth ? addDays(shiftMonth(to.slice(0, 8) + "01", -n + 1).slice(0, 8) + "01", -1) : shiftMonth(to, -n);
      return [shiftMonth(from, -n), pt, n === 1 ? "the same days of the month before" : `the same days of the ${n} months before`];
    }
    default: { const n = spanDays(from, to); return [addDays(from, -n), addDays(from, -1), `the ${n} day${n > 1 ? "s" : ""} before`]; }
  }
}
function range() {
  const from = $("from").value, to = $("to").value, c = compareWith(S.preset, from, to);
  return { from, to, pf: c[0], pt: c[1], cmp: c[2] };
}
const prevText = (r) => `${nice(r.pf)} – ${nice(r.pt)}`;

/* ---------- data ---------- */
async function rpc(fn, args) { const { data, error } = await sb.rpc(fn, args); if (error) throw error; return data; }
function filt() {
  return { p_teams: S.sel.team.size ? [...S.sel.team] : null, p_verticals: S.sel.vertical.size ? [...S.sel.vertical] : null,
    p_search: null, p_callers: S.sel.caller.size ? [...S.sel.caller] : null };
}
const CACHE = new Map();
function cached(fn, args) { const k = fn + JSON.stringify(args); if (!CACHE.has(k)) CACHE.set(k, rpc(fn, args).catch((e) => { CACHE.delete(k); throw e; })); return CACHE.get(k); }

/* ---------- charts ---------- */
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const barLabels = {
  id: "barLabels",
  afterDatasetsDraw(chart) {
    const o = chart.options.plugins.barLabels; if (!o) return;
    const meta = chart.getDatasetMeta(o.dataset ?? 0); if (!meta || meta.hidden || meta.data.length > 45) return;
    const ctx = chart.ctx, horiz = chart.options.indexAxis === "y"; ctx.save(); ctx.fillStyle = css("--ink"); ctx.font = "600 11px Inter, sans-serif";
    ctx.textAlign = horiz ? "left" : "center"; ctx.textBaseline = horiz ? "middle" : "bottom";
    meta.data.forEach((el, i) => { const t = o.labels[i]; if (t == null || t === "") return;
      if (horiz) ctx.fillText(String(t), Math.max(el.x, el.base) + 6, el.y); else ctx.fillText(String(t), el.x, Math.min(el.y, el.base) - 3); });
    ctx.restore();
  },
};
/* comparison charts (teams, verticals, callers): horizontal bars, names on the left, one per row, wrapped if long; the box grows to fit */
function horizontal(cfg, n, perRow = 34) {
  const o = cfg.options, x = o.scales.x || {}, y = o.scales.y || {};
  o.indexAxis = "y";
  o.scales = { x: { ...y, position: "bottom", grid: { color: css("--line") } },
    y: { grid: { display: false }, ticks: { autoSkip: false, color: css("--ink"), callback(v) { return wrapLabel(this.getLabelForValue(v), 26); } } } };
  o.layout = { padding: { right: 48, top: 4 } };
  cfg._height = Math.max(220, 70 + n * perRow);
  return cfg;
}
function chart(id, cfg) {
  if (cfg._height) { const box = $(id)?.parentElement; if (box) box.style.height = cfg._height + "px"; }
  if (S.charts[id]) S.charts[id].destroy();
  const el = $(id); if (!el) return;
  Chart.defaults.color = css("--muted"); Chart.defaults.borderColor = css("--line"); Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: css("--raise"), titleColor: css("--gold-2"), bodyColor: css("--ink"),
    borderColor: css("--line-2"), borderWidth: 1, padding: 10 });
  cfg.plugins = [barLabels];
  S.charts[id] = new Chart(el, cfg);
}
// axis labels: full names, wrapped onto several lines (no "…")
function wrapLabel(text, width = 14) {
  const out = []; let line = "";
  for (const w of String(text).split(/\s+/)) {
    if (line && (line + " " + w).length > width) { out.push(line); line = w; } else line = line ? line + " " + w : w;
  }
  if (line) out.push(line);
  return out.length > 1 ? out : out[0] || "";
}
const shortTick = { autoSkip: false, maxRotation: 0, callback(v) { return wrapLabel(this.getLabelForValue(v)); } };
const hours = (sec) => Math.round((sec / 3600) * 10) / 10;
/* one bar per period = talk time of calls > 3 mins; the number above it = total calls; hover = connected / not connected split */
function trendConfig(labels, g) {
  const dur = g.map(([, o]) => hours(o.dur_3m)), calls = g.map(([, o]) => o.total_calls);
  return { type: "bar",
    data: { labels, datasets: [{ label: "Call duration > 3 mins", data: dur, backgroundColor: css("--gold"), borderRadius: 4, maxBarThickness: 40 }] },
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 20 } },
      plugins: { legend: { display: false }, barLabels: { dataset: 0, labels: calls.map((v) => (v ? num(v) : "")) },
        tooltip: { callbacks: {
          label: (c) => ` Call duration > 3 mins: ${fmtHm(g[c.dataIndex][1].dur_3m)}`,
          afterLabel: (c) => { const o = g[c.dataIndex][1];
            return [` Total calls: ${num(o.total_calls)}`, ` Connected: ${num(o.answered)} (${pur(o.answered, o.total_calls)}%)`,
              ` Not connected: ${num(o.missed)}`, ` Calls > 3 mins: ${num(o.calls_3m)}`]; } } } },
      scales: { x: { grid: { display: false }, ticks: shortTick },
        y: { beginAtZero: true, ticks: { callback: (v) => `${v}h` }, title: { display: true, text: "Call duration > 3 mins" } } } } };
}
const GRAINS = { day: "Day", part: "Part of month", month: "Month" };
function grainKey(d, g) {
  if (g === "day") return d;
  if (g === "month") return d.slice(0, 8) + "01";
  const day = +d.slice(8, 10);   // part of month: days 1–10, 11–20, 21–end
  return d.slice(0, 8) + (day <= 10 ? "01" : day <= 20 ? "11" : "21");
}
function grainLabel(k, g) {
  if (g === "day") return short(k);
  if (g === "month") return D(k).toLocaleDateString("en-IN", { month: "short", year: "numeric", timeZone: "UTC" });
  const day = +k.slice(8, 10), end = day === 21 ? daysIn(+k.slice(0, 4), +k.slice(5, 7)) : day + 9;
  return `${day}–${end} ${D(k).toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" })}`;
}
function byGrain(rows, g, fields) {
  const m = new Map();
  for (const r of rows) { const k = grainKey(String(r.call_date).slice(0, 10), g), o = m.get(k) || Object.fromEntries(fields.map((f) => [f, 0]));
    fields.forEach((f) => (o[f] += +r[f] || 0)); m.set(k, o); }
  return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}
const segHtml = (id, cur) => `<div class="seg sm" id="${id}">${Object.entries(GRAINS).map(([g, t]) => `<button data-g="${g}" class="${cur === g ? "on" : ""}">${t}</button>`).join("")}</div>`;

/* ---------- the report (Streamlit's report_df) ---------- */
function buildReport(rep) {
  const rows = rep.rows.map((r) => ({
    "IN/OUT TIME": r.io || "", CALLER: r.caller, TEAM: r.team ?? "Others", "TOTAL CALLS": +r.total_calls,
    "CALL STATUS": `${+r.answered} Ans / ${+r.missed} Unans`, "PICK UP RATIO %": `${pur(+r.answered, +r.total_calls)}%`,
    "CALLS > 3 MINS": +r.calls_3m, "CALLS 15-20 MINS": +r.calls_15_20, "20+ MIN CALLS": +r.calls_20p,
    "CALL DURATION > 3 MINS": fmtHm(+r.dur_3m), "FOLLOW UPS DONE": +r.followup, "DISCOVERY DONE": +r.discovery, "ROADMAPS DONE": +r.roadmap,
    "PRODUCTIVE HOURS": fmtHm(+r.prod_sec), "BREAKS (>=5 MINS)": r.breaks || "0", "BREAK TIMINGS (>=5 MINS)": r.break_timings || "-",
    REMARKS: (r.issues || []).length ? r.issues.join(", ") : "None", raw_prod_sec: +r.prod_sec, raw_dur_sec: +r.dur_3m,
    _ans: +r.answered, _miss: +r.missed, _pur: +r.total_calls > 0 ? +r.answered / +r.total_calls : -1,
    _breaks: breaksTxt(+r.break_count, +r.break_shown), _bsec: +r.break_shown, _issues: r.issues || [], _src: r, _vertical: r.vertical,
  }));
  for (const s of rep.stage_only) rows.push({
    "IN/OUT TIME": "-", CALLER: s.caller, TEAM: s.team, "TOTAL CALLS": 0, "CALL STATUS": "-", "PICK UP RATIO %": "-",
    "CALLS > 3 MINS": 0, "CALLS 15-20 MINS": 0, "20+ MIN CALLS": 0, "CALL DURATION > 3 MINS": fmtHm(0),
    "FOLLOW UPS DONE": +s.followup, "DISCOVERY DONE": +s.discovery, "ROADMAPS DONE": +s.roadmap, "PRODUCTIVE HOURS": fmtHm(0),
    "BREAKS (>=5 MINS)": "-", "BREAK TIMINGS (>=5 MINS)": "-", REMARKS: "-", raw_prod_sec: 0, raw_dur_sec: 0, _ans: 0, _miss: 0, _pur: -1,
    _breaks: "-", _bsec: -1, _issues: [], _stage: true, _inSheet: !!s.in_sheet, _vertical: s.vertical });
  rows.sort((a, b) => b.raw_dur_sec - a.raw_dur_sec);
  rows.forEach((r, i) => (r.Rank = ["🥇", "🥈", "🥉"][i] || ""));
  return rows;
}
function totalsRow(rows) {
  const sum = (k) => rows.reduce((a, r) => a + (+r[k] || 0), 0), ans = sum("_ans"), miss = sum("_miss"), calls = sum("TOTAL CALLS");
  return { Rank: "", "IN/OUT TIME": "-", CALLER: "TOTAL", TEAM: "-", "TOTAL CALLS": calls, "CALL STATUS": `${ans} Ans / ${miss} Unans`,
    "PICK UP RATIO %": calls > 0 ? `${pur(ans, calls)}%` : "0%", "CALLS > 3 MINS": sum("CALLS > 3 MINS"),
    "CALLS 15-20 MINS": sum("CALLS 15-20 MINS"), "20+ MIN CALLS": sum("20+ MIN CALLS"), "CALL DURATION > 3 MINS": fmtHm(sum("raw_dur_sec")),
    "FOLLOW UPS DONE": sum("FOLLOW UPS DONE"), "DISCOVERY DONE": sum("DISCOVERY DONE"), "ROADMAPS DONE": sum("ROADMAPS DONE"),
    "PRODUCTIVE HOURS": fmtHm(sum("raw_prod_sec")), "BREAKS (>=5 MINS)": "-", "BREAK TIMINGS (>=5 MINS)": "-", REMARKS: "-",
    raw_prod_sec: sum("raw_prod_sec"), raw_dur_sec: sum("raw_dur_sec"), _ans: ans, _miss: miss,
    _breaks: breaksTxt(rows.reduce((a, r) => a + (+r._src?.break_count || 0), 0), rows.reduce((a, r) => a + (+r._src?.break_shown || 0), 0)), _total: 1 };
}
const topBy = (rows, k) => [...rows].sort((a, b) => b[k] - a[k])[0];
const FULL_COLS = ["Rank", "IN/OUT TIME", "CALLER", "TEAM", "TOTAL CALLS", "CALL STATUS", "PICK UP RATIO %", "CALLS > 3 MINS", "CALLS 15-20 MINS",
  "20+ MIN CALLS", "CALL DURATION > 3 MINS", "FOLLOW UPS DONE", "DISCOVERY DONE", "ROADMAPS DONE", "PRODUCTIVE HOURS",
  "BREAKS (>=5 MINS)", "BREAK TIMINGS (>=5 MINS)", "REMARKS"];
// on screen: [heading, row key, sort key]
const SHOW = [["Rank", "Rank", null], ["Caller", "CALLER", "CALLER"], ["Team", "TEAM", "TEAM"], ["Total calls", "TOTAL CALLS", "TOTAL CALLS"],
  ["Call status", "CALL STATUS", "_ans"], ["Pick up ratio %", "PICK UP RATIO %", "_pur"], ["Call duration > 3 mins", "CALL DURATION > 3 MINS", "raw_dur_sec"],
  ["Follow ups done", "FOLLOW UPS DONE", "FOLLOW UPS DONE"], ["Discovery done", "DISCOVERY DONE", "DISCOVERY DONE"],
  ["Roadmaps done", "ROADMAPS DONE", "ROADMAPS DONE"], ["Productive hours", "PRODUCTIVE HOURS", "raw_prod_sec"], ["Breaks (≥ 5 mins)", "_breaks", "_bsec"]];
const LEFT = new Set(["CALLER", "TEAM", "CALL STATUS", "_breaks"]);
const ISSUES = ["Late Check-In", "Early Check-Out", "Low Calls", "Low Duration", "Excessive Breaks", "Less Productive"];

function uniqueApprovals(list) {   // _unique_approvals: first spelling of each name, case ignored
  const seen = new Map();
  for (const v of list || []) { const t = String(v).trim(), k = t.toLowerCase(); if (k && !seen.has(k)) seen.set(k, t); }
  return seen.size ? [...seen.values()].join(", ") : "—";
}
function manualRows(src) {
  return src.filter((r) => +r.n_manual > 0).map((r) => ({ CALLER: r.caller, VERTICAL: r.vertical ?? "—", TEAM: r.team ?? "—",
    "MANUAL CALLS COUNT": +r.n_manual, "MANUAL CALLS DURATION": fmtHm(+r.manual_dur), "APPROVED BY": uniqueApprovals(r.approvals), _dur: +r.manual_dur }))
    .sort((a, b) => b["MANUAL CALLS COUNT"] - a["MANUAL CALLS COUNT"]);
}
const MAN_COLS = ["CALLER", "VERTICAL", "TEAM", "MANUAL CALLS COUNT", "MANUAL CALLS DURATION", "APPROVED BY"];
const manTotal = (man) => ({ CALLER: "TOTAL", VERTICAL: "—", TEAM: "—", "MANUAL CALLS COUNT": man.reduce((a, r) => a + r["MANUAL CALLS COUNT"], 0),
  "MANUAL CALLS DURATION": fmtHm(man.reduce((a, r) => a + r._dur, 0)), "APPROVED BY": "—", _total: 1 });

/* ---------- pages ---------- */
const PAGES = {
  overall: "Overall Performance", vertical: "Vertical Wise Performance", team: "Teamwise Performance",
  caller: "Caller Wise Performance", stage: "Stage Changed Performance", manual: "Manual Calls",
};
const inSheet = (x) => (x._stage ? !!x._inSheet : x._src?.team != null);   // in the current team sheet (KPI cards and highlights only)
function busy(on) { $("busy").hidden = !on; $("content").classList.toggle("loading", on); }
async function render() {
  const seq = ++S.seq, r = range(), page = S.page;
  if (!r.from || !r.to || r.from > r.to) return;
  $("page-sub").innerHTML = `Duration period · <b>${ddmmyyyy(r.from)} to ${ddmmyyyy(r.to)}</b> &nbsp;·&nbsp; compared with <b>${prevText(r)}</b> (${esc(r.cmp)})`;
  busy(true);
  try {
    const f = filt(), a = { p_from: r.from, p_to: r.to, ...f }, p = { p_from: r.pf, p_to: r.pt, ...f };
    const need = {
      uniq: page === "overall", daily: ["overall", "manual"].includes(page), stage: page === "stage",
    };
    const [rep, prep, uniq, puniq, daily, stage] = await Promise.all([cached("ca_report", a), cached("ca_report", p),
      need.uniq ? cached("ca_unique_leads", a) : null, need.uniq ? cached("ca_unique_leads", p) : null,
      need.daily ? cached("ca_daily", a) : null, need.stage ? cached("ca_stage_trend", a) : null]);
    if (seq !== S.seq) return;
    S.last = { from: r.from, to: r.to, f };
    const D0 = { r, rep, prep, uniq: +uniq, puniq: +puniq, daily, stage };
    S.rows = buildReport(rep); S.prow = buildReport(prep); S.manual = manualRows(rep.rows);
    if (!rep.rows.length && !rep.stage_only.length) $("content").innerHTML = `<div class="card empty">No data found for the selected period and filters.</div>`;
    else ({ overall: pageOverall, vertical: pageGroup, team: pageGroup, caller: pageCaller, stage: pageStage, manual: pageManual })[page](D0);
  } catch (e) {
    if (seq === S.seq) $("content").innerHTML = `<div class="card empty">Could not load data: ${esc(e.message || e)}</div>`;
  }
  if (seq === S.seq) busy(false);
}

/* shared bits */
const kpiCard = (label, v, c, p, pt, hint = "", fmtPrev = num) => (!+c && !+p) ? "" : `<div class="kpi" ${hint ? `title="${esc(hint)}"` : ""}><div class="k">${label}</div><div class="v">${v}</div>
  <div class="d">${chHtml(change(c, p))} vs ${esc(fmtPrev(p))} <span class="muted small">(${esc(pt)})</span></div></div>`;
function cards(rows, src) {   // KPI totals: callers in the current team sheet only
  const m = rows.filter(inSheet), s = src.filter((r) => r.team != null), ss = (k) => s.reduce((a, r) => a + (+r[k] || 0), 0), sm = (k) => m.reduce((a, r) => a + (+r[k] || 0), 0);
  return { rows: m, acefone: ss("n_acefone"), ozonetel: ss("n_ozonetel"), mcube: ss("n_mcube"), manual: ss("n_manual"), manualDur: ss("manual_dur"),
    manualCallers: s.filter((r) => +r.n_manual > 0).length, calls: sm("TOTAL CALLS"), dur: sm("raw_dur_sec"),
    avgProd: m.length ? m.reduce((a, r) => a + r.raw_prod_sec, 0) / m.length : 0,
    f: sm("FOLLOW UPS DONE"), d: sm("DISCOVERY DONE"), ro: sm("ROADMAPS DONE") };
}
function groupRows(rows, keyFn) {
  const m = new Map();
  for (const x of rows) {
    const k = keyFn(x) ?? "—", o = m.get(k) || { key: k, callers: 0, teams: new Set(), verticals: new Set(), total_calls: 0, answered: 0, missed: 0, calls_3m: 0,
      dur_3m: 0, prod: 0, active: 0, f: 0, d: 0, ro: 0, n_manual: 0, manual_dur: 0 };
    if (x["TOTAL CALLS"] > 0) o.callers += 1;
    o.teams.add(x.TEAM); o.verticals.add(x._vertical ?? "—");
    o.total_calls += x["TOTAL CALLS"]; o.answered += x._ans; o.missed += x._miss; o.calls_3m += x["CALLS > 3 MINS"]; o.dur_3m += x.raw_dur_sec;
    if (!x._stage) { o.prod += x.raw_prod_sec; o.active += 1; }
    o.f += x["FOLLOW UPS DONE"]; o.d += x["DISCOVERY DONE"]; o.ro += x["ROADMAPS DONE"];
    o.n_manual += +(x._src?.n_manual || 0); o.manual_dur += +(x._src?.manual_dur || 0); m.set(k, o);
  }
  return [...m.values()];
}
const vKey = (x) => x._vertical ?? "No vertical";
function trendCard(id, title, note) {
  return `<section class="card"><div class="card-h"><h3>${title}</h3><span class="muted small">${note}</span>${segHtml(id + "-g", S.grain)}</div>
    <div class="chart-box" style="height:340px"><canvas id="${id}"></canvas></div></section>`;
}
function wireGrain(id, draw) {
  const el = $(id + "-g"); if (!el) return;
  el.onclick = (e) => { const b = e.target.closest("button[data-g]"); if (!b) return; S.grain = b.dataset.g;
    el.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); draw(); };
  draw();
}
/* Contact Stage filter (Stage Changed Performance only): which of the three stages to show; none chosen = all three */
const STAGES = [["d", "Discovery", "--c2", "🔍 Discovery done", "DISCOVERY DONE"], ["ro", "Roadmap", "--c3", "🗺️ Roadmaps done", "ROADMAPS DONE"],
  ["f", "Follow up", "--gold", "📋 Follow ups done", "FOLLOW UPS DONE"]];
const stShown = () => STAGES.filter(([, l]) => !S.sel.stage.size || S.sel.stage.has(l));
const stSum = (o) => stShown().reduce((t, [k]) => t + (+o[k] || 0), 0);                 // o with f / d / ro
const stRow = (x) => stShown().reduce((t, s) => t + (+x[s[4]] || 0), 0);               // agent-table row
const stCols = (o) => stShown().map(([k]) => `<td class="num">${num(o[k])}</td>`).join("");
const stName = (l) => (l === "Discovery" ? l : l + "s");
const stHeads = () => stShown().map(([, l]) => `<th>${stName(l)}</th>`).join("");
const stTotalCol = () => stShown().length > 1;
const stWords = () => (stShown().length === 3 ? "all three together" : stShown().map(([, l]) => stName(l).toLowerCase()).join(" + "));
const stNote = () => `Number = ${stName(stShown()[0][1]).toLowerCase()}${stShown().length === 3 ? " · hover for all three" : stShown().length === 2 ? " · hover for both" : ""}`;
function stageBars(labels, g, keys = stShown().map(([k, l, c]) => [k, stName(l), c])) {
  return { type: "bar", data: { labels, datasets: keys.map(([k, l, c]) => ({ label: l, data: g.map(([, o]) => o[k]), backgroundColor: css(c), borderRadius: 3, maxBarThickness: 30 })) },
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 18 } }, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, barLabels: { dataset: 0, labels: g.map(([, o]) => (o[keys[0][0]] ? num(o[keys[0][0]]) : "")) } },
      scales: { x: { grid: { display: false }, ticks: shortTick }, y: { beginAtZero: true } } } };
}
function manualBars(labels, g) {
  return { type: "bar", data: { labels, datasets: [{ label: "Manual calls", data: g.map(([, o]) => o.n_manual), backgroundColor: css("--c3"), borderRadius: 4, maxBarThickness: 40 }] },
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 18 } },
      plugins: { legend: { display: false }, barLabels: { dataset: 0, labels: g.map(([, o]) => (o.n_manual ? num(o.n_manual) : "")) },
        tooltip: { callbacks: { label: (c) => ` Manual calls: ${num(c.raw)}`, afterLabel: (c) => { const o = g[c.dataIndex][1];
          return [` Duration: ${fmtHm(o.manual_dur)}`, ...(o.manual_callers != null ? [` Callers: ${num(o.manual_callers)}`] : [])]; } } } },
      scales: { x: { grid: { display: false }, ticks: shortTick }, y: { beginAtZero: true } } } };
}

/* ---------- Overall ---------- */
function pageOverall({ r, rep, prep, uniq, puniq, daily }) {
  const rows = S.rows, cur = cards(rows, rep.rows), prv = cards(S.prow, prep.rows), pt = prevText(r), mr = cur.rows.length ? cur.rows : rows;
  const t1 = topBy(mr, "raw_dur_sec"), t2 = topBy(mr, "TOTAL CALLS"), t3 = topBy(mr, "20+ MIN CALLS"),
    t4 = topBy(mr, "FOLLOW UPS DONE"), t5 = topBy(mr, "DISCOVERY DONE"), t6 = topBy(mr, "ROADMAPS DONE");
  const hl = (label, x, val, n) => !+n ? "" : `<div class="hl"><div class="k">${label}</div><div class="n caller-open" data-c="${esc(x.CALLER)}">${esc(x.CALLER)}</div><div class="d">${esc(val)}</div></div>`;
  const issueCount = Object.fromEntries(ISSUES.map((i) => [i, rows.filter((x) => x._issues.includes(i)).length]));
  $("content").innerHTML = `
    <h4 class="section-h">🏆 Top 6 performance highlights</h4>
    <section class="highlights">
      ${hl("🥇 Top performer", t1, `${t1["CALL DURATION > 3 MINS"]} Duration`, t1.raw_dur_sec)}${hl("✆ Highest calls", t2, `${t2["TOTAL CALLS"]} Total Calls`, t2["TOTAL CALLS"])}
      ${hl("🗣️ Deep engagement", t3, `${t3["20+ MIN CALLS"]} Long Calls`, t3["20+ MIN CALLS"])}${hl("📋 Highest follow ups", t4, `${t4["FOLLOW UPS DONE"]} Follow Ups Done`, t4["FOLLOW UPS DONE"])}
      ${hl("🔍 Highest discovery", t5, `${t5["DISCOVERY DONE"]} Discoveries Done`, t5["DISCOVERY DONE"])}${hl("🗺️ Highest roadmap", t6, `${t6["ROADMAPS DONE"]} Roadmaps Done`, t6["ROADMAPS DONE"])}
    </section>
    <h4 class="section-h">Summary metrics</h4>
    <p class="muted small" style="margin:-4px 0 10px;text-align:center">Callers in the current team sheet</p>
    <section class="kpis k9">
      ${kpiCard("🔵 Acefone calls", num(cur.acefone), cur.acefone, prv.acefone, pt)}${kpiCard("🟠 Ozonetel calls", num(cur.ozonetel), cur.ozonetel, prv.ozonetel, pt)}
      ${kpiCard("🟢 MCube calls", num(cur.mcube), cur.mcube, prv.mcube, pt)}${kpiCard("✏️ Manual calls", num(cur.manual), cur.manual, prv.manual, pt)}
      ${kpiCard("👤 Unique leads", num(uniq), uniq, puniq, pt, "Unique numbers dialled each day, added up over the days")}
      ${kpiCard("⏱ Avg prod hrs", fmtHm(cur.avgProd), cur.avgProd, prv.avgProd, pt, "", fmtHm)}
      ${kpiCard("📋 Follow ups done", num(cur.f), cur.f, prv.f, pt)}${kpiCard("🔍 Discovery done", num(cur.d), cur.d, prv.d, pt)}
      ${kpiCard("🗺️ Roadmaps done", num(cur.ro), cur.ro, prv.ro, pt)}
    </section>
    <h4 class="section-h">Remarks summary</h4>
    <section class="card"><p class="muted small" style="margin:0 0 10px">Callers flagged at least once in these dates · click one to see them on Caller Wise Performance</p>
      <div class="chips">${ISSUES.map((i) => `<button class="chip ${issueCount[i] ? "" : "zero"}" data-i="${esc(i)}">${esc(i)}<b>${num(issueCount[i])}</b></button>`).join("")}</div></section>
    ${daily && daily.length > 1 ? `<h4 class="section-h">Daily trend</h4>${trendCard("ch-trend", "Call duration > 3 mins", "Number above each bar = total calls · hover for connected / not connected")}` : ""}
    <h4 class="section-h">Downloads</h4>
    <section class="card"><div class="downloads" style="margin:0">
      <button class="btn primary" id="dl-cdr">📥 Download CDR</button><button class="btn" id="dl-stage">📥 Download Stage Changed Report</button>
      <span class="dl-msg" id="dl-msg"></span></div></section>`;
  if (daily && daily.length > 1) wireGrain("ch-trend", () => {
    const g = byGrain(daily, S.grain, ["total_calls", "calls_3m", "dur_3m", "answered", "missed"]);
    chart("ch-trend", trendConfig(g.map(([k]) => grainLabel(k, S.grain)), g));
  });
  $("content").onclick = (e) => {
    const chip = e.target.closest(".chip[data-i]"); if (chip) { S.issue = chip.dataset.i; location.hash = "#caller"; return; }
    const c = e.target.closest("[data-c]"); if (c) { S.stack = []; openCaller(c.dataset.c); }
  };
  $("dl-cdr").onclick = () => downloadCdr();
  $("dl-stage").onclick = downloadStage;
}

/* ---------- Vertical wise / Teamwise ---------- */
function groupTable(kind, list, prevMap) {
  const isV = kind === "vertical";
  return `<table class="agent-table"><thead><tr><th class="l">${isV ? "Vertical" : "Team"}</th>${isV ? "<th>Teams</th>" : '<th class="l">Vertical</th>'}<th>Callers</th>
    <th>Total calls</th><th>Pick up %</th><th>Calls > 3 mins</th><th>Call duration > 3 mins</th><th>Change</th><th>Avg productive hours</th>
    <th>Follow ups</th><th>Discovery</th><th>Roadmaps</th></tr></thead><tbody>${list.map((o) => { const p = prevMap.get(o.key);
      return `<tr class="click" data-g="${esc(o.key)}"><td class="l caller-link nw">${esc(o.key)}</td>${isV ? `<td class="num">${num(o.teams.size)}</td>` : `<td class="l nw">${esc([...o.verticals].join(", "))}</td>`}
      <td class="num">${num(o.callers)}</td><td class="num">${num(o.total_calls)}</td><td class="num">${pur(o.answered, o.total_calls)}%</td><td class="num">${num(o.calls_3m)}</td>
      <td class="num">${fmtHm(o.dur_3m)}</td><td class="num">${chHtml(change(o.dur_3m, p?.dur_3m))}</td><td class="num">${fmtHm(o.active ? o.prod / o.active : 0)}</td>
      <td class="num">${num(o.f)}</td><td class="num">${num(o.d)}</td><td class="num">${num(o.ro)}</td></tr>`; }).join("")}</tbody></table>`;
}
function pageGroup() {
  const kind = S.page, key = kind === "vertical" ? vKey : (x) => x.TEAM;
  const list = groupRows(S.rows, key).filter((o) => o.total_calls > 0 || o.f || o.d || o.ro).sort((a, b) => b.dur_3m - a.dur_3m);
  const prevMap = new Map(groupRows(S.prow, key).map((o) => [o.key, o]));
  const label = kind === "vertical" ? "vertical" : "team";
  $("content").innerHTML = `
    <section class="card"><div class="card-h"><h3>Call duration > 3 mins by ${label}</h3><span class="muted small">Number above each bar = total calls · hover for connected / not connected</span></div>
      <div class="chart-box" style="height:${kind === "team" ? 380 : 320}px"><canvas id="ch-group"></canvas></div></section>
    <section class="card"><div class="card-h"><h3>${kind === "vertical" ? "Verticals" : "Teams"}</h3><span class="muted small">Click a ${label} for its trend and ${kind === "vertical" ? "teams" : "callers"} · change = call duration vs ${esc(range().cmp)}</span></div>
      <div class="table-wrap">${groupTable(kind, list, prevMap)}</div></section>`;
  const top = list;
  chart("ch-group", horizontal(trendConfig(top.map((o) => o.key), top.map((o) => [o.key, o])), top.length));
  $("content").onclick = (e) => { const t = e.target.closest("tr[data-g]"); if (t) { S.stack = []; openGroup(kind, t.dataset.g); } };
}

/* ---------- Caller wise ---------- */
function pageCaller() {
  const rows = S.rows, issueCount = Object.fromEntries(ISSUES.map((i) => [i, rows.filter((x) => x._issues.includes(i)).length]));
  $("content").innerHTML = `
    <section class="card"><p class="muted small" style="margin:0 0 10px">Remarks · click one to show only those callers</p>
      <div class="chips" id="chips"><button class="chip ${S.issue ? "" : "on"}" data-i="">All callers<b>${num(rows.filter(shownInTable).length)}</b></button>${ISSUES.map((i) =>
        `<button class="chip ${S.issue === i ? "on" : ""} ${issueCount[i] ? "" : "zero"}" data-i="${esc(i)}">${esc(i)}<b>${num(issueCount[i])}</b></button>`).join("")}</div></section>
    <section class="card"><div class="card-h"><h3>Agent performance table</h3><span class="muted small">Click a caller for their trend, time of day and day-by-day detail · click a heading to sort</span></div>
      <div class="table-wrap tall" id="board"></div>
      <div class="downloads"><button class="btn primary" id="dl-agent">📥 Download agent performance table</button>
        <button class="btn" id="dl-cdr">📥 Download CDR</button><span class="dl-msg" id="dl-msg"></span></div></section>`;
  drawBoard();
  $("content").onclick = (e) => {
    const chip = e.target.closest(".chip"); if (chip) { S.issue = chip.dataset.i || null; document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("on", (c.dataset.i || null) === S.issue)); drawBoard(); return; }
    const th = e.target.closest("#board th[data-k]");
    if (th) { const k = th.dataset.k; S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : (k === "CALLER" || k === "TEAM" ? 1 : -1) }; drawBoard(); return; }
    const c = e.target.closest("[data-c]"); if (c) { S.stack = []; openCaller(c.dataset.c); }
  };
  $("dl-agent").onclick = downloadAgent;
  $("dl-cdr").onclick = () => downloadCdr();
}
const shownInTable = (x) => x["TOTAL CALLS"] > 0 && x.raw_dur_sec > 0;   // 0 calls or 0 duration > 3 mins: not listed
function drawBoard() {
  let rows = S.rows.filter((x) => shownInTable(x) && (!S.issue || x._issues.includes(S.issue)));
  const { k, dir } = S.sort;
  rows = [...rows].sort((a, b) => { const x = a[k], y = b[k]; return typeof x === "string" ? dir * String(x).localeCompare(String(y)) : dir * ((x ?? -1) - (y ?? -1)); });
  const total = totalsRow(rows);
  $("board").innerHTML = `<table class="agent-table"><thead><tr>${SHOW.map(([h, key, sk]) =>
      `<th class="${LEFT.has(key) ? "l" : ""} ${sk && sk === k ? "sorted" : ""}" ${sk ? `data-k="${sk}"` : ""}>${esc(h)}${sk && sk === k ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>
    <tbody>${[...rows, total].map((x) => `<tr class="${x._total ? "total" : "click"}" ${x._total ? "" : `data-c="${esc(x.CALLER)}"`}>${SHOW.map(([, key]) => {
      const v = x[key];
      return `<td class="${LEFT.has(key) ? "l" : "num"} ${key === "CALLER" && !x._total ? "caller-link" : ""} ${key === "CALL STATUS" || key === "CALLER" || key === "_breaks" ? "nw" : ""}">${typeof v === "number" ? num(v) : esc(v)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>`;
}

/* ---------- Stage changes ---------- */
function pageStage({ r, rep, prep, stage }) {
  const cur = cards(S.rows, rep.rows), prv = cards(S.prow, prep.rows), pt = prevText(r);
  const withStage = (x) => stRow(x) > 0;
  const prevBy = new Map(S.prow.map((x) => [x.CALLER, stRow(x)]));
  const top = S.rows.filter(withStage).map((x) => ({ ...x, _st: stRow(x) })).sort((a, b) => b._st - a._st);
  const byV = groupRows(S.rows.filter(withStage), vKey).sort((a, b) => stSum(b) - stSum(a));
  const byT = groupRows(S.rows.filter(withStage), (x) => x.TEAM).sort((a, b) => stSum(b) - stSum(a));
  const tot = (o) => (stTotalCol() ? `<td class="num">${num(stSum(o))}</td>` : "");
  const gt = (kind, list) => `<table class="agent-table"><thead><tr><th class="l">${kind === "vertical" ? "Vertical" : "Team"}</th>${stHeads()}${stTotalCol() ? "<th>Total</th>" : ""}</tr></thead>
    <tbody>${list.map((o) => `<tr class="click" data-k="${kind}" data-g="${esc(o.key)}"><td class="l caller-link nw">${esc(o.key)}</td>${stCols(o)}${tot(o)}</tr>`).join("")}</tbody></table>`;
  const callerCols = (x) => stShown().map((st) => `<td class="num">${num(x[st[4]])}</td>`).join("");
  $("content").innerHTML = `
    <section class="kpis">
      ${stShown().map(([k, , , lab]) => kpiCard(lab, num(cur[k]), cur[k], prv[k], pt)).join("")}
      ${stTotalCol() ? kpiCard("Total stage changes", num(stSum(cur)), stSum(cur), stSum(prv), pt) : ""}
    </section>
    ${stage && stage.length > 1 ? trendCard("ch-stage", "Stage changes over time", stNote()) : ""}
    <section class="grid2">
      <section class="card"><div class="card-h"><h3>By vertical</h3><span class="muted small">Click to open</span></div><div class="chart-box" style="height:260px"><canvas id="ch-sv"></canvas></div><div class="table-wrap" style="margin-top:10px">${gt("vertical", byV)}</div></section>
      <section class="card"><div class="card-h"><h3>By team</h3><span class="muted small">Click to open</span></div><div class="chart-box" style="height:260px"><canvas id="ch-st"></canvas></div><div class="table-wrap" style="margin-top:10px">${gt("team", byT)}</div></section>
    </section>
    <section class="card"><div class="card-h"><h3>Top callers</h3><span class="muted small">Change = ${esc(stWords())} vs ${esc(r.cmp)}</span></div>
      <div class="table-wrap tall"><table class="agent-table"><thead><tr><th class="l">Caller</th><th class="l">Team</th>${stHeads()}${stTotalCol() ? "<th>Total</th>" : ""}<th>Change</th><th>Calls > 3 mins</th></tr></thead>
      <tbody>${top.map((x) => `<tr class="click" data-c="${esc(x.CALLER)}"><td class="l caller-link nw">${esc(x.CALLER)}</td><td class="l nw">${esc(x.TEAM)}</td>${callerCols(x)}
        ${stTotalCol() ? `<td class="num">${num(x._st)}</td>` : ""}<td class="num">${chHtml(change(x._st, prevBy.get(x.CALLER)))}</td>
        <td class="num">${num(x["CALLS > 3 MINS"])}</td></tr>`).join("")}</tbody></table></div>
      <div class="downloads"><button class="btn" id="dl-stage">📥 Download Stage Changed Report</button><span class="dl-msg" id="dl-msg"></span></div></section>`;
  if (stage && stage.length > 1) wireGrain("ch-stage", () => {
    const g = byGrain(stage.map((x) => ({ call_date: x.call_date, f: +x.followup, d: +x.discovery, ro: +x.roadmap })), S.grain, ["f", "d", "ro"]);
    chart("ch-stage", stageBars(g.map(([k]) => grainLabel(k, S.grain)), g));
  });
  const cut = (l) => l.slice(0, 15).map((o) => [o.key, o]);
  const per = 18 + 17 * stShown().length;
  chart("ch-sv", horizontal(stageBars(cut(byV).map(([k]) => k), cut(byV)), cut(byV).length, per));
  chart("ch-st", horizontal(stageBars(cut(byT).map(([k]) => k), cut(byT)), cut(byT).length, per));
  $("content").onclick = (e) => {
    const g = e.target.closest("tr[data-g]"); if (g) { S.stack = []; openGroup(g.dataset.k, g.dataset.g); return; }
    const c = e.target.closest("[data-c]"); if (c) { S.stack = []; openCaller(c.dataset.c); }
  };
  $("dl-stage").onclick = downloadStage;
}

/* ---------- Manual calls ---------- */
function pageManual({ r, rep, prep, daily }) {
  const cur = cards(S.rows, rep.rows), prv = cards(S.prow, prep.rows), pt = prevText(r), man = S.manual;
  const withMan = S.rows.filter((x) => +(x._src?.n_manual || 0) > 0);
  const byV = groupRows(withMan, vKey).sort((a, b) => b.n_manual - a.n_manual), byT = groupRows(withMan, (x) => x.TEAM).sort((a, b) => b.n_manual - a.n_manual);
  const share = (n, all) => (all ? `${((n / all) * 100).toFixed(1)}%` : "–");
  const gt = (kind, list) => `<table class="agent-table"><thead><tr><th class="l">${kind === "vertical" ? "Vertical" : "Team"}</th><th>Manual calls</th><th>Duration</th><th>Callers</th><th>Share of all calls</th></tr></thead>
    <tbody>${list.map((o) => { const all = groupRows(S.rows.filter((x) => (kind === "vertical" ? vKey(x) : x.TEAM) === o.key), () => o.key)[0]?.total_calls || 0;
      return `<tr class="click" data-k="${kind}" data-g="${esc(o.key)}"><td class="l caller-link nw">${esc(o.key)}</td><td class="num">${num(o.n_manual)}</td><td class="num">${fmtHm(o.manual_dur)}</td>
      <td class="num">${num(withMan.filter((x) => (kind === "vertical" ? vKey(x) : x.TEAM) === o.key).length)}</td><td class="num">${share(o.n_manual, all)}</td></tr>`; }).join("")}</tbody></table>`;
  const manHtml = !man.length ? `<p class="muted">No manual calls in these dates.</p>` : `<div class="table-wrap tall"><table class="agent-table"><thead><tr>${MAN_COLS.map((c) =>
      `<th class="${["MANUAL CALLS COUNT", "MANUAL CALLS DURATION"].includes(c) ? "" : "l"}">${c}</th>`).join("")}</tr></thead>
    <tbody>${[...man, manTotal(man)].map((x) => `<tr class="${x._total ? "total" : "click"}" ${x._total ? "" : `data-c="${esc(x.CALLER)}"`}>${MAN_COLS.map((c) =>
      `<td class="${["MANUAL CALLS COUNT", "MANUAL CALLS DURATION"].includes(c) ? "num" : "l"} ${c === "CALLER" && !x._total ? "caller-link" : ""}">${typeof x[c] === "number" ? num(x[c]) : esc(x[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  $("content").innerHTML = `
    <section class="kpis">
      ${kpiCard("✏️ Manual calls", num(cur.manual), cur.manual, prv.manual, pt)}${kpiCard("⏱ Manual call duration", fmtHm(cur.manualDur), cur.manualDur, prv.manualDur, pt, "", fmtHm)}
      ${kpiCard("👥 Callers using manual calls", num(cur.manualCallers), cur.manualCallers, prv.manualCallers, pt)}
      ${kpiCard("% of all calls", share(cur.manual, cur.acefone + cur.ozonetel + cur.mcube + cur.manual), cur.manual / ((cur.acefone + cur.ozonetel + cur.mcube + cur.manual) || 1) * 100,
        prv.manual / ((prv.acefone + prv.ozonetel + prv.mcube + prv.manual) || 1) * 100, pt, "Manual calls ÷ all calls", (v) => `${(+v).toFixed(1)}%`)}
    </section>
    ${daily && daily.length > 1 ? trendCard("ch-man", "Manual calls over time", "Number above each bar = manual calls · hover for duration and callers") : ""}
    <section class="grid2">
      <section class="card"><div class="card-h"><h3>By vertical</h3><span class="muted small">Click to open</span></div><div class="table-wrap">${gt("vertical", byV)}</div></section>
      <section class="card"><div class="card-h"><h3>By team</h3><span class="muted small">Click to open</span></div><div class="table-wrap">${gt("team", byT)}</div></section>
    </section>
    <section class="card"><div class="card-h"><h3>⚠️ Highest manual calls</h3><span class="muted small">Click a caller to open</span></div>${manHtml}
      ${man.length ? `<div class="downloads"><button class="btn" id="dl-manual">📥 Download manual calls table</button></div>` : ""}</section>`;
  if (daily && daily.length > 1) wireGrain("ch-man", () => {
    const g = byGrain(daily, S.grain, ["n_manual", "manual_dur", "manual_callers"]);
    chart("ch-man", manualBars(g.map(([k]) => grainLabel(k, S.grain)), g));
  });
  $("content").onclick = (e) => {
    const g = e.target.closest("tr[data-g]"); if (g) { S.stack = []; openGroup(g.dataset.k, g.dataset.g); return; }
    const c = e.target.closest("[data-c]"); if (c) { S.stack = []; openCaller(c.dataset.c); }
  };
  if ($("dl-manual")) $("dl-manual").onclick = downloadManual;
}

/* ---------- vertical / team panel ---------- */
async function openGroup(kind, name) {
  const r = range(); S.caller = null; S.group = { kind, name };
  const mode = ["stage", "manual"].includes(S.page) ? S.page : "calls";   // the page it was opened from decides what it shows
  const key = kind === "vertical" ? vKey : (x) => x.TEAM;
  const mine = S.rows.filter((x) => key(x) === name), prevMine = S.prow.filter((x) => key(x) === name);
  const a = groupRows(mine, () => name)[0] || {}, b = groupRows(prevMine, () => name)[0] || {};
  const manUsers = (rows) => rows.filter((x) => +(x._src?.n_manual || 0) > 0).length;
  $("d-kind").textContent = kind === "vertical" ? "Vertical" : "Team";
  $("d-name").textContent = name;
  $("d-back").hidden = !S.stack.length;
  $("d-sub").innerHTML = `${nice(r.from)} – ${nice(r.to)}, compared with ${prevText(r)} (${esc(r.cmp)})`;
  $("d-body").innerHTML = `<p class="muted">Loading…</p>`; $("drawer").hidden = false; $("drawer").scrollTop = 0;
  const f = { ...filt(), ...(kind === "vertical" ? { p_verticals: [name] } : { p_teams: [name] }) };
  const daily = await cached(mode === "stage" ? "ca_stage_trend" : "ca_daily", { p_from: r.from, p_to: r.to, ...f });
  if (!S.group || S.group.name !== name) return;
  const k = (label, v, c, p, fm = num) => !c ? "" : `<div class="kpi"><div class="k">${label}</div><div class="v">${v}</div><div class="d">${chHtml(change(c, p))} vs ${esc(fm(p))}</div></div>`;
  const stTot = stSum;
  // children: teams of a vertical, callers of a team
  let kids = kind === "vertical" ? groupRows(mine, (x) => x.TEAM).map((o) => ({ ...o, name: o.key, team: true,
      manUsers: manUsers(mine.filter((x) => x.TEAM === o.key)) }))
    : mine.map((x) => ({ name: x.CALLER, total_calls: x["TOTAL CALLS"], answered: x._ans, missed: x._miss, calls_3m: x["CALLS > 3 MINS"], dur_3m: x.raw_dur_sec,
      f: x["FOLLOW UPS DONE"], d: x["DISCOVERY DONE"], ro: x["ROADMAPS DONE"], n_manual: +(x._src?.n_manual || 0), manual_dur: +(x._src?.manual_dur || 0),
      approvals: uniqueApprovals(x._src?.approvals), row: x }));
  const measure = (o) => (mode === "stage" ? stTot(o) : mode === "manual" ? o.n_manual : o.total_calls);
  kids = kids.filter((o) => measure(o) > 0).sort((x, y) => (mode === "calls" ? y.dur_3m - x.dur_3m : measure(y) - measure(x)));
  const attr = (o) => (o.team ? `data-team="${esc(o.name)}"` : `data-c="${esc(o.name)}"`);
  const first = (o) => `<td class="l caller-link nw">${esc(o.name)}</td>`;
  let cardsHtml, tableHtml, chartTitle, chartNote, trendTitle;
  if (mode === "manual") {
    cardsHtml = k("✏️ Manual calls", num(a.n_manual), a.n_manual, b.n_manual) + k("⏱ Manual call duration", fmtHm(a.manual_dur), a.manual_dur, b.manual_dur, fmtHm) +
      k("👥 Callers using manual calls", num(manUsers(mine)), manUsers(mine), manUsers(prevMine)) +
      `<div class="kpi"><div class="k">% of all calls</div><div class="v">${a.total_calls ? ((a.n_manual / a.total_calls) * 100).toFixed(1) : "0.0"}%</div></div>`;
    tableHtml = `<table class="agent-table"><thead><tr><th class="l">${kind === "vertical" ? "Team" : "Caller"}</th><th>Manual calls</th><th>Duration</th>
      ${kind === "vertical" ? "<th>Callers using manual</th>" : '<th class="l">Approved by</th>'}<th>% of their calls</th></tr></thead>
      <tbody>${kids.map((o) => `<tr class="click" ${attr(o)}>${first(o)}<td class="num">${num(o.n_manual)}</td><td class="num">${fmtHm(o.manual_dur)}</td>
        ${kind === "vertical" ? `<td class="num">${num(o.manUsers)}</td>` : `<td class="l">${esc(o.approvals)}</td>`}
        <td class="num">${o.total_calls ? ((o.n_manual / o.total_calls) * 100).toFixed(1) : "0.0"}%</td></tr>`).join("")}</tbody></table>`;
    chartTitle = "manual calls"; chartNote = "Number = manual calls · hover for duration"; trendTitle = "Manual calls over time";
  } else if (mode === "stage") {
    cardsHtml = stShown().map(([kk, , , lab]) => k(lab, num(a[kk]), a[kk], b[kk])).join("") +
      (stTotalCol() ? k("Total stage changes", num(stTot(a)), stTot(a), stTot(b)) : "");
    tableHtml = `<table class="agent-table"><thead><tr><th class="l">${kind === "vertical" ? "Team" : "Caller"}</th>${stHeads()}${stTotalCol() ? "<th>Total</th>" : ""}
      <th>Per 100 calls > 3 mins</th></tr></thead>
      <tbody>${kids.map((o) => `<tr class="click" ${attr(o)}>${first(o)}${stCols(o)}
        ${stTotalCol() ? `<td class="num">${num(stTot(o))}</td>` : ""}<td class="num">${o.calls_3m ? ((stTot(o) / o.calls_3m) * 100).toFixed(1) : "–"}</td></tr>`).join("")}</tbody></table>`;
    chartTitle = "stage changes"; chartNote = stNote(); trendTitle = "Stage changes over time";
  } else {
    cardsHtml = k("Total calls", num(a.total_calls), a.total_calls, b.total_calls) + k("Pick up ratio", `${pur(a.answered, a.total_calls)}%`, pur(a.answered, a.total_calls), pur(b.answered, b.total_calls), (v) => `${v}%`) +
      k("Call duration > 3 mins", fmtHm(a.dur_3m), a.dur_3m, b.dur_3m, fmtHm) + k("Avg productive hours", fmtHm(a.active ? a.prod / a.active : 0), a.active ? a.prod / a.active : 0, b.active ? b.prod / b.active : 0, fmtHm) +
      k("Callers", num(a.callers), a.callers, b.callers) + k("Follow ups done", num(a.f), a.f, b.f) + k("Discovery done", num(a.d), a.d, b.d) + k("Roadmaps done", num(a.ro), a.ro, b.ro);
    tableHtml = kind === "vertical"
      ? `<table class="agent-table"><thead><tr><th class="l">Team</th><th>Callers</th><th>Total calls</th><th>Pick up %</th><th>Duration > 3 mins</th><th>Avg productive hours</th><th>Follow ups</th><th>Discovery</th><th>Roadmaps</th></tr></thead>
        <tbody>${kids.map((o) => `<tr class="click" ${attr(o)}>${first(o)}<td class="num">${num(o.callers)}</td><td class="num">${num(o.total_calls)}</td>
          <td class="num">${pur(o.answered, o.total_calls)}%</td><td class="num">${fmtHm(o.dur_3m)}</td><td class="num">${fmtHm(o.active ? o.prod / o.active : 0)}</td>
          <td class="num">${num(o.f)}</td><td class="num">${num(o.d)}</td><td class="num">${num(o.ro)}</td></tr>`).join("")}</tbody></table>`
      : `<table class="agent-table"><thead><tr><th class="l">Caller</th><th>Total calls</th><th class="l">Call status</th><th>Pick up %</th><th>Duration > 3 mins</th><th>Productive hours</th><th class="l">Breaks</th><th>Follow ups</th><th>Discovery</th><th>Roadmaps</th></tr></thead>
        <tbody>${kids.map((o) => { const x = o.row; return `<tr class="click" ${attr(o)}>${first(o)}<td class="num">${num(x["TOTAL CALLS"])}</td><td class="l nw">${esc(x["CALL STATUS"])}</td>
          <td class="num">${esc(x["PICK UP RATIO %"])}</td><td class="num">${esc(x["CALL DURATION > 3 MINS"])}</td><td class="num">${esc(x["PRODUCTIVE HOURS"])}</td><td class="l nw">${esc(x._breaks)}</td>
          <td class="num">${num(x["FOLLOW UPS DONE"])}</td><td class="num">${num(x["DISCOVERY DONE"])}</td><td class="num">${num(x["ROADMAPS DONE"])}</td></tr>`; }).join("")}</tbody></table>`;
    chartTitle = "call duration > 3 mins"; chartNote = "Number = total calls · hover for connected / not connected"; trendTitle = "Trend";
  }
  const showTrend = daily.length > 1;
  $("d-body").innerHTML = `
    <section class="kpis" style="grid-template-columns:repeat(auto-fill,minmax(160px,1fr))">${cardsHtml}</section>
    <section class="card"><div class="card-h"><h3>${kind === "vertical" ? "Teams" : "Callers"} · ${chartTitle}</h3><span class="muted small">${chartNote}</span></div>
      <div class="chart-box" style="height:300px"><canvas id="d-kids"></canvas></div></section>
    ${showTrend ? `<section class="card"><div class="card-h"><h3>${trendTitle}</h3>${segHtml("d-grain", S.dgrain)}</div><div class="chart-box"><canvas id="d-trend"></canvas></div></section>` : ""}
    <section class="card"><div class="card-h"><h3>${kind === "vertical" ? "Teams" : "Callers"}</h3><span class="muted small">Click to open</span></div><div class="table-wrap">${tableHtml}</div></section>`;
  const kg = kids.map((o) => [o.name, o]);
  chart("d-kids", horizontal(mode === "stage" ? stageBars(kg.map(([n]) => n), kg) : mode === "manual" ? manualBars(kg.map(([n]) => n), kg) : trendConfig(kg.map(([n]) => n), kg),
    kg.length, mode === "stage" ? 18 + 17 * stShown().length : 34));
  if (showTrend) {
    const drawD = () => {
      if (mode === "stage") { const g = byGrain(daily.map((x) => ({ call_date: x.call_date, f: +x.followup, d: +x.discovery, ro: +x.roadmap })), S.dgrain, ["f", "d", "ro"]);
        chart("d-trend", stageBars(g.map(([kk]) => grainLabel(kk, S.dgrain)), g)); }
      else if (mode === "manual") { const g = byGrain(daily, S.dgrain, ["n_manual", "manual_dur", "manual_callers"]); chart("d-trend", manualBars(g.map(([kk]) => grainLabel(kk, S.dgrain)), g)); }
      else { const g = byGrain(daily, S.dgrain, ["total_calls", "calls_3m", "dur_3m", "answered", "missed"]); chart("d-trend", trendConfig(g.map(([kk]) => grainLabel(kk, S.dgrain)), g)); }
    };
    drawD();
    $("d-grain").onclick = (e) => { const bt = e.target.closest("button[data-g]"); if (!bt) return; S.dgrain = bt.dataset.g;
      document.querySelectorAll("#d-grain button").forEach((x) => x.classList.toggle("on", x === bt)); drawD(); };
  }
  $("d-body").onclick = (e) => {
    const t = e.target.closest("tr[data-team]"); if (t) { S.stack.push({ kind, name }); openGroup("team", t.dataset.team); return; }
    const c = e.target.closest("tr[data-c]"); if (c) { S.stack.push({ kind, name }); openCaller(c.dataset.c); }
  };
}
$("d-back").addEventListener("click", () => { const p = S.stack.pop(); if (!p) return; p.kind === "caller" ? openCaller(p.name) : openGroup(p.kind, p.name); });
function setPage(p) {
  if (!PAGES[p]) p = "overall";
  S.page = p; $("page-title").textContent = PAGES[p]; document.title = `${PAGES[p]} · Calling Analytics Dashboard`;
  document.querySelectorAll(".side nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === p));
  $("side").classList.remove("open"); $("drawer").hidden = true;
  if (p !== "caller") S.issue = null;
  $("f-stage").hidden = p !== "stage";
  render();
}

/* ---------- caller panel ---------- */
const PARTS = [["Till 2 PM", (h) => h < 14], ["2 PM – 6 PM", (h) => h >= 14 && h < 18], ["6 PM onwards", (h) => h >= 18]];
function sumDays(days, stage) {
  const s = (k) => days.reduce((a, d) => a + (+d[k] || 0), 0), st = (k) => stage.reduce((a, d) => a + (+d[k] || 0), 0);
  return { calls: s("total_calls"), ans: s("answered"), miss: s("missed"), c3: s("calls_3m"), c1520: s("calls_15_20"), c20: s("calls_20p"),
    dur: s("dur_3m"), prod: s("prod_sec"), uniq: s("unique_leads"), bc: s("break_count"), bs: s("break_shown"),
    d: st("discovery"), ro: st("roadmap"), f: st("followup"), days: days.length, manualN: s("n_manual"), manualD: s("manual_dur") };
}
async function openCaller(name) {
  const r = range(); S.caller = name; S.group = null;
  const row = S.rows.find((x) => x.CALLER === name) || {};
  $("d-kind").textContent = "Caller"; $("d-back").hidden = !(S.stack || []).length;
  $("d-name").textContent = name;
  $("d-sub").innerHTML = `${esc(row.TEAM || "")}${row._vertical ? " · " + esc(row._vertical) : ""}<br>${nice(r.from)} – ${nice(r.to)}, compared with ${prevText(r)} (${esc(r.cmp)})`;
  $("d-body").innerHTML = `<p class="muted">Loading…</p>`; $("drawer").hidden = false; $("drawer").scrollTop = 0;
  const [cur, prv] = await Promise.all([cached("ca_caller", { p_from: r.from, p_to: r.to, p_caller: name, p_hours: true }),
    cached("ca_caller", { p_from: r.pf, p_to: r.pt, p_caller: name, p_hours: false })]);
  if (S.caller !== name) return;
  const a = sumDays(cur.days, cur.stage), b = sumDays(prv.days, prv.stage);
  if (S.page === "stage" || S.page === "manual") return callerFocus(S.page, cur, a, b);
  const k = (label, v, c, p, f = num) => !c ? "" : `<div class="kpi"><div class="k">${label}</div><div class="v">${v}</div><div class="d">${chHtml(change(c, p))} vs ${esc(f(p))}</div></div>`;
  const stageBy = new Map(cur.stage.map((s) => [String(s.call_date).slice(0, 10), s]));
  const hrs = cur.hours || [];
  const parts = PARTS.map(([label, f]) => { const h = hrs.filter((x) => f(+x.hr)), s = (kk) => h.reduce((t, x) => t + (+x[kk] || 0), 0);
    return { label, calls: s("calls"), ans: s("answered"), c3: s("calls_3m"), dur: s("dur_3m") }; });
  const partTotal = parts.reduce((t, p) => t + p.calls, 0);
  const hrsNote = cur.hours_from && r.from < cur.hours_from ? `<p class="note">Time of day covers ${nice(cur.hours_from)} onwards (individual calls are kept for 60 days).</p>` : "";
  const approvals = uniqueApprovals(cur.days.flatMap((d) => d.approvals || []));
  const s2 = (kk) => cur.days.reduce((t, d) => t + (+d[kk] || 0), 0);
  const dayRows = [...cur.days].reverse().map((d) => { const dd = String(d.call_date).slice(0, 10), st = stageBy.get(dd) || {};
    return `<tr><td class="l nw">${short(dd)}</td><td class="l nw">${esc((d.io_line || "-").replace(/^\d\d\/\d\d: /, ""))}</td><td class="num">${num(+d.total_calls)}</td>
      <td class="l nw">${+d.answered} Ans / ${+d.missed} Unans</td><td class="num">${pur(+d.answered, +d.total_calls)}%</td><td class="num">${num(+d.calls_3m)}</td>
      <td class="num">${num(+d.calls_15_20)}</td><td class="num">${num(+d.calls_20p)}</td><td class="num">${fmtHm(+d.dur_3m)}</td><td class="num">${fmtHm(+d.prod_sec)}</td>
      <td class="l nw" ${d.break_timing ? `title="${esc(d.break_timing.replace(/^\d\d\/\d\d:\n?/, "").trim())}"` : ""}>${breaksTxt(+d.break_count, +d.break_shown)}${d.break_timing ? " ⓘ" : ""}</td>
      <td class="l nw">${(d.issues || []).map((i) => `<span class="tag bad">${esc(i)}</span>`).join("") || "None"}</td>
      <td class="num">${num(+st.followup || 0)}</td><td class="num">${num(+st.discovery || 0)}</td><td class="num">${num(+st.roadmap || 0)}</td></tr>`; }).join("");
  $("d-body").innerHTML = `
    <section class="kpis" style="grid-template-columns:repeat(auto-fill,minmax(160px,1fr))">
      ${k("Total calls", num(a.calls), a.calls, b.calls)}${k("Pick up ratio", `${pur(a.ans, a.calls)}%`, pur(a.ans, a.calls), pur(b.ans, b.calls), (v) => `${v}%`)}
      ${k("Call duration > 3 mins", fmtHm(a.dur), a.dur, b.dur, fmtHm)}${k("Productive hours", fmtHm(a.prod), a.prod, b.prod, fmtHm)}
      ${k("Unique leads", num(a.uniq), a.uniq, b.uniq)}${k("Breaks (≥ 5 mins)", breaksTxt(a.bc, a.bs), a.bs, b.bs, fmtHm)}
      ${k("Follow ups done", num(a.f), a.f, b.f)}${k("Discovery done", num(a.d), a.d, b.d)}${k("Roadmaps done", num(a.ro), a.ro, b.ro)}
    </section>
    <section class="card"><div class="card-h"><h3>Trend</h3>${segHtml("d-grain", S.dgrain)}</div><div class="chart-box"><canvas id="d-trend"></canvas></div></section>
    <section class="card"><div class="card-h"><h3>Time of day</h3><span class="muted small">By call start time (IST) · ${num(partTotal)} dialler calls</span></div>
      <div class="parts">${parts.map((p) => `<div class="part"><div class="k">${p.label}</div><div class="v">${num(p.calls)} calls</div>
        <div class="s">${partTotal ? Math.round((p.calls / partTotal) * 100) : 0}% of calls · pick up ${pur(p.ans, p.calls)}% · ${num(p.c3)} calls > 3 mins · ${fmtHm(p.dur)} talk time</div></div>`).join("")}</div>
      <div class="chart-box" style="height:230px;margin-top:12px"><canvas id="d-hours"></canvas></div>${hrsNote}</section>
    <section class="card"><h3>Day by day</h3><div class="table-wrap"><table class="agent-table"><thead><tr><th class="l">Date</th><th class="l">In / out</th>
      <th>Total calls</th><th class="l">Call status</th><th>Pick up %</th><th>Calls > 3 mins</th><th>15–20 mins</th><th>20+ mins</th><th>Duration > 3 mins</th>
      <th>Productive hours</th><th class="l" title="Hover a day's breaks for the exact times">Breaks</th><th class="l">Remarks</th><th>Follow ups</th><th>Discovery</th><th>Roadmaps</th></tr></thead>
      <tbody>${dayRows || `<tr><td class="l muted" colspan="15">No calls in these dates.</td></tr>`}</tbody></table></div></section>
    <section class="card"><h3>Dialler split</h3><div class="table-wrap"><table class="agent-table"><tbody>
      <tr><td class="l">🔵 Acefone</td><td class="num">${num(s2("n_acefone"))}</td></tr><tr><td class="l">🟠 Ozonetel</td><td class="num">${num(s2("n_ozonetel"))}</td></tr>
      <tr><td class="l">🟢 MCube</td><td class="num">${num(s2("n_mcube"))}</td></tr>
      <tr><td class="l">✏️ Manual</td><td class="num">${num(s2("n_manual"))} calls · ${fmtHm(s2("manual_dur"))}${approvals !== "—" ? ` · approved by ${esc(approvals)}` : ""}</td></tr>
    </tbody></table></div></section>`;
  const drawD = () => {
    const g = byGrain(cur.days, S.dgrain, ["total_calls", "calls_3m", "dur_3m", "answered", "missed"]);
    chart("d-trend", trendConfig(g.map(([kk]) => grainLabel(kk, S.dgrain)), g));
  };
  drawD();
  $("d-grain").onclick = (e) => { const bt = e.target.closest("button[data-g]"); if (!bt) return; S.dgrain = bt.dataset.g;
    document.querySelectorAll("#d-grain button").forEach((x) => x.classList.toggle("on", x === bt)); drawD(); };
  const hset = Array.from({ length: 24 }, (_, h) => hrs.find((x) => +x.hr === h) || { hr: h, calls: 0, answered: 0, calls_3m: 0 });
  const lo = Math.min(9, ...hrs.map((x) => +x.hr)), hi = Math.max(21, ...hrs.map((x) => +x.hr)), shown = hset.slice(lo, hi + 1);
  const hl12 = (h) => `${((h + 11) % 12) + 1} ${h < 12 ? "AM" : "PM"}`;
  chart("d-hours", { type: "bar", data: { labels: shown.map((x) => hl12(+x.hr)), datasets: [
      { label: "Calls", data: shown.map((x) => +x.calls), backgroundColor: shown.map((x) => +x.hr < 14 ? css("--gold") : +x.hr < 18 ? css("--c2") : css("--c3")), borderRadius: 3 },
      { label: "Calls > 3 mins", data: shown.map((x) => +x.calls_3m), backgroundColor: css("--ly"), borderRadius: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, layout: { padding: { top: 16 } }, plugins: { legend: { position: "bottom" },
      barLabels: { dataset: 0, labels: shown.map((x) => (+x.calls ? num(+x.calls) : "")) } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true } } } });
}
/* caller panel opened from Stage Changed Performance or Manual Calls: only that subject */
function callerFocus(mode, cur, a, b) {
  const k = (label, v, c, p, f = num) => `<div class="kpi"><div class="k">${label}</div><div class="v">${v}</div><div class="d">${chHtml(change(c, p))} vs ${esc(f(p))}</div></div>`;
  if (mode === "stage") {
    const rows = cur.stage.map((s) => ({ call_date: s.call_date, f: +s.followup, d: +s.discovery, ro: +s.roadmap }));
    const per100 = (n) => (a.c3 ? ((n / a.c3) * 100).toFixed(1) : "–");
    $("d-body").innerHTML = `
      <section class="kpis" style="grid-template-columns:repeat(auto-fill,minmax(160px,1fr))">
        ${stShown().map(([kk, , , lab]) => k(lab, num(a[kk]), a[kk], b[kk])).join("")}
        ${stTotalCol() ? k("Total stage changes", num(stSum(a)), stSum(a), stSum(b)) : ""}
      </section>
      <p class="muted small" style="margin:0">Per 100 calls over 3 minutes: ${stShown().map(([kk, l]) => `${stName(l).toLowerCase()} ${per100(a[kk])}`).join(" · ")} (${num(a.c3)} calls > 3 mins)</p>
      <section class="card"><div class="card-h"><h3>Stage changes over time</h3>${segHtml("d-grain", S.dgrain)}</div><div class="chart-box" style="height:300px"><canvas id="d-trend"></canvas></div></section>
      <section class="card"><h3>Day by day</h3><div class="table-wrap"><table class="agent-table"><thead><tr><th class="l">Date</th>${stHeads()}${stTotalCol() ? "<th>Total</th>" : ""}</tr></thead>
        <tbody>${[...rows].filter((x) => stSum(x) > 0).reverse().map((x) => `<tr><td class="l nw">${short(String(x.call_date).slice(0, 10))}</td>${stCols(x)}
          ${stTotalCol() ? `<td class="num">${num(stSum(x))}</td>` : ""}</tr>`).join("") || `<tr><td class="l muted" colspan="5">No stage changes in these dates.</td></tr>`}</tbody></table></div></section>`;
    const drawD = () => { const g = byGrain(rows, S.dgrain, ["f", "d", "ro"]); chart("d-trend", stageBars(g.map(([kk]) => grainLabel(kk, S.dgrain)), g)); };
    drawD();
    $("d-grain").onclick = (e) => { const bt = e.target.closest("button[data-g]"); if (!bt) return; S.dgrain = bt.dataset.g;
      document.querySelectorAll("#d-grain button").forEach((x) => x.classList.toggle("on", x === bt)); drawD(); };
    return;
  }
  const days = cur.days.filter((d) => +d.n_manual > 0);
  const s2 = (arr, kk) => arr.reduce((t, d) => t + (+d[kk] || 0), 0);
  const mc = s2(cur.days, "n_manual"), md = s2(cur.days, "manual_dur");
  const allCalls = s2(cur.days, "total_calls");
  $("d-body").innerHTML = `
    <section class="kpis" style="grid-template-columns:repeat(auto-fill,minmax(160px,1fr))">
      ${k("✏️ Manual calls", num(mc), mc, b.manualN)}${k("⏱ Manual call duration", fmtHm(md), md, b.manualD, fmtHm)}
      <div class="kpi"><div class="k">% of all calls</div><div class="v">${allCalls ? ((mc / allCalls) * 100).toFixed(1) : "0.0"}%</div><div class="d muted">${num(allCalls)} calls in total</div></div>
      <div class="kpi"><div class="k">Approved by</div><div class="v" style="font-size:15px">${esc(uniqueApprovals(cur.days.flatMap((d) => d.approvals || [])))}</div></div>
    </section>
    <section class="card"><div class="card-h"><h3>Manual calls over time</h3>${segHtml("d-grain", S.dgrain)}</div><div class="chart-box" style="height:280px"><canvas id="d-trend"></canvas></div></section>
    <section class="card"><h3>Day by day</h3><div class="table-wrap"><table class="agent-table"><thead><tr><th class="l">Date</th><th>Manual calls</th><th>Duration</th><th class="l">Approved by</th></tr></thead>
      <tbody>${[...days].reverse().map((d) => `<tr><td class="l nw">${short(String(d.call_date).slice(0, 10))}</td><td class="num">${num(+d.n_manual)}</td><td class="num">${fmtHm(+d.manual_dur)}</td>
        <td class="l">${esc(uniqueApprovals(d.approvals))}</td></tr>`).join("") || `<tr><td class="l muted" colspan="4">No manual calls in these dates.</td></tr>`}</tbody></table></div></section>`;
  const drawD = () => { const g = byGrain(cur.days, S.dgrain, ["n_manual", "manual_dur"]); chart("d-trend", manualBars(g.map(([kk]) => grainLabel(kk, S.dgrain)), g)); };
  drawD();
  $("d-grain").onclick = (e) => { const bt = e.target.closest("button[data-g]"); if (!bt) return; S.dgrain = bt.dataset.g;
    document.querySelectorAll("#d-grain button").forEach((x) => x.classList.toggle("on", x === bt)); drawD(); };
}
$("d-close").addEventListener("click", () => { $("drawer").hidden = true; S.caller = null; });
$("d-cdr").addEventListener("click", () => { if (S.caller) downloadCdr(S.caller);
  else if (S.group) downloadCdr(null, S.group.kind === "vertical" ? { p_verticals: [S.group.name] } : { p_teams: [S.group.name] }, S.group.name); });

/* ---------- downloads (same columns and text as Streamlit's CSV files) ---------- */
function csvCell(v) {
  if (v == null) return "";
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
const csvLine = (vals) => vals.map(csvCell).join(",") + "\n";
function save(name, parts) {
  const url = URL.createObjectURL(new Blob(parts, { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name }); document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
const CDR_COLS = ["client_number", "call_datetime", "call_starttime_clean", "call_endtime_clean", "call_duration", "status", "direction",
  "service", "reason", "call_owner", "Call Date", "updated_at_ampm", "Team Name", "Vertical", "Analyst", "source"];
async function downloadCdr(caller = null, extra = null, label = null) {
  if (!S.last) return;
  const btn = caller || extra ? $("d-cdr") : $("dl-cdr"), msg = $("dl-msg") || { set textContent(_) {} }; btn.disabled = true;
  try {
    const parts = [csvLine(CDR_COLS)]; let n = 0;
    for (const src of ["A", "O", "N", "M"]) {   // Streamlit order: Acefone, Ozonetel, Manual, MCube
      let after = null;
      for (;;) {
        const page = await rpc("ca_cdr", { p_from: S.last.from, p_to: S.last.to, p_src: src, p_after: after, p_limit: 20000, ...S.last.f, ...(extra || {}), p_caller: caller });
        if (!page.length) break;
        for (const r of page) parts.push(csvLine(CDR_COLS.map((c) => r[c])));
        n += page.length; after = page[page.length - 1].id; msg.textContent = `Preparing… ${num(n)} calls`;
        if (page.length < 20000) break;
      }
    }
    save(caller || label ? `CDR_LOG_${(caller || label).replace(/[^\w]+/g, "_")}.csv` : "CDR_LOG.csv", parts); msg.textContent = `${num(n)} calls downloaded.`;
  } catch (e) { msg.textContent = "Download failed: " + (e.message || e); }
  btn.disabled = false;
}
const SC_COLS = ["Date", "FirstName", "LastName", "EmailAddress", "Phone", "Alternate_Phone_Number", "New_Contact_Stage", "Stage_Changed_By",
  "Team Name", "Vertical", "StageChangeComment"];
async function downloadStage() {
  if (!S.last) return;
  const btn = $("dl-stage"), msg = $("dl-msg"); btn.disabled = true; msg.textContent = "Preparing…";
  try {
    const rows = await rpc("ca_stage_export", { p_from: S.last.from, p_to: S.last.to });
    save("Stage_Changed_Report.csv", rows.length ? [csvLine(SC_COLS), ...rows.map((r) => csvLine(SC_COLS.map((c) => r[c])))]
      : ["No stage changed data for this period."]);
    msg.textContent = `${num(rows.length)} stage changes downloaded.`;
  } catch (e) { msg.textContent = "Download failed: " + (e.message || e); }
  btn.disabled = false;
}

async function downloadAgent() {   // all 18 Streamlit columns, ranked by call duration, TOTAL row last
  const rows = S.rows.filter((x) => shownInTable(x) && (!S.issue || x._issues.includes(S.issue)));
  save("Agent_Performance_Table.csv", [csvLine(FULL_COLS), ...[...rows, totalsRow(rows)].map((x) => csvLine(FULL_COLS.map((c) => x[c])))]);
}
async function downloadManual() {
  save("Highest_Manual_Calls.csv", [csvLine(MAN_COLS), ...[...S.manual, manTotal(S.manual)].map((x) => csvLine(MAN_COLS.map((c) => x[c])))]);
}

/* ---------- Month filter: every month that has data, with the dates it covers, e.g. "October 2026 (1 - 27)" ---------- */
let MONTHS = [];   // [{ label, from, to }] newest first
function buildMonths() {
  const lo = S.meta?.min_date, hiData = S.meta?.max_date, t = istToday();
  MONTHS = []; if (!lo) return;
  const hi = hiData && hiData < t ? t : (hiData || t);
  for (let m = lo.slice(0, 8) + "01"; m <= hi; m = shiftMonth(m, 1)) {
    const [y, mo] = m.split("-").map(Number), last = `${m.slice(0, 8)}${String(daysIn(y, mo)).padStart(2, "0")}`;
    const from = m < lo ? lo : m, to = last > hi ? hi : last;
    const name = D(m).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
    MONTHS.unshift({ label: `${name} (${+from.slice(8)} - ${+to.slice(8)})`, from, to });
  }
  MS.month.opts = MONTHS.map((x) => x.label);
}
/* picked months -> From / To; months in between are ticked too, so the dates stay one unbroken period */
function applyMonths() {
  const idx = MONTHS.map((x, i) => (S.sel.month.has(x.label) ? i : -1)).filter((i) => i >= 0);
  if (!idx.length) { setPreset("today"); return false; }
  const a = Math.min(...idx), b = Math.max(...idx);
  for (let i = a; i <= b; i++) S.sel.month.add(MONTHS[i].label);
  $("from").value = MONTHS[b].from; $("to").value = MONTHS[a].to;
  S.preset = "months"; document.querySelectorAll("#presets button").forEach((x) => x.classList.remove("on"));
  msButton("month"); const box = $("ms-month"); if (box && !box.querySelector(".ms-pop").hidden) msList("month");
  return true;
}
function clearMonths() { if (!S.sel.month.size) return; S.sel.month.clear(); msButton("month"); }

/* ---------- multi-select filters (with search) ---------- */
const MS = { month: { all: "All months", one: "month", opts: [] }, vertical: { all: "All verticals", one: "vertical", opts: [] }, team: { all: "All teams", one: "team", opts: [] },
  caller: { all: "All callers", one: "caller", opts: [] }, stage: { all: "All stages", one: "stage", opts: STAGES.map(([, l]) => l) } };
let msTimer;
function msChanged(id) { clearTimeout(msTimer); msTimer = setTimeout(() => { if (id === "month") { if (applyMonths()) render(); return; } buildOptions(); render(); }, id === "stage" ? 50 : 450); }
/* team list follows the chosen verticals; caller list follows the chosen verticals and teams */
function buildOptions() {
  const V = S.sel.vertical, T = S.sel.team, cs = S.meta?.callers || [];
  MS.vertical.opts = S.meta?.verticals || [];
  MS.team.opts = V.size ? [...new Set(cs.filter((c) => V.has(c.vertical)).map((c) => c.team).filter(Boolean))].sort() : (S.meta?.teams || []);
  MS.caller.opts = cs.filter((c) => (!V.size || V.has(c.vertical)) && (!T.size || T.has(c.team))).map((c) => c.caller);
  MS.caller.sub = Object.fromEntries(cs.map((c) => [c.caller, c.team || "Others"]));
  for (const id of Object.keys(MS)) { msButton(id); const box = $(`ms-${id}`); if (box && !box.querySelector(".ms-pop").hidden) msList(id); }
}
function msButton(id) {
  const set = S.sel[id], m = MS[id], b = document.querySelector(`#ms-${id} .ms-btn`); if (!b) return;
  b.textContent = !set.size ? m.all : set.size === 1 ? [...set][0] : `${set.size} ${m.one}s selected`;
  b.classList.toggle("on", set.size > 0); b.title = [...set].join(", ");
}
function msList(id) {
  const box = $(`ms-${id}`), q = box.querySelector(".ms-q").value.trim().toLowerCase(), set = S.sel[id];
  const hits = MS[id].opts.filter((o) => !q || o.toLowerCase().includes(q));
  box.querySelector(".ms-list").innerHTML = [...hits.filter((o) => set.has(o)), ...hits.filter((o) => !set.has(o))].map((o) =>
    `<label class="ms-item"><input type="checkbox" value="${esc(o)}" ${set.has(o) ? "checked" : ""}><span>${esc(o)}</span>${MS[id].sub ? `<small>${esc(MS[id].sub[o] || "")}</small>` : ""}</label>`).join("")
    || `<div class="ms-more">No match</div>`;
  box._hits = hits;
}
function msInit(id) {
  const box = $(`ms-${id}`);
  box.innerHTML = `<button type="button" class="ms-btn"></button><div class="ms-pop" hidden><input class="ms-q" placeholder="Search ${MS[id].one}s…">
    <div class="ms-act"><button type="button" data-a="all">Select shown</button><button type="button" data-a="none">Clear</button></div><div class="ms-list"></div></div>`;
  const pop = box.querySelector(".ms-pop");
  box.querySelector(".ms-btn").onclick = () => { document.querySelectorAll(".ms-pop").forEach((p) => p !== pop && (p.hidden = true));
    pop.hidden = !pop.hidden; if (!pop.hidden) { msList(id); box.querySelector(".ms-q").focus(); } };
  box.querySelector(".ms-q").oninput = () => msList(id);
  box.querySelector(".ms-list").onchange = (e) => { const v = e.target.value; e.target.checked ? S.sel[id].add(v) : S.sel[id].delete(v); msButton(id); msChanged(id); };
  box.querySelector(".ms-act").onclick = (e) => { const a = e.target.dataset.a; if (!a) return;
    if (a === "none") S.sel[id].clear(); else (box._hits || []).forEach((o) => S.sel[id].add(o));
    msList(id); msButton(id); msChanged(id); };
  msButton(id);
}
document.addEventListener("click", (e) => { if (!e.target.closest(".ms")) document.querySelectorAll(".ms-pop").forEach((p) => (p.hidden = true)); });

/* ---------- dates / presets ---------- */
function markPreset(p) { S.preset = p; document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.dataset.p === p)); }
function setPreset(p) { clearMonths(); const [f, t] = preset(p); $("from").value = f; $("to").value = t; markPreset(p); render(); }
function clearFilters() { S.issue = null; S.sel.team.clear(); S.sel.vertical.clear(); S.sel.caller.clear(); S.sel.stage.clear(); S.sel.month.clear(); buildOptions(); setPreset("today"); }

/* ---------- freshness + update button ---------- */
function showFresh(run) {
  if (!run) { $("fresh").textContent = "Not refreshed yet"; return; }
  const t = new Date(run.run_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  $("fresh").innerHTML = run.status === "ok" ? `Data updated ${t} IST` : `<span class="down">Last update failed ${t} IST</span>`;
  $("fresh").title = run.status === "ok" ? "" : (run.error || "");
}
async function lastRun() { const { data } = await sb.from("refresh_log").select("run_at,status,detail").order("run_at", { ascending: false }).limit(1);
  const r = data && data[0]; return r && { run_at: r.run_at, status: r.status, updated_at_ampm: r.detail?.updated_at_ampm, error: r.detail?.error }; }
async function update() {
  const btn = $("update"); btn.disabled = true;
  const before = (await lastRun())?.run_at || "";
  const { data, error } = await sb.rpc("request_refresh");
  if (error) { $("update-msg").textContent = /token/i.test(error.message) ? "Update isn't set up yet (GitHub token missing in Supabase)." : error.message; btn.disabled = false; return; }
  if (data === "recent") { $("update-msg").textContent = "The data was updated less than an hour ago. To keep the database fast, Update runs at most once an hour."; btn.disabled = false; return; }
  $("update-msg").textContent = data === "already_running" ? "An update is already running…" : "Update started. This takes about 5–15 minutes (the diallers limit how fast calls can be read)…";
  const t0 = Date.now();
  const poll = async () => {
    const run = await lastRun();
    if (run && run.run_at > before) { showFresh(run); $("update-msg").textContent = run.status === "ok" ? "Updated." : "Update failed; see the note above.";
      btn.disabled = false; CACHE.clear(); await loadMeta(); render(); return; }
    if (Date.now() - t0 > 40 * 60e3) { $("update-msg").textContent = "Still running; check back in a few minutes."; btn.disabled = false; return; }
    setTimeout(poll, 30000);
  };
  setTimeout(poll, 45000);
}

/* ---------- theme ---------- */
const SUN = `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`;
const MOON = `<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
function themeButton() { const dark = document.documentElement.dataset.theme !== "light"; $("theme").innerHTML = dark ? `${SUN} Light` : `${MOON} Dark`; }
$("theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next; try { localStorage.setItem("ca-theme", next); } catch (_) {}
  themeButton(); if (!$("app").hidden) render();
});

/* ---------- auth + start ---------- */
async function loadMeta() {
  S.meta = await rpc("ca_meta", {});
  buildMonths(); buildOptions();
  if (S.meta?.min_date) { $("from").min = $("to").min = S.meta.min_date; }
  showFresh(S.meta?.last_run);
}
// Sales Intelligence Portal (Amit, 18 Nov 2026): this dashboard has no sign-in of its own. Without a session (or without access)
// it goes to the portal, which checks the sign-in and access and comes back with a one-time sign-in link.
const PORTAL = "https://sales-intelligence-portal.amitray.workers.dev/";
function toPortal(key) {
  let last = 0; try { last = +sessionStorage.getItem("si-hop") || 0; } catch (_) {}
  if (Date.now() - last < 30000) return false;   // just came back from the portal and still not in: show the card, never loop
  try { sessionStorage.setItem("si-hop", String(Date.now())); } catch (_) {}
  location.replace(`${PORTAL}go.html?d=${key}`); return true;
}
// who is signed in, shown in the sidebar box (name from the email: amit.ray@… → Amit Ray)
function fillMe(email) {
  const e = String(email || ""), n = e.split("@")[0].split(/[._-]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
  const box = document.getElementById("si-me"); if (!box) return;
  box.querySelector(".si-av").textContent = (n || "?")[0]; box.querySelector("b").textContent = n || e; box.querySelector("small").textContent = e;
  box.title = e; box.hidden = false;
}
async function start() {
  themeButton();
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { if (toPortal("calling")) return; $("auth").hidden = false; $("app").hidden = true; return; }
  const { data: ok } = await sb.rpc("is_allowed");
  if (!ok) { if (toPortal("calling")) return; $("auth").hidden = false; $("app").hidden = true;
    $("auth-msg").textContent = `${session.user.email} has no access to this dashboard. Ask a portal admin to give it in the Sales Intelligence Portal.`; return; }
  $("auth").hidden = true; $("app").hidden = false; fillMe(session.user.email);
  // Update data for this dashboard's admins only (Amit, 25 Nov 2026); viewers see when the data was updated
  sb.rpc("ca_can_update").then(({ data }) => { $("update").hidden = !data; });
  Object.keys(MS).forEach(msInit);
  try { await loadMeta(); } catch (e) { $("content").innerHTML = `<div class="card empty">Could not load: ${esc(e.message || e)}</div>`; return; }
  const [f, t] = preset("today"); $("from").value = f; $("to").value = t; markPreset("today");
  setPage(location.hash.slice(1));
}
$("signout").addEventListener("click", async () => { await sb.auth.signOut({ scope: "local" }); location.replace(PORTAL + "?signout=1"); });

/* ---------- events ---------- */
window.addEventListener("hashchange", () => setPage(location.hash.slice(1)));
document.querySelectorAll("#presets button").forEach((b) => b.addEventListener("click", () => setPreset(b.dataset.p)));
["from", "to"].forEach((id) => $(id).addEventListener("change", () => { clearMonths(); markPreset(null); render(); }));
$("clear-filters").addEventListener("click", clearFilters);
$("update").addEventListener("click", update);
$("menu").addEventListener("click", () => {
  if (matchMedia("(max-width: 820px)").matches) { $("side").classList.toggle("open"); return; }
  const c = $("app").classList.toggle("collapsed"); try { localStorage.setItem("ca-side", c ? "hidden" : "shown"); } catch (_) {}
});
try { if (localStorage.getItem("ca-side") === "hidden") $("app").classList.add("collapsed"); } catch (_) {}
start();
