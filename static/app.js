/* ShopPilot — клиент. Ванильный JS, без сборки. */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ic = (n, cls = '') => `<svg class="i ${cls}"><use href="#i-${n}"/></svg>`;
const money = n => Number(n).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const store = { get: k => { try { return localStorage.getItem(k) } catch { return null } }, set: (k, v) => { try { localStorage.setItem(k, v) } catch {} } };

const state = { mode: store.get('sp-mode') || 'react', view: 'assistant', history: [], busy: false, products: [], tools: {} };

const MODE_HINT = {
  react: 'ReAct: модель рассуждает текстом (Thought → Action → Observation), программа сама разбирает этот текст и вызывает инструмент.',
  tools: 'Tool calling: модель сама возвращает вызов функции в структурированном виде (нативный function calling OpenAI), без текстового парсинга.'
};
const STARTERS = [
  { ic: 'laptop', c: 'brand', t: 'Промокод + доставка', q: 'Сколько будет стоить ноутбук с промокодом SUMMER20 и доставкой в Гянджу?' },
  { ic: 'box', c: 'violet', t: 'Корзина из двух позиций', q: 'Хочу 2 смартфона и наушники. Всё ли есть на складе? Посчитай итог с промокодом WELCOME10.' },
  { ic: 'tablet', c: 'rose', t: 'Нет в наличии — что делать', q: 'Возьму планшет Slate и доставку в Ленкорань. Что по срокам и цене?' },
  { ic: 'search', c: 'amber', t: 'Подбор по бюджету', q: 'Что у вас есть дешевле 150 манат? Покажи с ценами.' },
];
const TICKETS = [
  'Заказ #4471 не приходит уже 9 дней, никто не отвечает! Верните деньги, иначе напишу жалобу.',
  'Salam, noutbukun qiyməti endirimlə neçədir? Şəkiyə çatdırılma varmı?',
  'Подскажите, сколько стоит доставка в Шеки и сколько идёт? Хочу заказать колонку.',
  'Спасибо! Наушники Pulse пришли быстро и звучат отлично, всё супер.'
];

/* ───────── тема ───────── */
$('#themeBtn').onclick = () => {
  const d = document.documentElement, next = d.dataset.theme === 'dark' ? 'light' : 'dark';
  d.classList.add('tx'); d.dataset.theme = next; store.set('sp-theme', next);
  setTimeout(() => d.classList.remove('tx'), 500);
};

/* ───────── статус ───────── */
async function loadStatus() {
  const st = $('#status'), tx = $('#statusText');
  try {
    const s = await (await fetch('/api/status')).json();
    st.dataset.ready = s.ai_ready ? 'yes' : 'no';
    tx.textContent = s.ai_ready ? s.model : 'нет ключа OpenAI';
    $('#keyBanner').hidden = s.ai_ready;
  } catch { st.dataset.ready = 'no'; tx.textContent = 'сервер недоступен'; }
}

/* ───────── каталог ───────── */
async function loadCatalog() {
  try {
    const d = await (await fetch('/api/catalog')).json();
    state.products = d.products;
    d.tools.forEach(t => state.tools[t.name] = t);
    $('#productList').innerHTML = d.products.map((p, n) => `
      <li><button class="prod" style="--n:${n}" data-stock="${p.stock}" data-low="${p.stock > 0 && p.stock <= 5 ? 1 : 0}" data-q="Расскажи про «${esc(p.name)}»: цена, рейтинг и есть ли в наличии?">
        <span class="tile">${ic(p.icon)}</span>
        <span><b>${esc(p.name)}</b><small>${p.stock === 0 ? 'нет в наличии' : p.stock <= 5 ? 'осталось ' + p.stock : '★ ' + p.rating + ' · ' + p.stock + ' шт.'}</small></span>
        <span class="price">${p.price}<i>AZN</i></span></button></li>`).join('');
    $('#promoList').innerHTML = d.promos.map(p => `
      <button class="promo" data-off="${p.active ? 0 : 1}" data-q="Подойдёт ли промокод ${p.code} для заказа на 600 манат?">${ic('gift')}${p.code}</button>`).join('');
    $('#toolChips').innerHTML = d.tools.map((t, n) => `<span style="--n:${n}" title="${esc(t.desc)}">${ic(t.icon)}${t.name}</span>`).join('');
  } catch { /* без каталога интерфейс всё равно работает */ }
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-q]');
  if (!b) return;
  switchView('assistant'); ask(b.dataset.q);
});

