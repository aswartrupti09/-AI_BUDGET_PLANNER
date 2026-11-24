(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inr = n => (n < 0 ? '-₹' : '₹') + Math.abs(Math.round(n || 0)).toLocaleString('en-IN');
const pad = n => String(n).padStart(2, '0');
const now = new Date(), curYm = `${now.getFullYear()}-${pad(now.getMonth() + 1)}`, todayIso = `${curYm}-${pad(now.getDate())}`;
const ymAdd = (ym, k) => { const [y, m] = ym.split('-').map(Number), t = y * 12 + m - 1 + k; return `${Math.floor(t / 12)}-${pad(t % 12 + 1)}`; };
const label = ym => { const [y, m] = ym.split('-').map(Number); return new Date(y, m - 1, 1).toLocaleString('en', {month: 'long', year: 'numeric'}); };
const ICON = {Food: '🍽️', Transport: '🚌', Rent: '🏠', Bills: '💡', Shopping: '🛍️', Entertainment: '🎬', Health: '💊', Education: '📚', Other: '📦'};
const S = {token: localStorage.getItem('token'), cats: [], type: {}, ym: curYm, summary: [], month: null, shot: null};


const API_BASE_URL = "https://ai-budget-planner-niai.onrender.com";

// ---------- helpers ----------
async function api(path, {method = 'GET', body, form} = {}) {
  const h = {};
  if (S.token) h.Authorization = 'Bearer ' + S.token;

  let b;
  if (form) b = form;
  else if (body !== undefined) {
    h['Content-Type'] = 'application/json';
    b = JSON.stringify(body);
  }

  const r = await fetch(API_BASE_URL + '/api' + path, {
    method,
    headers: h,
    body: b
  });

  if (r.status === 401 && S.token) {
    signOut();
    throw new Error('Please log in again.');
  }

  const j = await r.json().catch(() => ({}));

  if (!r.ok) {
    throw new Error(typeof j.detail === 'string'
      ? j.detail
      : 'Something went wrong. Check the details you entered.');
  }

  return j;
}
function toast(msg, bad) { const t = $('#toast'); t.textContent = msg; t.hidden = false; t.style.background = bad ? 'var(--bad)' : ''; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 3500); }
const catOptions = sel => S.cats.map(c => `<option ${c === sel ? 'selected' : ''}>${esc(c)}</option>`).join('');
const sum = o => Object.values(o).reduce((a, b) => a + b, 0);
function defaultDate() { if (S.ym === curYm) return todayIso; const [y, m] = S.ym.split('-').map(Number); return S.ym < curYm ? `${S.ym}-${pad(new Date(y, m, 0).getDate())}` : `${S.ym}-01`; }
const root = document.documentElement; root.dataset.theme = localStorage.getItem('theme') || (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
$('#theme').onclick = () => { root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark'; localStorage.setItem('theme', root.dataset.theme); };

// ---------- login / register ----------
const authMsg = m => { const e = $('#authMsg'); e.textContent = m || ''; e.hidden = !m; };
const authTab = reg => { $('#tabLogin').className = reg ? '' : 'on'; $('#tabRegister').className = reg ? 'on' : ''; $('#loginForm').hidden = reg; $('#regForm').hidden = !reg; authMsg(); };
$('#tabLogin').onclick = () => authTab(false); $('#tabRegister').onclick = () => authTab(true);
$('#lGo').onclick = async () => { try { begin(await api('/login', {method: 'POST', body: {username: $('#lUser').value, password: $('#lPass').value}})); } catch (e) { authMsg(e.message); } };
$('#rGo').onclick = async () => {
  if ($('#rPass').value !== $('#rPass2').value) return authMsg("The two passwords don't match.");
  try { begin(await api('/register', {method: 'POST', body: {username: $('#rUser').value, password: $('#rPass').value}})); } catch (e) { authMsg(e.message); }
};
['lPass', 'rPass2'].forEach(id => $('#' + id).addEventListener('keydown', e => { if (e.key === 'Enter') $(id === 'lPass' ? '#lGo' : '#rGo').click(); }));
function begin(r) {
  S.token = r.token; localStorage.setItem('token', r.token); S.cats = r.cats;
  S.type = Object.fromEntries(Object.entries(r.groups).flatMap(([g, cs]) => cs.map(c => [c, g.split(' ')[0]])));
  $('#who').textContent = r.user; $('#auth').hidden = true; $('#app').hidden = false; init();
}
function signOut() { S.token = null; localStorage.removeItem('token'); $('#app').hidden = true; $('#auth').hidden = false; }
$('#logout').onclick = async () => { try { await api('/logout', {method: 'POST'}); } catch (e) {} signOut(); };

// ---------- startup, months, sidebar plan ----------
async function init() { S.ym = curYm; await loadSummary(); buildMonths(); $('#txtDate').value = defaultDate(); await refresh(); }
async function loadSummary() { S.summary = await api('/summary'); }
function buildMonths() {
  const ms = [...new Set([...Array.from({length: 13}, (_, i) => ymAdd(curYm, i - 11)), ...S.summary.map(x => x.Month)])].sort();
  $('#monthSel').innerHTML = ms.map(m => `<option value="${m}" ${m === S.ym ? 'selected' : ''}>${label(m)}</option>`).join('');
}
$('#monthSel').onchange = () => { S.ym = $('#monthSel').value; $('#txtDate').value = defaultDate(); $('#result').innerHTML = ''; refresh(); };
async function refresh() { $('#title').textContent = label(S.ym); await Promise.all([loadPlanForm(), loadMonth()]); if (!$('#tab-hist').hidden) loadHist(); }
async function loadPlanForm() {
  const p = await api('/plan/' + S.ym);
  $('#planTitle').textContent = 'Budget for ' + label(S.ym); $('#savePlan').textContent = 'Save plan for ' + label(S.ym);
  $('#planNote').textContent = p.prefilled_from ? `Pre-filled from ${label(p.prefilled_from)}. Change anything, then save.` : p.exists ? '' : 'No plan for this month yet. Fill it in and save.';
  $('#inc').value = p.income; $('#goal').value = p.goal;
  $('#budgets').innerHTML = S.cats.map(c => `<label>${ICON[c]} ${esc(c)} <span class="tag ${S.type[c]}">${S.type[c]}</span><input type="number" min="0" step="100" data-cat="${esc(c)}" value="${p.budgets[c] ?? 0}"></label>`).join('');
  calcPlan();
}
const planValues = () => { const b = {}; $$('#budgets input').forEach(i => b[i.dataset.cat] = +i.value || 0); return {income: +$('#inc').value || 0, goal: +$('#goal').value || 0, budgets: b}; };
function calcPlan() {
  const p = planValues(), total = sum(p.budgets) + p.goal, m = $('#planMsg');
  $('#planTotal').textContent = `Budgets + savings: ${inr(total)} / Income: ${inr(p.income)}`; m.hidden = false;
  if (total > p.income) { m.className = 'msg bad'; m.textContent = `Over income by ${inr(total - p.income)}. Reduce budgets or raise income.`; }
  else if (total < p.income) { m.className = 'msg info'; m.textContent = `${inr(p.income - total)} is unallocated and will count as extra savings.`; }
  else m.hidden = true;
  $('#savePlan').disabled = total > p.income;
}
$('.side').addEventListener('input', e => { if (e.target.closest('#budgets') || e.target.id === 'inc' || e.target.id === 'goal') calcPlan(); });
$('#savePlan').onclick = async () => { try { await api('/plan/' + S.ym, {method: 'PUT', body: planValues()}); toast('Plan saved.'); await loadSummary(); buildMonths(); refresh(); } catch (e) { toast(e.message, true); } };

// ---------- tabs ----------
$$('.tabs button').forEach(b => b.onclick = () => {
  $$('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  ['add', 'month', 'hist'].forEach(t => $('#tab-' + t).hidden = t !== b.dataset.tab);
  if (b.dataset.tab === 'hist') loadHist(); if (b.dataset.tab === 'month') loadMonth();
});
$$('input[name=mode]').forEach(r => r.onchange = () => { const shot = $('input[name=mode]:checked').value === 'shot'; $('#textMode').hidden = shot; $('#shotMode').hidden = !shot; });
$$('.chip').forEach(c => c.onclick = () => { $('#txt').value = c.dataset.ex; $('#txt').focus(); });
$('#shotFile').onchange = () => $('#fname').textContent = $('#shotFile').files[0]?.name || 'PNG or JPG';

// ---------- budget status, plan change, reasoning ----------
function reasoningHTML(r) {
  const icon = {ok: '✅', warn: '⚠️', fail: '❌'};
  return `<details class="why"><summary>Step-by-step reasoning</summary>`
    + r.steps.map(([t, pts]) => `<h4>${esc(t)}</h4><ul>${pts.map(p => `<li>${esc(p)}</li>`).join('')}</ul>`).join('')
    + (r.checks.length ? `<h4>Double-check</h4><ul style="list-style:none;margin-left:0">${r.checks.map(([t, s]) => `<li>${icon[s]} ${esc(t)}</li>`).join('')}</ul>` : '')
    + `<div class="msg info">${esc(r.recommendation)}</div></details>`;
}
function catGrid(m) {
  return '<div class="cats">' + [...m.rows].sort((a, b) => S.cats.indexOf(a.Category) - S.cats.indexOf(b.Category)).map(r => {
    const c = r.Category, b = r['Current budget'], s = r['Spent so far'], pct = b ? s / b * 100 : s ? 101 : 0, cls = s > b ? 'over' : pct >= 80 ? 'warn' : '';
    const st = s > b ? ['Over budget', 'bad'] : pct >= 80 ? ['Almost used', 'up'] : ['On track', ''];
    return `<div class="cat"><div class="cathead"><span class="ico">${ICON[c]}</span><div><b>${esc(c)}</b><br><span class="tag ${S.type[c]}">${S.type[c]}</span></div><span class="badge ${st[1]}">${st[0]}</span></div>`
      + `<div class="bar"><i class="${cls}" style="width:${Math.min(pct, 100)}%"></i></div>`
      + `<div class="catfoot"><span>${inr(s)} of ${inr(b)}</span><span class="${s > b ? 'over' : ''}">${s > b ? inr(s - b) + ' over' : inr(b - s) + ' left'}</span></div>`
      + (Math.round(r['Starting budget']) !== Math.round(b) ? `<small>Started the month at ${inr(r['Starting budget'])}</small>` : '') + `</div>`;
  }).join('') + '</div>';
}
function changeHTML(ch) {   // an applied plan change: before -> after, savings, AI explanation, step-by-step reasons, undo
  const rs = ch.reasoning;
  const rows = S.cats.filter(c => c in ch.before).map(c => { const d = ch.after[c] - ch.before[c];
    return `<tr><td>${ICON[c]} ${esc(c)} <span class="tag ${S.type[c]}">${S.type[c]}</span></td><td>${inr(ch.before[c])}</td><td class="arrow">→</td><td><b>${inr(ch.after[c])}</b></td>`
      + `<td>${d ? `<span class="chipx ${d > 0 ? 'up' : 'down'}">${d > 0 ? '+' : ''}${inr(d)}</span>` : '<span class="note">unchanged</span>'}</td></tr>`; }).join('');
  return `<div class="change"><div class="chhead"><span class="ico">🔄</span><div><b>Your plan was changed automatically</b><small>${esc(ch.ts)} · because of ${esc(ch.trigger)}</small></div></div>`
    + `<div class="tablewrap"><table><tr><th>Category</th><th>Before</th><th></th><th>New budget</th><th>Change</th></tr>${rows}</table></div>`
    + `<p>Savings after the change: <b>${inr(rs.savings)}</b> ${rs.shortfall ? `<span class="badge bad">${inr(rs.shortfall)} short of your ${inr(rs.goal)} goal</span>` : '<span class="badge">goal protected ✅</span>'}</p>`
    + `<div class="ai" data-ym="${ch.ym}"><b>🤖 Why the budget changed</b><div class="aitext wait">Writing the explanation…</div><p class="note small aisrc"></p></div>`
    + reasoningHTML(rs) + `<button data-undo="${ch.ym}">↩ Undo this change</button></div>`;
}
const bullets = t => '<ul>' + t.split('\n').filter(l => l.trim()).map(l => `<li>${esc(l.replace(/^[-•*]\s*/, ''))}</li>`).join('') + '</ul>';
async function explainAll() {   // AI explanation of the latest change: Claude if a key is set, else a built-in summary
  for (const ym of new Set($$('.ai:not([data-done])').map(e => e.dataset.ym))) {
    let r = {text: '', source: ''}; try { r = await api(`/plan/${ym}/explain`); } catch (e) {}
    $$(`.ai[data-ym="${ym}"]`).forEach(el => {
      el.dataset.done = 1; el.hidden = !r.text; $('.aitext', el).classList.remove('wait'); $('.aitext', el).innerHTML = bullets(r.text);
      $('.aisrc', el).textContent = r.source === 'Claude' ? 'Written by Claude from the computed numbers (checked: no invented figures).' : 'Built-in explanation. Set ANTHROPIC_API_KEY to get an AI-written one.';
    });
  }
}
document.addEventListener('click', async e => {
  const a = e.target.closest('[data-replan]'), u = e.target.closest('[data-undo]'), d = e.target.closest('[data-del]');
  try {
    if (a) { toast(await api(`/plan/${a.dataset.replan}/replan`, {method: 'POST'}) ? 'Plan changed.' : 'Nothing needed to change.'); $('#result').innerHTML = ''; await loadMonth(); }
    if (u) { await api(`/plan/${u.dataset.undo}/undo`, {method: 'POST'}); toast('Change undone.'); $('#result').innerHTML = ''; await loadMonth(); }
    if (d && confirm('Delete this entry?')) { await api('/txn/' + d.dataset.del, {method: 'DELETE'}); await loadSummary(); await loadMonth(); }
  } catch (err) { toast(err.message, true); }
});

// ---------- this month ----------
async function loadMonth() {
  const m = S.month = await api('/month/' + S.ym), has = m.has_plan;
  $('#noPlan').hidden = has; $('#monthBody').hidden = !has;
  if (!has) $('#noPlan').innerHTML = `<div style="font-size:2rem">🗓️</div><p>No budget for <b>${label(S.ym)}</b> yet.<br>Fill in the budget panel and click <b>Save plan</b>.</p>`;
  if (has) {
    const spent = sum(m.spent), left = sum(m.budgets) - spent;
    $('#cards').innerHTML = [['💸', 'Spent', spent, 'i-rose'], ['🧾', 'Budget left', left, 'i-ind'], ['🏦', 'Saved so far', m.income - spent, 'i-grn'], ['🎯', 'Savings goal', m.goal, 'i-amb']]
      .map(([i, t, v, c]) => `<div class="metric"><span class="ico ${c}">${i}</span><div><span class="l">${t}</span><b>${inr(v)}</b></div></div>`).join('');
    $('#incNote').textContent = m.received > 0 ? `Income ${inr(m.income)} = planned ${inr(m.base_income)} + received ${inr(m.received)}.` : '';
    $('#planBox').innerHTML = (m.needs_replan ? `<div class="msg warn">Some budgets no longer fit what you spent. <button class="primary" data-replan="${m.ym}">Re-plan now</button></div>` : '')
      + `<h3>Budget status</h3>` + catGrid(m) + (m.change ? `<h3>Latest change to your plan</h3>` + changeHTML(m.change) : '');
    explainAll();
  }
  const tx = m.transactions, ex = tx.filter(t => (t.kind || 'expense') === 'expense'), rc = tx.filter(t => t.kind === 'income');
  const tbl = (rows, cat) => rows.length ? `<table><tr><th>Date</th><th class="l">Description</th><th>Amount</th>${cat ? '<th class="l">Category</th>' : ''}<th></th></tr>`
    + rows.map(t => `<tr><td>${esc(t.date)}</td><td class="l">${esc(t.description)}${t.unusual ? ' <span class="badge bad" title="Much larger than your usual amount for this category. It is left out of the planner\'s estimates.">Unusual</span>' : ''}</td><td>${inr(t.amount)}</td>${cat ? `<td class="l">${ICON[t.category] || ''} ${esc(t.category)}</td>` : ''}<td><button class="link" data-del="${t.id}">Delete</button></td></tr>`).join('') + '</table>'
    : '<p class="note" style="padding:1em">Nothing yet.</p>';
  $('#exTable').innerHTML = tbl(ex, true); $('#rcBox').innerHTML = rc.length ? `<h3>Money received this month</h3><div class="card tablewrap">${tbl(rc, false)}</div>` : '';
}

// ---------- add: typed ----------
$('#addBtn').onclick = async () => {
  const text = $('#txt').value.trim(); if (!text) return;
  try { const r = await api('/expense/text', {method: 'POST', body: {text, date: $('#txtDate').value || null}}); $('#txt').value = ''; await showResult(r); } catch (e) { toast(e.message, true); }
};
$('#txt').addEventListener('keydown', e => { if (e.key === 'Enter') $('#addBtn').click(); });
async function showResult(r) {
  const box = $('#result');
  if (r.error) { box.innerHTML = `<div class="msg bad">${esc(r.error)}</div>`; return; }
  const m = r.ym ? await api('/month/' + r.ym) : null; let h = '';
  const log = `<details class="card pad"><summary>Technical details: how the agents handled this</summary><ul>${(r.log || []).map(l => `<li>${esc(l)}</li>`).join('')}</ul></details>`;
  if (r.income_added) {
    h = `<div class="msg ok">Added ${inr(r.txn.amount)} received (${esc(r.txn.description)}) to your income for ${label(r.ym)}.</div>`
      + (m && m.has_plan ? `<div class="msg info">Income for ${label(r.ym)}: ${inr(m.base_income)} planned + ${inr(m.received)} received = ${inr(m.income)}.</div>`
        : `<div class="msg info">There's no budget for ${label(r.ym)} yet. The money is saved and will count once you set a plan for that month.</div>`);
  } else if (r.need_confirm) {
    h = `<div class="msg warn">I'm not sure about “${esc(r.txn.description)}” (${inr(r.txn.amount)}). Which category is it?</div>`
      + `<div class="row"><select id="confCat" style="width:auto">${catOptions(r.txn.category)}</select><button id="confBtn" class="primary">Confirm and save</button></div>`;
  } else {
    h = `<div class="msg ok">Saved ${inr(r.txn.amount)} under <b>${ICON[r.txn.category] || ''} ${esc(r.txn.category)}</b> on ${esc(r.txn.date)}.</div>`;
    if (r.no_plan) h += `<div class="msg info">There's no budget for ${label(r.ym)} yet. Choose that month in the budget panel and save a plan to track it.</div>`;
    r.alerts.forEach(a => h += `<div class="msg warn">${esc(a)}</div>`);
    if (r.change) h += changeHTML(r.change);
  }
  box.innerHTML = h + log; explainAll();
  if (r.need_confirm) $('#confBtn').onclick = async () => {
    try { await showResult(await api('/expense/confirm', {method: 'POST', body: {amount: r.txn.amount, description: r.txn.description, date: r.txn.date, kind: 'expense', category: $('#confCat').value, source: r.txn.source || 'cash'}})); } catch (e) { toast(e.message, true); }
  };
  await loadSummary(); buildMonths(); if (r.ym === S.ym) await loadMonth();
}

// ---------- add: screenshot ----------
$('#readShot').onclick = async () => {
  const f = $('#shotFile').files[0]; if (!f) return toast('Choose a screenshot first.', true);
  const fd = new FormData(); fd.append('file', f); $('#readShot').disabled = true; $('#result').innerHTML = '';
  try { S.shot = await api('/expense/screenshot', {method: 'POST', form: fd}); renderReview(); } catch (e) { toast(e.message, true); } finally { $('#readShot').disabled = false; }
};
function renderReview() {
  const r = S.shot, opts = [r.amount || 0].concat(r.alt_amount ? [r.alt_amount] : []), income = r.kind === 'income';
  $('#review').innerHTML = `<div class="msg info">Check what was read, fix anything wrong, then save.</div>`
    + (r.error ? `<div class="msg bad">${esc(r.error)}</div>` : '')
    + (r.auto_fixed ? `<div class="msg ok">The ₹ sign was misread and has been corrected to ${inr(r.amount)}.</div>` : '')
    + `<div class="seg left"><label><input type="radio" name="kind" value="expense" ${income ? '' : 'checked'}> Money I paid</label><label><input type="radio" name="kind" value="income" ${income ? 'checked' : ''}> Money I received</label></div>`
    + (opts.length > 1 ? `<div class="msg warn">Read ${inr(opts[0])}, but the ₹ sign may have been read as a digit. Which is right?</div><div class="seg left">${opts.map((v, i) => `<label><input type="radio" name="amtpick" value="${v}" ${i ? '' : 'checked'}> ${inr(v)}</label>`).join('')}</div>` : '')
    + `<div class="grid3"><label>Amount (₹)<input id="rvAmt" type="number" min="0" step="10" value="${opts[0]}"></label><label>Merchant / description<input id="rvDesc" value="${esc(r.description)}"></label><label>Date<input id="rvDate" type="date" value="${esc(r.date)}"></label></div>`
    + `<div id="rvCatWrap" ${income ? 'hidden' : ''}><p class="note">Suggested: ${esc(r.suggested || '')} (${Math.round((r.confidence || 0) * 100)}%)${r.suggest_source === 'feedback' ? ' - remembered from your earlier choice' : ''}</p><label>Category<select id="rvCat">${catOptions(r.suggested)}</select></label></div>`
    + `<p id="rvIncNote" class="note" ${income ? '' : 'hidden'}>This will be added to your income for that month.</p><button id="rvSave" class="primary">Save</button>`
    + `<details style="margin-top:1em"><summary>Technical details (screenshot reading)</summary><p class="note">${esc(r.glyph_info || 'not run')}</p><pre class="raw">${esc(r.raw_text || '(nothing read)')}</pre></details>`;
  $$('input[name=kind]').forEach(k => k.onchange = () => { const inc = $('input[name=kind]:checked').value === 'income'; $('#rvCatWrap').hidden = inc; $('#rvIncNote').hidden = !inc; });
  $$('input[name=amtpick]').forEach(k => k.onchange = () => $('#rvAmt').value = k.value);
  $('#rvSave').onclick = async () => {
    const amount = +$('#rvAmt').value; if (!(amount > 0)) return toast('Enter an amount.', true);
    const pick = +($('input[name=amtpick]:checked')?.value ?? opts[0]), kind = $('input[name=kind]:checked').value;
    const body = {amount, description: $('#rvDesc').value || 'payment', date: $('#rvDate').value, kind, category: kind === 'expense' ? $('#rvCat').value : null, source: 'screenshot',
                  learn_lead: opts.length > 1 ? (pick !== opts[0] ? String(Math.trunc(opts[0]))[0] : false) : null};
    try { const res = await api('/expense/confirm', {method: 'POST', body}); S.shot = null; $('#review').innerHTML = ''; await showResult(res); } catch (e) { toast(e.message, true); }
  };
}

// ---------- months & import ----------
async function loadHist() {
  await loadSummary(); const s = S.summary, has = s.filter(x => x.Saved !== null), top = Math.max(1, ...has.map(x => Math.max(x.Saved, x.Goal || 0)));
  $('#sumTable').innerHTML = s.length ? `<table><tr><th>Month</th><th>Income</th><th>Received</th><th>Spent</th><th>Saved</th><th>Goal</th><th>Goal met</th></tr>`
    + s.map(x => `<tr><td>${label(x.Month)}${x.Month === curYm ? ' (so far)' : ''}</td><td>${x.Income === null ? '-' : inr(x.Income)}</td><td>${inr(x.Received)}</td><td>${inr(x.Spent)}</td><td>${x.Saved === null ? '-' : inr(x.Saved)}</td><td>${x.Goal === null ? '-' : inr(x.Goal)}</td><td>${x['Goal met'] === null ? '-' : x['Goal met'] ? '✅' : '❌'}</td></tr>`).join('') + '</table>'
    : '<p class="note" style="padding:1em">Add a monthly plan and some expenses to see your savings history here.</p>';
  $('#saveChart').innerHTML = has.length ? has.map(x => `<div class="brow"><span>${label(x.Month)}${x.Month === curYm ? ' (so far)' : ''}</span><div class="bar"><i class="${x.Saved < 0 ? 'neg' : x.Saved >= x.Goal ? 'ok' : 'warn'}" style="width:${Math.max(0, x.Saved) / top * 100}%"></i><b class="mark" title="Goal ${inr(x.Goal)}" style="left:${(x.Goal || 0) / top * 100}%"></b></div><b>${inr(x.Saved)}</b></div>`).join('')
    + '<p class="note small">The dark marker on each bar shows your savings goal for that month.</p>' : '<p class="note">No saved amounts yet.</p>';
  const done = has.filter(x => x.Month < curYm); $('#histTotal').innerHTML = has.length ? `Total saved in finished months: <b>${done.length ? inr(sum(done.map(x => x.Saved))) : 'No finished month yet'}</b>` : '';
}
$('#impFile').onchange = async () => {
  const f = $('#impFile').files[0], box = $('#impPreview'); if (!f) return; const fd = new FormData(); fd.append('file', f);
  try {
    const r = await api('/import/preview', {method: 'POST', form: fd});
    if (!r.rows.length) { box.innerHTML = `<div class="msg warn">I couldn't find any expenses in this file.</div>` + (r.no_text ? `<div class="msg info">This PDF has no readable text. It may be a scanned image or password-protected. Try a CSV or a text-based PDF instead.</div>` : r.text ? `<details open><summary>Text I could read from this PDF (send this if you need help)</summary><pre class="raw">${esc(r.text)}</pre></details>` : ''); return; }
    box.innerHTML = `<p class="note">Check the rows, untick anything that isn't right, fix categories, then import.</p><div class="tablewrap"><table><tr><th></th><th>Date</th><th class="l">Description</th><th>Amount</th><th class="l">Category</th><th class="l">Kind</th></tr>`
      + r.rows.map(x => `<tr class="improw"><td><input type="checkbox" checked></td><td><input type="date" value="${esc(x.date)}"></td><td><input value="${esc(x.description)}"></td><td><input type="number" step="0.01" value="${x.amount}"></td><td><select>${catOptions(x.category)}</select></td><td><select><option ${x.kind === 'expense' ? 'selected' : ''}>expense</option><option ${x.kind === 'income' ? 'selected' : ''}>income</option></select></td></tr>`).join('')
      + `</table></div><button id="impGo" class="primary">Import selected</button>`;
    $('#impGo').onclick = async () => {
      const rows = $$('.improw').filter(tr => $('input[type=checkbox]', tr).checked).map(tr => { const i = $$('input:not([type=checkbox])', tr), s = $$('select', tr); return {date: i[0].value, description: i[1].value, amount: +i[2].value, category: s[0].value, kind: s[1].value}; });
      try { const c = await api('/import/commit', {method: 'POST', body: {rows}}); box.innerHTML = ''; $('#impFile').value = ''; toast(`Imported ${c.count} rows.`); await loadSummary(); buildMonths(); loadHist(); loadMonth(); } catch (e) { toast(e.message, true); }
    };
  } catch (e) { box.innerHTML = `<div class="msg bad">${esc(e.message)}</div>`; }
};

// ---------- boot ----------
if (S.token) api('/me').then(r => begin({token: S.token, cats: r.cats, groups: r.groups, user: r.user})).catch(() => signOut());
})();