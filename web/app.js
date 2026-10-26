/* Calling Analytics Dashboard — Dynamic Dashboard. Data: Supabase RPC ca_meta / ca_report / ca_daily / ca_caller / ca_unique_leads /
   ca_cdr / ca_stage_export. Numbers follow the Streamlit calling dashboard exactly (docs/definitions.md); the pipeline pre-computes each caller's day. */
const sb = supabase.createClient(CA_CONFIG.supabaseUrl, CA_CONFIG.supabaseKey);
const $ = (id) => document.getElementById(id);
const S = { meta: null, seq: 0, sel: { team: new Set(), vertical: new Set(), caller: new Set() }, last: null, preset: null, charts: {},
  sort: { k: "raw_dur_sec", dir: -1 }, issue: null, grain: "day", dgrain: "day", rows: [], caller: null };

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
    const ctx = chart.ctx; ctx.save(); ctx.fillStyle = css("--ink"); ctx.font = "600 11px Inter, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    meta.data.forEach((el, i) => { const t = o.labels[i]; if (t == null || t === "") return; ctx.fillText(String(t), el.x, Math.min(el.y, el.base) - 3); });
    ctx.restore();
  },
};
function chart(id, cfg) {
  if (S.charts[id]) S.charts[id].destroy();
  const el = $(id); if (!el) return;
  Chart.defaults.color = css("--muted"); Chart.defaults.borderColor = css("--line"); Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: css("--raise"), titleColor: css("--gold-2"), bodyColor: css("--ink"),
    borderColor: css("--line-2"), borderWidth: 1, padding: 10 });
  cfg.plugins = [barLabels];
  S.charts[id] = new Chart(el, cfg);
}
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
      scales: { x: { grid: { display: false } },
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
    _breaks: "-", _bsec: -1, _issues: [], _stage: true, _vertical: s.vertical });
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

/* ---------- render ---------- */
function busy(on) { $("busy").hidden = !on; $("content").classList.toggle("loading", on); }
async function render() {
  const seq = ++S.seq, r = range();
  if (!r.from || !r.to || r.from > r.to) return;
  $("page-sub").innerHTML = `Duration period · <b>${ddmmyyyy(r.from)} to ${ddmmyyyy(r.to)}</b> &nbsp;·&nbsp; compared with <b>${prevText(r)}</b> (${esc(r.cmp)})`;
  busy(true);
  try {
    const f = filt(), a = { p_from: r.from, p_to: r.to, ...f }, p = { p_from: r.pf, p_to: r.pt, ...f };
    const [rep, uniq, prep, puniq, daily] = await Promise.all([cached("ca_report", a), cached("ca_unique_leads", a),
      cached("ca_report", p), cached("ca_unique_leads", p), spanDays(r.from, r.to) > 1 ? cached("ca_daily", a) : null]);
    if (seq !== S.seq) return;
    S.last = { from: r.from, to: r.to, f };
    draw(r, rep, +uniq, prep, +puniq, daily);
  } catch (e) {
    if (seq === S.seq) $("content").innerHTML = `<div class="card empty">Could not load data: ${esc(e.message || e)}</div>`;
  }
  if (seq === S.seq) busy(false);
}

function summary(rep, uniq) {
  const rows = buildReport(rep), src = rep.rows, ssum = (k) => src.reduce((a, r) => a + (+r[k] || 0), 0), t = totalsRow(rows);
  return { rows, t, acefone: ssum("n_acefone"), ozonetel: ssum("n_ozonetel"), mcube: ssum("n_mcube"), manual: ssum("n_manual"), uniq,
    avgProd: rows.length ? rows.reduce((a, r) => a + r.raw_prod_sec, 0) / rows.length : 0 };
}