/* ───────── вкладки и режим ───────── */
function switchView(v) {
  if (state.view === v) return;
  state.view = v;
  $$('.nav-btn').forEach(b => b.setAttribute('aria-current', b.dataset.view === v ? 'page' : 'false'));
  $('#view-assistant').hidden = v !== 'assistant';
  $('#view-tickets').hidden = v !== 'tickets';
}
$$('.nav-btn').forEach(b => b.onclick = () => switchView(b.dataset.view));

function setMode(m) {
  state.mode = m; store.set('sp-mode', m);
  const sw = $('#modeSwitch'); $$('.how-card').forEach(c => c.dataset.on = c.dataset.m === m ? 1 : 0);
  sw.style.setProperty('--i', m === 'react' ? 0 : 1);
  $$('button', sw).forEach(b => b.setAttribute('aria-selected', b.dataset.mode === m));
  const h = $('#modeHint'); h.style.opacity = 0;
  setTimeout(() => { h.textContent = MODE_HINT[m]; h.style.opacity = 1 }, 160);
}
$$('#modeSwitch button').forEach(b => b.onclick = () => setMode(b.dataset.mode));

/* ───────── чат ───────── */
const chat = $('#chat');
const scrollDown = () => requestAnimationFrame(() => chat.scrollTo({ top: chat.scrollHeight }));

function addMsg(role, html) {
  $('#hero')?.remove(); $('#view-assistant').classList.add('chatting');
  const m = document.createElement('div');
  m.className = 'msg ' + role;
  m.innerHTML = `<div class="avatar">${role === 'bot' ? ic('logo') : ic('user')}</div><div class="bubble">${html}</div>`;
  chat.append(m); scrollDown();
  return m.querySelector('.bubble');
}

const q = $('#q');
const grow = () => { q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 140) + 'px' };
q.addEventListener('input', grow);
q.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#composer').requestSubmit() } });
$('#composer').onsubmit = e => { e.preventDefault(); const v = q.value.trim(); if (v) { q.value = ''; grow(); ask(v) } };
$('#resetBtn').onclick = () => {
  if (state.busy) return;
  $('#view-assistant').classList.remove('chatting');
  state.history = []; chat.innerHTML = '<div class="hero" id="hero"><div class="how" id="how"></div><div class="starters" id="starters"></div></div>';
  renderStarters(); clearTrace();
};
function renderHow() {
  const el = $('#how'); if (!el) return;
  el.innerHTML = `
    <div class="how-card" style="--n:0" data-m="react" data-on="${state.mode === 'react' ? 1 : 0}">
      <h4>${ic('repeat')}ReAct — думает текстом</h4>
      <p>Модель пишет рассуждение обычным текстом, а программа разбирает его и запускает инструмент.</p>
<pre><b>Thought:</b> нужна цена ноутбука
<i>Action:</i> get_price
<u>Action Input:</u> {"product": "ноутбук"}
<b>Observation:</b> 1500 AZN</pre></div>
    <div class="how-card" style="--n:1" data-m="tools" data-on="${state.mode === 'tools' ? 1 : 0}">
      <h4>${ic('bolt')}Tool calling — вызывает функции</h4>
      <p>Модель сразу возвращает готовый вызов функции в виде данных. Текст разбирать не нужно — надёжнее.</p>
<pre><i>tool_calls:</i> [{
  <u>name:</u> "get_price",
  <u>args:</u> {"product": "ноутбук"}
}]</pre></div>`;
}
function renderStarters() {
  renderHow();
  const el = $('#starters');
  el.innerHTML = STARTERS.map((s, n) => `
    <button class="starter" style="--n:${n};--c:var(--${s.c});--c-soft:var(--${s.c}-soft)" data-q="${esc(s.q)}">
      <span class="tile">${ic(s.ic)}</span><span><b>${s.t}</b><span class="t">${esc(s.q)}</span></span></button>`).join('');
}
renderStarters();

/* ───────── ход мысли ───────── */
const tb = $('#traceBody');
let nodes = new Map(), structNode = null;
function clearTrace() {
  tb.innerHTML = ''; nodes = new Map(); structNode = null; $('#traceStats').innerHTML = '';
}
function node(kind, state_, icon, title, body, extra = '', ms) {
  const n = document.createElement('div');
  n.className = 'node ' + kind; n.dataset.state = state_;
  n.innerHTML = `<div class="ndot">${ic(icon)}</div><div class="ncard"><div class="ntitle"><span>${title}</span>${ms != null ? `<time>${(ms / 1000).toFixed(1)}с</time>` : ''}</div>${body ? `<div class="nbody ${kind === 'thought' ? '' : 'mono'}">${body}</div>` : ''}${extra}</div>`;
  tb.append(n); tb.scrollTo({ top: tb.scrollHeight, behavior: 'smooth' });
  return n;
}
const kvs = a => `<div class="kvs">${Object.entries(a || {}).map(([k, v]) => `<span class="kv"><b>${esc(k)}</b>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</span>`).join('')}</div>`;
const stat = (icon, text, cls = '') => { $('#traceStats').insertAdjacentHTML('beforeend', `<span class="stat ${cls}">${ic(icon)}${text}</span>`) };

