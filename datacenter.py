"""Data Center: 8 data types — download / upload / auto-delete (Telegram backup pehle) / month-end backup.
Har type ek JSON file banati hai: {"format":"ffdata-v1","type":..,"docs":[{"p":path,"d":data}]}"""
import os, json, time, threading, datetime as dt
import fb, backup, tgbackup

SETTINGS = ("bot_meta", "dc_settings")
IST = dt.timezone(dt.timedelta(minutes=int(os.environ.get("DC_TZ_OFFSET_MIN", "330"))))
FORMAT = "ffdata-v1"

TYPES = {
    "leaderboard":   {"label": "Leaderboard History", "icon": "fa-ranking-star",   "color": "#f59e0b", "deletable": True,  "desc": "Daily / weekly / monthly winnings boards"},
    "balance":       {"label": "Balance History",     "icon": "fa-scale-balanced", "color": "#6366f1", "deletable": True,  "desc": "Admin add/cut, prizes, refunds, gifts, match entries"},
    "matches":       {"label": "Match History",       "icon": "fa-gamepad",        "color": "#a855f7", "deletable": True,  "desc": "Completed matches"},
    "chat":          {"label": "Old Chat (legacy)",   "icon": "fa-comments",       "color": "#0ea5e9", "deletable": True,  "desc": "Old world / group / private chat (main database)"},
    "chat_messages": {"label": "Chat Messages",       "icon": "fa-message",        "color": "#06b6d4", "deletable": True,  "desc": "World room, friend chats and group messages (chat database)", "db": "chat"},
    "chat_profiles": {"label": "Chat Profiles & Friends", "icon": "fa-address-card", "color": "#8b5cf6", "deletable": False, "desc": "Chat profiles, friend lists, blocks, groups (download / upload only)", "db": "chat"},
    "chat_requests": {"label": "Friend Requests",     "icon": "fa-user-plus",      "color": "#f97316", "deletable": True,  "desc": "Friend requests history (chat database)", "db": "chat"},
    "chat_reports":  {"label": "Chat Reports",        "icon": "fa-flag",           "color": "#e11d48", "deletable": True,  "desc": "User reports sent to support (chat database)", "db": "chat"},
    "deposit":       {"label": "Deposit History",     "icon": "fa-arrow-down",     "color": "#10b981", "deletable": True,  "desc": "Deposit transactions, requests, verified payments"},
    "withdraw":      {"label": "Withdrawal History",  "icon": "fa-arrow-up",       "color": "#ef4444", "deletable": True,  "desc": "Withdrawal transactions"},
    "user_balance":  {"label": "User Balance",        "icon": "fa-wallet",         "color": "#14b8a6", "deletable": False, "desc": "Every user's balances (download / upload only)"},
    "user_accounts": {"label": "User Account History", "icon": "fa-users",         "color": "#64748b", "deletable": False, "desc": "All user profiles (download / upload only)"},
}
BAL_FIELDS = ("appName", "depositBalance", "withdrawBalance", "balance", "totalEarned", "totalDeposited", "totalWithdrawn")


# ------------------------------------------------------------ helpers
def kind_of(t):
    """Same rule as the admin panel's A._kindOf: DP / WD / AD / AC / PZ / RF / GM"""
    k = t.get("kind"); title = str(t.get("title") or ""); typ = str(t.get("type") or "")
    if k in ("admin_add", "gift"): return "AD"
    if k == "admin_cut": return "AC"
    if k == "prize": return "PZ"
    if k == "refund" or typ == "refund": return "RF"
    if typ == "game": return "GM"
    if title.startswith("Match win"): return "PZ"
    if title.startswith(("System", "Admin credit", "🎁")) and typ == "deposit" and not t.get("method"): return "AD"
    if title.startswith("Admin deduct"): return "AC"
    return "WD" if typ == "withdraw" else "DP"


def doc_ms(d):
    for k in ("createdAt", "timestamp", "updatedAt", "paidAt", "usedAt", "receivedAt", "at", "startTime"):
        v = d.get(k)
        if v is None: continue
        if isinstance(v, (int, float)): return int(v)
        if isinstance(v, dt.datetime):
            if v.tzinfo is None: v = v.replace(tzinfo=dt.timezone.utc)
            return int(v.timestamp() * 1000)
        if isinstance(v, str):
            try: return int(dt.datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None).timestamp() * 1000)
            except Exception: pass
    return None


def dbof(key):
    return fb.get_chat_db() if TYPES.get(key, {}).get("db") == "chat" else fb.get_db()


