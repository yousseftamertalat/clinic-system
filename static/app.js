'use strict';

/* ============================================================ helpers */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const app = $('#app');
const dlg = $('#dlg');
let me = null;
let meta = { service_rates: {}, statuses: [], days: [] };

const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const fmtDate = iso => {
  const d = new Date(iso + 'T00:00:00');
  return isNaN(d) ? esc(iso) : d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
};
const money = n => Number(n).toLocaleString(undefined, { maximumFractionDigits: 3 });
const label = s => esc(String(s ?? '').replace(/_/g, ' '));
const badge = s => s ? `<span class="badge ${esc(s)}">${label(s)}</span>` : '<span class="sub">Not set</span>';
const PRI = { 1: 'EMERGENCY', 2: 'URGENT', 3: 'REGULAR' };

function toast(msg, type = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), type === 'error' ? 6000 : 3500);
}

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch('/api' + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && me) {
    me = null;
    renderAuth('Your session ended. Please log in again.');
    throw new Error('Your session ended. Please log in again.');
  }
  if (!res.ok) throw new Error((data && data.error) || 'Request failed.');
  return data;
}

function head(title, sub = '', actions = '') {
  return `<div class="page-head"><div><h1>${esc(title)}</h1>${sub ? `<p>${esc(sub)}</p>` : ''}</div>${actions}</div>`;
}

/* rows: array of arrays, or {cls, cells:[...]} ; header: string or {t, cls} */
function table(headers, rows, empty = 'Nothing to show yet.') {
  if (!rows.length) return `<div class="panel panel-flush"><p class="empty">${esc(empty)}</p></div>`;
  const th = headers.map(h => typeof h === 'string' ? `<th>${esc(h)}</th>` : `<th class="${h.cls || ''}">${esc(h.t)}</th>`).join('');
  const tr = rows.map(r => {
    const cells = Array.isArray(r) ? r : r.cells;
    const cls = Array.isArray(r) ? '' : (r.cls || '');
    const tds = cells.map((c, i) => {
      const h = headers[i];
      const hc = typeof h === 'string' ? '' : (h.cls || '');
      return `<td class="${hc}">${c}</td>`;
    }).join('');
    return `<tr class="${cls}">${tds}</tr>`;
  }).join('');
  return `<div class="panel panel-flush"><div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div></div>`;
}

function openDialog({ title, body, submit = 'Save', cancel = 'Cancel', danger = false, onSubmit }) {
  return new Promise(resolve => {
    dlg.innerHTML = `<form><h2 id="dlg-title">${esc(title)}</h2><div class="stack">${body}</div>
      <div class="form-error" role="alert"></div>
      <div class="actions"><button type="button" class="btn secondary" data-cancel>${esc(cancel)}</button>
      <button class="btn ${danger ? 'solid-danger' : ''}">${esc(submit)}</button></div></form>`;
    const form = $('form', dlg);
    let settled = false;
    const finish = v => { if (!settled) { settled = true; resolve(v); } };
    dlg.onclose = () => finish(null);
    $('[data-cancel]', dlg).onclick = () => dlg.close();
    form.onsubmit = async e => {
      e.preventDefault();
      const btn = $('button:not([data-cancel])', form);
      btn.disabled = true;
      try {
        const result = await onSubmit(form);
        finish(result ?? true);
        dlg.close();
      } catch (err) {
        $('.form-error', dlg).textContent = err.message;
        btn.disabled = false;
      }
    };
    dlg.showModal();
    const first = $('input, select, textarea', form);
    if (first) first.focus(); else $('button:not([data-cancel])', form).focus();
  });
}
const confirmDialog = (title, text, submit = 'Confirm', danger = true) =>
  openDialog({ title, body: `<p>${esc(text)}</p>`, submit, danger, onSubmit: async () => true }).then(Boolean);

const reload = () => route();
const act = async (fn, okMsg) => {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, 'error'); return null; }
};

function statusSelect(a) {
  const opts = meta.statuses.map(s => `<option ${s === a.status ? 'selected' : ''} value="${s}">${label(s)}</option>`).join('');
  return `<select class="inline" data-status="${esc(a.appointment_id)}" aria-label="Status for ${esc(a.appointment_id)}">${opts}</select>`;
}
function wireStatusSelects(el) {
  $$('select[data-status]', el).forEach(sel => {
    sel.onchange = async () => {
      const ok = await act(() => api(`/appointments/${sel.dataset.status}/status`, { method: 'POST', body: { status: sel.value } }), 'Status updated.');
      if (!ok) reload();
    };
  });
}

