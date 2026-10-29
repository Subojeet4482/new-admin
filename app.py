"""FF Admin server — admin panel + login (env) + backup/delete API + Fampay bot control."""
import os, sys, time, shutil, platform, threading
import datetime as dt
from flask import Flask, jsonify, request, send_from_directory, send_file, g, Response
from werkzeug.middleware.proxy_fix import ProxyFix

import fb, security, backup, keepalive, sysmon, datacenter, tgbackup, jobs, chatadmin, slow
from fampay_bot import bot
import admin_lite as lite  # LITE panel (users/wallet/matches/settings) isi service mein merge

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
BOOT = time.time()

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024

ok = lambda **k: jsonify(ok=True, **k)
bad = lambda msg, code=400: (jsonify(ok=False, error=msg), code)


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/") or request.path.endswith((".html", ".js", ".css")) or request.path == "/":
        r.headers["Cache-Control"] = "no-store"
    return r


# ------------------------------------------------------------------ static
@app.route("/")
@app.route("/admin")
def index():
    # Owner session -> main panel (admin.html). Admin session -> lite panel. Koi session nahi -> admin.html
    # khud apna login screen dikhata hai (ek hi form, email+password+PIN /api/login par jaata hai).
    if lite.read_session():
        return Response(lite.HTML, mimetype="text/html")
    return send_from_directory(PUBLIC, "admin.html")


@app.route("/<path:p>")
def static_files(p):
    if p.startswith("api/"):
        return bad("Not found", 404)
    return send_from_directory(PUBLIC, p)


@app.route("/health")
def health():
    # Render health check + UptimeRobot yahi URL ping karte hain (koi secret nahi dikhta)
    return jsonify(ok=True, uptime=int(time.time() - BOOT), bot=bot.state["status"])


# ------------------------------------------------------------------ TEMP debug (login troubleshoot ke baad HATA DENA)
# Koi password/PIN/email ki asli value nahi dikhata — sirf length/basic shape, taaki pata chale env
# mein sahi cheez bhari hai ya nahi. Render free plan mein Shell nahi hota isliye yeh route banaya.
@app.get("/api/_login_debug")
def _login_debug():
    oe, op, on = security.creds()
    ae, ap, an = security.admin_creds()
    return jsonify(
        owner_email_set=bool(oe), owner_email_len=len(oe), owner_email_has_at=("@" in oe),
        owner_password_len=len(op), owner_password_has_space=(op != op.strip()),
        owner_pin_len=len(on), owner_pin_is_digits_only=on.isdigit(), owner_pin_has_space=(on != on.strip()),
        admin_email_set=bool(ae), admin_email_len=len(ae),
        admin_password_len=len(ap), admin_pin_len=len(an),
    )


# ------------------------------------------------------------------ auth
# Ek hi login form (email + password + PIN): OWNER_* creds match -> main panel, ADMIN_* creds
# match -> lite panel, koi bhi match nahi -> 1 hi shared counter mein fail ginta hai (5 galat
# attempt -> IP 1 hour lock, disk par save — security.py dekho).
@app.post("/api/login")
def login():
    if not security.creds_ready():
        return bad("Server par OWNER_EMAIL/PASSWORD/PIN ya ADMIN_EMAIL/PASSWORD/PIN env set nahi hain (Render → Environment).", 503)
    ip = security.client_ip()
    wait = security.locked_for(ip)
    if wait:
        return bad(f"Bahut zyada galat attempts. {wait // 60 + 1} minute baad try karo.", 429)
    d = request.get_json(silent=True) or {}
    role = security.check_login_combined(d.get("email"), d.get("password"), d.get("pin"))
    if not role:
        security.register_fail(ip)
        time.sleep(0.8)
        return bad("Email, password ya PIN galat hai", 401)
    security.register_ok(ip)
    resp = jsonify(ok=True, role=role)
    if role == "owner":
        resp.set_cookie(security.SESSION_COOKIE, security.make_session(security.creds()[0]),
                        max_age=int(security.SESSION_HOURS * 3600), httponly=True,
                        samesite="Strict", secure=request.is_secure)
        resp.delete_cookie(lite.COOKIE)
    else:
        resp.set_cookie(lite.COOKIE, lite.make_session(),
                        max_age=lite.SESSION_H * 3600, httponly=True,
                        samesite="Strict", secure=request.is_secure)
        resp.delete_cookie(security.SESSION_COOKIE)
    return resp


