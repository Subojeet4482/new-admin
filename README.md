# FF Admin — Server + MAIN panel + LITE panel + Fampay Bot (ek hi Render service, ek hi login)

Ab **ek hi** Flask service mein **ek hi login form** hai (email + password + PIN). Server khud decide karta hai
kaunsa panel kholna hai:

- **OWNER_EMAIL / OWNER_PASSWORD / OWNER_PIN** sahi diye → **MAIN panel** khulta hai (sab kuch: matches,
  wallet, users, backup/restore/delete, Fampay bot control).
- **ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_PIN** sahi diye → **LITE panel** khulta hai (sirf matches, wallet,
  users, settings — backup/restore/delete/bot nahi).
- Dono mein se kuch bhi match nahi hua → **1 hi shared counter**: 5 galat attempt ke baad us IP ke liye
  **1 hour** lock. Yeh lock **disk par** (`backups/login_locks.json`) save hota hai, Firebase mein kuch nahi —
  process restart tak yaad rehta hai (Render free plan redeploy par disk wipe karta hai, jaisa neeche likha hai).

Kaam isi ek service ke andar:

1. **Admin panel serve karta hai** (`public/` = main panel, `admin_lite.py` ka embedded HTML = lite panel).
2. **Backup / Restore / Data Delete** — sirf owner ke liye, Firebase Admin SDK se (service key ka base64 env mein).
3. **Fampay bot** (Gmail watcher) isi service ke andar thread mein chalta hai — sirf owner panel se Start / Stop / Poll now / settings / live log.
4. **Self keepalive + health** — ek hi `/health` URL (UptimeRobot isi ko ping kare), neeche "Auto-alive" section mein details.

User app (`tournament app`) ko kuch nahi chhua gaya.

## Render par deploy (5 steps)

1. Yeh folder GitHub repo mein push karo → Render → **New → Web Service** (ya `render.yaml` se Blueprint).
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --workers 1 --threads 16 --timeout 120 --bind 0.0.0.0:$PORT`
   - **Workers 1 hi rakho** (bot aur scheduler isi process mein hain).
2. Service key ka base64 banao: Firebase Console → Project settings → Service accounts → *Generate new private key*, phir
   `python tools/make_b64.py serviceAccount.json` → output copy karo.
3. Render → Environment mein yeh set karo:

| Key | Kaam |
|---|---|
| `OWNER_EMAIL`, `OWNER_PASSWORD`, `OWNER_PIN` | Login se **MAIN panel** khulta hai (teeno chahiye) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_PIN` | Login se **LITE panel** khulta hai (teeno chahiye) |
| `LOGIN_MAX_FAILS` (5), `LOGIN_LOCK_MINUTES` (60) | Galat attempt lock (dono panel ke liye shared) |
| `SESSION_SECRET` | Random lamba string (Blueprint auto bana deta hai) |
| `FIREBASE_CREDENTIALS_B64` | Step 2 ka base64 |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | Fampay bot (Gmail) |
| `POLL_INTERVAL` (15), `GMAIL_QUERY`, `BOT_AUTOSTART` (true) | Bot defaults — panel se bhi badal sakte ho |
| `BACKUP_INTERVAL_HOURS` (24), `BACKUP_KEEP` (10), `BACKUP_DIR` | Auto-backup |

4. **Firestore rules** — `firestore.rules` publish karo. Sirf `isAdmin()` badla hai: ab `request.auth.token.admin == true` bhi chalta hai (server yahi claim deta hai). Purani email wali condition bhi rakhi hai, baaki rules same.
5. Service URL kholo → login → **Fampay Bot** tab mein status dekho.

> Pehli baar rules publish karne ke baad hi panel ka Firestore data (users, matches, wallet…) dikhega, kyunki panel ab custom admin token se login hota hai.

## Security

- Password/PIN ab `config.js` mein nahi hain. Purani file mein the, isliye **Firebase Auth mein admin account ka password badal do** (purana password GitHub / zip mein dikh chuka hai).
- Login: constant-time check (owner + admin dono creds), 5 galat attempt par 1 hour lock (IP + global, disk par save), signed HttpOnly SameSite=Strict cookie (12h), env badalte hi purane session band.
- Delete / restore / backup-delete par **PIN dobara** lagta hai; collection delete par naam type karna padta hai; default mein delete se pehle auto-backup banta hai.
- Service key sirf server par rehti hai, browser ko kabhi nahi milti.

## Backup ke baare mein

- Format `.ffbak.gz` (Timestamp, GeoPoint, references sahi se restore hote hain). Purane `.json` backups bhi restore ho jaate hain.
- **Render free plan ka disk restart par saaf ho jaata hai.** Isliye backup ko panel se **download** karke rakho, ya Render Disk lagao aur `BACKUP_DIR=/var/data/backups` set karo.
- Free plan 15 min idle par sleep karta hai — bot tab tak band rehta hai. Bot 24x7 chahiye to paid plan ya UptimeRobot se `/health` ping karo.

## Auto-alive + uptime