function setBusyLabel(bubble, text) { const l = $('.lbl', bubble); if (l) { l.textContent = text; l.classList.remove('swap'); void l.offsetWidth; l.classList.add('swap') } }

function onEvent(ev, bubble, ctx) {
  if (ev.type === 'step') {
    if (ev.kind === 'thought') { node('thought', 'ok', 'bulb', 'Thought', esc(ev.text), '', ev.ms); setBusyLabel(bubble, 'Размышляю…') }
    else if (ev.kind === 'note') node('note', 'ok', 'alert', 'Заметка', esc(ev.text), '', ev.ms);
    else if (ev.kind === 'action') {
      const t = state.tools[ev.tool];
      const n = node('action', 'run', t?.icon || 'bolt', 'Action · ' + esc(ev.tool), '', kvs(ev.args), ev.ms);
      nodes.set(ev.id, n); ctx.steps++;
      setBusyLabel(bubble, 'Вызываю: ' + (t?.title || ev.tool));
    } else if (ev.kind === 'observation') {
      const n = nodes.get(ev.id);
      if (n) {
        n.dataset.state = ev.ok ? 'ok' : 'err';
        $('.ndot', n).innerHTML = ic(ev.ok ? 'check' : 'alert');
        $('.ntitle span', n).textContent = (ev.ok ? 'Observation · ' : 'Ошибка · ') + ev.tool;
        const clean = ev.text.replace(/^ОШИБКА:\s*/, '');
        $('.ncard', n).insertAdjacentHTML('beforeend', `<div class="result">${esc(clean)}</div>`);
        if (!ev.ok) ctx.errors++;
        tb.scrollTo({ top: tb.scrollHeight, behavior: 'smooth' });
      }
    }
  } else if (ev.type === 'structuring') {
    structNode = node('struct', 'run', 'layers', 'Structured output', 'with_structured_output(ShopReport)', '', ev.ms);
    setBusyLabel(bubble, 'Собираю структурированный чек…');
  } else if (ev.type === 'final') {
    if (structNode) { structNode.dataset.state = 'ok'; $('.ndot', structNode).innerHTML = ic('check') }
    stat('repeat', ev.steps + ' шаг.'); stat('clock', (ev.ms / 1000).toFixed(1) + ' с', 'ok');
    if (ctx.errors) stat('alert', ctx.errors + ' ошиб.');
    renderFinal(bubble, ev);
  } else if (ev.type === 'error') {
    throw new Error(ev.message);
  }
}

async function ask(text) {
  if (state.busy) return;
  state.busy = true; const btn = $('#sendBtn'); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  addMsg('user', `<p>${esc(text)}</p>`);
  const bubble = addMsg('bot', `<div class="typing"><span class="dots"><i></i><i></i><i></i></span><span class="lbl">Думаю…</span></div>`);
  clearTrace();
  tb.insertAdjacentHTML('afterbegin', `<div class="run-head">${ic(state.mode === 'react' ? 'repeat' : 'bolt')}<span class="q">${esc(text)}</span></div>`);
  const ctx = { steps: 0, errors: 0 };
  try {
    const res = await fetch('/api/agent/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: text, mode: state.mode, history: state.history.slice(-6) })
    });
    if (!res.ok) throw new Error(res.status === 422 ? 'Слишком короткий или слишком длинный вопрос.' : 'Сервер вернул ошибку ' + res.status);
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (line.startsWith('data:')) onEvent(JSON.parse(line.slice(5)), bubble, ctx);
      }
    }
    state.history.push({ role: 'user', content: text });
  } catch (e) {
    bubble.innerHTML = `<div class="err">${ic('alert')}<p>${esc(e.message)}</p></div>`;
    $$('.node[data-state=run]', tb).forEach(n => { n.dataset.state = 'err'; $('.ndot', n).innerHTML = ic('x') });
  } finally {
    state.busy = false; btn.disabled = false; btn.innerHTML = ic('send'); scrollDown(); q.focus();
  }
}

