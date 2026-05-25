"""
MCUBE page semantics probe — is `page` a page index or a cumulative limit?

Compare:
  set(page=1)  vs  set(page=2)  vs  set(page=3)

If pagination is disjoint (docs):    page=1 ∩ page=2 = empty
If pagination is cumulative (likely): page=1 ⊂ page=2 ⊂ page=3
"""
import os, time, requests

API_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
TOKEN   = os.getenv("MCUBE_TOKEN")
START   = "2026-05-14 00:00:00"
END     = "2026-05-25 23:59:59"

def fetch(page):
    r = requests.post(API_URL, json={
        "start_date": START, "end_date": END,
        "Authorization": TOKEN, "page": page,
    }, headers={"Content-Type": "application/json"}, timeout=60)
    msg = r.json().get("msg") or []
    return {str(x.get("callid")) for x in msg if x.get("callid")}

p1 = fetch(1); print(f"page=1: {len(p1)}")
time.sleep(21)
p2 = fetch(2); print(f"page=2: {len(p2)}")
time.sleep(21)
p3 = fetch(3); print(f"page=3: {len(p3)}")

print(f"\np1 ⊂ p2 ? {p1.issubset(p2)}    (cumulative if True)")
print(f"p1 ∩ p2 empty ? {p1.isdisjoint(p2)}    (disjoint if True)")
print(f"p2 ⊂ p3 ? {p2.issubset(p3)}")
print(f"p2 ∩ p3 size: {len(p2 & p3)}")
print(f"p1 first 5 ids: {list(p1)[:5]}")
print(f"p2 first 5 ids: {list(p2)[:5]}")
