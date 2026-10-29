// Live server strip (top of dashboard): RAM / CPU / disk gauges, history sparklines, request + Firestore stats, Fampay ON/OFF switch
const $ = (id) => document.getElementById(id);
const H = { cpu: [], ram: [] }, MAXH = 40;
let timer = null, open = false, busy = false;
const fmtB = (n) => { n = Number(n) || 0; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return n.toFixed(n >= 100 || i === 0 ? 0 : 1) + ' ' + u[i]; };
const col = (p) => p >= 85 ? '#ef4444' : p >= 65 ? '#f59e0b' : '#10b981';
const kv = (k, v, c) => `<div class="lkv"><span>${k}</span><b${c ? ` style="color:${c}"` : ''}>${v}</b></div>`;

function gauge(id, pct, sub) {
  const g = $(id); if (!g) return;
  const f = g.querySelector('.gf'), C = 2 * Math.PI * 42, p = Math.max(0, Math.min(100, pct || 0));
  f.style.strokeDasharray = C; f.style.strokeDashoffset = C * (1 - p / 100); f.style.stroke = col(p);
  g.querySelector('.gv b').innerText = Math.round(p) + '%'; g.querySelector('.gv b').style.color = col(p);
  $(id + '-s').innerText = sub;
}
function spark(id, arr) {
  const el = $(id); if (!el || !arr.length) return;
  const n = arr.length, pts = arr.map((v, i) => `${(i / Math.max(MAXH - 1, 1)) * 120},${30 - Math.max(0, Math.min(100, v)) * 0.28 - 1}`).join(' ');
  el.setAttribute('points', pts);
}

async function tick() {
  if (busy || document.hidden) return;
  if (!$('tab-dash').classList.contains('show')) return;
  busy = true;
  try {
    const s = await A.api('/api/server/live');
    const ramP = s.mem.pct, cpuP = s.cpu;
    H.cpu.push(cpuP); H.ram.push(ramP); if (H.cpu.length > MAXH) { H.cpu.shift(); H.ram.shift(); }
    $('ls-dot').className = 'ls-dot ' + (s.firestore.ok === false ? 'bad' : 'on');
    $('ls-title').innerText = s.firestore.ok === false ? 'Server — DB issue' : 'Server LIVE';
    $('ls-sub').innerText = 'up ' + A.fmtDur(s.uptime) + ' · ' + s.req.per_min + ' req/min';
    $('ls-ram').innerText = Math.round(ramP) + '%'; $('ls-ram').style.color = col(ramP);
    $('ls-cpu').innerText = Math.round(cpuP) + '%'; $('ls-cpu').style.color = col(cpuP);
    const bot = s.bot || {}, on = bot.status === 'running' || bot.status === 'starting';
    const sw = $('ls-bot'); if (sw && document.activeElement !== sw) sw.checked = on;
    $('ls-bot').closest('.ls-switch').className = 'ls-switch ' + (bot.status === 'running' ? 'on' : bot.status === 'error' || bot.status === 'not_configured' ? 'bad' : '');
    if (open) {
      gauge('g-ram', ramP, fmtB(s.mem.used) + ' / ' + fmtB(s.mem.total));
      gauge('g-cpu', cpuP, s.cores + ' core' + (s.cores === 1 ? '' : 's'));
      gauge('g-disk', s.disk.pct, fmtB(s.disk.used) + ' / ' + fmtB(s.disk.total));
      spark('sp-cpu', H.cpu); spark('sp-ram', H.ram);
      $('live-kv').innerHTML =
        kv('Uptime', A.fmtDur(s.uptime)) + kv('Load avg', s.load.join(' · ')) +
        kv('Processes / threads', s.procs + ' / ' + s.threads) + kv('App memory (RSS)', fmtB(s.proc_rss)) +
        kv('Open files', s.fds) + kv('Python', s.python) +
        kv('Requests / min', s.req.per_min) + kv('Total requests', s.req.total) +
        kv('Server errors (5xx)', s.req.errors, s.req.errors ? '#ef4444' : '') + kv('Avg response', s.req.avg_ms + ' ms') +
        kv('Firestore', s.firestore.ok === false ? '❌ error' : (s.firestore.ms == null ? '…' : s.firestore.ms + ' ms'), s.firestore.ok === false ? '#ef4444' : (s.firestore.ms > 800 ? '#f59e0b' : '#10b981')) +
        kv('Fampay bot', ({ running: '🟢 Running', starting: '🟡 Starting', stopped: '⚪ Stopped', error: '🔴 Error', not_configured: '🔴 Env missing' })[bot.status] || bot.status) +
        kv('Bot last poll', bot.last_run ? new Date(bot.last_run).toLocaleTimeString() : '—') + kv('Bot saved (this run)', bot.total_saved == null ? '—' : bot.total_saved) +
        kv('Keep-alive pings', s.keepalive.enabled ? (s.keepalive.pings + ' ok / ' + s.keepalive.fails + ' fail') : 'off');
      $('ls-upd').innerText = 'Updated ' + new Date(s.t).toLocaleTimeString();
    }
  } catch (e) {
    $('ls-dot').className = 'ls-dot bad'; $('ls-title').innerText = 'Server unreachable'; $('ls-sub').innerText = String(e.message || e).slice(0, 40);
  } finally { busy = false; }
}

Object.assign(window.A, {
  liveStart() { clearInterval(timer); tick(); timer = setInterval(tick, 10000); },
  liveStop() { clearInterval(timer); timer = null; },
  liveToggle() {
    open = !open; $('live-panel').classList.toggle('open', open); $('ls-chev').classList.toggle('up', open);
    if (open) { busy = false; tick(); }
  },
  async liveBotSwitch(el) {
    const want = el.checked;
    try { await A.api('/api/bot/' + (want ? 'start' : 'stop'), { body: {} }); A.toast('Fampay deposit ' + (want ? 'ON' : 'OFF')); }
    catch (e) { el.checked = !want; A.fail(e); }
    busy = false; setTimeout(tick, 600);
  }
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) { busy = false; tick(); } });