@app.get("/api/session")
def session():
    u = security.current_user()
    if u:
        return jsonify(ok=True, ready=security.creds_ready(), role="owner", email=u.get("u"))
    if lite.read_session():
        return jsonify(ok=True, ready=security.creds_ready(), role="admin", email=lite.EMAIL)
    return jsonify(ok=False, ready=security.creds_ready())


@app.post("/api/logout")
def logout():
    resp = jsonify(ok=True)
    resp.delete_cookie(security.SESSION_COOKIE)
    resp.delete_cookie(lite.COOKIE)
    return resp


@app.get("/api/firebase-token")
@security.login_required
def firebase_token():
    """Panel ke Firestore client ko admin claim wala token — service key server par hi rehti hai."""
    try:
        return ok(token=fb.make_custom_token(security.creds()[0]))
    except Exception as e:
        return bad(f"Firebase token nahi bana: {e}", 500)


# ------------------------------------------------------------------ server info
@app.before_request
def _sys_req_start():
    g._t0 = sysmon.req_start()


@app.after_request
def _sys_req_end(resp):
    try:
        if not request.path.startswith("/api/server/live"):
            sysmon.req_end(getattr(g, "_t0", time.time()), resp.status_code)
    except Exception:
        pass
    return resp


@app.after_request
def _lite_cache_bust(resp):
    """Lite panel ki list-cache: koi write (POST) kamyab hua to turant saaf (taaki naya data dikhe)."""
    try:
        if request.method == "POST" and resp.status_code < 400 and request.path.startswith(("/api/users", "/api/wallet", "/api/matches")):
            lite.bust_cache()
    except Exception:
        pass
    return resp


@app.get("/api/server/live")
@security.login_required
def server_live():
    """Live RAM / CPU / disk / requests / Firestore latency — admin panel ka top strip isse chalta hai."""
    try:
        st = bot.status()
        slim = {k: st.get(k) for k in ("status", "last_run", "last_saved", "total_saved", "emails_found", "gmail_connected")}
        ka = {k: keepalive.state.get(k) for k in ("enabled", "pings", "fails", "watchdog_restarts", "last_ok")}
        return ok(**sysmon.snapshot(fb.get_db, slim, ka, backup.BACKUP_DIR))
    except Exception as e:
        return bad(f"Monitor error: {e}", 500)


@app.get("/api/status")
@security.login_required
def status():
    try:
        total, used, free = shutil.disk_usage(backup.BACKUP_DIR)
    except Exception:
        total = used = free = 0
    st = fb.firebase_status()
    if not st["ok"]:
        try:
            fb.get_db()
        except Exception:
            pass
        st = fb.firebase_status()
    return ok(
        uptime=int(time.time() - BOOT), python=platform.python_version(), firebase=st,
        env={
            "OWNER_EMAIL": bool(os.environ.get("OWNER_EMAIL")), "OWNER_PASSWORD": bool(os.environ.get("OWNER_PASSWORD")),
            "OWNER_PIN": bool(os.environ.get("OWNER_PIN")),
            "ADMIN_EMAIL": bool(os.environ.get("ADMIN_EMAIL")), "ADMIN_PASSWORD": bool(os.environ.get("ADMIN_PASSWORD")),
            "ADMIN_PIN": bool(os.environ.get("ADMIN_PIN")), "SESSION_SECRET": security.secret_from_env(),
            **bot.env_report(),
        },
        backup_dir=backup.BACKUP_DIR, disk_free=free, session_hours=security.SESSION_HOURS,
        user=g.user.get("u"), keepalive=keepalive.state,
    )


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs/<jid>")
@security.login_required
def job(jid):
    j = backup.get_job(jid)
    return ok(job=j) if j else bad("Job nahi mila", 404)


def _start(kind, fn, *a, **kw):
    j = backup.run_job(kind, fn, *a, **kw)
    if not j:
        return bad("Doosra backup/restore/delete abhi chal raha hai, thoda ruko.", 409)
    return ok(job=j["id"])




