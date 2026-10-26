import random
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd

from etl.rules import IST, UTC, acefone_row, call_times, day_metrics, fmt_hm, hid, mcube_row, ozonetel_row
from etl.sources import manual_rows, stage_rows
from tests.streamlit_reference import process_metrics_logic


def make_calls(seed=7):
    rnd = random.Random(seed)
    today = datetime.now(IST).date()
    days = [today - timedelta(days=3), today - timedelta(days=1), today]
    out = []
    for owner in ("Asha Rao", "Bilal Khan", "Chitra N"):
        for d in days:
            t = IST.localize(datetime.combine(d, datetime.min.time())) + timedelta(hours=rnd.choice([9.5, 10, 10.4, 11]))
            for _ in range(rnd.randint(5, 60)):
                t += timedelta(seconds=rnd.choice([20, 60, 200, 400, 900, 1500, 2400, 4000]))
                dur = rnd.choice([0, 30, 179, 180, 600, 950, 1250, 2000])
                src = rnd.choice("AOM")
                st = "answered" if dur and rnd.random() < .7 else "missed"
                dt = (t + timedelta(seconds=dur)) if src == "A" else t   # Acefone stores the end, others the start
                out.append({"src": src, "call_owner": owner, "call_date": d, "call_datetime": dt.astimezone(UTC), "call_duration": dur,
                            "status": st, "client_number": str(rnd.randint(9000000000, 9000000050)), "reason": None})
                t += timedelta(seconds=dur)
            for _ in range(rnd.randint(0, 2)):   # manual calls: no time
                out.append({"src": "N", "call_owner": owner, "call_date": d, "call_datetime": None, "call_duration": rnd.choice([60, 300]),
                            "status": "answered", "client_number": "1", "reason": rnd.choice(["Ravi", "ravi", "Sita"])})
    # a caller with only manual calls on one day
    out.append({"src": "N", "call_owner": "Dev", "call_date": days[0], "call_datetime": None, "call_duration": 120, "status": "answered",
                "client_number": "2", "reason": "Ravi"})
    return out, today