/* ================================================================ auth */
function renderAuth(message = '', tab = 'login', prefillId = '') {
  app.innerHTML = `
  <div class="auth">
    <section class="auth-art">
      <div class="brand">${logo()} Smart Clinic</div>
      <div>
        <h1>Care that runs on time.</h1>
        <p>Book visits, pay online, check in at the desk and see patients in the order they need to be seen.</p>
      </div>
      <div class="tri" aria-hidden="true">
        <div style="--c:var(--emergency)">Emergency seen first</div>
        <div style="--c:#d99a1d">Urgent next</div>
        <div style="--c:#9bb3bc">Regular in arrival order</div>
      </div>
    </section>
    <section class="auth-form">
      <div class="auth-card">
        <div class="tabs" role="tablist">
          <button role="tab" id="t-login" aria-selected="${tab === 'login'}">Log in</button>
          <button role="tab" id="t-reg" aria-selected="${tab === 'register'}">Create patient account</button>
        </div>
        ${message ? `<div class="notice ${tab === 'login' && !prefillId ? 'warn' : ''}">${esc(message)}</div>` : ''}
        <form id="auth-form" class="stack" novalidate></form>
      </div>
    </section>
  </div>`;
  $('#t-login').onclick = () => renderAuth('', 'login');
  $('#t-reg').onclick = () => renderAuth('', 'register');
  const form = $('#auth-form');
  if (tab === 'login') {
    form.innerHTML = `
      <label>ID<input name="user_id" autocomplete="username" placeholder="P001, D001, N001 or A001" value="${esc(prefillId)}" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <div class="form-error" role="alert"></div>
      <button class="btn">Log in</button>`;
    form.onsubmit = async e => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(form));
      try {
        me = await api('/login', { method: 'POST', body: d });
        location.hash = '';
        renderShell();
      } catch (err) { $('.form-error', form).textContent = err.message; }
    };
    (prefillId ? $('[name=password]', form) : $('[name=user_id]', form)).focus();
  } else {
    form.innerHTML = `
      <label>Full name<input name="name" autocomplete="name" required></label>
      <label>Phone<input name="phone" inputmode="numeric" autocomplete="tel" placeholder="01012345678" required>
        <span class="field-hint">11 digits starting with 010, 011, 012 or 015.</span></label>
      <label>Age<input name="age" type="number" min="1" max="120" required></label>
      <label>Password<input name="password" type="password" autocomplete="new-password" minlength="4" required>
        <span class="field-hint">At least 4 characters.</span></label>
      <div class="form-error" role="alert"></div>
      <button class="btn">Create account</button>`;
    form.onsubmit = async e => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(form));
      try {
        const r = await api('/register', { method: 'POST', body: d });
        renderAuth(`Account created. Your patient ID is ${r.patient_id}. Log in with it.`, 'login', r.patient_id);
      } catch (err) { $('.form-error', form).textContent = err.message; }
    };
    $('[name=name]', form).focus();
  }
}
const logo = () => `<svg width="30" height="30" viewBox="0 0 100 100" aria-hidden="true"><rect width="100" height="100" rx="22" fill="#fff"/><path d="M42 22h16v20h20v16H58v20H42V58H22V42h20z" fill="#0a5c63"/></svg>`;

/* ================================================================ shell */
const NAV = {
  patient: [['book', 'Book appointment'], ['appointments', 'My appointments'], ['payments', 'Payments'], ['history', 'Visit history'], ['profile', 'Profile']],
  nurse: [['today', "Today's appointments"], ['checkin', 'Check in'], ['queue', 'Waiting queue'], ['patients', 'Find patient'], ['doctors', 'My doctors'], ['profile', 'Profile']],
  doctor: [['today', 'Appointments'], ['queue', 'Waiting queue'], ['visits', 'Visits'], ['history', 'Patient history'], ['schedule', 'Schedule'], ['profile', 'Profile']],
  admin: [['overview', 'Overview'], ['doctors', 'Doctors'], ['nurses', 'Nurses'], ['patients', 'Patients'], ['appointments', 'Appointments'], ['log', 'Activity log'], ['profile', 'Profile']],
};

function renderShell() {
  const links = NAV[me.role].map(([k, t]) => `<a href="#/${k}" data-k="${k}">${esc(t)}</a>`).join('');
  app.innerHTML = `<div class="shell">
    <aside class="side">
      <div class="brand">${logo()} Smart Clinic</div>
      <div class="who"><strong>${esc(me.name)}</strong><span>${esc(me.role)} · ${esc(me.user_id)}</span></div>
      <nav class="nav" aria-label="Main">${links}</nav>
      <button class="btn secondary logout" id="logout">Log out</button>
    </aside>
    <main class="main" id="main" tabindex="-1"></main></div>`;
  $('#logout').onclick = async () => { await act(() => api('/logout', { method: 'POST' })); me = null; location.hash = ''; renderAuth(); };
  route();
}