/* ───────── итоговый ответ + чек ───────── */
function iconFor(name) {
  const n = (name || '').toLowerCase();
  const p = state.products.find(p => n.includes(p.name.toLowerCase()) || p.name.toLowerCase().includes(n) || n.split(/\s+/).some(w => w.length > 3 && p.name.toLowerCase().includes(w)));
  return p ? p.icon : 'box';
}
const hl = o => esc(JSON.stringify(o, null, 2)).replace(/(&quot;[^&]*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\d+\.?\d*)/g,
  (m, s, colon, b, num) => s ? (colon ? `<span class="k">${s}</span>${colon}` : `<span class="s">${s}</span>`) : b ? `<span class="b">${b}</span>` : `<span class="n">${num}</span>`);

function renderFinal(bubble, ev) {
  const r = ev.report, rows = [];
  let n = 0;
  const row = (l, v, cls = '') => rows.push(`<div class="r-row ${cls}" style="--n:${n++}"><span>${l}</span><span>${v}</span></div>`);
  if (r.subtotal != null) row('Сумма товаров', `${money(r.subtotal)} AZN`);
  if (r.promo_code) row('Промокод', esc(r.promo_code));
  if (r.savings) row('Экономия', `−${money(r.savings)} AZN`, 'save');
  if (r.delivery_city || r.delivery_days != null || r.delivery_cost != null)
    row(`Доставка${r.delivery_city ? ' · ' + esc(r.delivery_city) : ''}`, [r.delivery_days != null ? r.delivery_days + ' дн.' : '', r.delivery_cost != null ? (r.delivery_cost === 0 ? 'бесплатно' : money(r.delivery_cost) + ' AZN') : ''].filter(Boolean).join(' · '));
  const hasReceipt = r.items.length || r.total != null || rows.length;
  const receipt = !hasReceipt ? '' : `
    <div class="receipt">
      <div class="r-head">${ic('layers')}Структурированный чек<span class="badge" data-s="${r.status}">${r.status}</span></div>
      ${r.items.map((it, k) => `
        <div class="r-item" style="--n:${k}">
          <span class="tile">${ic(iconFor(it.product))}</span>
          <span><b>${esc(it.product)}</b><small>${it.quantity} × ${money(it.unit_price)} AZN${it.discount_percent ? `<span class="disc">−${+it.discount_percent}%</span>` : ''}</small></span>
          <span class="sum">${money(it.line_total)}</span></div>`).join('')}
      ${rows.length ? `<div class="r-sum">${rows.join('')}</div>` : ''}
      ${r.warnings.length ? `<div class="warns">${r.warnings.map(w => `<div class="warn">${ic('alert')}<span>${esc(w)}</span></div>`).join('')}</div>` : ''}
      ${r.total != null ? `<div class="r-total"><span>К оплате</span><strong data-count="${r.total}">0,00<small>AZN</small></strong></div>` : ''}
    </div>`;
  const follow = r.follow_ups.length ? `<div class="follow">${r.follow_ups.map(f => `<button class="chip" data-q="${esc(f)}">${ic('arrow')}${esc(f)}</button>`).join('')}</div>` : '';
  bubble.innerHTML = `<p class="swap">${esc(ev.answer)}</p>${receipt}${follow}
    <button class="json-btn" aria-expanded="false">${ic('code')}JSON структурированного ответа${ic('chevron', 'chev')}</button>
    <pre class="json" hidden>${hl(r)}</pre>`;
  const jb = $('.json-btn', bubble), pre = $('.json', bubble);
  jb.onclick = () => { const o = pre.hidden; pre.hidden = !o; jb.setAttribute('aria-expanded', o); scrollDown() };
  const tot = $('[data-count]', bubble);
  if (tot) countUp(tot, +tot.dataset.count);
  if (r.status === 'ok' && tot) burst(tot);
  state.history.push({ role: 'assistant', content: ev.answer });
  scrollDown();
}

function countUp(el, to) {
  const small = el.querySelector('small'), t0 = performance.now(), dur = 1000;
  const fmt = v => money(v);
  const tick = now => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.firstChild.textContent = fmt(to * e); if (k < 1) requestAnimationFrame(tick);
  };
  el.firstChild.textContent = fmt(0); requestAnimationFrame(tick);
}
function burst(target) {
  const host = target.closest('.r-total'); if (!host) return;
  const b = document.createElement('div'); b.className = 'burst'; b.style.right = '70px'; b.style.top = '50%';
  const colors = ['var(--brand)', 'var(--amber)', 'var(--violet)', 'var(--brand-2)'];
  for (let i = 0; i < 16; i++) {
    const a = (Math.PI * 2 * i) / 16, d = 34 + Math.random() * 34, p = document.createElement('i');
    p.style.cssText = `--x:${Math.cos(a) * d}px;--y:${Math.sin(a) * d}px;--c:${colors[i % 4]};animation-delay:${i * 8}ms`;
    b.append(p);
  }
  host.append(b); setTimeout(() => b.remove(), 1400);
}

