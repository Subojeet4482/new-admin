// Data Center: 8 data types (download / upload / auto-delete / delete older) + Telegram backup account + month-end backup
const $ = (id) => document.getElementById(id);
const esc = A.esc;
const when = (ms) => ms ? new Date(ms).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' }) : '—';
let DC = null, upKey = null;

Object.assign(window.A, {
  async dcLoad() {
    try {
      DC = await A.api('/api/dc/types');
      A.dcPaintTg();
      $('dc-types').innerHTML = DC.types.map((t, i) => {
        const a = t.auto || {};
        return `<div class="card dc-card fade-up" style="animation-delay:${i * 50}ms" id="dc-${t.key}">
          <div class="dc-head"><div class="dc-ico" style="background:${t.color}"><i class="fa-solid ${t.icon}"></i></div>
            <div style="flex:1;min-width:0;"><b>${esc(t.label)}</b><small>${esc(t.desc)}</small></div>
            <div class="dc-count" id="dcn-${t.key}"><i class="fa-solid fa-spinner fa-spin"></i></div></div>
          <div class="dc-meta" id="dcm-${t.key}">&nbsp;</div>
          <div class="dc-btns">
            <button class="btn btn-outline" onclick="A.dcDownload('${t.key}')"><i class="fa-solid fa-download"></i> Download</button>
            <button class="btn btn-outline" onclick="A.dcUpload('${t.key}')"><i class="fa-solid fa-upload"></i> Upload</button>
            ${t.deletable ? `<button class="btn btn-outline" onclick="A.dcAutoToggle('${t.key}')"><i class="fa-solid fa-clock-rotate-left"></i> Auto delete ${a.enabled ? '<span class="tag green">ON</span>' : ''}</button>
            <button class="btn btn-danger" onclick="A.dcDelete('${t.key}')"><i class="fa-solid fa-trash"></i> Delete old</button>` : `<span class="muted" style="flex:1;align-self:center;font-size:.7rem;"><i class="fa-solid fa-lock"></i> Delete option not available</span>`}
          </div>
          ${t.deletable ? `<div class="dc-auto" id="dca-${t.key}">
            <label class="ls-switch2"><input type="checkbox" id="dca-on-${t.key}" ${a.enabled ? 'checked' : ''}><i></i><span>Auto delete</span></label>
            <div class="dc-auto-row"><span>Delete data older than</span><input class="inp" type="number" min="1" id="dca-days-${t.key}" value="${a.days || 30}" style="width:80px;margin:0;"><span>days, on</span><select class="inp" id="dca-day-${t.key}" style="width:112px;margin:0;background:#fff;"><option value="0" ${!a.day ? 'selected' : ''}>every day</option>${Array.from({ length: 28 }, (_, i) => `<option value="${i + 1}" ${a.day === i + 1 ? 'selected' : ''}>day ${i + 1}</option>`).join('')}</select><span>at</span><input class="inp" type="time" id="dca-time-${t.key}" value="${a.time || '03:00'}" style="width:110px;margin:0;"></div>
            <div class="muted">Pehle backup Telegram par upload hota hai, phir data database se delete hota hai. ${a.ranAt ? 'Last run: ' + new Date(a.ranAt).toLocaleString() + ' → ' + esc(String(a.result)) : ''}</div>
            <button class="btn btn-primary btn-full" onclick="A.dcAutoSave('${t.key}')"><i class="fa-solid fa-floppy-disk"></i> Save auto-delete</button></div>` : ''}
        </div>`;
      }).join('');
      DC.types.forEach(t => A.api('/api/dc/' + t.key + '/summary').then(s => {
        const n = $('dcn-' + t.key); if (!n) return; n.innerHTML = '<b>' + s.count + '</b><small>docs</small>';
        $('dcm-' + t.key).innerHTML = s.count ? `Oldest ${when(s.oldest)} · Newest ${when(s.newest)}` : 'Empty';
      }).catch(() => { const n = $('dcn-' + t.key); if (n) n.innerHTML = '<small>error</small>'; }));
      $('dc-files').innerHTML = DC.files.length ? DC.files.map(f => `<div class="row"><div><div class="row-t">${esc(f.name)}</div><div class="row-s">${A.fmtBytes(f.size)} · ${A.fmtAgo(f.mtime)}</div></div><div class="row-btns"><button class="btn btn-outline mini" onclick="location.href='/api/dc/file/${encodeURIComponent(f.name)}'"><i class="fa-solid fa-download"></i></button></div></div>`).join('') : '<span class="muted">No files yet.</span>';
    } catch (e) { A.fail(e); }
  },
  dcPaintTg() {
    const t = DC.telegram, m = DC.monthly || {};
    $('dc-tg').innerHTML = `<div class="tg-top"><div class="tg-logo"><i class="fa-brands fa-telegram"></i></div>
        <div style="flex:1;min-width:0;"><b>Telegram backup</b><small>${t.configured ? `Connected${t.account ? ' as ' + esc(t.account) : ''} · send to: ${esc(t.chat)}` : 'No account added — backups cannot be sent'}</small>${t.last_err ? `<small style="color:#ef4444;">Last error: ${esc(t.last_err)}</small>` : ''}</div>
        <span class="tg-dot ${t.configured ? 'on' : ''}"></span></div>
      <div class="dc-btns">
        <button class="btn btn-primary" onclick="A.tgOpen()"><i class="fa-solid fa-user-plus"></i> ${t.configured ? 'Change account' : 'Add account'}</button>
        ${t.configured ? `<button class="btn btn-outline" onclick="A.tgTest()"><i class="fa-solid fa-paper-plane"></i> Test</button><button class="btn btn-danger" onclick="A.tgRemove()"><i class="fa-solid fa-user-minus"></i></button>` : ''}
      </div>
      <div class="dc-month"><label class="ls-switch2"><input type="checkbox" id="dc-month-on" ${m.enabled !== false ? 'checked' : ''} onchange="A.dcMonthSave()"><i></i><span>Monthly backup — every data type (main + chat database) as its own JSON file</span></label></div>
      <div class="dc-month2"><span>Send on day</span>
        <select class="inp" id="dc-month-day" onchange="A.dcMonthSave()" style="width:120px;margin:0;background:#fff;"><option value="0" ${!m.day ? 'selected' : ''}>Last day</option>${Array.from({ length: 28 }, (_, i) => `<option value="${i + 1}" ${m.day === i + 1 ? 'selected' : ''}>${i + 1}${['st', 'nd', 'rd'][i] || 'th'}</option>`).join('')}</select>
        <span>at</span><input class="inp" type="time" id="dc-month-time" value="${m.time || '23:00'}" onchange="A.dcMonthSave()" style="width:112px;margin:0;">
        <button class="btn btn-outline" onclick="A.dcMonthRun()"><i class="fa-solid fa-play"></i> Run now</button></div>
      <div class="muted" style="margin-top:6px;">Both databases are backed up on the same day, so all files reach Telegram together (days 29–31 use the month's last day).</div>
      ${m.ranAt ? `<div class="muted">Last month-end run: ${new Date(m.ranAt).toLocaleString()} → ${esc(String(m.result || ''))}</div>` : ''}`;
  },
  async tgOpen() {
    $('m-tg').classList.add('show');
    try { const t = await A.api('/api/tg'); $('tg-id').value = t.api_id || ''; $('tg-chat').value = t.chat || 'me'; $('tg-hash').value = ''; $('tg-sess').value = '';
      $('tg-hash').placeholder = t.api_hash ? 'saved (' + t.api_hash + ') — leave empty to keep' : '32 character hash (my.telegram.org)';
      $('tg-sess').placeholder = t.session ? 'saved (' + t.session + ') — leave empty to keep' : '1BVts…  (StringSession)'; } catch (e) {}
  },
  async tgSave() {
    const v = await A.askDanger({ title: 'Save Telegram account', text: 'Enter your PIN to save these credentials.', backup: null, okLabel: 'Save' }); if (!v) return;
    try { await A.api('/api/tg', { body: { api_id: $('tg-id').value, api_hash: $('tg-hash').value, session: $('tg-sess').value, chat: $('tg-chat').value, pin: v.pin } });
      A.toast('Telegram account saved'); A.closeModal('m-tg'); if (DC) A.dcLoad(); } catch (e) { A.fail(e); }
  },
  async tgTest() { try { const r = await A.api('/api/tg/test', { body: {} }); A.toast('✅ Message sent as ' + r.account); A.dcLoad(); } catch (e) { A.fail(e); } },
  async tgRemove() {
    const v = await A.askDanger({ title: 'Remove Telegram account?', text: 'Auto-delete will stop working until you add an account again.', backup: null, okLabel: 'Remove' }); if (!v) return;
    try { await A.api('/api/tg/remove', { body: { pin: v.pin } }); A.toast('Removed'); A.dcLoad(); } catch (e) { A.fail(e); }
  },
  async dcMonthSave() { try { await A.api('/api/dc/monthly', { body: { enabled: $('dc-month-on').checked, day: parseInt($('dc-month-day').value, 10) || 0, time: $('dc-month-time').value || '23:00' } }); A.toast('Monthly backup schedule saved ✓'); } catch (e) { A.fail(e); } },
  async dcMonthRun() {
    if (!confirm('Send every data type as a separate JSON file to Telegram now?')) return;
    try { const r = await A.api('/api/dc/monthly', { body: { run: true } }); const j = await A.trackJob(r.job, 'Month-end backup'); if (j.status === 'done') A.toast('Sent to Telegram ✓'); A.dcLoad(); } catch (e) { A.fail(e); }
  },
  async dcDownload(key) {
    try { const r = await A.api('/api/dc/' + key + '/export', { body: {} }); const j = await A.trackJob(r.job, 'Export');
      if (j.status === 'done') { A.toast(j.result.docs + ' docs exported'); location.href = '/api/dc/file/' + encodeURIComponent(j.result.name); setTimeout(A.dcLoad, 800); } } catch (e) { A.fail(e); }
  },
  dcUpload(key) {
    upKey = key; const f = $('dc-file'); f.value = '';
    f.onchange = async () => {
      const file = f.files[0]; if (!file) return;
      const v = await A.askDanger({ title: 'Upload & restore?', text: `${file.name} → docs will be written back into "${key}" (existing docs overwritten).`, typed: 'RESTORE', backup: null, okLabel: 'Restore' }); if (!v) return;
      try { const fd = new FormData(); fd.append('file', file); fd.append('pin', v.pin);
        const r = await A.api('/api/dc/' + upKey + '/upload', { form: fd }); const j = await A.trackJob(r.job, 'Restore');
        if (j.status === 'done') { A.toast(j.result.restored + ' docs restored'); A.dcLoad(); } else if (j.error) A.toast(j.error, 'err'); } catch (e) { A.fail(e); }
    };
    f.click();
  },
  dcAutoToggle(key) { const p = $('dca-' + key); p.classList.toggle('open'); },
  async dcAutoSave(key) {
    try { await A.api('/api/dc/' + key + '/auto', { body: { enabled: $('dca-on-' + key).checked, days: parseInt($('dca-days-' + key).value, 10) || 30, time: $('dca-time-' + key).value || '03:00', day: parseInt($('dca-day-' + key).value, 10) || 0 } });
      A.toast('Auto-delete saved'); A.dcLoad(); } catch (e) { A.fail(e); }
  },
  async dcDelete(key) {
    const t = DC.types.find(x => x.key === key);
    const days = prompt(`Delete "${t.label}" older than how many days?`, '30'); if (days === null) return;
    const n = parseInt(days, 10); if (isNaN(n) || n < 0) return A.toast('Enter a valid number of days', 'err');
    const v = await A.askDanger({ title: `Delete old ${t.label}?`, text: `Everything older than ${n} days is uploaded to Telegram first, then DELETED from the database.`, typed: 'DELETE', backup: null, okLabel: 'Delete' }); if (!v) return;
    try { const r = await A.api('/api/dc/' + key + '/delete', { body: { days: n, pin: v.pin } }); const j = await A.trackJob(r.job, 'Delete');
      if (j.status === 'done') A.toast(`${j.result.deleted || 0} docs deleted (backup sent to Telegram)`); else if (j.error) A.toast(j.error, 'err'); A.dcLoad(); } catch (e) { A.fail(e); }
  }
});