def iter_docs(key):
    """yield (snapshot) for every doc of this data type."""
    db = dbof(key)
    if key == "leaderboard":
        yield from db.collection("lb").stream()
    elif key in ("balance", "deposit", "withdraw"):
        want = {"balance": {"AD", "AC", "PZ", "RF", "GM"}, "deposit": {"DP"}, "withdraw": {"WD"}}[key]
        for s in db.collection_group("transactions").stream():
            if kind_of(s.to_dict() or {}) in want: yield s
        if key == "deposit":
            yield from db.collection("deposit_requests").stream()
            yield from db.collection("fampay_deposits_history").stream()
    elif key == "matches":
        yield from db.collection("matches").where("status", "==", "completed").stream()
    elif key == "chat":
        yield from db.collection("world_messages").stream()
        for root in ("chats", "groups"):
            for parent in db.collection(root).stream():
                yield from parent.reference.collection("messages").stream()
    elif key in ("user_balance", "user_accounts"):
        yield from db.collection("users").stream()
    elif key == "chat_messages":
        yield from db.collection("cworld").stream()
        for root in ("cchats", "cgroups"):
            for parent in db.collection(root).stream():
                yield from parent.reference.collection("msgs").stream()
    elif key == "chat_profiles":
        for col in ("cprofiles", "cusernames", "cfriends", "cblocks", "cgroups", "cchats", "cconfig"):
            yield from db.collection(col).stream()
    elif key == "chat_requests":
        yield from db.collection("crequests").stream()
    elif key == "chat_reports":
        yield from db.collection("creports").stream()


def summary(key):
    n = 0; oldest = None; newest = None
    for s in iter_docs(key):
        n += 1; m = doc_ms(s.to_dict() or {})
        if m: oldest = m if oldest is None else min(oldest, m); newest = m if newest is None else max(newest, m)
    return {"count": n, "oldest": oldest, "newest": newest}


# ------------------------------------------------------------ export / restore
def export_file(key, job=None, only_older_ms=None):
    """JSON file banao (BACKUP_DIR mein). only_older_ms: sirf us time se purane docs. Returns (path, count)."""
    ts = dt.datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup.BACKUP_DIR, f"ff_{key}_{ts}.json")
    n = 0
    with open(path + ".part", "w", encoding="utf-8") as f:
        f.write('{"format":"%s","type":"%s","exportedAt":"%s","docs":[' % (FORMAT, key, dt.datetime.now(IST).isoformat()))
        first = True
        for s in iter_docs(key):
            d = s.to_dict() or {}
            if only_older_ms is not None:
                m = doc_ms(d)
                if m is None or m >= only_older_ms: continue
            if key == "user_balance": d = {k: d[k] for k in BAL_FIELDS if k in d}
            f.write(("" if first else ",") + json.dumps({"p": s.reference.path, "d": fb.enc(d)}, ensure_ascii=False))
            first = False; n += 1
            if job is not None and n % 200 == 0: job["progress"] = f"{TYPES[key]['label']}: {n} docs…"
        f.write("]}")
    os.replace(path + ".part", path)
    return path, n


def restore_file(job, key, path):
    db = dbof(key); data = json.load(open(path, "r", encoding="utf-8"))
    if data.get("format") != FORMAT: raise RuntimeError("Ye Data Center ki file nahi hai (format galat)")
    if data.get("type") != key: raise RuntimeError(f"File '{data.get('type')}' type ki hai, yahan '{key}' chahiye")
    n = 0
    for it in data.get("docs", []):
        d = fb.dec(it["d"], db)
        db.document(it["p"]).set(d, merge=True)
        n += 1
        if n % 100 == 0: job["progress"] = f"{n} docs restore…"
    return {"restored": n}


# ------------------------------------------------------------ delete (always Telegram backup first)
def delete_older(job, key, days, require_telegram=True):
    if not TYPES[key]["deletable"]: raise RuntimeError("Is type ko delete nahi kar sakte")
    cutoff = int((time.time() - days * 86400) * 1000)
    job["progress"] = "Purana data dhoondh raha hu…"
    path, n = export_file(key, job, only_older_ms=cutoff)
    res = {"type": key, "days": days, "matched": n}
    if n == 0:
        os.remove(path); return res
    if require_telegram:
        cap = f"🗂 {TYPES[key]['label']} — {n} docs (older than {days} days) — auto-delete backup {dt.datetime.now(IST):%d %b %Y %H:%M}"
        job["progress"] = "Telegram par backup upload ho raha hai…"
        tgbackup.send([(path, cap)])               # fail hua to exception => delete NAHI hoga
        res["telegram"] = True
    job["progress"] = "Delete ho raha hai…"
    db = dbof(key); deleted = 0
    for s in iter_docs(key):
        d = s.to_dict() or {}; m = doc_ms(d)
        if m is None or m >= cutoff: continue
        if key == "matches": db.recursive_delete(s.reference)
        else: s.reference.delete()
        deleted += 1
        if deleted % 50 == 0: job["progress"] = f"{deleted}/{n} delete…"
    res["deleted"] = deleted; res["file"] = os.path.basename(path)
    return res