# ------------------------------------------------------------------ CHAT: token bridge (user app -> chat project)
@app.route("/api/chat/token", methods=["POST", "OPTIONS"])
def chat_token():
    """User app apna main ID token bhejta hai; chat project ke liye custom token milta hai (uid same)."""
    origin = os.environ.get("CHAT_CORS_ORIGIN", "*")
    hdr = {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "POST, OPTIONS", "Vary": "Origin"}
    if request.method == "OPTIONS":
        return Response(status=204, headers=hdr)
    try:
        if not fb.chat_separate():
            return Response('{"ok":false,"error":"separate chat project not configured"}', status=400, mimetype="application/json", headers=hdr)
        tok = (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        info = fb.verify_main_token(tok)
        return Response(jsonify(ok=True, token=fb.chat_custom_token(info["uid"])).get_data(), mimetype="application/json", headers=hdr)
    except Exception as e:
        return Response(jsonify(ok=False, error=str(e)[:160]).get_data(), status=401, mimetype="application/json", headers=hdr)


# ------------------------------------------------------------------ CHAT: admin manager
@app.get("/api/chatadm/overview")
@security.login_required
def ca_overview():
    try: return ok(**chatadmin.overview())
    except Exception as e: return bad(f"Chat DB error: {e}", 500)


@app.post("/api/chatadm/world")
@security.login_required
def ca_world_save():
    try: return ok(cfg=chatadmin.save_world_cfg(request.get_json(silent=True) or {}))
    except Exception as e: return bad(str(e), 500)


@app.get("/api/chatadm/users")
@security.login_required
def ca_users():
    try: return ok(users=chatadmin.users(request.args.get("q", "")))
    except Exception as e: return bad(f"Chat DB error: {e}", 500)


@app.get("/api/chatadm/user/<uid>")
@security.login_required
def ca_user(uid):
    try:
        d = chatadmin.user_detail(uid)
        return ok(**d) if d else bad("Chat profile nahi mila", 404)
    except Exception as e: return bad(f"Chat DB error: {e}", 500)


@app.get("/api/chatadm/messages/<kind>/<cid>")
@security.login_required
def ca_messages(kind, cid):
    try: return ok(messages=chatadmin.messages(kind, cid))
    except Exception as e: return bad(str(e), 500)


@app.post("/api/chatadm/msg/delete")
@security.login_required
def ca_msg_delete():
    try: chatadmin.soft_delete_message((request.get_json(silent=True) or {}).get("path", "")); return ok()
    except Exception as e: return bad(str(e), 400)


@app.post("/api/chatadm/user/<uid>/ban")
@security.login_required
def ca_ban(uid):
    d = request.get_json(silent=True) or {}
    try: chatadmin.set_banned(uid, d.get("banned"), d.get("reason", "")); return ok()
    except Exception as e: return bad(str(e), 500)


@app.post("/api/chatadm/<kind>/<cid>/delete")
@security.pin_required
def ca_delete(kind, cid):
    try: chatadmin.delete_chat(kind, cid); return ok()
    except Exception as e: return bad(str(e), 400)


@app.post("/api/chatadm/world/clear")
@security.pin_required
def ca_world_clear():
    try: return ok(deleted=chatadmin.world_clear())
    except Exception as e: return bad(str(e), 500)


@app.get("/api/chatadm/groups")
@security.login_required
def ca_groups():
    try: return ok(groups=chatadmin.groups())
    except Exception as e: return bad(str(e), 500)


@app.get("/api/chatadm/reports")
@security.login_required
def ca_reports():
    try: return ok(reports=chatadmin.reports(request.args.get("status", "open")))
    except Exception as e: return bad(str(e), 500)


@app.post("/api/chatadm/report/<rid>")
@security.login_required
def ca_report(rid):
    d = request.get_json(silent=True) or {}
    try: chatadmin.resolve_report(rid, d.get("status", "resolved"), d.get("note", "")); return ok()
    except Exception as e: return bad(str(e), 500)


# ------------------------------------------------------------------ DATA CENTER (8 data types) + TELEGRAM
@app.get("/api/dc/types")
@security.login_required
def dc_types():
    st = datacenter.get_settings()
    return ok(types=[{"key": k, **v, "auto": ((st.get("types") or {}).get(k) or {}).get("auto") or {}} for k, v in datacenter.TYPES.items()],
              monthly=st.get("monthly") or {}, telegram=tgbackup.public_cfg(), files=[f for f in backup.list_backups() if f["name"].startswith("ff_")][:20])


@app.get("/api/dc/<key>/summary")
@security.login_required
def dc_summary(key):
    if key not in datacenter.TYPES: return bad("Unknown type", 404)
    try: return ok(**datacenter.summary(key))
    except Exception as e: return bad(f"Firestore error: {e}", 500)


@app.post("/api/dc/<key>/export")
@security.login_required
def dc_export(key):
    if key not in datacenter.TYPES: return bad("Unknown type", 404)
    return _start("dc-export", lambda job: (lambda r: {"name": os.path.basename(r[0]), "docs": r[1]})(datacenter.export_file(key, job)))


@app.post("/api/dc/<key>/upload")
@security.pin_required
def dc_upload(key):
    if key not in datacenter.TYPES: return bad("Unknown type", 404)
    f = request.files.get("file")
    if not f: return bad("File select karo")
    tmp = os.path.join(backup.BACKUP_DIR, f"dcup_{int(time.time())}.json")
    f.save(tmp)

    def run(job):
        try: return datacenter.restore_file(job, key, tmp)
        finally:
            try: os.remove(tmp)
            except OSError: pass
    return _start("dc-restore", run)


@app.post("/api/dc/<key>/delete")
@security.pin_required
def dc_delete(key):
    if key not in datacenter.TYPES or not datacenter.TYPES[key]["deletable"]: return bad("Is type ka delete option nahi hai", 400)
    d = request.get_json(silent=True) or {}
    try: days = int(d.get("days"))
    except Exception: return bad("Din (days) dalo")
    if days < 0 or days > 3650: return bad("Days 0-3650 ke beech hona chahiye")
    if not tgbackup.public_cfg()["configured"]: return bad("Pehle Telegram account add karo — delete se pehle backup Telegram par jata hai", 400)
    return _start("dc-delete", lambda job: datacenter.delete_older(job, key, days))


@app.post("/api/dc/<key>/auto")
@security.login_required
def dc_auto(key):
    if key not in datacenter.TYPES or not datacenter.TYPES[key]["deletable"]: return bad("Is type ka auto-delete nahi hai", 400)
    d = request.get_json(silent=True) or {}
    if d.get("enabled") and not tgbackup.public_cfg()["configured"]: return bad("Auto-delete ke liye Telegram account add karna zaroori hai (backup pehle jata hai)", 400)
    try: datacenter.set_auto(key, d.get("enabled"), d.get("days") or 30, str(d.get("time") or "03:00"), d.get("day") or 0)
    except Exception as e: return bad(str(e))
    return ok()


@app.get("/api/dc/file/<name>")
@security.login_required
def dc_file(name):
    p = backup.backup_path(name)
    if not p or not name.startswith("ff_"): return bad("File nahi mili", 404)
    return send_file(p, as_attachment=True, download_name=name)


@app.post("/api/dc/monthly")
@security.login_required
def dc_monthly():
    d = request.get_json(silent=True) or {}
    if d.get("run"):
        if not tgbackup.public_cfg()["configured"]: return bad("Telegram account add nahi hai", 400)
        return _start("dc-month", lambda job: datacenter.month_end_backup(job))
    patch = {"enabled": bool(d.get("enabled"))}
    if "day" in d: patch["day"] = max(0, min(31, int(d.get("day") or 0)))
    if d.get("time"): patch["time"] = str(d["time"])[:5]
    datacenter.save_settings({"monthly": patch})
    return ok()


@app.get("/api/tg")
@security.login_required
def tg_get(): return ok(**tgbackup.public_cfg())


@app.post("/api/tg")
@security.pin_required
def tg_save():
    d = request.get_json(silent=True) or {}
    try: tgbackup.save_cfg(d.get("api_id"), d.get("api_hash"), d.get("session"), d.get("chat") or "me")
    except Exception as e: return bad(f"Save error: {e}")
    return ok(**tgbackup.public_cfg())


@app.post("/api/tg/test")
@security.login_required
def tg_test():
    try: who = tgbackup.send(text="✅ FF Admin — Telegram backup account connected.")
    except Exception as e: return bad(str(e), 400)
    return ok(account=who)


@app.post("/api/tg/remove")
@security.pin_required
def tg_remove():
    tgbackup.remove_cfg(); return ok()


# ------------------------------------------------------------------ backups
_col_cache = {"t": 0.0, "v": None}
_col_lock = threading.Lock()


@app.get("/api/collections")
@security.login_required
def collections():
    """Collection counts: 2 min cache + har count ke beech 0.4s aaram (pehle sab ek saath)."""
    if _col_cache["v"] is not None and time.time() - _col_cache["t"] < 120:
        return ok(collections=_col_cache["v"])
    with _col_lock:
        if _col_cache["v"] is not None and time.time() - _col_cache["t"] < 120:
            return ok(collections=_col_cache["v"])
        try:
            cols = backup.list_root_collections()
        except Exception as e:
            return bad(f"Firestore error: {e}", 500)
        out = []
        for c in cols:
            out.append({"name": c, "count": backup.count_docs(c)})
            slow.rest(0.4)
        _col_cache.update(t=time.time(), v=out)
    return ok(collections=out)


@app.get("/api/backups")
@security.login_required
def backups():
    return ok(backups=backup.list_backups(), settings=backup.get_settings(), dir=backup.BACKUP_DIR)


@app.post("/api/backups")
@security.login_required
def backup_create():
    d = request.get_json(silent=True) or {}
    cols = [c for c in (d.get("collections") or []) if isinstance(c, str) and "/" not in c] or None
    return _start("backup", backup.do_backup, cols, bool(d.get("subcollections", True)), "manual")


@app.get("/api/backups/<name>/download")
@security.login_required
def backup_download(name):
    p = backup.backup_path(name)
    return send_file(p, as_attachment=True, download_name=name) if p else bad("File nahi mili", 404)


@app.post("/api/backups/<name>/delete")
@security.pin_required
def backup_delete(name):
    return ok() if backup.delete_backup(name) else bad("File nahi mili", 404)


@app.post("/api/backups/<name>/restore")
@security.pin_required
def backup_restore(name):
    p = backup.backup_path(name)
    if not p:
        return bad("File nahi mili", 404)
    only = (request.get_json(silent=True) or {}).get("collections")
    return _start("restore", backup.do_restore, p, only)


@app.post("/api/restore-upload")
@security.pin_required
def restore_upload():
    f = request.files.get("file")
    if not f:
        return bad("File select karo")
    fn = f.filename or ""
    ext = ".ffbak.gz" if fn.endswith(".ffbak.gz") else (".json.gz" if fn.endswith(".json.gz") else ".json")
    tmp = os.path.join(backup.BACKUP_DIR, f"upload_{int(time.time())}{ext}")
    f.save(tmp)

    def run(job, path):
        try:
            return backup.do_restore(job, path)
        finally:
            try: os.remove(path)
            except OSError: pass
    return _start("restore", run, tmp)


@app.post("/api/backup-settings")
@security.login_required
def backup_settings():
    d = request.get_json(silent=True) or {}
    backup.save_settings(d.get("interval_hours"), d.get("keep"))
    return ok(settings=backup.get_settings())


# ------------------------------------------------------------------ data delete
@app.post("/api/data/delete-collection")
@security.pin_required
def del_collection():
    d = request.get_json(silent=True) or {}
    col = (d.get("collection") or "").strip()
    if not col or "/" in col:
        return bad("Collection ka naam galat hai")
    if d.get("confirm") != col:
        return bad("Confirm text collection ke naam se match nahi hua")
    return _start("delete", backup.do_delete_collection, col, bool(d.get("backup", True)))


@app.post("/api/data/delete-doc")
@security.pin_required
def del_doc():
    d = request.get_json(silent=True) or {}
    path = (d.get("path") or "").strip().strip("/")
    if not path or len(path.split("/")) % 2 != 0:
        return bad("Doc path galat (format: collection/docId)")
    return _start("delete", backup.do_delete_doc, path, bool(d.get("recursive", True)), bool(d.get("backup", True)))


@app.post("/api/data/delete-older")
@security.pin_required
def del_older():
    d = request.get_json(silent=True) or {}
    col, field = (d.get("collection") or "").strip(), (d.get("field") or "").strip()
    try:
        days = float(d.get("days"))
    except (TypeError, ValueError):
        return bad("Days number hona chahiye")
    if not col or "/" in col or not field or days < 0:
        return bad("Collection / field / days check karo")
    return _start("delete", backup.do_delete_older, col, field, days,
                  "ts" if d.get("type") == "ts" else "ms", bool(d.get("backup", True)))


# ------------------------------------------------------------------ fampay bot
@app.get("/api/bot/status")
@security.login_required
def bot_status():
    return ok(bot=bot.status(), logs=list(bot.logs)[-120:])


@app.post("/api/bot/<action>")
@security.login_required
def bot_action(action):
    if action == "start":
        good, msg = bot.start()
    elif action == "stop":
        good, msg = bot.stop()
    elif action == "poll":
        good, msg = bot.poll_now()
    else:
        return bad("Unknown action", 404)
    return (ok(message=msg) if good else bad(msg, 400))


@app.post("/api/bot/settings")
@security.login_required
def bot_settings():
    d = request.get_json(silent=True) or {}
    try:
        bot.save_settings(poll_interval=int(d.get("poll_interval", bot.settings["poll_interval"])), query=d.get("query"))
    except (TypeError, ValueError):
        return bad("Poll interval number hona chahiye")
    return ok(settings=bot.settings)


@app.post("/api/bot/clear-processed")
@security.pin_required
def bot_clear():
    bot.clear_processed()
    return ok()


@app.get("/api/bot/deposits")
@security.login_required
def bot_deposits():
    try:
        snaps = fb.get_db().collection("fampay_deposits").order_by("receivedAt", direction="DESCENDING").limit(100).stream()
        rows = []
        for s in snaps:
            d = s.to_dict() or {}
            if d.get("payId") and d.get("payId") != s.id:
                continue          # UTR / Transaction ID ki duplicate copy — ek payment ek hi baar dikhao
            rows.append({"id": s.id, **{k: v for k, v in d.items() if k in ("utr", "txnId", "amount", "receivedAt", "source", "addedBy")}})
        return ok(deposits=rows[:50])
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)


