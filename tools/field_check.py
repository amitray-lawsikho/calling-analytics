"""One-off, read-only field check for the Suspicious Calls module (Amit, 26 Nov 2026).

Reads a small window from the MCube and Acefone APIs and prints only field NAMES, types and masked value
shapes (digits -> 9, letters -> x), plus low-cardinality values of status-like fields. Never prints customer
numbers, names, emails, URLs or tokens. Does not read or write Supabase.

Also checks: how many days back each API still returns calls, and whether a recording link can be streamed
from a web page (HTTP status, content type, CORS / range headers).
"""
from __future__ import annotations

import os, re, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pytz, requests

IST = pytz.timezone("Asia/Kolkata")
MCUBE_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
ACEFONE_URL = "https://api.acefone.in/v1/call/records"
PII = re.compile(r"name|number|phone|mobile|email|callto|callfrom|client|customer|caller_id|did|url|file|record|ref|id$", re.I)
OUT = []


def say(s=""):
    print(s); OUT.append(s)


def shape(v) -> str:
    if v is None: return "null"
    if isinstance(v, bool): return f"bool"
    if isinstance(v, (int, float)): return type(v).__name__
    if isinstance(v, (list, dict)): return type(v).__name__
    s = str(v)
    if s.startswith("http"):
        u = urlparse(s); return f"url host={u.netloc} path~{len(u.path)} chars"
    return "str '" + re.sub(r"[A-Za-z]", "x", re.sub(r"\d", "9", s))[:40] + "'"


def walk(rows: list[dict], label: str):
    shapes, values = defaultdict(Counter), defaultdict(Counter)
    def go(d, prefix):
        for k, v in d.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                go(v, key + ".")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                for x in v[:3]: go(x, key + "[].")
            else:
                shapes[key][shape(v)] += 1
                if not PII.search(k) and not isinstance(v, (list, dict)) and v is not None and len(str(v)) <= 40:
                    values[key][str(v)] += 1
    for r in rows: go(r, "")
    say(f"\n### {label}: {len(rows)} records, {len(shapes)} fields")
    say("| field | shape (count) | values (only status-like fields, if <= 15 distinct) |")
    say("|---|---|---|")
    for k in sorted(shapes):
        sh = ", ".join(f"{s} ({n})" for s, n in shapes[k].most_common(3))
        vals = values.get(k, {})
        vs = ", ".join(f"{v} ({n})" for v, n in vals.most_common(15)) if vals and len(vals) <= 15 and not re.search(r"\d{4}-\d\d|\d\d:\d\d", " ".join(vals)) else ""
        say(f"| {k} | {sh} | {vs} |")


def stream_check(url: str | None, label: str, headers: dict | None = None):
    if not url:
        say(f"\n{label} recording check: no recording link in the sample"); return
    try:
        r = requests.get(url, headers={"Range": "bytes=0-1023", "Origin": "https://example.org", **(headers or {})}, timeout=30, stream=True)
        h = r.headers
        say(f"\n{label} recording check (link not printed): HTTP {r.status_code}, host {urlparse(url).netloc}, "
            f"content-type={(h.get('Content-Type') or '')[:40]}, disposition={(h.get('Content-Disposition') or 'none').split(';')[0]}, "
            f"accept-ranges={h.get('Accept-Ranges')}, access-control-allow-origin={h.get('Access-Control-Allow-Origin')}, "
            f"redirected={bool(r.history)}")
        r.close()
    except requests.RequestException as e:
        say(f"\n{label} recording check: {type(e).__name__}")


def mcube(start: datetime, end: datetime) -> list[dict]:
    payload = {"start_date": start.strftime("%Y-%m-%d %H:%M:%S"), "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
               "Authorization": os.environ["MCUBE_TOKEN"], "page": 1}
    for attempt in range(1, 4):
        res = requests.post(MCUBE_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
        if res.status_code == 429: time.sleep(65); continue
        if res.status_code != 200: say(f"MCube HTTP {res.status_code}"); time.sleep(10); continue
        d = res.json()
        return d.get("msg") if d.get("status") == "succ" and isinstance(d.get("msg"), list) else []
    return []


def acefone(start: datetime, end: datetime) -> list[dict]:
    h = {"Authorization": f"Bearer {os.environ['ACEFONE_API_KEY']}", "accountid": os.environ["ACEFONE_ACCOUNT_ID"]}
    p = {"from_date": start.strftime("%Y-%m-%d %H:%M:%S"), "to_date": end.strftime("%Y-%m-%d %H:%M:%S"), "page": 1, "limit": 100}
    res = requests.get(ACEFONE_URL, headers=h, params=p, timeout=120)
    if res.status_code != 200:
        say(f"Acefone HTTP {res.status_code}"); return []
    return res.json().get("results", []) or []


def first_url(rows, keys):
    for r in rows:
        for k in keys:
            v = r.get(k)
            if isinstance(v, str) and v.startswith("http"): return v
    return None


def main():
    now = datetime.now(IST).replace(minute=0, second=0, microsecond=0)
    # a busy weekday hour inside the last 2 days
    day = now - timedelta(days=1)
    while day.weekday() >= 5: day -= timedelta(days=1)
    s, e = day.replace(hour=11), day.replace(hour=11, minute=30)
    say(f"# Field check, sample window {s:%d %b %Y %H:%M}-{e:%H:%M} IST")

    m = mcube(s, e); walk(m, "MCube callsdatabydate")
    rec_keys = [k for k in (m[0].keys() if m else []) if re.search(r"file|record|url", k, re.I)]
    stream_check(first_url(m, rec_keys), "MCube")

    a = acefone(s, e); walk(a, "Acefone call/records")
    rk = [k for k in (a[0].keys() if a else []) if re.search(r"record|url", k, re.I)]
    stream_check(first_url(a, rk), "Acefone")
    stream_check(first_url(a, rk), "Acefone (with API headers)",
                 {"Authorization": f"Bearer {os.environ['ACEFONE_API_KEY']}", "accountid": os.environ["ACEFONE_ACCOUNT_ID"]})

    say("\n### How far back the APIs return calls (one 30-min weekday window per probe, page 1 only)")
    say("| days back | date | MCube records | Acefone records |"); say("|---|---|---|---|")
    for back in (7, 20, 30, 45, 59, 75):
        d = now - timedelta(days=back)
        while d.weekday() >= 5: d -= timedelta(days=1)
        ps, pe = d.replace(hour=11), d.replace(hour=11, minute=30)
        time.sleep(21)  # MCube API limit
        say(f"| {back} | {ps:%d %b} | {len(mcube(ps, pe))} | {len(acefone(ps, pe))} |")

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f: f.write("\n".join(OUT) + "\n")


if __name__ == "__main__":
    sys.exit(main())