function route() {
  if (!me) return;
  const items = NAV[me.role];
  const key = location.hash.replace(/^#\//, '');
  const item = items.find(i => i[0] === key) || items[0];
  $$('.nav a').forEach(a => { if (a.dataset.k === item[0]) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  const main = $('#main');
  main.innerHTML = '<p class="loading">Loading…</p>';
  document.title = `${item[1]} · Smart Clinic`;
  VIEWS[me.role][item[0]](main).catch(e => { main.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; });
}
window.addEventListener('hashchange', route);

/* ============================================================== views */
const VIEWS = { patient: {}, nurse: {}, doctor: {}, admin: {} };

/* ------------ shared ------------ */
async function profileView(el) {
  const p = await api('/me').then(r => r.user);
  const rows = [['ID', p.user_id], ['Name', p.name], ['Phone', p.phone], ['Role', p.role]];
  if (p.role === 'patient') rows.push(['Age', p.age], ['Case type', p.case_type || 'Not set'], ['Appointments', p.appointment_count]);
  if (p.role === 'doctor') rows.push(['Specialty', p.specialty], ['Rate', money(p.rate)], ['Appointment length', p.appointment_duration + ' minutes'],
    ['Working days', p.working_days.join(', ')], ['Working hours', `${p.working_hours.start} to ${p.working_hours.end}`]);
  if (p.role === 'nurse') rows.push(['Assigned doctors', p.assigned_doctors.join(', ') || 'None']);
  el.innerHTML = head('Profile') + `<div class="panel"><dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;
}
Object.values(VIEWS).forEach(v => v.profile = profileView);

function patientTable(list, empty) {
  return table(['Patient', 'Age', 'Phone', 'Case type', 'Priority'],
    list.map(p => [`<span class="id">${esc(p.patient_id)}</span><br>${esc(p.name)}`, esc(p.age), esc(p.phone), badge((p.case_type || '').toUpperCase()), p.priority ? 'P' + esc(p.priority) : '<span class="sub">Not set</span>']), empty);
}

function visitCards(visits, empty) {
  if (!visits.length) return `<div class="panel empty">${esc(empty)}</div>`;
  return `<div class="cards">${visits.map(v => `<article class="card visit">
    <div class="row" style="justify-content:space-between;align-items:center"><h3>${esc(v.visit_id)}</h3>${badge(v.status)}</div>
    <p class="sub">Doctor ${esc(v.doctor_name || v.doctor_id)}${v.case_type ? ' · ' + badge(v.case_type.toUpperCase()) : ''}</p>
    <p><b>Diagnosis</b> ${esc(v.diagnosis || 'Not recorded')}</p>
    <p><b>Treatment</b> ${esc(v.treatment || 'Not recorded')}</p>
    <p><b>Follow-up</b> ${esc(v.follow_up || 'Not recorded')}</p></article>`).join('')}</div>`;
}

/* ------------ patient ------------ */
VIEWS.patient.book = async el => {
  const specialties = await api('/specialties');
  if (!specialties.length) {
    el.innerHTML = head('Book an appointment') + '<div class="panel empty">No doctors are available yet. Please check back later.</div>';
    return;
  }
  const st = {};
  const today = todayISO();
  el.innerHTML = head('Book an appointment', 'Choose a specialty and a date, then pick a doctor and a time.') + `
    <div class="stack">
      <form class="panel" id="f1"><div class="row">
        <label>Specialty<select name="specialty" required>${specialties.map(s => `<option>${esc(s)}</option>`).join('')}</select></label>
        <label>Date<input type="date" name="date" min="${today}" value="${today}" required></label>
        <button class="btn">Find doctors</button></div></form>
      <div id="docs"></div><div id="slots"></div><div id="svc"></div></div>`;

  const clear = (...ids) => ids.forEach(i => $('#' + i, el).innerHTML = '');
  $('#f1', el).onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    st.specialty = f.get('specialty'); st.date = f.get('date'); st.doctor = null; st.time = null;
    clear('docs', 'slots', 'svc');
    const list = await act(() => api(`/doctors/recommend?specialty=${encodeURIComponent(st.specialty)}&date=${st.date}`));
    if (list) showDoctors(list);
  };

  function showDoctors(list) {
    const box = $('#docs', el);
    if (!list.length) { box.innerHTML = `<div class="panel empty">No ${esc(st.specialty)} doctor works on ${fmtDate(st.date)}. Try another date.</div>`; return; }
    box.innerHTML = `<h2 style="margin-bottom:10px">Available doctors</h2><div class="cards">${list.map((x, i) => `
      <button type="button" class="card pick" data-i="${i}" aria-pressed="false">
        <h3>${esc(x.doctor.name)}</h3>
        <span class="sub">${esc(x.doctor.specialty)} · ${esc(x.doctor.working_hours.start)} to ${esc(x.doctor.working_hours.end)}</span>
        <span>Rate ${esc(money(x.doctor.rate))}</span>
        <span class="sub">${x.load === 0 ? 'No bookings yet that day' : x.load + ' booked that day'}</span>
      </button>`).join('')}</div>`;
    $$('.pick', box).forEach(b => b.onclick = async () => {
      $$('.pick', box).forEach(x => x.setAttribute('aria-pressed', x === b));
      st.doctor = list[+b.dataset.i].doctor; st.time = null; clear('svc');
      const slots = await act(() => api(`/slots?doctor_id=${st.doctor.doctor_id}&date=${st.date}`));
      if (slots) showSlots(slots);
    });
  }

  function showSlots(slots) {
    const box = $('#slots', el);
    if (!slots.length) { box.innerHTML = `<div class="panel empty">${esc(st.doctor.name)} is fully booked on ${fmtDate(st.date)}. Choose another doctor or date.</div>`; return; }
    box.innerHTML = `<h2 style="margin-bottom:10px">Times with ${esc(st.doctor.name)} on ${fmtDate(st.date)}</h2><div class="slots">${slots.map(t => `<button type="button" class="slot" aria-pressed="false">${esc(t)}</button>`).join('')}</div>`;
    $$('.slot', box).forEach(b => b.onclick = () => {
      $$('.slot', box).forEach(x => x.setAttribute('aria-pressed', x === b));
      st.time = b.textContent; showService();
    });
  }

  function showService() {
    const rate = Number(st.doctor.rate);
    const names = { consultation: 'Consultation', examination: 'Examination', emergency: 'Emergency' };
    const order = ['consultation', 'examination', 'emergency'];
    const radios = Object.entries(meta.service_rates).sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).map(([k, base], i) => `
      <label><input type="radio" name="service" value="${esc(k)}" ${i === 0 ? 'checked' : ''}>
      <span><b>${esc(names[k] || k)}</b><br><span class="sub">Price ${esc(money(Math.round(base * rate * 1000) / 1000))}</span></span></label>`).join('');
    $('#svc', el).innerHTML = `<form class="panel stack" id="f2"><h2>Service</h2><div class="radios">${radios}</div>
      <p class="sub">${esc(st.doctor.name)} · ${fmtDate(st.date)} at ${esc(st.time)}</p>
      <div><button class="btn">Book appointment</button></div></form>`;
    $('#f2', el).onsubmit = async e => {
      e.preventDefault();
      const service_type = new FormData(e.target).get('service');
      const r = await act(() => api('/appointments', { method: 'POST', body: { doctor_id: st.doctor.doctor_id, date: st.date, time: st.time, service_type } }));
      if (r) showBooked(r);
    };
  }

  function showBooked(r) {
    el.innerHTML = head('Appointment booked') + `<div class="stack">
      <div class="notice">Payment code<strong>${esc(r.payment.payment_code)}</strong>
        <span>Pay it from the Payments page. Paying gives you the confirmation number the nurse needs at check-in.</span></div>
      <div class="panel"><dl class="facts">
        <dt>Appointment</dt><dd>${esc(r.appointment.appointment_id)}</dd>
        <dt>Doctor</dt><dd>${esc(r.doctor.name)}, ${esc(r.doctor.specialty)}</dd>
        <dt>When</dt><dd>${fmtDate(r.appointment.date)} at ${esc(r.appointment.time)}</dd>
        <dt>Service</dt><dd>${label(r.appointment.service_type)}</dd>
        <dt>Doctor rate</dt><dd>${esc(money(r.bill.doctor_rate))}</dd>
        <dt>Base rate</dt><dd>${esc(money(r.bill.base_rate))}</dd>
        <dt>Total</dt><dd>${esc(money(r.bill.total))}</dd></dl></div>
      <div class="row"><a class="btn" href="#/payments">Pay now</a><button class="btn secondary" id="again">Book another</button></div></div>`;
    $('#again', el).onclick = reload;
  }
};

VIEWS.patient.appointments = async el => {
  const list = await api('/my/appointments');
  const rows = list.map(a => {
    const canPay = a.payment_code && a.status !== 'CANCELLED';
    const canCancel = !['CANCELLED', 'COMPLETED', 'IN_PROGRESS'].includes(a.status);
    return [`<span class="id">${esc(a.appointment_id)}</span>`,
      `${esc(a.doctor_name)}<br><span class="sub">${esc(a.specialty || '')}</span>`,
      `${fmtDate(a.date)}<br><span class="sub">${esc(a.time)}</span>`, label(a.service_type), badge(a.status), badge(a.payment_status),
      a.confirmation_number ? `<span class="id">${esc(a.confirmation_number)}</span>` : '<span class="sub">After payment</span>',
      `${canPay ? `<button class="btn small" data-pay="${esc(a.payment_code)}">Pay ${esc(money(a.amount))}</button>` : ''}${canCancel ? `<button class="btn small danger" data-cancel="${esc(a.appointment_id)}">Cancel</button>` : ''}`];
  });
  el.innerHTML = head('My appointments', 'Keep your confirmation number handy for check-in.') +
    table(['Appointment', 'Doctor', 'When', 'Service', 'Status', 'Payment', 'Confirmation number', { t: '', cls: 'actions' }], rows, 'You have no appointments yet. Book one from the menu.');
  $$('[data-pay]', el).forEach(b => b.onclick = async () => { if (await act(() => api('/payments/pay', { method: 'POST', body: { payment_code: b.dataset.pay } }), 'Payment received.')) reload(); });
  $$('[data-cancel]', el).forEach(b => b.onclick = async () => {
    if (!(await confirmDialog('Cancel this appointment?', `Appointment ${b.dataset.cancel} will be cancelled and its time slot released.`, 'Cancel appointment'))) return;
    if (await act(() => api(`/appointments/${b.dataset.cancel}/cancel`, { method: 'POST' }), 'Appointment cancelled.')) reload();
  });
};

VIEWS.patient.payments = async (el, notice = '') => {
  const list = await api('/payments/pending');
  el.innerHTML = head('Payments', 'Pay pending bills to confirm your visit.') + notice +
    table(['Appointment', 'Doctor', 'When', { t: 'Amount', cls: 'num' }, { t: '', cls: 'actions' }],
      list.map(p => [`<span class="id">${esc(p.appointment_id)}</span>`, esc(p.doctor_name || ''), `${fmtDate(p.date)}<br><span class="sub">${esc(p.time)}</span>`, esc(money(p.amount)),
        `<button class="btn small" data-pay="${esc(p.payment_code)}">Pay ${esc(money(p.amount))}</button>`]), 'No pending payments.');
  $$('[data-pay]', el).forEach(b => b.onclick = async () => {
    const r = await act(() => api('/payments/pay', { method: 'POST', body: { payment_code: b.dataset.pay } }));
    if (r) VIEWS.patient.payments(el, `<div class="notice" style="margin-bottom:18px">Payment successful for ${esc(r.appointment_id)}. Confirmation number<strong>${esc(r.confirmation_number)}</strong><span>Show this number to the nurse at check-in. It also stays on My appointments.</span></div>`);
  });
};

VIEWS.patient.history = async el => {
  el.innerHTML = head('Visit history') + visitCards(await api('/my/history'), 'No visits recorded yet.');
};

/* ------------ nurse ------------ */
VIEWS.nurse.today = async el => {
  const list = await api('/nurse/today');
  el.innerHTML = head("Today's appointments", fmtDate(todayISO())) +
    table(['Appointment', 'Patient', 'Doctor', 'Time', 'Status', 'Payment'],
      list.map(a => [`<span class="id">${esc(a.appointment_id)}</span>`, `${esc(a.patient_name)}<br><span class="sub">${esc(a.patient_id)}</span>`, esc(a.doctor_name), esc(a.time), statusSelect(a), badge(a.payment_status)]),
      'No appointments today for your doctors.');
  wireStatusSelects(el);
};

VIEWS.nurse.checkin = async el => {
  el.innerHTML = head('Check in a patient', 'Ask the patient for the confirmation number they received after paying.') + `
    <div class="stack"><form class="panel" id="f"><div class="row">
      <label>Confirmation number<input name="confirmation_number" inputmode="numeric" autocomplete="off" required></label>
      <button class="btn">Check in</button></div></form><div id="out"></div></div>`;
  $('#f', el).onsubmit = async e => {
    e.preventDefault();
    const num = new FormData(e.target).get('confirmation_number');
    const r = await act(() => api('/nurse/checkin', { method: 'POST', body: { confirmation_number: num } }));
    if (r) {
      e.target.reset();
      $('#out', el).innerHTML = `<div class="notice"><b>${esc(r.patient_name)}</b> is checked in and waiting.
        <div style="margin-top:6px">${badge(PRI[r.priority])} ${esc(r.appointment_id)} · arrived ${esc(r.arrival_time)}</div></div>`;
    }
  };
};

async function queueRows(path, { start = false } = {}) {
  const list = await api(path);
  return table(['Patient', 'Priority', 'Arrived', 'Doctor', 'Appointment', ...(start ? [{ t: '', cls: 'actions' }] : [])],
    list.map((q, i) => ({
      cls: 'tag-' + q.priority,
      cells: [`<span class="id">${esc(q.patient_name)}</span><br><span class="sub">${esc(q.patient_id)}</span>`, badge(q.priority_name), esc(q.arrival_time), esc(q.doctor_id), esc(q.appointment_id),
        ...(start ? [`<button class="btn small" data-start="${esc(q.appointment_id)}">Start visit</button>`] : [])],
    })), 'The waiting queue is empty.');
}

VIEWS.nurse.queue = async el => {
  const mode = el.dataset.mode || 'all';
  el.dataset.mode = mode;
  el.innerHTML = head('Waiting queue', 'Emergency first, then urgent, then regular by arrival time.',
    `<div class="seg" role="group" aria-label="Filter"><button aria-pressed="${mode === 'all'}" data-m="all">All patients</button><button aria-pressed="${mode === 'emergency'}" data-m="emergency">Emergency only</button></div>`) +
    await queueRows('/queue' + (mode === 'emergency' ? '?emergency=1' : ''));
  $$('[data-m]', el).forEach(b => b.onclick = () => { el.dataset.mode = b.dataset.m; VIEWS.nurse.queue(el); });
};

async function patientSearch(el, title, sub) {
  el.innerHTML = head(title, sub) + `<div class="stack"><form class="panel" id="f"><div class="row">
    <label>Patient ID or name<input name="q" autocomplete="off" placeholder="P001 or Ali"></label><button class="btn">Search</button></div></form><div id="out"></div></div>`;
  const run = async q => { const list = await act(() => api('/patients/search?q=' + encodeURIComponent(q))); if (list) $('#out', el).innerHTML = patientTable(list, 'No patient matches that search.'); };
  $('#f', el).onsubmit = e => { e.preventDefault(); run(new FormData(e.target).get('q')); };
  run('');
}
VIEWS.nurse.patients = el => patientSearch(el, 'Find a patient', 'Search by ID or by any part of the name.');

VIEWS.nurse.doctors = async el => {
  const list = await api('/nurse/doctors');
  el.innerHTML = head('My doctors') + (list.length ? `<div class="cards">${list.map(d => `<article class="card"><h3>${esc(d.name)}</h3>
    <span class="sub">${esc(d.doctor_id)} · ${esc(d.specialty)}</span>
    <span>${esc(d.working_days.join(', '))}</span><span>${esc(d.working_hours.start)} to ${esc(d.working_hours.end)}, ${esc(d.appointment_duration)} minutes each</span></article>`).join('')}</div>` : '<div class="panel empty">No doctors are assigned to you.</div>');
};

/* ------------ doctor ------------ */
VIEWS.doctor.today = async el => {
  const all = el.dataset.scope === 'all';
  const list = await api('/doctor/appointments' + (all ? '' : '?today=1'));
  el.innerHTML = head('Appointments', all ? 'All of your appointments.' : "Today's appointments.",
    `<div class="seg" role="group" aria-label="Scope"><button aria-pressed="${!all}" data-s="today">Today</button><button aria-pressed="${all}" data-s="all">All</button></div>`) +
    table(['Appointment', 'Patient', 'When', 'Status', 'Payment'],
      list.map(a => [`<span class="id">${esc(a.appointment_id)}</span>`, `${esc(a.patient_name)}<br><span class="sub">${esc(a.patient_id)}</span>`, `${fmtDate(a.date)}<br><span class="sub">${esc(a.time)}</span>`, statusSelect(a), badge(a.payment_status)]),
      all ? 'You have no appointments.' : 'No appointments today.');
  wireStatusSelects(el);
  $$('[data-s]', el).forEach(b => b.onclick = () => { el.dataset.scope = b.dataset.s; VIEWS.doctor.today(el); });
};

VIEWS.doctor.queue = async el => {
  el.innerHTML = head('Waiting queue', 'Your checked-in patients, most urgent first.') + await queueRows('/doctor/queue', { start: true });
  $$('[data-start]', el).forEach(b => b.onclick = async () => {
    const v = await act(() => api('/doctor/start', { method: 'POST', body: { appointment_id: b.dataset.start } }));
    if (v) { toast(`Visit ${v.visit_id} started.`); location.hash = '#/visits'; }
  });
};

VIEWS.doctor.visits = async el => {
  const list = await api('/doctor/visits');
  list.sort((a, b) => (a.status === 'COMPLETED') - (b.status === 'COMPLETED'));
  el.innerHTML = head('Visits', 'Record the assessment, then complete the visit.') +
    table(['Visit', 'Patient', 'Case type', 'Diagnosis', 'Status', { t: '', cls: 'actions' }],
      list.map(v => [`<span class="id">${esc(v.visit_id)}</span>`, `${esc(v.patient_name)}<br><span class="sub">${esc(v.patient_id)}</span>`, badge((v.case_type || '').toUpperCase()), esc(v.diagnosis || '—'), badge(v.status),
        v.status === 'COMPLETED' ? '' : `<button class="btn small secondary" data-assess="${esc(v.visit_id)}">Assess</button><button class="btn small" data-done="${esc(v.visit_id)}">Complete</button>`]),
      'No visits yet. Start one from the waiting queue.');
  $$('[data-assess]', el).forEach(b => b.onclick = async () => {
    const v = list.find(x => x.visit_id === b.dataset.assess);
    const sel = c => (v.case_type || 'regular') === c ? 'selected' : '';
    const ok = await openDialog({
      title: `Assess ${v.patient_name}`, submit: 'Save assessment',
      body: `<label>Case type<select name="case_type"><option value="regular" ${sel('regular')}>Regular</option><option value="urgent" ${sel('urgent')}>Urgent</option><option value="emergency" ${sel('emergency')}>Emergency</option></select></label>
        <label>Diagnosis<textarea name="diagnosis">${esc(v.diagnosis || '')}</textarea></label>
        <label>Treatment<textarea name="treatment">${esc(v.treatment || '')}</textarea></label>
        <label>Follow-up<textarea name="follow_up">${esc(v.follow_up || '')}</textarea></label>`,
      onSubmit: form => api(`/doctor/visits/${v.visit_id}/assess`, { method: 'POST', body: Object.fromEntries(new FormData(form)) }),
    });
    if (ok) { toast('Assessment saved.'); reload(); }
  });
  $$('[data-done]', el).forEach(b => b.onclick = async () => {
    if (!(await confirmDialog('Complete this visit?', `Visit ${b.dataset.done} will be marked completed and cannot be reopened.`, 'Complete visit', false))) return;
    if (await act(() => api(`/doctor/visits/${b.dataset.done}/complete`, { method: 'POST' }), 'Visit completed.')) reload();
  });
};

VIEWS.doctor.history = async el => {
  el.innerHTML = head('Patient history', 'Look a patient up by ID or name to see their visits.') + `<div class="stack">
    <form class="panel" id="f"><div class="row"><label>Patient ID or name<input name="q" autocomplete="off" required></label><button class="btn">Search</button></div></form><div id="out"></div></div>`;
  const out = $('#out', el);
  const showHistory = async p => {
    const visits = await act(() => api(`/patients/${encodeURIComponent(p.patient_id)}/history`));
    if (!visits) return;
    out.innerHTML = `<h2 style="margin-bottom:10px">${esc(p.name)} <span class="sub">${esc(p.patient_id)}</span></h2>` + visitCards(visits, 'No visit history for this patient.');
  };
  $('#f', el).onsubmit = async e => {
    e.preventDefault();
    const q = new FormData(e.target).get('q').trim();
    const list = await act(() => api('/patients/search?q=' + encodeURIComponent(q)));
    if (!list) return;
    const exact = list.find(p => p.patient_id.toLowerCase() === q.toLowerCase());
    if (exact || list.length === 1) return showHistory(exact || list[0]);
    if (!list.length) { out.innerHTML = '<div class="panel empty">No patient matches that search.</div>'; return; }
    out.innerHTML = `<div class="cards">${list.map((p, i) => `<button type="button" class="card pick" data-i="${i}"><h3>${esc(p.name)}</h3><span class="sub">${esc(p.patient_id)} · age ${esc(p.age)}</span></button>`).join('')}</div>`;
    $$('.pick', out).forEach(b => b.onclick = () => showHistory(list[+b.dataset.i]));
  };
};

VIEWS.doctor.schedule = async el => {
  const p = await api('/me').then(r => r.user);
  const days = meta.days.map(d => `<span class="badge ${p.working_days.includes(d) ? 'CONFIRMED' : ''}" style="${p.working_days.includes(d) ? '' : 'opacity:.55'}">${esc(d)}</span>`).join(' ');
  el.innerHTML = head('Schedule') + `<div class="panel stack"><div class="row" style="gap:8px">${days}</div>
    <dl class="facts"><dt>Working hours</dt><dd>${esc(p.working_hours.start)} to ${esc(p.working_hours.end)}</dd>
    <dt>Appointment length</dt><dd>${esc(p.appointment_duration)} minutes</dd><dt>Rate</dt><dd>${esc(money(p.rate))}</dd></dl></div>`;
};

/* ------------ admin ------------ */
VIEWS.admin.overview = async el => {
  const date = el.dataset.date || todayISO();
  const [r, rev] = await Promise.all([api('/admin/report?date=' + date), api('/admin/revenue')]);
  const stat = (n, t, hot) => `<div class="stat ${hot ? 'hot' : ''}"><b>${esc(n)}</b><span>${esc(t)}</span></div>`;
  el.innerHTML = head('Overview', 'Daily figures for the selected date.', `<label>Date<input type="date" id="d" value="${esc(date)}"></label>`) + `<div class="stack">
    <div class="grid">
      ${stat(r.total_patients, 'Registered patients')}${stat(r.completed_visits, 'Completed visits')}${stat(r.waiting_patients, 'Waiting today')}
      ${stat(r.cancelled, 'Cancelled')}${stat(r.emergency_cases, 'Emergency cases', r.emergency_cases > 0)}${stat(r.urgent_cases, 'Urgent cases')}
      ${stat(r.regular_cases, 'Regular cases')}${stat(r.doctors_working, 'Doctors working')}${stat(r.nurses_working, 'Nurses on duty')}
      ${stat(r.paid_appointments, 'Paid appointments')}${stat(r.unpaid_appointments, 'Unpaid appointments')}${stat(money(r.total_revenue), 'Total revenue, all dates')}</div>
    <h2>Revenue by doctor</h2>
    ${table(['Doctor', 'Specialty', { t: 'Revenue', cls: 'num' }], rev.map(d => [`<span class="id">${esc(d.doctor_id)}</span> ${esc(d.name)}`, esc(d.specialty), esc(money(d.revenue))]), 'No doctors yet.')}</div>`;
  $('#d', el).onchange = e => { el.dataset.date = e.target.value || todayISO(); VIEWS.admin.overview(el); };
};

const phoneField = '<label>Phone<input name="phone" inputmode="numeric" placeholder="01012345678" required><span class="field-hint">11 digits starting with 010, 011, 012 or 015.</span></label>';
const passField = '<label>Password<input name="password" type="password" autocomplete="new-password" minlength="4" required><span class="field-hint">At least 4 characters.</span></label>';

VIEWS.admin.doctors = async el => {
  const list = await api('/admin/doctors');
  el.innerHTML = head('Doctors', '', '<button class="btn" id="add">Add doctor</button>') +
    table(['Doctor', 'Specialty', { t: 'Rate', cls: 'num' }, 'Schedule', 'Nurses', { t: '', cls: 'actions' }],
      list.map(d => [`<span class="id">${esc(d.doctor_id)}</span><br>${esc(d.name)}`, esc(d.specialty), esc(money(d.rate)),
        `${esc(d.working_days.join(', '))}<br><span class="sub">${esc(d.working_hours.start)} to ${esc(d.working_hours.end)}, ${esc(d.appointment_duration)} min</span>`,
        esc(d.assigned_nurses.join(', ') || 'None'), `<button class="btn small danger" data-rm="${esc(d.doctor_id)}">Remove</button>`]), 'No doctors yet. Add the first one.');
  $('#add', el).onclick = async () => {
    const ok = await openDialog({
      title: 'Add doctor', submit: 'Add doctor',
      body: `<div class="form-grid"><label class="wide">Name<input name="name" required></label>${phoneField}
        <label>Specialty<input name="specialty" required></label>
        <label>Rate<input name="rate" type="number" min="0.01" step="0.01" required></label>
        <label>Appointment length (minutes)<input name="duration" type="number" min="1" value="30" required></label>
        <label>Starts<input name="start" type="time" value="09:00" required></label><label>Ends<input name="end" type="time" value="17:00" required></label>
        <div class="wide"><b style="font-size:.9rem;font-weight:500">Working days (choose exactly 3)</b>
          <div class="checks" style="margin-top:6px">${meta.days.map(d => `<label><input type="checkbox" name="day" value="${esc(d)}">${esc(d)}</label>`).join('')}</div></div>
        <div class="wide">${passField}</div></div>`,
      onSubmit: form => {
        const d = Object.fromEntries(new FormData(form));
        d.working_days = $$('[name=day]:checked', form).map(c => c.value);
        delete d.day;
        return api('/admin/doctors', { method: 'POST', body: d });
      },
    });
    if (ok) { toast(`Doctor ${ok.doctor_id} added.`); reload(); }
  };
  $$('[data-rm]', el).forEach(b => b.onclick = async () => {
    if (!(await confirmDialog('Remove this doctor?', `Doctor ${b.dataset.rm} will lose access and be removed from nurse assignments. Existing appointments stay in the records.`, 'Remove doctor'))) return;
    if (await act(() => api('/admin/doctors/' + b.dataset.rm, { method: 'DELETE' }), 'Doctor removed.')) reload();
  });
};

VIEWS.admin.nurses = async el => {
  const [nurses, doctors] = await Promise.all([api('/admin/nurses'), api('/admin/doctors')]);
  el.innerHTML = head('Nurses', '', '<button class="btn" id="add">Add nurse</button>') +
    table(['Nurse', 'Phone', 'Assigned doctors', { t: '', cls: 'actions' }],
      nurses.map(n => [`<span class="id">${esc(n.nurse_id)}</span><br>${esc(n.name)}`, esc(n.phone), esc(n.assigned_doctors.join(', ') || 'None'), `<button class="btn small danger" data-rm="${esc(n.nurse_id)}">Remove</button>`]), 'No nurses yet.');
  $('#add', el).onclick = async () => {
    if (!doctors.length) { toast('Add a doctor first. Every nurse is assigned to a doctor.', 'error'); return; }
    const ok = await openDialog({
      title: 'Add nurse', submit: 'Add nurse',
      body: `<div class="form-grid"><label class="wide">Name<input name="name" required></label>${phoneField}
        <label>Assigned doctor<select name="doctor_id">${doctors.map(d => `<option value="${esc(d.doctor_id)}">${esc(d.name)} (${esc(d.specialty)})</option>`).join('')}</select></label>
        <div class="wide">${passField}</div></div>`,
      onSubmit: form => api('/admin/nurses', { method: 'POST', body: Object.fromEntries(new FormData(form)) }),
    });
    if (ok) { toast(`Nurse ${ok.nurse_id} added.`); reload(); }
  };
  $$('[data-rm]', el).forEach(b => b.onclick = async () => {
    if (!(await confirmDialog('Remove this nurse?', `Nurse ${b.dataset.rm} will lose access.`, 'Remove nurse'))) return;
    if (await act(() => api('/admin/nurses/' + b.dataset.rm, { method: 'DELETE' }), 'Nurse removed.')) reload();
  });
};

VIEWS.admin.patients = async el => {
  const list = await api('/admin/patients');
  el.innerHTML = head('Patients') + table(['Patient', 'Age', 'Phone', 'Case type', 'Appointments', { t: '', cls: 'actions' }],
    list.map(p => [`<span class="id">${esc(p.patient_id)}</span><br>${esc(p.name)}`, esc(p.age), esc(p.phone), badge((p.case_type || '').toUpperCase()), esc(p.appointments.length), `<button class="btn small danger" data-rm="${esc(p.patient_id)}">Remove</button>`]), 'No patients have registered yet.');
  $$('[data-rm]', el).forEach(b => b.onclick = async () => {
    if (!(await confirmDialog('Remove this patient?', `Patient ${b.dataset.rm} will lose access. Their visits and bills stay in the records.`, 'Remove patient'))) return;
    if (await act(() => api('/admin/patients/' + b.dataset.rm, { method: 'DELETE' }), 'Patient removed.')) reload();
  });
};

VIEWS.admin.appointments = async el => {
  const list = await api('/admin/appointments');
  el.innerHTML = head('Appointments', 'All appointments, oldest first.') + table(['Appointment', 'Patient', 'Doctor', 'When', 'Service', 'Status', 'Payment'],
    list.map(a => [`<span class="id">${esc(a.appointment_id)}</span>`, `${esc(a.patient_name)}<br><span class="sub">${esc(a.patient_id)}</span>`, `${esc(a.doctor_name)}<br><span class="sub">${esc(a.doctor_id)}</span>`,
      `${fmtDate(a.date)}<br><span class="sub">${esc(a.time)}</span>`, label(a.service_type), badge(a.status), badge(a.payment_status)]), 'No appointments yet.');
};

VIEWS.admin.log = async el => {
  const list = await api('/admin/history');
  el.innerHTML = head('Activity log', 'Newest first.') + (list.length
    ? `<div class="panel panel-flush"><ol class="log">${list.map((t, i) => [i + 1, t]).reverse().map(([n, t]) => `<li><span class="n">${n}</span><span>${esc(t)}</span></li>`).join('')}</ol></div>`
    : '<div class="panel empty">No actions recorded yet.</div>');
};

/* ================================================================ boot */
(async function boot() {
  try {
    meta = await api('/meta');
    const r = await api('/me');
    me = r.user;
  } catch (e) { /* fall through to login */ }
  if (me) renderShell(); else renderAuth();
})();