# ------------------------------------------------------------ settings + schedulers
def get_settings():
    try:
        d = fb.get_db().collection(SETTINGS[0]).document(SETTINGS[1]).get()
        return (d.to_dict() or {}) if d.exists else {}
    except Exception:
        return {}


def save_settings(patch):
    fb.get_db().collection(SETTINGS[0]).document(SETTINGS[1]).set(patch, merge=True)


def set_auto(key, enabled, days, run_time, day=0):
    days = max(1, min(3650, int(days))); run_time = run_time if (len(run_time) == 5 and run_time[2] == ":") else "03:00"
    save_settings({"types": {key: {"auto": {"enabled": bool(enabled), "days": days, "time": run_time, "day": max(0, min(31, int(day or 0)))}}}})


def month_end_backup(job=None, force=False):
    """Har type ki alag JSON file Telegram par (month ke end par)."""
    items = []; counts = {}
    for key in TYPES:
        if job: job["progress"] = f"{TYPES[key]['label']} export…"
        path, n = export_file(key, job)
        counts[key] = n
        items.append((path, f"🗂 {TYPES[key]['label']} — {n} docs — {dt.datetime.now(IST):%B %Y}"))
    try:
        tgbackup.send(items, text=f"✅ Month-end backup — {dt.datetime.now(IST):%d %b %Y}\n" + "\n".join(f"• {TYPES[k]['label']}: {v}" for k, v in counts.items()))
    finally:
        for pth, _ in items:
            try: os.remove(pth)
            except OSError: pass
    return counts


def _tick():
    now = dt.datetime.now(IST); st = get_settings()
    # 1) per-type auto delete
    for key, cfg in (st.get("types") or {}).items():
        a = (cfg or {}).get("auto") or {}
        if key not in TYPES or not a.get("enabled") or not TYPES[key]["deletable"]: continue
        today = now.strftime("%Y-%m-%d")
        if a.get("last") == today or now.strftime("%H:%M") < a.get("time", "03:00"): continue
        if int(a.get("day") or 0) and now.day != _clamp_day(now, int(a["day"])): continue
        save_settings({"types": {key: {"auto": {"last": today}}}})          # pehle mark: crash par loop na ho
        j = backup.run_job("dc-auto", lambda job, k=key, d=a.get("days", 30): delete_older(job, k, d))
        if j is None: save_settings({"types": {key: {"auto": {"last": ""}}}}); continue
        while j["status"] == "running": time.sleep(2)
        save_settings({"types": {key: {"auto": {"result": (j.get("result") or {}).get("deleted", 0) if j["status"] == "done" else ("ERR: " + str(j.get("error"))[:120]), "ranAt": int(time.time() * 1000)}}}})
    # 2) monthly backup (all data types of BOTH databases, one JSON file per type) on the chosen day / time
    mo = st.get("monthly") or {}
    mk = now.strftime("%Y-%m")
    day = int(mo.get("day") or 0)                       # 0 = last day of the month
    target = _clamp_day(now, day) if day else _last_day(now)
    if mo.get("enabled", True) and now.day == target and now.strftime("%H:%M") >= mo.get("time", "23:00") and mo.get("last") != mk:
        try:
            if tgbackup.public_cfg()["configured"]:
                save_settings({"monthly": {"last": mk}})
                j = backup.run_job("dc-month", lambda job: month_end_backup(job))
                if j:
                    while j["status"] == "running": time.sleep(2)
                    save_settings({"monthly": {"result": "ok" if j["status"] == "done" else "ERR: " + str(j.get("error"))[:120], "ranAt": int(time.time() * 1000)}})
        except Exception as e:
            print("[dc] monthly:", e, flush=True)


def _last_day(now):
    import calendar
    return calendar.monthrange(now.year, now.month)[1]


def _clamp_day(now, day):
    return max(1, min(day, _last_day(now)))
