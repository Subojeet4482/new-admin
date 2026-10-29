"""Telegram backup (Telethon). Admin panel se account add hota hai: api_id + api_hash + Telethon StringSession.
Files us Telegram account ke 'Saved Messages' (ya chosen chat) mein upload hoti hain.
Credentials Firestore bot_meta/telegram mein rehte hain (sirf admin padh sakta hai)."""
import os, asyncio, threading, time
import fb

DOC = ("bot_meta", "telegram")
_lock = threading.Lock()


def get_cfg():
    try:
        d = fb.get_db().collection(DOC[0]).document(DOC[1]).get()
        return (d.to_dict() or {}) if d.exists else {}
    except Exception:
        return {}


def public_cfg():
    c = get_cfg()
    mask = lambda s: (s[:3] + "…" + s[-3:]) if s and len(s) > 8 else ("set" if s else "")
    return {"configured": bool(c.get("api_id") and c.get("api_hash") and c.get("session")),
            "api_id": str(c.get("api_id") or ""), "api_hash": mask(c.get("api_hash") or ""), "session": mask(c.get("session") or ""),
            "chat": c.get("chat") or "me", "account": c.get("account") or "", "last_ok": c.get("last_ok"), "last_err": c.get("last_err")}


def save_cfg(api_id, api_hash, session, chat="me"):
    cur = get_cfg()
    new = {"api_id": int(api_id) if str(api_id).strip() else cur.get("api_id"),
           "api_hash": (api_hash or "").strip() or cur.get("api_hash"),
           "session": (session or "").strip() or cur.get("session"),
           "chat": (chat or "me").strip() or "me"}
    fb.get_db().collection(DOC[0]).document(DOC[1]).set(new, merge=True)


def remove_cfg():
    fb.get_db().collection(DOC[0]).document(DOC[1]).delete()


def _client(cfg):
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        raise RuntimeError("telethon install nahi hai (requirements.txt mein telethon hai — redeploy karo)")
    if not (cfg.get("api_id") and cfg.get("api_hash") and cfg.get("session")):
        raise RuntimeError("Telegram account add nahi hai (api_id / api_hash / session)")
    return TelegramClient(StringSession(cfg["session"]), int(cfg["api_id"]), cfg["api_hash"])


def _target(chat):
    chat = (chat or "me").strip()
    if chat.lstrip("-").isdigit():
        return int(chat)
    return chat


async def _run(items, text, cfg):
    c = _client(cfg)
    await c.connect()
    try:
        if not await c.is_user_authorized():
            raise RuntimeError("Telegram session expire / galat hai — naya session string banao")
        me = await c.get_me()
        who = ("@" + me.username) if getattr(me, "username", None) else (getattr(me, "first_name", "") or str(me.id))
        tgt = _target(cfg.get("chat"))
        if text:
            await c.send_message(tgt, text)
        for path, caption in items:
            await c.send_file(tgt, path, caption=caption[:1000] if caption else None, force_document=True)
        return who
    finally:
        await c.disconnect()


def send(items=None, text=None):
    """items = [(file_path, caption)]. Returns account name. Raises on failure."""
    cfg = get_cfg()
    with _lock:                       # ek time par ek hi Telegram session
        try:
            who = asyncio.run(_run(items or [], text, cfg))
            fb.get_db().collection(DOC[0]).document(DOC[1]).set({"account": who, "last_ok": int(time.time() * 1000), "last_err": ""}, merge=True)
            return who
        except Exception as e:
            try:
                fb.get_db().collection(DOC[0]).document(DOC[1]).set({"last_err": str(e)[:200]}, merge=True)
            except Exception:
                pass
            raise