/* ───────── разбор обращений ───────── */
$('#ticketExamples').innerHTML = TICKETS.map(t => `<button class="chip" data-ticket="${esc(t)}">${ic('inbox')}${esc(t.length > 46 ? t.slice(0, 44) + '…' : t)}</button>`).join('');
$('#ticketExamples').onclick = e => { const b = e.target.closest('[data-ticket]'); if (b) { $('#ticketText').value = b.dataset.ticket; $('#ticketText').focus() } };
$('#ticketText').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) analyze() });
$('#ticketBtn').onclick = analyze;

const CAT_ICON = { 'цена': 'tag', 'доставка': 'truck', 'жалоба': 'flame', 'возврат': 'repeat', 'другое': 'chat' };
const SENT = { 'негативный': ['frown', 'bad'], 'нейтральный': ['meh', 'warn'], 'позитивный': ['smile', ''] };

async function analyze() {
  const text = $('#ticketText').value.trim(), out = $('#ticketOut'), btn = $('#ticketBtn');
  if (text.length < 5) { out.innerHTML = `<div class="err">${ic('alert')}<p>Введи хотя бы пару слов обращения.</p></div>`; return }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span><span>Анализирую…</span>';
  try {
    const res = await fetch('/api/ticket', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Ошибка ' + res.status);
    const tone = d.priority >= 4 ? 'var(--rose)' : d.priority === 3 ? 'var(--amber)' : 'var(--brand)';
    const [si, st] = SENT[d.sentiment] || ['meh', 'warn'];
    let n = 0;
    out.innerHTML = `<div class="t-card">
      <div class="t-grid">
        <div class="t-cell" style="--n:${n++}"><label>Тема</label><div class="t-val" style="--n:0"><span class="tile">${ic(CAT_ICON[d.category] || 'chat')}</span>${esc(d.category)}</div></div>
        <div class="t-cell" style="--n:${n++}"><label>Срочность</label><div class="t-val" data-tone="${d.is_urgent ? 'bad' : ''}" style="--n:1"><span class="tile">${ic(d.is_urgent ? 'flame' : 'check')}</span>${d.is_urgent ? 'Срочно' : 'Не срочно'}</div></div>
        <div class="t-cell" style="--n:${n++}"><label>Тон</label><div class="t-val" data-tone="${st}" style="--n:2"><span class="tile">${ic(si)}</span>${esc(d.sentiment)}</div></div>
        <div class="t-cell" style="--n:${n++}"><label>Приоритет · ${d.priority} из 5</label><div class="dotsrow" style="--c:${tone}">${[1, 2, 3, 4, 5].map(k => `<i class="${k <= d.priority ? 'on' : ''}" style="--k:${k}"></i>`).join('')}</div></div>
        <div class="t-cell" style="--n:${n++}"><label>Язык</label><div class="t-val" style="--n:4"><span class="tile">${ic('chat')}</span>${esc(d.language.toUpperCase())}</div></div>
        <div class="t-cell" style="--n:${n++}"><label>Товары</label>${d.products.length ? `<div class="t-chips">${d.products.map(p => `<span>${esc(p)}</span>`).join('')}</div>` : '<span class="hint">не упомянуты</span>'}</div>
      </div>
      <div class="t-text"><label>Суть</label><p>${esc(d.summary)}</p></div>
      <div class="t-text"><label>Черновик ответа<button class="copy">${ic('copy')}Копировать</button></label><p>${esc(d.suggested_reply)}</p></div>
      <div class="t-foot"><button class="json-btn" aria-expanded="false">${ic('code')}JSON по схеме TicketAnalysis${ic('chevron', 'chev')}</button><pre class="json" hidden>${hl(d)}</pre></div>
    </div>`;
    const jb = $('.json-btn', out), pre = $('.json', out);
    jb.onclick = () => { const o = pre.hidden; pre.hidden = !o; jb.setAttribute('aria-expanded', o) };
    $('.copy', out).onclick = e => { navigator.clipboard?.writeText(d.suggested_reply); e.currentTarget.innerHTML = ic('check') + 'Скопировано'; };
  } catch (e) {
    out.innerHTML = `<div class="err">${ic('alert')}<p>${esc(e.message)}</p></div>`;
  } finally { btn.disabled = false; btn.innerHTML = ic('layers') + '<span>Разобрать</span>' }
}

/* ───────── старт ───────── */
setMode(state.mode);
loadStatus(); loadCatalog();
})();