@app.post("/api/bot/deposits")
@security.login_required
def bot_deposit_add():
    """Manual transaction add — jab bot Gmail se UTR/Transaction ID pakad nahi paata, admin khud daal sake."""
    d = request.get_json(silent=True) or {}
    try:
        amount = float(d.get("amount"))
        assert amount > 0
    except Exception:
        return bad("Amount sahi number hona chahiye")
    utr = (d.get("utr") or "").strip() or None
    txn_id = (d.get("txnId") or "").strip() or None
    if not (utr or txn_id):
        return bad("UTR ya Transaction ID mein se kam se kam ek do")
    try:
        did = bot.save_deposit(amount, utr, txn_id, source="manual", added_by=g.user.get("u"))
    except ValueError as e:
        return bad(str(e))
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    if not did:
        return bad("Yeh UTR/Transaction ID + amount pehle se maujood hai (duplicate)")
    return ok(id=did)


@app.post("/api/bot/deposits/<dep_id>/delete")
@security.pin_required
def bot_deposit_delete(dep_id):
    """Payment ki saari copies (UTR + Transaction ID) delete karta hai."""
    try:
        bot.delete_deposit(dep_id)
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    return ok()


@app.post("/api/bot/deposits/<dep_id>/use")
@security.login_required
def bot_deposit_use(dep_id):
    """Pending entry ko 'successful' maan kar history mein move kar deta hai (wallet approve se bhi
    yeh auto hota hai — yeh button un entries ke liye jo wallet flow se match nahi hui par admin ne
    kahin aur verify kar li)."""
    d = request.get_json(silent=True) or {}
    try:
        row = bot.mark_used(dep_id, used_by=g.user.get("u"), note=d.get("note"))
    except ValueError as e:
        return bad(str(e), 404)
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    return ok(row=row)


