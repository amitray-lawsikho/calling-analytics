/* Calling Analytics Dashboard — Dynamic Dashboard. Data: Supabase RPC ca_meta / ca_report / ca_unique_leads / ca_cdr / ca_stage_export.
   Numbers follow the Streamlit calling dashboard exactly (docs/definitions.md); the pipeline pre-computes each caller's day. */
const sb = supabase.createClient(CA_CONFIG.supabaseUrl, CA_CONFIG.supabaseKey);
const $ = (id) => document.getElementById(id);
const S = { meta: null, seq: 0, sel: { team: new Set(), vertical: new Set() }, last: null };

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

/* ---------- dates (IST) ---------- */
const istToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const D = (s) => new Date(s + "T00:00:00Z");
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = D(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const nice = (s) => D(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const ddmmyyyy = (s) => `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}`;
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

/* ---------- data ---------- */
async function rpc(fn, args) { const { data, error } = await sb.rpc(fn, args); if (error) throw error; return data; }
function filt() {
  return { p_teams: S.sel.team.size ? [...S.sel.team] : null, p_verticals: S.sel.vertical.size ? [...S.sel.vertical] : null,
    p_search: $("search").value.trim() || null };
}
const CACHE = new Map();
function cached(fn, args) { const k = fn + JSON.stringify(args); if (!CACHE.has(k)) CACHE.set(k, rpc(fn, args).catch((e) => { CACHE.delete(k); throw e; })); return CACHE.get(k); }

/* ---------- the report (Streamlit's report_df) ---------- */
function buildReport(rep) {
  const rows = rep.rows.map((r) => ({
    "IN/OUT TIME": r.io || "", CALLER: r.caller, TEAM: r.team ?? "Others", "TOTAL CALLS": +r.total_calls,
    "CALL STATUS": `${+r.answered} Ans / ${+r.missed} Unans`,
    "PICK UP RATIO %": `${r.total_calls > 0 ? pyRound((r.answered / r.total_calls) * 100) : 0}%`,
    "CALLS > 3 MINS": +r.calls_3m, "CALLS 15-20 MINS": +r.calls_15_20, "20+ MIN CALLS": +r.calls_20p,
    "CALL DURATION > 3 MINS": fmtHm(+r.dur_3m), "FOLLOW UPS DONE": +r.followup, "DISCOVERY DONE": +r.discovery, "ROADMAPS DONE": +r.roadmap,
    "PRODUCTIVE HOURS": fmtHm(+r.prod_sec), "BREAKS (>=5 MINS)": r.breaks || "0", "BREAK TIMINGS (>=5 MINS)": r.break_timings || "-",
    REMARKS: (r.issues || []).length ? r.issues.join(", ") : "None", raw_prod_sec: +r.prod_sec, raw_dur_sec: +r.dur_3m,
    _ans: +r.answered, _miss: +r.missed, _src: r,
  }));
  for (const s of rep.stage_only) rows.push({
    "IN/OUT TIME": "-", CALLER: s.caller, TEAM: s.team, "TOTAL CALLS": 0, "CALL STATUS": "-", "PICK UP RATIO %": "-",
    "CALLS > 3 MINS": 0, "CALLS 15-20 MINS": 0, "20+ MIN CALLS": 0, "CALL DURATION > 3 MINS": fmtHm(0),
    "FOLLOW UPS DONE": +s.followup, "DISCOVERY DONE": +s.discovery, "ROADMAPS DONE": +s.roadmap, "PRODUCTIVE HOURS": fmtHm(0),
    "BREAKS (>=5 MINS)": "-", "BREAK TIMINGS (>=5 MINS)": "-", REMARKS: "-", raw_prod_sec: 0, raw_dur_sec: 0, _ans: 0, _miss: 0, _stage: true });
  rows.sort((a, b) => b.raw_dur_sec - a.raw_dur_sec);
  rows.forEach((r, i) => (r.Rank = ["🥇", "🥈", "🥉"][i] || ""));
  return rows;
}
const topBy = (rows, k) => [...rows].sort((a, b) => b[k] - a[k])[0];
const COLS = ["Rank", "IN/OUT TIME", "CALLER", "TEAM", "TOTAL CALLS", "CALL STATUS", "PICK UP RATIO %", "CALLS > 3 MINS", "CALLS 15-20 MINS",
  "20+ MIN CALLS", "CALL DURATION > 3 MINS", "FOLLOW UPS DONE", "DISCOVERY DONE", "ROADMAPS DONE", "PRODUCTIVE HOURS",
  "BREAKS (>=5 MINS)", "BREAK TIMINGS (>=5 MINS)", "REMARKS"];
const LEFT = new Set(["IN/OUT TIME", "CALLER", "TEAM", "CALL STATUS", "BREAKS (>=5 MINS)", "BREAK TIMINGS (>=5 MINS)", "REMARKS"]);
const WIDE = new Set(["IN/OUT TIME", "BREAKS (>=5 MINS)", "BREAK TIMINGS (>=5 MINS)"]);

function uniqueApprovals(list) {   // _unique_approvals: first spelling of each name, case ignored
  const seen = new Map();
  for (const v of list || []) { const t = String(v).trim(), k = t.toLowerCase(); if (k && !seen.has(k)) seen.set(k, t); }
  return seen.size ? [...seen.values()].join(", ") : "—";
}

/* ---------- render ---------- */
function busy(on) { $("busy").hidden = !on; $("content").classList.toggle("loading", on); }
async function render() {
  const seq = ++S.seq, from = $("from").value, to = $("to").value;
  if (!from || !to || from > to) return;
  $("page-sub").innerHTML = `Duration period · <b>${ddmmyyyy(from)} to ${ddmmyyyy(to)}</b>`;
  busy(true);
  try {
    const f = filt(), args = { p_from: from, p_to: to, ...f };
    const [rep, uniq] = await Promise.all([cached("ca_report", args), cached("ca_unique_leads", args)]);
    if (seq !== S.seq) return;
    S.last = { from, to, f };
    draw(rep, uniq);
  } catch (e) {
    if (seq === S.seq) $("content").innerHTML = `<div class="card empty">Could not load data: ${esc(e.message || e)}</div>`;
  }
  if (seq === S.seq) busy(false);
}

function draw(rep, uniq) {
  if (!rep.rows.length && !rep.stage_only.length) {
    $("content").innerHTML = `<div class="card empty">No data found for the selected period and filters.</div>`;
    return;
  }
  const rows = buildReport(rep), src = rep.rows;
  const sum = (k) => rows.reduce((a, r) => a + (+r[k] || 0), 0);
  const ssum = (k) => src.reduce((a, r) => a + (+r[k] || 0), 0);
  const ans = sum("_ans"), miss = sum("_miss"), calls = sum("TOTAL CALLS");
  const avgProd = rows.length ? rows.reduce((a, r) => a + r.raw_prod_sec, 0) / rows.length : 0;
  const t1 = rows[0], t2 = topBy(rows, "TOTAL CALLS"), t3 = topBy(rows, "20+ MIN CALLS"),
    t4 = topBy(rows, "FOLLOW UPS DONE"), t5 = topBy(rows, "DISCOVERY DONE"), t6 = topBy(rows, "ROADMAPS DONE");
  const hl = (label, r, val) => `<div class="hl"><div class="k">${label}</div><div class="n">${esc(r.CALLER)}</div><div class="d">${esc(val)}</div></div>`;
  const kpi = (label, v) => `<div class="kpi"><div class="k">${label}</div><div class="v">${v}</div></div>`;
  const total = { Rank: "", "IN/OUT TIME": "-", CALLER: "TOTAL", TEAM: "-", "TOTAL CALLS": calls, "CALL STATUS": `${ans} Ans / ${miss} Unans`,
    "PICK UP RATIO %": calls > 0 ? `${pyRound((ans / calls) * 100)}%` : "0%", "CALLS > 3 MINS": sum("CALLS > 3 MINS"),
    "CALLS 15-20 MINS": sum("CALLS 15-20 MINS"), "20+ MIN CALLS": sum("20+ MIN CALLS"), "CALL DURATION > 3 MINS": fmtHm(sum("raw_dur_sec")),
    "FOLLOW UPS DONE": sum("FOLLOW UPS DONE"), "DISCOVERY DONE": sum("DISCOVERY DONE"), "ROADMAPS DONE": sum("ROADMAPS DONE"),
    "PRODUCTIVE HOURS": fmtHm(sum("raw_prod_sec")), "BREAKS (>=5 MINS)": "-", "BREAK TIMINGS (>=5 MINS)": "-", REMARKS: "-", _total: 1 };
  const cell = (c, r) => {
    const v = r[c];
    if (c === "REMARKS" && !r._total && v !== "None" && v !== "-") return String(v).split(", ").map((x) => `<span class="tag bad">${esc(x)}</span>`).join("");
    return typeof v === "number" ? num(v) : esc(v);
  };
  const tableHtml = `<table class="agent-table"><thead><tr>${COLS.map((c) => `<th class="${LEFT.has(c) ? "l" : ""}">${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${[...rows, total].map((r) => `<tr class="${r._total ? "total" : ""}">${COLS.map((c) =>
      `<td class="${LEFT.has(c) ? "l" : "num"} ${WIDE.has(c) ? "wide" : ""} ${c === "CALL STATUS" || c === "CALLER" ? "nw" : ""}">${cell(c, r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;

  // highest manual calls (by caller)
  const man = src.filter((r) => +r.n_manual > 0).map((r) => ({ CALLER: r.caller, VERTICAL: r.vertical ?? "—", TEAM: r.team ?? "—",
    "MANUAL CALLS COUNT": +r.n_manual, "MANUAL CALLS DURATION": fmtHm(+r.manual_dur), "APPROVED BY": uniqueApprovals(r.approvals), _dur: +r.manual_dur }))
    .sort((a, b) => b["MANUAL CALLS COUNT"] - a["MANUAL CALLS COUNT"]);
  const manCols = ["CALLER", "VERTICAL", "TEAM", "MANUAL CALLS COUNT", "MANUAL CALLS DURATION", "APPROVED BY"];
  const manTotal = { CALLER: "TOTAL", VERTICAL: "—", TEAM: "—", "MANUAL CALLS COUNT": man.reduce((a, r) => a + r["MANUAL CALLS COUNT"], 0),
    "MANUAL CALLS DURATION": fmtHm(man.reduce((a, r) => a + r._dur, 0)), "APPROVED BY": "—", _total: 1 };
  const manHtml = !man.length ? "" : `<h4 class="section-h">⚠️ Highest manual calls</h4><section class="card"><div class="table-wrap">
    <table class="agent-table"><thead><tr>${manCols.map((c) => `<th class="${["MANUAL CALLS COUNT", "MANUAL CALLS DURATION"].includes(c) ? "" : "l"}">${c}</th>`).join("")}</tr></thead>
    <tbody>${[...man, manTotal].map((r) => `<tr class="${r._total ? "total" : ""}">${manCols.map((c) =>
      `<td class="${["MANUAL CALLS COUNT", "MANUAL CALLS DURATION"].includes(c) ? "num" : "l"}">${typeof r[c] === "number" ? num(r[c]) : esc(r[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;

  $("content").innerHTML = `
    <h4 class="section-h">🏆 Top 6 performance highlights</h4>
    <section class="highlights">
      ${hl("🥇 Top performer", t1, `${t1["CALL DURATION > 3 MINS"]} Duration`)}
      ${hl("✆ Highest calls", t2, `${t2["TOTAL CALLS"]} Total Calls`)}
      ${hl("🗣️ Deep engagement", t3, `${t3["20+ MIN CALLS"]} Long Calls`)}
      ${hl("📋 Highest follow ups", t4, `${t4["FOLLOW UPS DONE"]} Follow Ups Done`)}
      ${hl("🔍 Highest discovery", t5, `${t5["DISCOVERY DONE"]} Discoveries Done`)}
      ${hl("🗺️ Highest roadmap", t6, `${t6["ROADMAPS DONE"]} Roadmaps Done`)}
    </section>
    <h4 class="section-h">Summary metrics</h4>
    <section class="kpis k9">
      ${kpi("🔵 Acefone calls", num(ssum("n_acefone")))}${kpi("🟠 Ozonetel calls", num(ssum("n_ozonetel")))}${kpi("🟢 MCube calls", num(ssum("n_mcube")))}
      ${kpi("✏️ Manual calls", num(ssum("n_manual")))}<div class="kpi" title="Unique numbers dialled each day, added up over the days"><div class="k">👤 Unique leads</div><div class="v">${num(+uniq)}</div></div>
      ${kpi("⏱ Avg prod hrs", fmtHm(avgProd))}${kpi("📋 Follow ups done", num(total["FOLLOW UPS DONE"]))}
      ${kpi("🔍 Discovery done", num(total["DISCOVERY DONE"]))}${kpi("🗺️ Roadmaps done", num(total["ROADMAPS DONE"]))}
    </section>
    <h4 class="section-h">Agent performance table</h4>
    <section class="card"><div class="table-wrap tall">${tableHtml}</div>
      <div class="downloads">
        <button class="btn primary" id="dl-cdr">📥 Download CDR</button>
        <button class="btn" id="dl-stage">📥 Download Stage Changed Report</button>
        <span class="dl-msg" id="dl-msg"></span>
      </div></section>
    ${manHtml}`;
  $("dl-cdr").onclick = downloadCdr;
  $("dl-stage").onclick = downloadStage;
}

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
async function downloadCdr() {
  if (!S.last) return;
  const btn = $("dl-cdr"), msg = $("dl-msg"); btn.disabled = true;
  try {
    const parts = [csvLine(CDR_COLS)]; let n = 0;
    for (const src of ["A", "O", "N", "M"]) {   // Streamlit order: Acefone, Ozonetel, Manual, MCube
      let after = null;
      for (;;) {
        const page = await rpc("ca_cdr", { p_from: S.last.from, p_to: S.last.to, p_src: src, p_after: after, p_limit: 20000, ...S.last.f });
        if (!page.length) break;
        for (const r of page) parts.push(csvLine(CDR_COLS.map((c) => r[c])));
        n += page.length; after = page[page.length - 1].id; msg.textContent = `Preparing… ${num(n)} calls`;
        if (page.length < 20000) break;
      }
    }
    save("CDR_LOG.csv", parts); msg.textContent = `${num(n)} calls downloaded.`;
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

/* ---------- multi-select filters (with search) ---------- */
const MS = { team: { all: "All teams", one: "team", opts: [] }, vertical: { all: "All verticals", one: "vertical", opts: [] } };
let msTimer;
function msChanged() { clearTimeout(msTimer); msTimer = setTimeout(render, 450); }
function msButton(id) {
  const set = S.sel[id], m = MS[id], b = document.querySelector(`#ms-${id} .ms-btn`); if (!b) return;
  b.textContent = !set.size ? m.all : set.size === 1 ? [...set][0] : `${set.size} ${m.one}s selected`;
  b.classList.toggle("on", set.size > 0); b.title = [...set].join(", ");
}
function msList(id) {
  const box = $(`ms-${id}`), q = box.querySelector(".ms-q").value.trim().toLowerCase(), set = S.sel[id];
  const hits = MS[id].opts.filter((o) => !q || o.toLowerCase().includes(q));
  box.querySelector(".ms-list").innerHTML = [...hits.filter((o) => set.has(o)), ...hits.filter((o) => !set.has(o))].map((o) =>
    `<label class="ms-item"><input type="checkbox" value="${esc(o)}" ${set.has(o) ? "checked" : ""}><span>${esc(o)}</span></label>`).join("")
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
function markPreset(p) { document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.dataset.p === p)); }
function setPreset(p) { const [f, t] = preset(p); $("from").value = f; $("to").value = t; markPreset(p); render(); }
function clearFilters() { S.sel.team.clear(); S.sel.vertical.clear(); $("search").value = ""; Object.keys(MS).forEach(msButton); setPreset("today"); }

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
  themeButton();
});

/* ---------- auth + start ---------- */
async function loadMeta() {
  S.meta = await rpc("ca_meta", {});
  MS.team.opts = S.meta?.teams || []; MS.vertical.opts = S.meta?.verticals || [];
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
let searchTimer; $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(render, 500); });
$("clear-filters").addEventListener("click", clearFilters);
$("update").addEventListener("click", update);
$("menu").addEventListener("click", () => {
  if (matchMedia("(max-width: 820px)").matches) { $("side").classList.toggle("open"); return; }
  const c = $("app").classList.toggle("collapsed"); try { localStorage.setItem("ca-side", c ? "hidden" : "shown"); } catch (_) {}
});
try { if (localStorage.getItem("ca-side") === "hidden") $("app").classList.add("collapsed"); } catch (_) {}
start();
