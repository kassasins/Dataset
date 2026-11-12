#!/usr/bin/env python3
"""Resume-capable full scan: enumerate every location:Japan user (up to the
Search API's 1,000-result cap) and check each PUBLIC profile for a publicly
listed gmail/outlook-family e-mail. Saves progress continuously so it can be
resumed after Core-API rate-limit windows."""
import json, sys, time
sys.path.insert(0, '.')
from recruit_search import GitHubClient, extract_public_emails, is_personal_mail

STATE = "fullscan_state.json"
client = GitHubClient(token="")

# search results are cached from the enumeration run (no extra Search quota)
raw = json.load(open("japan_1000_search_raw.json"))
logins = [u["login"] for u in raw]

try:
    state = json.load(open(STATE))
except Exception:
    state = {"checked": {}, "hits": []}

done = set(state["checked"].keys())
todo = [l for l in logins if l not in done]
print(f"total={len(logins)} already checked={len(done)} remaining={len(todo)}", flush=True)

start = time.time()
for i, lg in enumerate(todo, 1):
    try:
        d = client.get_user(lg)
    except SystemExit:
        break  # rate-limited hard; save & exit, rerun later
    if d is None:
        continue
    email = (d.get("email") or "").strip().lower()
    bio = d.get("bio") or ""
    extra = [m for m in extract_public_emails(bio) if is_personal_mail(m)]
    allp = ([email] if is_personal_mail(email) else []) + [m for m in extra if m != email]
    state["checked"][lg] = {"has_email": bool(allp)}
    if allp:
        hit = {"login": lg, "name": d.get("name") or "", "emails": allp,
               "location": d.get("location") or "", "followers": d.get("followers", 0),
               "profile_url": d.get("html_url") or f"https://github.com/{lg}"}
        state["hits"].append(hit)
        print(f"[{len(done)+i}] @{lg}: {allp}", flush=True)
    if i % 25 == 0:
        json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
        el = time.time() - start
        eta = el / i * (len(todo) - i)
        print(f"  progress: {len(done)+i}/{len(logins)} checked, "
              f"{len(state['hits'])} hits, ETA {eta/60:.1f} min", flush=True)

json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
remaining = len(logins) - len(state["checked"])
print(f"\nSaved. checked={len(state['checked'])}/{len(logins)}, hits={len(state['hits'])}, "
      f"remaining={remaining}", flush=True)
if remaining:
    print("Run again to continue after the rate-limit window.", flush=True)
    sys.exit(2)