@app.get("/api/bot/deposits/history")
@security.login_required
def bot_deposit_history():
    rng = (request.args.get("range") or "all").strip().lower()
    days = {"today": 1, "7d": 7, "30d": 30, "all": None}.get(rng, None)
    try:
        rows = bot.history_list(days=days)
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    return ok(history=rows)


@app.get("/api/bot/deposits/history/export")
@security.login_required
def bot_deposit_history_export():
    import csv, io
    rng = (request.args.get("range") or "all").strip().lower()
    days = {"today": 1, "7d": 7, "30d": 30, "all": None}.get(rng, None)
    try:
        rows = bot.history_list(days=days, limit=5000)
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "utr", "txnId", "amount", "source", "usedBy", "usedAt", "note"])
    for r in rows:
        used_at = r.get("usedAt")
        used_at = dt.datetime.utcfromtimestamp(used_at / 1000).strftime("%Y-%m-%d %H:%M:%S") if used_at else ""
        w.writerow([r.get("id", ""), r.get("utr") or "", r.get("txnId") or "", r.get("amount", ""),
                    r.get("source", ""), r.get("usedBy") or "", used_at, r.get("note") or ""])
    data = buf.getvalue().encode("utf-8-sig")
    resp = Response(data, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="fampay_history_{rng}.csv"'
    return resp


@app.post("/api/bot/deposits/history/delete-old")
@security.pin_required
def bot_deposit_history_delete_old():
    d = request.get_json(silent=True) or {}
    try:
        days = float(d.get("days"))
        assert days >= 0
    except Exception:
        return bad("Days number hona chahiye")
    try:
        n = bot.history_delete_before(days)
    except Exception as e:
        return bad(f"Firestore error: {e}", 500)
    return ok(deleted=n)


# ------------------------------------------------------------------ lite panel routes (merged)
# admin_lite.py ke functions pehle se @login_required (lite ka apna session/cookie check) se decorated
# hain, isliye yahan unhi ko seedha register kar rahe hain — ek hi process, ek hi Firestore connection.
@app.errorhandler(lite.ApiError)
def _lite_api_err(e):
    return jsonify(ok=False, error=e.msg), e.code


@app.errorhandler(500)
def _json_500(e):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Server error"), 500
    return e


_LITE_ROUTES = [
    ("/api/users", ["GET"], lite.users),
    ("/api/users/<uid>", ["GET"], lite.user_detail),
    ("/api/users/<uid>/balance", ["POST"], lite.user_balance),
    ("/api/users/<uid>/ban", ["POST"], lite.user_ban),
    ("/api/users/<uid>/unban", ["POST"], lite.user_unban),
    ("/api/wallet", ["GET"], lite.wallet),
    ("/api/wallet/process", ["POST"], lite.wallet_process),
    ("/api/matches", ["GET"], lite.matches),
    ("/api/matches", ["POST"], lite.match_create),
    ("/api/matches/<mid>", ["GET"], lite.match_get),
    ("/api/matches/<mid>/delete", ["POST"], lite.match_delete),
    ("/api/matches/<mid>/room", ["POST"], lite.match_room),
    ("/api/matches/<mid>/complete", ["POST"], lite.match_complete),
    ("/api/matches/<mid>/pay", ["POST"], lite.match_pay),
    ("/api/settings", ["GET"], lite.settings_get),
    ("/api/settings", ["POST"], lite.settings_save),
]
for _rule, _methods, _fn in _LITE_ROUTES:
    app.add_url_rule(_rule, endpoint=f"lite_{_fn.__name__}_{_methods[0]}", view_func=_fn, methods=_methods)


# ------------------------------------------------------------------ boot
_booted = False
_boot_lock = threading.Lock()


def boot():
    global _booted
    with _boot_lock:
        if _booted:
            return
        _booted = True
    if not security.creds_ready():
        print("[warn] OWNER_EMAIL/PASSWORD/PIN aur ADMIN_EMAIL/PASSWORD/PIN dono missing — login disabled", flush=True)
    if not security.secret_from_env():
        print("[warn] SESSION_SECRET set nahi — restart par sab admin logout ho jayenge", flush=True)
    threading.Thread(target=bot.autostart, daemon=True, name="bot-boot").start()
    backup.start_scheduler()
    jobs.start()
    keepalive.start()


boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