function draw(r, rep, uniq, prep, puniq, daily) {
  if (!rep.rows.length && !rep.stage_only.length) {
    $("content").innerHTML = `<div class="card empty">No data found for the selected period and filters.</div>`; return;
  }
  const cur = summary(rep, uniq), prv = summary(prep, puniq), rows = cur.rows, total = cur.t;
  S.rows = rows; S.manual = manualRows(rep.rows);
  const pt = prevText(r);
  const t1 = rows[0], t2 = topBy(rows, "TOTAL CALLS"), t3 = topBy(rows, "20+ MIN CALLS"),
    t4 = topBy(rows, "FOLLOW UPS DONE"), t5 = topBy(rows, "DISCOVERY DONE"), t6 = topBy(rows, "ROADMAPS DONE");
  const hl = (label, x, val) => `<div class="hl"><div class="k">${label}</div><div class="n caller-open" data-c="${esc(x.CALLER)}">${esc(x.CALLER)}</div><div class="d">${esc(val)}</div></div>`;
  const kpi = (label, v, c, p, hint = "", fmtPrev = num) => `<div class="kpi" ${hint ? `title="${esc(hint)}"` : ""}><div class="k">${label}</div><div class="v">${v}</div>
    <div class="d">${chHtml(change(c, p))} vs ${esc(fmtPrev(p))} <span class="muted small">(${esc(pt)})</span></div></div>`;

  // remarks summary
  const issueCount = Object.fromEntries(ISSUES.map((i) => [i, rows.filter((x) => x._issues.includes(i)).length]));
  const chips = `<div class="chips" id="chips"><button class="chip ${S.issue ? "" : "on"}" data-i="">All callers<b>${num(rows.length)}</b></button>${ISSUES.map((i) =>
    `<button class="chip ${S.issue === i ? "on" : ""} ${issueCount[i] ? "" : "zero"}" data-i="${esc(i)}">${esc(i)}<b>${num(issueCount[i])}</b></button>`).join("")}</div>`;

  // team summary
  const teams = new Map();
  for (const x of rows) {
    const k = x.TEAM, o = teams.get(k) || { team: k, callers: 0, calls: 0, ans: 0, c3: 0, dur: 0, prod: 0, active: 0, d: 0, ro: 0, f: 0 };
    o.callers += 1; o.calls += x["TOTAL CALLS"]; o.ans += x._ans; o.c3 += x["CALLS > 3 MINS"]; o.dur += x.raw_dur_sec;
    if (!x._stage) { o.prod += x.raw_prod_sec; o.active += 1; }
    o.d += x["DISCOVERY DONE"]; o.ro += x["ROADMAPS DONE"]; o.f += x["FOLLOW UPS DONE"]; teams.set(k, o);
  }
  const teamRows = [...teams.values()].sort((a, b) => b.dur - a.dur);
  const teamHtml = `<table class="agent-table" id="team-table"><thead><tr><th class="l">Team</th><th>Callers</th><th>Total calls</th><th>Pick up %</th>
    <th>Calls > 3 mins</th><th>Call duration > 3 mins</th><th>Avg productive hours</th><th>Follow ups</th><th>Discovery</th><th>Roadmaps</th></tr></thead>
    <tbody>${teamRows.map((o) => `<tr class="click" data-team="${esc(o.team)}"><td class="l caller-link">${esc(o.team)}</td><td class="num">${num(o.callers)}</td>
      <td class="num">${num(o.calls)}</td><td class="num">${pur(o.ans, o.calls)}%</td><td class="num">${num(o.c3)}</td><td class="num">${fmtHm(o.dur)}</td>
      <td class="num">${fmtHm(o.active ? o.prod / o.active : 0)}</td><td class="num">${num(o.f)}</td><td class="num">${num(o.d)}</td><td class="num">${num(o.ro)}</td></tr>`).join("")}</tbody></table>`;

  const man = S.manual;
  const manHtml = !man.length ? "" : `<h4 class="section-h">⚠️ Highest manual calls</h4><section class="card"><div class="table-wrap">
    <table class="agent-table"><thead><tr>${MAN_COLS.map((c) => `<th class="${["MANUAL CALLS COUNT", "MANUAL CALLS DURATION"].includes(c) ? "" : "l"}">${c}</th>`).join("")}</tr></thead>
    <tbody>${[...man, manTotal(man)].map((x) => `<tr class="${x._total ? "total" : "click"}" ${x._total ? "" : `data-c="${esc(x.CALLER)}"`}>${MAN_COLS.map((c) =>
      `<td class="${["MANUAL CALLS COUNT", "MANUAL CALLS DURATION"].includes(c) ? "num" : "l"} ${c === "CALLER" && !x._total ? "caller-link" : ""}">${typeof x[c] === "number" ? num(x[c]) : esc(x[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <div class="downloads"><button class="btn" id="dl-manual">📥 Download manual calls table</button></div></section>`;

  $("content").innerHTML = `
    <h4 class="section-h">🏆 Top 6 performance highlights</h4>
    <section class="highlights">
      ${hl("🥇 Top performer", t1, `${t1["CALL DURATION > 3 MINS"]} Duration`)}${hl("✆ Highest calls", t2, `${t2["TOTAL CALLS"]} Total Calls`)}
      ${hl("🗣️ Deep engagement", t3, `${t3["20+ MIN CALLS"]} Long Calls`)}${hl("📋 Highest follow ups", t4, `${t4["FOLLOW UPS DONE"]} Follow Ups Done`)}
      ${hl("🔍 Highest discovery", t5, `${t5["DISCOVERY DONE"]} Discoveries Done`)}${hl("🗺️ Highest roadmap", t6, `${t6["ROADMAPS DONE"]} Roadmaps Done`)}
    </section>
    <h4 class="section-h">Summary metrics</h4>
    <section class="kpis k9">
      ${kpi("🔵 Acefone calls", num(cur.acefone), cur.acefone, prv.acefone)}${kpi("🟠 Ozonetel calls", num(cur.ozonetel), cur.ozonetel, prv.ozonetel)}
      ${kpi("🟢 MCube calls", num(cur.mcube), cur.mcube, prv.mcube)}${kpi("✏️ Manual calls", num(cur.manual), cur.manual, prv.manual)}
      ${kpi("👤 Unique leads", num(cur.uniq), cur.uniq, prv.uniq, "Unique numbers dialled each day, added up over the days")}
      ${kpi("⏱ Avg prod hrs", fmtHm(cur.avgProd), cur.avgProd, prv.avgProd, "", fmtHm)}
      ${kpi("📋 Follow ups done", num(total["FOLLOW UPS DONE"]), total["FOLLOW UPS DONE"], prv.t["FOLLOW UPS DONE"])}
      ${kpi("🔍 Discovery done", num(total["DISCOVERY DONE"]), total["DISCOVERY DONE"], prv.t["DISCOVERY DONE"])}
      ${kpi("🗺️ Roadmaps done", num(total["ROADMAPS DONE"]), total["ROADMAPS DONE"], prv.t["ROADMAPS DONE"])}
    </section>
    <h4 class="section-h">Remarks summary</h4>
    <section class="card"><p class="muted small" style="margin:0 0 10px">Callers flagged at least once in these dates · click one to show only those callers in the table</p>${chips}</section>
    ${daily ? `<h4 class="section-h">Daily trend</h4><section class="card"><div class="card-h"><h3>Call duration > 3 mins</h3><span class="muted small">Number above each bar = total calls · hover for connected / not connected</span>${segHtml("grain", S.grain)}</div>
      <div class="chart-box" style="height:340px"><canvas id="ch-trend"></canvas></div></section>` : ""}
    <h4 class="section-h">Team summary</h4>
    <section class="card"><p class="muted small" style="margin:0 0 10px">Click a team to show only that team</p><div class="table-wrap">${teamHtml}</div></section>
    <h4 class="section-h">Agent performance table</h4>
    <section class="card"><p class="muted small" style="margin:0 0 10px">Click a caller for their trend, time of day and day-by-day detail · click a heading to sort</p>
      <div class="table-wrap tall" id="board"></div>
      <div class="downloads">
        <button class="btn primary" id="dl-agent">📥 Download agent performance table</button>
        <button class="btn" id="dl-cdr">📥 Download CDR</button>
        <button class="btn" id="dl-stage">📥 Download Stage Changed Report</button>
        <span class="dl-msg" id="dl-msg"></span>
      </div></section>
    ${manHtml}`;
  drawBoard();
  if (daily) drawTrend(daily);

  $("content").onclick = (e) => {
    const chip = e.target.closest(".chip"); if (chip) { S.issue = chip.dataset.i || null; document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("on", (c.dataset.i || null) === S.issue)); drawBoard(); return; }
    const g = e.target.closest("#grain button[data-g]"); if (g) { S.grain = g.dataset.g; document.querySelectorAll("#grain button").forEach((b) => b.classList.toggle("on", b === g)); drawTrend(daily); return; }
    const team = e.target.closest("tr[data-team]"); if (team) { S.sel.team = new Set([team.dataset.team]); buildOptions(); render(); return; }
    const th = e.target.closest("#board th[data-k]");
    if (th) { const k = th.dataset.k; S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : (k === "CALLER" || k === "TEAM" ? 1 : -1) }; drawBoard(); return; }
    const c = e.target.closest("[data-c]"); if (c) openCaller(c.dataset.c);
  };
  $("dl-agent").onclick = downloadAgent;
  $("dl-cdr").onclick = () => downloadCdr();
  $("dl-stage").onclick = downloadStage;
  if ($("dl-manual")) $("dl-manual").onclick = downloadManual;
}