- **Self-ping:** server har 10 min (`KEEPALIVE_INTERVAL`) apne public `/health` ko ping karta hai, taaki Render free plan idle sleep na kare. URL Render ka `RENDER_EXTERNAL_URL` automatically use hota hai. Status panel → Server tab mein dikhta hai.
- **Bot watchdog:** bot thread mar jaye aur tumne Stop nahi dabaya ho to 60s mein apne aap restart.
- **Uptime monitor (zaroori backup):** self-ping tabhi kaam karta hai jab server jaag raha ho. Agar crash ya deploy ke baad so gaya, to bahar se koi jagaye. [UptimeRobot](https://uptimerobot.com) (free) → *Add New Monitor* → type **HTTP(s)**, URL `https://<tumhari-service>.onrender.com/health`, interval **5 minutes**. Chaho to Keyword monitor mein `"ok":true` daalo, aur email/Telegram alert on karo.
- `/health` sirf ok, uptime aur bot status batata hai — koi secret nahi.
- Free plan ke 750 ghante/mahina ek service ko 24x7 chalane ke liye kaafi hain.

## Local test

```bash
pip install -r requirements.txt
set -a; . ./.env; set +a      # .env.example copy karke bharo
python app.py                 # http://localhost:10000
```


---
## Chat system (new) — separate database

The user app's Chat tab (World room, friends, groups, profile, search) stores everything in its **own Firestore** (collections `cprofiles, cusernames, cfriends, cblocks, crequests, cchats, cgroups, cworld, creports, cconfig`).

1. Create a 2nd Firebase project -> enable **Firestore** (and Authentication, no sign-in provider needed).
2. Paste `FIREBASE_CREDENTIALS_B641.txt` (rules) into that project's Firestore -> Rules -> Publish.
3. Project settings -> Service accounts -> generate key -> base64 it -> add on the admin server (Render) as env key
   **`FIREBASE_CREDENTIALS_B641`** (same as `FIREBASE_CREDENTIALS_B64` + `1`). Optional: `CHAT_CORS_ORIGIN` = your user-app URL.
4. In the user app edit `js/core/chat-firebase.js`: paste the chat project's **web config** into `CHAT_FIREBASE_CONFIG` and set
   `CHAT_TOKEN_URL = "https://<your-admin>.onrender.com/api/chat/token"` (the admin server signs users into the chat project with a custom token).
5. Redeploy. Until step 3/4 are done the chat runs inside the main project (publish the optional chat block of `FIREBASE_CREDENTIALS_B64.txt`).

Rules: max **20 friends** per user, a message can be **edited / deleted only within 10 minutes**, every risky action asks for confirmation.
Admin: **Chat Manager** (overview + world-room name/slow-mode/lock, users, friend chats, groups, reports, ban) and **Data Center** (chat types are backed up / deleted with the same monthly schedule as the main database).


## Slow mode (Render free plan ke liye)

Free plan mein CPU bahut kam hai, isliye **Fampay bot sabse upar** rakha gaya hai aur baaki heavy kaam chhote tukdon mein, aaram se chalte hain (`slow.py`):

| Kaam | Kaise chalta hai |
|---|---|
| **Fampay bot** | 24/7, har `POLL_INTERVAL` (15s) par. Bot poll karte waqt baaki heavy kaam ruk jaate hain. Watchdog: thread mara ya atka (heartbeat band) to khud dobara start. |
| **Backup** | Har 10 docs ke baad 1s aaram (auto-backup mein 2s). Server start ke 5 min baad hi, aur pehli baar turant nahi (ab se 24h gino). |
| **Restore / Delete** | 100 docs ke batch + 1.5s aaram; delete mein Firestore ops/sec limit. |
| **Users / Wallet / Matches list (lite panel)** | 20-45s cache, sirf zaroori fields, 200 docs ke baad aaram. Koi write hote hi cache saaf. |
| **Data Center** (summary/export/delete) | Har 25 docs ke baad 0.5s aaram. |
| **Background jobs** | Boot ke 2.5 min baad; reward 15 min, data-center tick 10 min, unverified cleanup 30 min (200 users ke page, 3s aaram). |
| **Panel polling** | Server strip 10s, bot tab 8s, finance 45s, dashboard counts 90s, users tab 60s cache. |

Env: `SLOW_FACTOR` (1 = normal, 2 = aur dheema), `BACKUP_PACE_EVERY`, `BACKUP_PACE_REST`.


## Load / down fix (latest)

Render free (0.1 CPU, 512 MB RAM) par slow aur down hone ke asli karan aur unka ilaaj:

| Pehle | Ab |
|---|---|
| Backup har doc par alag `collections()` call karta tha (10k users = 10k extra calls) | Subcollections (users/transactions, notifications, matchHistory, redeems, msgs, messages) collection-group se **ek stream** mein. Purana tareeka: `BACKUP_PROBE_SUBS=1` |
| Lite panel Wallet "All" saare users ke saare transactions RAM mein laata tha (OOM → service restart) | Sirf sabse naye 300 (`WALLET_HISTORY_LIMIT`). Pending list pehle jaisi (kuch miss nahi hota) |
| Lite panel Matches poori collection padhta tha | Naye 300 (`MATCH_LIST_LIMIT`) |
| Unverified-signup cleanup har 30 min mein saare Auth users list karta tha | Har 6 ghante |
| Data Center "summary" har baar poora type stream karta tha | 10 min cache |
| Auto-backup sirf disk par (restart par gayab) | Telegram account add ho to uski copy Telegram par bhi (`AUTO_BACKUP_TELEGRAM`) |
| `/api/_login_debug` bina login ke password/PIN ki length batata tha | Band; sirf `LOGIN_DEBUG=1` par chalta hai |
| gunicorn heartbeat disk par | `--worker-tmp-dir /dev/shm` (Render dashboard mein Start Command bhi yahi karo) |
| Backup pace: har 10 docs par 1s | Ab har 200 docs par 0.5s (N+1 hatne ke baad itna rukna zaroori nahi) |

**Free hours:** Render har workspace ko mahine ke 750 free hours deta hai, per service nahi. Yeh service 24x7 chalegi (bot + self-ping) to ~720 hours kha leti hai. Dusri koi free service bhi 24x7 hai to hours khatam hone par Render sab free services mahine ke end tak suspend kar deta hai.