def streamlit_frame(calls):
    """The DataFrame fetch_call_data builds (same start/end derivation), team already merged."""
    src_name = {"A": "Acefone", "O": "Ozonetel", "M": "MCube", "N": "Manual"}
    df = pd.DataFrame([{"call_owner": c["call_owner"], "Call Date": c["call_date"], "status": c["status"], "call_duration": c["call_duration"],
                        "call_datetime": c["call_datetime"] if c["call_datetime"] else pd.NaT, "source": src_name[c["src"]],
                        "Team Name": "Elite"} for c in calls])
    df["call_endtime"] = pd.to_datetime(df["call_datetime"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0)
    df["call_starttime"] = df["call_endtime"] - pd.to_timedelta(df["call_duration"], unit="s")
    m = df["source"].isin(["Ozonetel", "MCube"])
    df.loc[m, "call_starttime"] = df.loc[m, "call_endtime"]
    df.loc[m, "call_endtime"] = df.loc[m, "call_starttime"] + pd.to_timedelta(df.loc[m, "call_duration"], unit="s")
    return df


def ours(calls, today):
    """day_metrics per caller-day, then summed over days the way ca_report (SQL) does."""
    g = defaultdict(list)
    for c in calls:
        s, e = call_times(c["src"], c["call_datetime"], c["call_duration"])
        g[(c["call_owner"], c["call_date"])].append({**c, "start": s, "end": e})
    per = defaultdict(list)
    for (o, d), cs in sorted(g.items(), key=lambda kv: kv[0][1]):
        per[o].append(day_metrics(cs, d, today))
    out = {}
    for o, ds in per.items():
        j = lambda k, sep: sep.join(x[k] for x in ds if x[k]) or None
        out[o] = {"TOTAL CALLS": sum(x["total_calls"] for x in ds),
                  "CALL STATUS": f"{sum(x['answered'] for x in ds)} Ans / {sum(x['missed'] for x in ds)} Unans",
                  "CALLS > 3 MINS": sum(x["calls_3m"] for x in ds), "CALLS 15-20 MINS": sum(x["calls_15_20"] for x in ds),
                  "20+ MIN CALLS": sum(x["calls_20p"] for x in ds), "CALL DURATION > 3 MINS": fmt_hm(sum(x["dur_3m"] for x in ds)),
                  "PRODUCTIVE HOURS": fmt_hm(sum(x["prod_sec"] for x in ds)), "IN/OUT TIME": j("io_line", "\n") or "",
                  "BREAKS (>=5 MINS)": j("break_line", "\n") or "0", "BREAK TIMINGS (>=5 MINS)": j("break_timing", "\n---\n") or "-",
                  "REMARKS": ", ".join(sorted({i for x in ds for i in x["issues"]})) or "None",
                  "raw_prod_sec": sum(x["prod_sec"] for x in ds), "raw_dur_sec": sum(x["dur_3m"] for x in ds)}
    return out


def test_metrics_match_streamlit():
    for seed in range(6):
        calls, today = make_calls(seed)
        ref, _ = process_metrics_logic(streamlit_frame(calls))
        mine = ours(calls, today)
        assert set(ref["CALLER"]) == set(mine)
        for _, r in ref.iterrows():
            m = mine[r["CALLER"]]
            for k, v in m.items():
                rv = r[k]
                if k in ("raw_prod_sec", "raw_dur_sec"):
                    assert abs(float(rv) - v) < 1, (seed, r["CALLER"], k, rv, v)
                else:
                    assert str(rv) == str(v), (seed, r["CALLER"], k, rv, v)


def test_break_summary():
    calls, today = make_calls(3)
    d = calls[0]["call_date"]
    day = [{**c, "start": call_times(c["src"], c["call_datetime"], c["call_duration"])[0],
            "end": call_times(c["src"], c["call_datetime"], c["call_duration"])[1]} for c in calls if c["call_owner"] == "Asha Rao" and c["call_date"] == d]
    m = day_metrics(day, d, today)
    if m["break_line"]:
        n = int(m["break_line"].split(": ")[1].split(" breaks")[0])
        assert n == m["break_count"] and fmt_hm(m["break_shown"]) == m["break_line"].rsplit(": ", 1)[1]


def test_format_and_ids():
    assert fmt_hm(0) == "0h 0m" and fmt_hm(90) == "0h 2m" and fmt_hm(30) == "0h 0m" and fmt_hm(3600 + 150) == "1h 2m"
    assert hid("ACE", "1") == hid("ACE", "1") != hid("ACE", "2")


def test_source_rows():
    a = acefone_row({"call_id": 5, "end_stamp": "2026-10-25 10:00:00", "client_number": "+919876543210", "agent_name": "Asha-Extension",
                     "call_duration": "00:03:20", "status": "answered", "direction": "outbound", "service": None, "reason": None}, "x")
    assert a["client_number"] == "9876543210" and a["call_duration"] == 200 and a["call_owner"] == "Asha"
    assert a["service"] == "None" and a["call_date"] == date(2026, 10, 25) and a["call_datetime"].hour == 4
    assert acefone_row({"call_id": 6, "end_stamp": "2026-10-25 10:00:00", "status": "missed", "direction": "inbound"}, "x") is None
    assert acefone_row({"call_id": 7, "end_stamp": "2026-10-25 10:00:00", "status": "answered", "direction": "outbound", "call_duration": 0}, "x") is None
    m = mcube_row({"callid": 9, "starttime": "2026-10-25 11:00:00", "endtime": "2026-10-25 11:05:00", "dialstatus": "ANSWER",
                   "agentname": " Bilal ", "callto": "09876543210", "direction": "OUTBOUND"}, "x")
    assert m["status"] == "answered" and m["call_duration"] == 300 and m["direction"] == "outbound" and m["call_owner"] == "Bilal"
    assert mcube_row({"callid": 1, "starttime": "2026-10-25 11:00:00", "dialstatus": "Executive Busy"}, "x") is None
    o = ozonetel_row({"CallID": "1", "AgentName": "Chitra", "E164": "919876543210", "Status": "Unanswered", "Type": "Manual",
                      "StartTime": "10:00:00", "EndTime": "10:00:30", "Duration": "00:00:30", "Disposition": None},
                     date(2026, 10, 25), {"chitra"}, "x")
    assert o["status"] == "missed" and o["direction"] == "outbound" and o["call_duration"] == 30
    assert ozonetel_row({"AgentName": "Other", "E164": "1"}, date(2026, 10, 25), {"chitra"}, "x") is None


def test_manual_and_stage_sheets():
    df = pd.DataFrame([{"Timestamp": "25/10/2026 12:00:00", "Email address": "x", "Date": "24/10/2026", "Slot Timing": "",
                        "Counselor Name (Refer your LSQ name)": "asha rao", "Duration of the call in minutes(for a single call only)": "5",
                        "Phone Number of Submitted Manual (Numbers will be audited kindly provide accurate details)": 9876543210.0,
                        "Google Drive Link of added calls and screenshots (Please give access of the drive)*": "",
                        "Approved By": "Ravi", "Name of Submitted Manual lead": "L"}])
    m = manual_rows(df, date(2026, 8, 1), date(2026, 10, 26))
    assert m[0]["call_owner"] == "Asha Rao" and m[0]["call_duration"] == 300 and m[0]["client_number"] == "9876543210"
    assert m[0]["call_date"] == "2026-10-24"
    s = pd.DataFrame([{"Timestamp": "25/10/2026 12:00:00", "ProspectID": "p", "FirstName": "A", "EmailAddress": "e", "Phone": "+91-9876543210",
                       "Old_Contact_Stage": "x", "New_Contact_Stage": "Discovery Call Done", "Stage Changed By": " Asha ",
                       "Stage_Changed_On": "t", "BotComment": "c"}] * 2)
    st = stage_rows(s, date(2026, 8, 1))
    assert len(st) == 1 and st[0]["phone"] == "9876543210" and st[0]["last_name"] == "9876543210" and st[0]["changed_by"] == "Asha"
    assert st[0]["comment"] == "c" and st[0]["date"] == "2026-10-25"
