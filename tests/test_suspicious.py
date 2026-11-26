from datetime import date

from etl import suspicious as SUS
from etl.rules import hid


def mc(**kw):
    r = {"callid": "111", "direction": "Outbound", "starttime": "2026-11-25 11:00:00", "dialstatus": "ANSWER", "disconnectedBy": "Agent",
         "answeredtime": "00:00:04", "cust_ring_sttime": "2026-11-25 11:00:10", "cust_ring_endtime": "2026-11-25 11:00:16",
         "agentname": " Aman Kumar Roy ", "callto": "919876543210", "filename": "https://recordings.mcube.com/x.wav"}
    r.update(kw)
    return r


def ace(**kw):
    c = {"call_id": "abc", "direction": "outbound", "status": "answered", "reason": "disconnected_by_caller", "answered_seconds": 3,
         "call_duration": 20, "end_stamp": "2026-11-25 11:00:20", "agent_name": "Soumya Sanskar", "client_number": "+919000000001",
         "recording_url": "https://console.acefone.in/r/x"}
    c.update(kw)
    return c


def test_mcube_pickup_cut():
    r = SUS.mcube(mc())
    assert r["id"] == hid("MCB", "111") and r["k"] == "A" and r["agent"] and r["talk"] == 4 and r["cring"] == 6
    assert r["caller"] == "Aman Kumar Roy" and r["num"] == "9876543210" and r["d"] == "2026-11-25" and not r["low"]


def test_mcube_low_ring_exec_busy_and_inbound():
    assert SUS.mcube(mc(dialstatus="BUSY", answeredtime="00:00:00"))["low"] is True
    assert SUS.mcube(mc(dialstatus="BUSY", cust_ring_endtime="2026-11-25 11:00:40"))["low"] is False       # 30 s ring: dialer timeout
    assert SUS.mcube(mc(dialstatus="BUSY", disconnectedBy="Customer"))["low"] is False
    assert SUS.mcube(mc(dialstatus="Executive Busy", cust_ring_sttime="0000-00-00 00:00:00"))["k"] == "X"
    assert SUS.mcube(mc(direction="inbound")) is None


def test_acefone_rules_match_calling_ids():
    r = SUS.acefone(ace())
    assert r["src"] == "A" and r["k"] == "A" and r["agent"] and r["talk"] == 3 and r["caller"] == "Soumya Sanskar"
    assert r["id"] == hid("ACE", "abc") and r["num"] == "9000000001"
    m = SUS.acefone(ace(status="missed", answered_seconds=0, call_duration=8))
    assert m["k"] == "R" and m["low"] and m["cring"] == 8
    assert SUS.acefone(ace(reason="disconnected_by_callee"))["agent"] is False
    assert SUS.acefone(ace(direction="inbound")) is None


def test_keep_drops_call_details_unless_suspicious_and_never_raises():
    SUS.reset()
    SUS.keep("M", mc())                                   # pick-up cut: kept with number and recording
    SUS.keep("M", mc(callid="2", answeredtime="00:05:00"))  # 5 minute call
    SUS.keep("M", {"broken": True})
    SUS.keep("A", None)
    SUS.keep("M", mc(callid="3", dialstatus="BUSY"))       # not answered: counts only, no recording
    assert len(SUS.ROWS) == 3 and "num" in SUS.ROWS[0] and "rec" in SUS.ROWS[0]
    assert "rec" in SUS.ROWS[1] and "num" not in SUS.ROWS[1]   # answered, not suspicious: recording kept for All Call Recordings
    assert "rec" not in SUS.ROWS[2]


class FakeStore:
    def __init__(self): self.calls = []
    def rpc(self, fn, args):
        self.calls.append((fn, args))
        return len(args["p_rows"]) if fn == "sus_ingest" else {"db_mb": 1}


def test_ingest_batches_and_trims():
    SUS.reset()
    for i in range(5):
        SUS.keep("M", mc(callid=str(i)))
    SUS.keep("M", mc(callid="old", starttime="2026-08-01 10:00:00"))
    s = FakeStore()
    out = SUS.ingest(s, date(2026, 9, 28), batch=2)
    assert out["read"] == 5 and out["new"] == 5 and [c[0] for c in s.calls] == ["sus_ingest"] * 3 + ["sus_trim"]


def test_excel_download_has_recording_link():
    """Suspicious Calls Excel download (Amit, 27 Nov 2026): every row of the list, with the recording link."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "web/suspicious/app.js").read_text()
    sql = (root / "supabase/migrations/20261127_suspicious_export.sql").read_text()
    assert 'rpc("sus_export"' in js and '"Recording link"' in js and "c.l = { Target: r.recording" in js
    assert "_rec_url(recording) recording" in sql and "sus_allowed()" in sql