const shownInTable = (x) => x["TOTAL CALLS"] > 0 && x.raw_dur_sec > 0;   // 0 calls or 0 duration > 3 mins: not listed
function drawBoard() {
  let rows = S.rows.filter((x) => shownInTable(x) && (!S.issue || x._issues.includes(S.issue)));
  const { k, dir } = S.sort;
  rows = [...rows].sort((a, b) => { const x = a[k], y = b[k]; return typeof x === "string" ? dir * String(x).localeCompare(String(y)) : dir * ((x ?? -1) - (y ?? -1)); });
  const total = totalsRow(rows);
  $("board").innerHTML = `<table class="agent-table"><thead><tr>${SHOW.map(([h, , sk]) =>
      `<th class="${LEFT.has(SHOW.find((s) => s[0] === h)[1]) ? "l" : ""} ${sk && sk === k ? "sorted" : ""}" ${sk ? `data-k="${sk}"` : ""}>${esc(h)}${sk && sk === k ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>
    <tbody>${[...rows, total].map((x) => `<tr class="${x._total ? "total" : "click"}" ${x._total ? "" : `data-c="${esc(x.CALLER)}"`}>${SHOW.map(([, key]) => {
      const v = x[key];
      return `<td class="${LEFT.has(key) ? "l" : "num"} ${key === "CALLER" && !x._total ? "caller-link" : ""} ${key === "CALL STATUS" || key === "CALLER" || key === "_breaks" ? "nw" : ""}">${typeof v === "number" ? num(v) : esc(v)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>`;
}

function drawTrend(daily) {
  const g = byGrain(daily, S.grain, ["total_calls", "calls_3m", "dur_3m", "answered", "missed"]);
  chart("ch-trend", trendConfig(g.map(([k]) => grainLabel(k, S.grain)), g));
}

/* ---------- caller panel ---------- */
const PARTS = [["Till 2 PM", (h) => h < 14], ["2 PM – 6 PM", (h) => h >= 14 && h < 18], ["6 PM onwards", (h) => h >= 18]];
function sumDays(days, stage) {
  const s = (k) => days.reduce((a, d) => a + (+d[k] || 0), 0), st = (k) => stage.reduce((a, d) => a + (+d[k] || 0), 0);
  return { calls: s("total_calls"), ans: s("answered"), miss: s("missed"), c3: s("calls_3m"), c1520: s("calls_15_20"), c20: s("calls_20p"),
    dur: s("dur_3m"), prod: s("prod_sec"), uniq: s("unique_leads"), bc: s("break_count"), bs: s("break_shown"),
    d: st("discovery"), ro: st("roadmap"), f: st("followup"), days: days.length };
}
async function openCaller(name) {
  const r = range(); S.caller = name;
  const row = S.rows.find((x) => x.CALLER === name) || {};
  $("d-name").textContent = name;
  $("d-sub").innerHTML = `${esc(row.TEAM || "")}${row._vertical ? " · " + esc(row._vertical) : ""}<br>${nice(r.from)} – ${nice(r.to)}, compared with ${prevText(r)} (${esc(r.cmp)})`;
  $("d-body").innerHTML = `<p class="muted">Loading…</p>`; $("drawer").hidden = false; $("drawer").scrollTop = 0;
  const [cur, prv] = await Promise.all([cached("ca_caller", { p_from: r.from, p_to: r.to, p_caller: name, p_hours: true }),
    cached("ca_caller", { p_from: r.pf, p_to: r.pt, p_caller: name, p_hours: false })]);
  if (S.caller !== name) return;
  const a = sumDays(cur.days, cur.stage), b = sumDays(prv.days, prv.stage);
  const k = (label, v, c, p, f = num) => !c ? "" : `<div class="kpi"><div class="k">${label}</div><div class="v">${v}</div><div class="d">${chHtml(change(c, p))} vs ${esc(f(p))}</div></div>`;
  const stageBy = new Map(cur.stage.map((s) => [String(s.call_date).slice(0, 10), s]));
  const hrs = cur.hours || [];
  const parts = PARTS.map(([label, f]) => { const h = hrs.filter((x) => f(+x.hr)), s = (kk) => h.reduce((t, x) => t + (+x[kk] || 0), 0);
    return { label, calls: s("calls"), ans: s("answered"), c3: s("calls_3m"), dur: s("dur_3m") }; });
  const partTotal = parts.reduce((t, p) => t + p.calls, 0);
  const hrsNote = cur.hours_from && r.from < cur.hours_from ? `<p class="note">Time of day covers ${nice(cur.hours_from)} onwards (individual calls are kept for 90 days).</p>` : "";
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
$("d-close").addEventListener("click", () => { $("drawer").hidden = true; S.caller = null; });
$("d-cdr").addEventListener("click", () => S.caller && downloadCdr(S.caller));

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
async function downloadCdr(caller = null) {
  if (!S.last) return;
  const btn = caller ? $("d-cdr") : $("dl-cdr"), msg = $("dl-msg") || { set textContent(_) {} }; btn.disabled = true;
  try {
    const parts = [csvLine(CDR_COLS)]; let n = 0;
    for (const src of ["A", "O", "N", "M"]) {   // Streamlit order: Acefone, Ozonetel, Manual, MCube
      let after = null;
      for (;;) {
        const page = await rpc("ca_cdr", { p_from: S.last.from, p_to: S.last.to, p_src: src, p_after: after, p_limit: 20000, ...S.last.f, p_caller: caller });
        if (!page.length) break;
        for (const r of page) parts.push(csvLine(CDR_COLS.map((c) => r[c])));
        n += page.length; after = page[page.length - 1].id; msg.textContent = `Preparing… ${num(n)} calls`;
        if (page.length < 20000) break;
      }
    }
    save(caller ? `CDR_LOG_${caller.replace(/[^\w]+/g, "_")}.csv` : "CDR_LOG.csv", parts); msg.textContent = `${num(n)} calls downloaded.`;
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

/* ---------- multi-select filters (with search) ---------- */
const MS = { vertical: { all: "All verticals", one: "vertical", opts: [] }, team: { all: "All teams", one: "team", opts: [] },
  caller: { all: "All callers", one: "caller", opts: [] } };
let msTimer;
function msChanged() { clearTimeout(msTimer); msTimer = setTimeout(() => { buildOptions(); render(); }, 450); }
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
  box.querySelector(".ms-list").onchange = (e) => { const v = e.target.value; e.target.checked ? S.sel[id].add(v) : S.sel[id].delete(v); msButton(id); msChanged(); };
  box.querySelector(".ms-act").onclick = (e) => { const a = e.target.dataset.a; if (!a) return;
    if (a === "none") S.sel[id].clear(); else (box._hits || []).forEach((o) => S.sel[id].add(o));
    msList(id); msButton(id); msChanged(); };
  msButton(id);
}
document.addEventListener("click", (e) => { if (!e.target.closest(".ms")) document.querySelectorAll(".ms-pop").forEach((p) => (p.hidden = true)); });

/* ---------- dates / presets ---------- */
function markPreset(p) { S.preset = p; document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.dataset.p === p)); }
function setPreset(p) { const [f, t] = preset(p); $("from").value = f; $("to").value = t; markPreset(p); render(); }
function clearFilters() { S.issue = null; S.sel.team.clear(); S.sel.vertical.clear(); S.sel.caller.clear(); buildOptions(); setPreset("today"); }

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
  buildOptions();
  if (S.meta?.min_date) { $("from").min = $("to").min = S.meta.min_date; }
  showFresh(S.meta?.last_run);
}
async function start() {
  themeButton();
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { $("auth").hidden = false; $("app").hidden = true; return; }
  const { data: ok } = await sb.rpc("is_allowed");
  if (!ok) { $("auth").hidden = false; $("app").hidden = true;
    $("auth-msg").textContent = `${session.user.email} is signed in, but only @lawsikho.in emails can open this dashboard.`; return; }
  $("auth").hidden = true; $("app").hidden = false;
  Object.keys(MS).forEach(msInit);
  try { await loadMeta(); } catch (e) { $("content").innerHTML = `<div class="card empty">Could not load: ${esc(e.message || e)}</div>`; return; }
  setPreset("today");
}
$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault(); $("auth-msg").textContent = "Signing in…";
  const { error } = await sb.auth.signInWithPassword({ email: $("auth-email").value.trim().toLowerCase(), password: $("auth-pass").value });
  $("auth-msg").textContent = error ? error.message : ""; if (!error) start();
});
$("auth-signup").addEventListener("click", async () => {
  const email = $("auth-email").value.trim().toLowerCase(), password = $("auth-pass").value;
  if (!email || password.length < 8) { $("auth-msg").textContent = "Enter your email and a password of 8+ characters first."; return; }
  const { error } = await sb.auth.signUp({ email, password, options: { emailRedirectTo: location.origin + location.pathname } });
  $("auth-msg").textContent = error ? error.message : "Check your inbox and click the confirmation link, then sign in here.";
});
$("signout").addEventListener("click", async () => { await sb.auth.signOut(); location.reload(); });

/* ---------- events ---------- */
document.querySelectorAll("#presets button").forEach((b) => b.addEventListener("click", () => setPreset(b.dataset.p)));
["from", "to"].forEach((id) => $(id).addEventListener("change", () => { markPreset(null); render(); }));
$("clear-filters").addEventListener("click", clearFilters);
$("update").addEventListener("click", update);
$("menu").addEventListener("click", () => {
  if (matchMedia("(max-width: 820px)").matches) { $("side").classList.toggle("open"); return; }
  const c = $("app").classList.toggle("collapsed"); try { localStorage.setItem("ca-side", c ? "hidden" : "shown"); } catch (_) {}
});
try { if (localStorage.getItem("ca-side") === "hidden") $("app").classList.add("collapsed"); } catch (_) {}
start();
