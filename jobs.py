"""Background jobs: month-end leaderboard reward, Data Center scheduler, unverified-signup cleanup."""
import time, threading, datetime as dt
import fb, datacenter, slow

IST = datacenter.IST


def _order(trx_id, now):
    return f"PZ-{now:%Y%m%d}-{trx_id[-6:].upper()}"


def monthly_reward():
    """1st of the month (after 12:00 AM IST): pay last month's top winners (rewards from config/leaderboard). Runs once per month."""
    from google.cloud import firestore
    now = dt.datetime.now(IST)
    prev = (now.replace(day=1) - dt.timedelta(days=1))
    mk = prev.strftime("%Y%m")
    db = fb.get_db()
    cfg = (db.collection("config").document("leaderboard").get().to_dict() or {})
    rewards = [float(r.get("amount") or 0) for r in (cfg.get("rewards") or [])]
    rewards = [r for r in rewards if r > 0]
    if not rewards or cfg.get("rewardEnabled") is False: return
    marker = db.collection("lb_rewards").document(mk)
    if marker.get().exists: return
    try:
        marker.create({"month": mk, "startedAt": int(time.time() * 1000)})          # create() = sirf ek baar (race safe)
    except Exception:
        return
    players = ((db.collection("lb").document("m-" + mk).get().to_dict() or {}).get("players") or {})
    rank = sorted([(uid, p) for uid, p in players.items() if float(p.get("a") or 0) > 0], key=lambda x: -float(x[1].get("a") or 0))[:len(rewards)]
    paid = []
    for i, (uid, p) in enumerate(rank):
        amt = rewards[i]; uref = db.collection("users").document(uid)
        if not uref.get().exists: continue
        tref = uref.collection("transactions").document()
        order = _order(tref.id, now)
        label = prev.strftime("%B %Y")
        b = db.batch()
        b.set(uref, {"withdrawBalance": firestore.Increment(amt), "totalEarned": firestore.Increment(amt)}, merge=True)
        b.set(tref, {"title": f"Monthly Leaderboard Rank #{i+1}", "amount": f"+₹{amt:g}", "type": "deposit", "status": "success", "kind": "prize",
                     "orderNo": order, "balanceType": "withdraw", "date": now.strftime("%d/%m/%Y"), "createdAt": int(time.time() * 1000)})
        b.set(uref.collection("notifications").document(), {"title": f"🏆 Monthly Rank #{i+1} Reward — ₹{amt:g}",
              "message": f"Congratulations! You finished #{i+1} on the {label} leaderboard (₹{float(p.get('a')):g} winnings). ₹{amt:g} was added to your withdrawal balance. Order {order}.",
              "date": now.strftime("%d %b %Y"), "timestamp": firestore.SERVER_TIMESTAMP, "type": "reward"})
        b.commit(); paid.append({"uid": uid, "rank": i + 1, "amount": amt})
    marker.set({"paid": paid, "doneAt": int(time.time() * 1000)}, merge=True)
    print("[reward] monthly paid:", paid, flush=True)


def cleanup_unverified(minutes=20):
    """Email+password signups jinhone `minutes` mein email verify nahi kiya: Auth account + (khaali) Firestore doc delete.
    SLOW MODE: users 200-200 ke page mein, har page ke baad 3s aaram (pehle ek saath saare users)."""
    from firebase_admin import auth
    cutoff = (time.time() - minutes * 60) * 1000
    db = fb.get_db(); n = 0
    page = auth.list_users(max_results=200)
    while page:
        for u in page.users:
            if not u.email or u.email_verified or u.disabled: continue
            ids = {p.provider_id for p in u.provider_data}
            if "google.com" in ids or "password" not in ids: continue
            if (u.user_metadata.creation_timestamp or 0) > cutoff: continue
            ref = db.collection("users").document(u.uid); snap = ref.get(timeout=20)
            slow.rest(0.3)
            if snap.exists:
                d = snap.to_dict() or {}
                if float(d.get("depositBalance") or 0) + float(d.get("withdrawBalance") or 0) + float(d.get("balance") or 0) > 0: continue
                slow.delete_tree(db, ref)
            try: auth.delete_user(u.uid); n += 1
            except Exception as e: print("[cleanup] delete fail", u.email, e, flush=True)
            slow.rest(0.5)
        slow.rest(3)
        page = page.get_next_page()
    if n: print(f"[cleanup] {n} unverified signup(s) removed", flush=True)
    return n


def _loop():
    """Background jobs — SLOW MODE: boot ke 2.5 min baad, ek-ek karke (staggered), kam baar.
    reward: 15 min | data-center tick: 10 min | unverified cleanup: 30 min."""
    t0 = time.time()
    # alag-alag waqt par pehli baar chalein (ek saath nahi): reward ~0s, dc ~+2min, cleanup ~+5min
    last = {"reward": t0 - 900, "dc": t0 - 600 + 120, "clean": t0 - 1800 + 300}
    time.sleep(150)
    while True:
        t = time.time()
        for name, every, fn in (("reward", 900, monthly_reward), ("dc", 600, datacenter._tick), ("clean", 1800, cleanup_unverified)):
            if t - last[name] >= every:
                last[name] = t
                try: fn()
                except Exception as e: print(f"[jobs] {name}:", e, flush=True)
                slow.rest(5)          # ek job ke baad doosre se pehle aaram
        time.sleep(30)


def start():
    threading.Thread(target=_loop, daemon=True, name="ff-jobs").start()
