/* Suspicious Call Analysis (Amit, 26 Nov 2026). Data: Supabase RPC sus_meta / sus_report / sus_daily / sus_list (sus_* tables,
   filled by the Calling Analytics refresh). Pick-up cut = answered, agent ended the call, talk <= the chosen limit.
   Low-ring cut = not answered, agent ended it while the customer's phone had rung <= 10 s. Executive Busy = dialer call not taken. */
const sb = supabase.createClient(CA_CONFIG.supabaseUrl, CA_CONFIG.supabaseKey);
const $ = (id) => document.getElementById(id);
const S = { meta: null, page: "report", talk: "0-5", src: "", preset: "month", sel: { vertical: new Set(), team: new Set(), caller: new Set() },
  sort: { k: "rate", dir: -1 }, list: { rows: [], total: 0, offset: 0, number: "", repeat: false, sort: "new" },
  all: { rows: [], total: 0, offset: 0, number: "", talk: "", ended: "" }, seq: 0, playing: null };

/* ---------- format ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const num = (v) => (v == null || !isFinite(v) ? "–" : inr.format(v));
const pct = (a, b) => (b > 0 ? (a / b) * 100 : 0);
const pctTxt = (a, b) => (b > 0 ? `${Math.round(pct(a, b))}%` : "–");
const hm = (sec) => { const t = Math.round((+sec || 0) / 60), h = Math.floor(t / 60), m = t % 60; return h ? `${h}h ${m}m` : `${m}m`; };
const mmss = (s) => { s = Math.max(0, Math.floor(+s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
// talk-time bands after pick-up, agent ended the call (Amit, 26 Nov 2026): key "lo-max", lo exclusive (0 = from zero), max inclusive
const TALK = { "0-180": "Any length", "0-5": "≤ 5 s", "5-10": "5 – 10 s", "10-20": "10 – 20 s", "20-60": "20 s – 1 min", "60-180": "1 – 3 min" };
const TALKP = { "0-180": "3 min or less", "0-5": "5 s or less", "5-10": "more than 5 s and up to 10 s", "10-20": "more than 10 s and up to 20 s",
  "20-60": "more than 20 s and up to 1 min", "60-180": "more than 1 min and up to 3 min" };
const band = () => S.talk.split("-").map(Number);
const SRC = { M: "MCube", A: "Acefone" };

/* ---------- dates (IST) ---------- */
const istToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const D = (s) => new Date(s + "T00:00:00Z");
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = D(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const nice = (s) => D(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const short = (s) => D(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const when = (t) => new Date(t).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function preset(p) {
  const t = istToday(), ms = t.slice(0, 8) + "01";
  switch (p) {
    case "today": return [t, t];
    case "yesterday": { const y = addDays(t, -1); return [y, y]; }
    case "week": return [addDays(t, -((D(t).getUTCDay() + 6) % 7)), t];
    case "lastweek": { const mon = addDays(t, -((D(t).getUTCDay() + 6) % 7) - 7); return [mon, addDays(mon, 6)]; }
    case "month": return [ms, t];
    case "lastmonth": { const e = addDays(ms, -1); return [e.slice(0, 8) + "01", e]; }
    default: return [S.meta?.min_date || addDays(t, -59), t];
  }
}

/* ---------- data ---------- */
async function rpc(fn, args) { const { data, error } = await sb.rpc(fn, args); if (error) throw error; return data; }
const CACHE = new Map();
function cached(fn, args) { const k = fn + JSON.stringify(args); if (!CACHE.has(k)) CACHE.set(k, rpc(fn, args).catch((e) => { CACHE.delete(k); throw e; })); return CACHE.get(k); }
function args() {
  const a = (s) => (s.size ? [...s] : null);
  return { p_from: $("from").value, p_to: $("to").value, p_lo: band()[0], p_max: band()[1], p_teams: a(S.sel.team), p_verticals: a(S.sel.vertical),
    p_callers: a(S.sel.caller), p_srcs: S.src ? [S.src] : null };
}

/* ---------- count-up numbers (KPI cards) ---------- */
function countUp(root) {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.querySelectorAll("[data-n]").forEach((el) => {
    const end = +el.dataset.n, suf = el.dataset.suf || "";
    if (still || !isFinite(end) || end === 0) { el.textContent = num(end) + suf; return; }
    const t0 = performance.now(), dur = 700;
    const step = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.textContent = num(Math.round(end * e)) + suf; if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}

/* ---------- flags: compared with the team as a whole for the same filters ---------- */
function flagOf(r, team) {
  const pc = pct(r.cuts, r.answered), lr = pct(r.lowring, r.reached), eb = pct(r.exec_busy, r.calls);
  const tp = pct(team.cuts, team.answered), tl = pct(team.lowring, team.reached), out = [];
  let lvl = 0;
  if (r.answered >= 10 && pc >= 20 && pc >= tp * 1.8) { out.push("cuts pick-ups"); lvl = 2; }
  else if (r.answered >= 10 && pc >= 10 && pc >= tp * 1.3) { out.push("cuts pick-ups"); lvl = Math.max(lvl, 1); }
  if (r.reached >= 20 && lr >= 25 && lr >= tl * 1.8) { out.push("cuts before the customer answers"); lvl = 2; }
  else if (r.reached >= 20 && lr >= 12 && lr >= tl * 1.3) { out.push("frequent low-ring cuts"); lvl = Math.max(lvl, 1); }
  if (r.calls >= 30 && eb >= 20) { out.push("ignores dialer calls"); lvl = Math.max(lvl, 1); }
  return { lvl, text: lvl === 2 ? "High" : lvl === 1 ? "Watch" : "Normal", why: out.join(", ") };
}
const bar = (a, b, lvl) => `<span class="pct ${lvl === 2 ? "hot" : lvl === 1 ? "watch" : ""}"><span>${pctTxt(a, b)}</span><span class="track"><span class="fill bar-in" style="width:${Math.min(100, pct(a, b)).toFixed(1)}%"></span></span></span>`;

/* ---------- pages ---------- */
const PAGES = { report: ["Agent Report", pageReport], daily: ["Daily Heatmap", pageDaily], calls: ["Suspicious Calls", pageCalls], all: ["All Call Recordings", pageAll] };
function busy(on) { $("busy").hidden = !on; $("content").classList.toggle("loading", on); }
async function render() {
  const seq = ++S.seq, [title, fn] = PAGES[S.page];
  $("page-title").textContent = title;
  const a = args();
  $("page-sub").innerHTML = `<b>${nice(a.p_from)}</b> – <b>${nice(a.p_to)}</b> · ${S.page === "all" ? "every answered call with a recording" : `agent ended the call, talk <b>${S.talk === "0-180" ? "any length up to 3 min" : TALK[S.talk]}</b>`}${S.src ? ` · <b>${SRC[S.src]}</b>` : " · MCube + Acefone"}`;
  busy(true);
  try { const html = await fn(a, seq); if (seq !== S.seq) return; if (html != null) { $("content").innerHTML = html; afterRender(); } }
  catch (e) { if (seq === S.seq) $("content").innerHTML = `<div class="card empty">Could not load: ${esc(e.message || e)}</div>`; }
  finally { if (seq === S.seq) busy(false); }
}
function afterRender() { countUp($("content")); wire(); }

function sumRows(rows) {
  const t = { calls: 0, reached: 0, answered: 0, cuts: 0, lowring: 0, exec_busy: 0, talk_sec: 0, repeat_numbers: 0 };
  rows.forEach((r) => Object.keys(t).forEach((k) => (t[k] += +r[k] || 0)));
  return t;
}
const kpi = (k, n, sub, opts = {}) => `<div class="kpi"><div class="k">${k}</div><div class="v ${opts.hot ? "hot" : ""}" data-n="${n}" data-suf="${opts.suf || ""}">0</div><div class="p">${sub}</div></div>`;

async function pageReport(a) {
  const rows = await cached("sus_report", a);
  if (!rows.length) return emptyState();
  const t = sumRows(rows);
  rows.forEach((r) => { r.rate = pct(r.cuts, r.answered); r.lrate = pct(r.lowring, r.reached); r.erate = pct(r.exec_busy, r.calls); r.flag = flagOf(r, t); });
  const k = S.sort.k, dir = S.sort.dir;
  rows.sort((x, y) => (typeof x[k] === "string" ? String(x[k]).localeCompare(String(y[k])) : (x[k] ?? -1) - (y[k] ?? -1)) * dir || y.cuts - x.cuts);
  S.reportRows = rows;
  const hot = rows.filter((r) => r.flag.lvl === 2).length;
  const th = (key, label, l) => `<th class="${l ? "l" : ""} ${k === key ? "sorted" : ""}" data-k="${key}">${label}${k === key ? (dir < 0 ? " ▾" : " ▴") : ""}</th>`;
  return `<section class="kpis rise">
      ${kpi("Answered calls", t.answered, `${num(t.calls)} calls dialled`)}
      ${kpi(`Pick-up cuts <span class="hint">${S.talk === "0-180" ? "≤ 3 min" : TALK[S.talk]}</span>`, t.cuts, `${pctTxt(t.cuts, t.answered)} of answered calls`, { hot: true })}
      ${kpi("Low-ring cuts", t.lowring, `${pctTxt(t.lowring, t.reached)} of calls that rang · count only`)}
      ${kpi("Executive Busy", t.exec_busy, `${pctTxt(t.exec_busy, t.calls)} of calls · count only`)}
      ${kpi("Agents flagged High", hot, `of ${rows.length} agents`)}
    </section>
    <section class="card rise"><div>
      <div class="card-h"><h3>Agents — pick-up cuts, talk ${TALKP[S.talk]}</h3><span class="small muted">Click an agent to open their suspicious calls · High / Watch compare each agent with the whole selection</span></div>
      <div class="table-wrap tall"><table class="rep agent-table"><thead><tr>
        ${th("caller", "Agent", 1)}${th("team", "Team", 1)}${th("calls", "Calls")}${th("answered", "Answered")}${th("cuts", "Pick-up cuts")}${th("rate", "% of answered")}
        ${th("lowring", "Low-ring cuts")}${th("lrate", "% of rang")}${th("exec_busy", "Exec Busy")}${th("talk_sec", "Talk time")}${th("repeat_numbers", "Numbers cut 2+")}<th class="l">Read</th>
      </tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="caller" data-c="${esc(r.caller)}">${esc(r.caller)}</td><td class="l">${esc(r.team || "—")}</td>
        <td class="num">${num(r.calls)}</td><td class="num">${num(r.answered)}</td><td class="num">${num(r.cuts)}</td><td>${bar(r.cuts, r.answered, r.flag.lvl && r.rate >= pct(t.cuts, t.answered) ? r.flag.lvl : 0)}</td>
        <td class="num">${num(r.lowring)}</td><td class="num">${pctTxt(r.lowring, r.reached)}</td><td class="num" title="${pctTxt(r.exec_busy, r.calls)} of calls">${num(r.exec_busy)}</td>
        <td class="num">${hm(r.talk_sec)}</td><td class="num">${num(r.repeat_numbers)}</td>
        <td class="l"><span class="flag ${["ok", "watch", "hot"][r.flag.lvl]}" title="${esc(r.flag.why)}">${r.flag.text}</span>${r.flag.why ? ` <span class="small muted">${esc(r.flag.why)}</span>` : ""}</td></tr>`).join("")}
      <tr class="total"><td class="l">Total</td><td class="l">—</td><td class="num">${num(t.calls)}</td><td class="num">${num(t.answered)}</td><td class="num">${num(t.cuts)}</td>
        <td class="num">${pctTxt(t.cuts, t.answered)}</td><td class="num">${num(t.lowring)}</td><td class="num">${pctTxt(t.lowring, t.reached)}</td><td class="num">${num(t.exec_busy)}</td>
        <td class="num">${hm(t.talk_sec)}</td><td class="num">${num(t.repeat_numbers)}</td><td></td></tr>
      </tbody></table></div>
      <p class="note">Pick-up cut: the customer answered and the agent ended the call after ${TALKP[S.talk]} of talk (MCube "Disconnected By: Agent", Acefone "disconnected by caller").
        Low-ring cut: the agent ended it while the customer's phone had rung 10 s or less. Executive Busy (MCube): the dialer rang the agent and the agent did not take it. Ozonetel is not included.</p>
    </div></section>`;
}

async function pageDaily(a) {
  const rows = await cached("sus_daily", a);
  if (!rows.length) return emptyState();
  const days = [...new Set(rows.map((r) => r.call_date))].sort(), by = new Map();
  rows.forEach((r) => { if (!by.has(r.caller)) by.set(r.caller, { tot: { a: 0, c: 0 }, d: {} }); const o = by.get(r.caller); o.d[r.call_date] = r; o.tot.a += +r.answered; o.tot.c += +r.cuts; });
  const callers = [...by.entries()].filter(([, o]) => o.tot.a > 0).sort((x, y) => pct(y[1].tot.c, y[1].tot.a) - pct(x[1].tot.c, x[1].tot.a));
  const dark = document.documentElement.dataset.theme !== "light";
  const color = (p) => { const k = Math.min(1, p / 70);
    const c1 = dark ? [25, 25, 27] : [248, 247, 252], c2 = dark ? [167, 139, 250] : [124, 58, 237], c3 = dark ? [240, 143, 134] : [180, 71, 58];
    const mix = (x, y, t) => x.map((v, i) => Math.round(v + (y[i] - v) * t)); const c = k < .5 ? mix(c1, c2, k * 2) : mix(c2, c3, (k - .5) * 2);
    return `background:rgb(${c});color:${k > .35 ? (dark ? "#0a0a0b" : "#fff") : "var(--ink)"}`; };
  return `<section class="card rise"><div>
    <div class="card-h"><h3>Pick-up cuts ÷ answered calls, per agent per day</h3>
      <span class="hm-legend">0%<span class="grad"></span>70%+ · click a cell to listen</span></div>
    <div class="heat-wrap"><table class="hm"><thead><tr><th class="l">Agent</th>${days.map((d) => `<th>${short(d)}</th>`).join("")}<th>All</th></tr></thead><tbody>
    ${callers.map(([c, o]) => `<tr><td class="l">${esc(c)}</td>${days.map((d) => { const r = o.d[d];
        if (!r || !(+r.answered)) return `<td class="c none">·</td>`;
        const p = pct(r.cuts, r.answered); return `<td class="c" style="${color(p)}" data-c="${esc(c)}" data-d="${d}" title="${esc(c)} · ${short(d)}: ${num(r.cuts)} of ${num(r.answered)} answered calls cut">${Math.round(p)}%</td>`; }).join("")}
      <td class="c" style="${color(pct(o.tot.c, o.tot.a))}" data-c="${esc(c)}" title="${num(o.tot.c)} of ${num(o.tot.a)}">${Math.round(pct(o.tot.c, o.tot.a))}%</td></tr>`).join("")}
    </tbody></table></div>
    <p class="note">Only the agent-ended pick-ups with talk ${TALKP[S.talk]} are counted; change the talk-time band above to see the same grid for another band.</p>
  </div></section>`;
}

async function pageCalls(a, seq, append = false) {
  const L = S.list;
  if (!append) { L.offset = 0; L.rows = []; stopAudio(); }
  const res = await rpc("sus_list", { ...a, p_number: L.number || null, p_repeat: L.repeat, p_sort: L.sort, p_offset: L.offset, p_limit: 50 });
  if (seq !== S.seq) return null;
  L.rows = L.rows.concat(res.rows); L.total = res.total; L.offset = L.rows.length;
  const tools = `<div class="toolbar">
      <input type="search" id="l-num" placeholder="Search a phone number" value="${esc(L.number)}">
      <label class="toggle"><input type="checkbox" id="l-rep" ${L.repeat ? "checked" : ""}> Only numbers cut 2+ times by the same agent</label>
      <div class="seg sm" id="l-sort"><button data-s="new" class="${L.sort === "new" ? "on" : ""}">Newest first</button><button data-s="repeat" class="${L.sort === "repeat" ? "on" : ""}">Most repeated</button></div>
      <span class="small muted" style="margin-left:auto">${num(L.total)} calls</span></div>`;
  if (!L.rows.length) return `<section class="card rise"><div>${tools}<div class="empty">No calls the agent ended after ${TALKP[S.talk]} of talk for these filters.</div></div></section>`;
  return `<section class="card rise"><div>${tools}
    <div class="table-wrap tall"><table class="calls agent-table"><thead><tr><th class="l">Listen</th><th class="l">Time (IST)</th><th class="l">Agent</th><th class="l">Team</th><th class="l">Phone</th>
      <th>Dialler</th><th>Talk</th><th>Customer ring</th><th title="Calls this agent cut on this number in the period">Cuts on number</th></tr></thead>
    <tbody id="l-body">${L.rows.map(callRow).join("")}</tbody></table></div>
    ${L.rows.length < L.total ? `<div class="more"><button class="btn sm" id="l-more">Load 50 more</button><span class="small muted">${num(L.rows.length)} of ${num(L.total)}</span></div>` : ""}
  </div></section>`;
}
/* All Call Recordings (Amit, 26 Nov 2026): every answered call that has a recording, any length, filter by talk time and who ended it */
const TALKR = { "": ["Any length", 0, null], s: ["Under 1 min", 0, 60], m: ["1 – 3 min", 60, 180], l: ["3 – 10 min", 180, 600], xl: ["10 – 20 min", 600, 1200], xxl: ["20 min +", 1200, null] };
async function pageAll(a, seq, append = false) {
  const L = S.all;
  if (!append) { L.offset = 0; L.rows = []; stopAudio(); }
  const [, mn, mx] = TALKR[L.talk];
  const res = await rpc("all_list", { p_from: a.p_from, p_to: a.p_to, p_teams: a.p_teams, p_verticals: a.p_verticals, p_callers: a.p_callers, p_srcs: a.p_srcs,
    p_number: L.number || null, p_min: mn, p_max: mx, p_ended: L.ended || null, p_offset: L.offset, p_limit: 50 });
  if (seq !== S.seq) return null;
  L.rows = L.rows.concat(res.rows); L.total = res.total; L.offset = L.rows.length;
  const tools = `<div class="toolbar">
      <input type="search" id="a-num" placeholder="Search a phone number" value="${esc(L.number)}">
      <div class="seg sm" id="a-talk">${Object.entries(TALKR).map(([k, [t]]) => `<button data-k="${k}" class="${L.talk === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <div class="seg sm" id="a-end">${[["", "Anyone ended"], ["agent", "Agent ended"], ["customer", "Customer ended"]].map(([k, t]) => `<button data-k="${k}" class="${L.ended === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <span class="small muted" style="margin-left:auto">${L.total > 10000 ? "10,000+" : num(L.total)} calls</span></div>`;
  if (!L.rows.length) return `<section class="card rise"><div>${tools}<div class="empty">No recorded calls for these filters.</div></div></section>`;
  return `<section class="card rise"><div>${tools}
    <div class="table-wrap tall"><table class="calls agent-table"><thead><tr><th class="l">Listen</th><th class="l">Time (IST)</th><th class="l">Agent</th><th class="l">Team</th><th class="l">Phone</th>
      <th>Dialler</th><th>Talk</th><th class="l">Ended by</th></tr></thead>
    <tbody id="l-body">${L.rows.map(allRow).join("")}</tbody></table></div>
    ${L.rows.length < L.total ? `<div class="more"><button class="btn sm" id="l-more">Load 50 more</button><span class="small muted">${num(L.rows.length)} of ${L.total > 10000 ? "10,000+" : num(L.total)}</span></div>` : ""}
  </div></section>`;
}
const mins = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`);
function allRow(r) {
  return `<tr data-id="${r.id}"><td class="l"><button class="play" data-id="${r.id}" aria-label="Play recording">${PLAY}</button></td><td class="l nw">${when(r.call_datetime)}</td>
    <td class="l">${esc(r.caller)}</td><td class="l">${esc(r.team || "—")}</td><td class="l nw">${esc(r.client_number || "")}</td>
    <td><span class="src-tag">${SRC[r.src] || r.src}</span></td><td class="num">${mins(r.talk_sec)}</td><td class="l">${r.agent_end ? "Agent" : "Customer"}</td></tr>`;
}
const PLAY = `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>`, PAUSE = `<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>`;
function callRow(r) {
  return `<tr data-id="${r.id}"><td class="l"><button class="play" data-id="${r.id}" ${r.recording ? "" : "disabled title=\"No recording for this call\""} aria-label="Play recording">${PLAY}</button></td><td class="l nw">${when(r.call_datetime)}</td><td class="l">${esc(r.caller)}</td><td class="l">${esc(r.team || "—")}</td>
    <td class="l nw">${esc(r.client_number || "")}</td><td><span class="src-tag">${SRC[r.src] || r.src}</span></td><td class="num">${r.talk_sec}s</td>
    <td class="num">${r.cring_sec == null ? "–" : r.cring_sec + "s"}</td><td><span class="rep-n ${r.cuts_on_number >= 2 ? "many" : ""}">${r.cuts_on_number}</span></td></tr>`;
}

function emptyState() {
  const b = S.meta?.backfill;
  return `<div class="card empty rise"><div>No suspicious-call data for these dates and filters yet.${b && b.done_through ? ` History is loaded up to ${nice(b.done_through)}.` : " The first 60 days are still loading."}</div></div>`;
}

/* ---------- inline player: one audio element, plays in the row, never opens a link ---------- */
const audio = $("audio");
function stopAudio() { audio.pause(); audio.removeAttribute("src"); audio.load(); document.querySelectorAll("tr.player-row").forEach((x) => x.remove());
  document.querySelectorAll("tr.playing").forEach((x) => x.classList.remove("playing", "paused")); document.querySelectorAll(".play").forEach((b) => (b.innerHTML = PLAY)); S.playing = null; }
function openPlayer(id) {
  const r = (S.page === "all" ? S.all : S.list).rows.find((x) => x.id === id), tr = document.querySelector(`tr[data-id="${id}"]`);
  if (!r || !tr) return;
  if (S.playing === id) { audio.paused ? audio.play().catch(() => {}) : audio.pause(); return; }
  stopAudio(); S.playing = id; tr.classList.add("playing");
  const row = document.createElement("tr"); row.className = "player-row";
  row.innerHTML = `<td colspan="${tr.children.length}"><div class="player"><button class="play pp" aria-label="Play or pause">${PAUSE}</button>
    <span class="time"><span class="cur">0:00</span> / <span class="dur">–</span></span>
    <div class="seek" role="slider" aria-label="Seek" tabindex="0"><span class="buf"></span><span class="done"></span><span class="knob"></span></div>
    <div class="speed">${[1, 1.5, 2].map((s) => `<button data-r="${s}" class="${s === 1 ? "on" : ""}">${s}×</button>`).join("")}</div>
    <span class="meta">${esc(r.caller)} · talk ${mins(r.talk_sec)}<span class="mini-eq"><i></i><i></i><i></i></span></span></div></td>`;
  tr.after(row);
  const P = row.querySelector(".player"), cur = P.querySelector(".cur"), dur = P.querySelector(".dur"), done = P.querySelector(".done"), knob = P.querySelector(".knob"), buf = P.querySelector(".buf"), seek = P.querySelector(".seek");
  const sync = () => { const d = audio.duration || 0, p = d ? (audio.currentTime / d) * 100 : 0; done.style.width = knob.style.left = p + "%"; cur.textContent = mmss(audio.currentTime);
    if (d) dur.textContent = mmss(d); try { if (audio.buffered.length && d) buf.style.width = (audio.buffered.end(audio.buffered.length - 1) / d) * 100 + "%"; } catch (_) {} };
  const icon = () => { const on = !audio.paused; P.querySelector(".pp").innerHTML = on ? PAUSE : PLAY; tr.querySelector(".play").innerHTML = on ? PAUSE : PLAY; tr.classList.toggle("paused", !on); row.classList.toggle("paused", !on); };
  audio.ontimeupdate = sync; audio.onprogress = sync; audio.onloadedmetadata = sync; audio.onplay = icon; audio.onpause = icon; audio.onended = icon;
  audio.onerror = () => { if (S.playing !== id) return; P.innerHTML = `<span class="err">This recording could not be played here. The dialler may have removed it, or it is still being processed.</span>`; };
  P.querySelector(".pp").onclick = () => (audio.paused ? audio.play().catch(() => {}) : audio.pause());
  P.querySelector(".speed").onclick = (e) => { const s = +e.target.dataset.r; if (!s) return; audio.playbackRate = s; P.querySelectorAll(".speed button").forEach((b) => b.classList.toggle("on", +b.dataset.r === s)); };
  const jump = (x) => { const b = seek.getBoundingClientRect(); if (audio.duration) audio.currentTime = Math.max(0, Math.min(1, (x - b.left) / b.width)) * audio.duration; };
  seek.onpointerdown = (e) => { jump(e.clientX); const mv = (ev) => jump(ev.clientX); window.addEventListener("pointermove", mv); window.addEventListener("pointerup", () => window.removeEventListener("pointermove", mv), { once: true }); };
  seek.onkeydown = (e) => { if (e.key === "ArrowRight") audio.currentTime += 5; if (e.key === "ArrowLeft") audio.currentTime -= 5; };
  audio.src = r.recording; audio.playbackRate = 1; audio.play().catch(() => icon());
}

/* ---------- events inside the content ---------- */
function wire() {
  document.querySelectorAll(".rep th[data-k]").forEach((th) => th.onclick = () => { const k = th.dataset.k; S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : (k === "caller" || k === "team" ? 1 : -1) }; render(); });
  document.querySelectorAll("td.caller, .hm td.c[data-c]").forEach((td) => td.onclick = () => {
    S.sel.caller = new Set([td.dataset.c]); if (td.dataset.d) { $("from").value = $("to").value = td.dataset.d; markPreset(null); }
    buildOptions(); if (location.hash === "#calls") render(); else location.hash = "calls"; });
  const n = $("l-num"); if (n) { let t; n.oninput = () => { clearTimeout(t); t = setTimeout(() => { S.list.number = n.value.replace(/\D/g, ""); render(); }, 400); }; }
  const rp = $("l-rep"); if (rp) rp.onchange = () => { S.list.repeat = rp.checked; render(); };
  const so = $("l-sort"); if (so) so.onclick = (e) => { const s = e.target.dataset.s; if (!s) return; S.list.sort = s; render(); };
  const more = $("l-more"); if (more) more.onclick = async () => { more.disabled = true; const seq = S.seq;
    try { const html = await (S.page === "all" ? pageAll : pageCalls)(args(), seq, true); if (html != null && seq === S.seq) { $("content").innerHTML = html; wire(); } } finally { more.disabled = false; } };
  const an = $("a-num"); if (an) { let t; an.oninput = () => { clearTimeout(t); t = setTimeout(() => { S.all.number = an.value.replace(/\D/g, ""); render(); }, 400); }; }
  const at = $("a-talk"); if (at) at.onclick = (e) => { const k = e.target.dataset.k; if (k == null) return; S.all.talk = k; render(); };
  const ae = $("a-end"); if (ae) ae.onclick = (e) => { const k = e.target.dataset.k; if (k == null) return; S.all.ended = k; render(); };
  const body = $("l-body"); if (body) body.onclick = (e) => { const b = e.target.closest("button.play[data-id]"); if (b && !b.disabled) openPlayer(b.dataset.id); };
}

/* ---------- multi-select filters (same control as Calling Analytics) ---------- */
const MS = { vertical: { all: "All verticals", one: "vertical", opts: [] }, team: { all: "All teams", one: "team", opts: [] }, caller: { all: "All callers", one: "caller", opts: [] } };
let msTimer;
function msChanged() { clearTimeout(msTimer); msTimer = setTimeout(() => { buildOptions(); render(); }, 450); }
function buildOptions() {
  const V = S.sel.vertical, T = S.sel.team, cs = S.meta?.callers || [];
  MS.vertical.opts = S.meta?.verticals || [];
  MS.team.opts = V.size ? [...new Set(cs.filter((c) => V.has(c.vertical)).map((c) => c.team).filter(Boolean))].sort() : (S.meta?.teams || []);
  MS.caller.opts = [...new Set(cs.filter((c) => (!V.size || V.has(c.vertical)) && (!T.size || T.has(c.team))).map((c) => c.caller))];
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

/* ---------- filters, pages, freshness ---------- */
function markPreset(p) { S.preset = p; document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.dataset.p === p)); }
function setPreset(p) { const [f, t] = preset(p); $("from").value = f; $("to").value = t; markPreset(p); render(); }
function markSeg(id, key, v) { document.querySelectorAll(`#${id} button`).forEach((b) => b.classList.toggle("on", b.dataset[key] === String(v))); }
function setPage(p) {
  S.page = PAGES[p] ? p : "report";
  $("talk-lbl").hidden = S.page === "all";
  document.querySelectorAll(".side nav a[data-page]").forEach((a) => a.classList.toggle("on", a.dataset.page === S.page));
  $("side").classList.remove("open"); render();
}
function showFresh() {
  const run = S.meta?.last_run, b = S.meta?.backfill;
  if (!run) { $("fresh").textContent = "Not refreshed yet"; } else {
    const ok = run.status === "ok" && !(run.suspicious && run.suspicious.error);
    $("fresh").innerHTML = ok ? `Data updated ${when(run.run_at)} IST` : `<span class="down">Last update ${run.status === "ok" ? "skipped these calls" : "failed"} · ${when(run.run_at)} IST</span>`;
  }
  const w = [];
  if (run && run.status === "ok" && run.suspicious && run.suspicious.error) w.push("The last Calling refresh could not update the suspicious calls; numbers may be older than the Calling dashboard until the next refresh.");
  if (b && b.done_through && b.done_through < addDays(istToday(), -1)) w.push(`History is still loading: available from ${nice(b.from)} up to ${nice(b.done_through)}.`);
  $("warn").hidden = !w.length; $("warn").textContent = w.join(" ");
}
function fillMe(email) {
  const e = String(email || ""), n = e.split("@")[0].split(/[._-]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
  const box = $("si-me"); box.querySelector(".si-av").textContent = (n || "?")[0]; box.querySelector("b").textContent = n || e; box.querySelector("small").textContent = e; box.title = e; box.hidden = false;
}
const SUN = `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`;
const MOON = `<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
function themeButton() { const dark = document.documentElement.dataset.theme !== "light"; $("theme").innerHTML = dark ? `${SUN} Light` : `${MOON} Dark`; }
const unboot = () => { const b = $("boot"); b.classList.add("gone"); setTimeout(() => b.remove(), 600); };

// Like every dashboard (portal rule, 18 Nov 2026): no sign-in of its own; without a session or access it goes to the portal,
// which checks access to 'suspicious' and comes back with a one-time sign-in link (30-second guard against loops).
const PORTAL = "https://sales-intelligence-portal.amitray.workers.dev/";
function toPortal() {
  let last = 0; try { last = +sessionStorage.getItem("si-hop") || 0; } catch (_) {}
  if (Date.now() - last < 30000) return false;
  try { sessionStorage.setItem("si-hop", String(Date.now())); } catch (_) {}
  location.replace(`${PORTAL}go.html?d=suspicious`); return true;
}
async function start() {
  themeButton();
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { if (toPortal()) return; $("auth").hidden = false; unboot(); return; }
  const { data: ok } = await sb.rpc("sus_allowed");
  if (!ok) { if (toPortal()) return; $("auth").hidden = false; $("auth-msg").textContent = `${session.user.email} has no access to Suspicious Call Analysis. Ask a portal admin to give it in the Sales Intelligence Portal.`; unboot(); return; }
  $("app").hidden = false; fillMe(session.user.email);
  Object.keys(MS).forEach(msInit);
  try { S.meta = await rpc("sus_meta", {}); } catch (e) { $("content").innerHTML = `<div class="card empty">Could not load: ${esc(e.message || e)}</div>`; unboot(); return; }
  buildOptions(); showFresh();
  if (S.meta?.min_date) $("from").min = $("to").min = S.meta.min_date;
  const [f, t] = preset("month"); $("from").value = f; $("to").value = t; markPreset("month");
  markSeg("talk", "t", S.talk); markSeg("src", "s", S.src);
  setPage(location.hash.slice(1));
  setTimeout(unboot, 250);
}

window.addEventListener("hashchange", () => setPage(location.hash.slice(1)));
document.querySelectorAll("#presets button").forEach((b) => b.addEventListener("click", () => setPreset(b.dataset.p)));
["from", "to"].forEach((id) => $(id).addEventListener("change", () => { markPreset(null); render(); }));
$("talk").addEventListener("click", (e) => { const t = e.target.dataset.t; if (!t) return; S.talk = t; markSeg("talk", "t", t); render(); });
$("src").addEventListener("click", (e) => { const s = e.target.dataset.s; if (s == null) return; S.src = s; markSeg("src", "s", s); render(); });
$("clear-filters").addEventListener("click", () => { S.sel.vertical.clear(); S.sel.team.clear(); S.sel.caller.clear(); S.src = ""; S.talk = "0-5"; S.list.number = ""; S.list.repeat = false; S.all.number = ""; S.all.talk = ""; S.all.ended = "";
  markSeg("talk", "t", "0-5"); markSeg("src", "s", ""); buildOptions(); setPreset("month"); });
$("theme").addEventListener("click", () => { const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next; try { localStorage.setItem("ca-theme", next); } catch (_) {} themeButton(); if (!$("app").hidden) render(); });
$("signout").addEventListener("click", async () => { stopAudio(); await sb.auth.signOut({ scope: "local" }); location.replace(PORTAL + "?signout=1"); });
$("menu").addEventListener("click", () => {
  if (matchMedia("(max-width: 820px)").matches) { $("side").classList.toggle("open"); return; }
  const c = $("app").classList.toggle("collapsed"); try { localStorage.setItem("sus-side", c ? "hidden" : "shown"); } catch (_) {}
});
try { if (localStorage.getItem("sus-side") === "hidden") $("app").classList.add("collapsed"); } catch (_) {}
start();
