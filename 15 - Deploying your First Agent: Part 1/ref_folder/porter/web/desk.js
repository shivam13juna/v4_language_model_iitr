// What both pages share: calling the API, the top band, the sign-in card, and small formatting
// helpers. Plain browser JavaScript with no build step. Data is always written with textContent,
// never as HTML, because some of it (a return's reason) is text a customer typed.
const Desk = (() => {
  // Browser storage can be missing (private windows, blocked site data): the pages still work,
  // they just forget the sign-in on reload.
  const store = {
    get(key) { try { return localStorage.getItem(key) || ''; } catch { return ''; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* not kept */ } },
    drop(key) { try { localStorage.removeItem(key); } catch { /* nothing kept */ } },
  };

  // el('div', {class: 'x', text: 'y', onclick: f}, child, …): one element, built safely.
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  // An Idempotency-Key for each message. crypto.randomUUID only exists on https and localhost,
  // so anywhere else the same thing is built from crypto.getRandomValues.
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  // One call to the service. Always resolves: {ok, status, data}, where a failure's data is the
  // service's error body ({error, detail, request_id}).
  async function api(path, {method = 'GET', body, token, headers = {}} = {}) {
    const sent = {...headers};
    if (token) sent['Authorization'] = 'Bearer ' + token;
    if (body !== undefined) sent['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(path, {method, headers: sent, body: body === undefined ? undefined : JSON.stringify(body)});
    } catch {
      return {ok: false, status: 0, data: {detail: 'The desk could not be reached. Is the service running?'}};
    }
    const data = await res.json().catch(() => ({detail: res.statusText}));
    return {ok: res.ok, status: res.status, data};
  }

  const money = n => (n < 0 ? '−£' : '£') +
    Math.abs(n).toLocaleString('en-GB', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  // "2011-12-05 12:44:00" → "5 Dec 2011"
  const day = s => new Date(String(s).replace(' ', 'T'))
    .toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
  // an ISO time → "29 Sep, 19:45", in the viewer's time zone
  const when = s => s ? new Date(s).toLocaleString('en-GB',
    {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : '';

  function toast(text, kind = 'ok') {
    let box = document.querySelector('.toasts');
    if (!box) {
      box = el('div', {class: 'toasts', role: 'status', 'aria-live': 'polite'});
      document.body.append(box);
    }
    const note = el('div', {class: kind === 'bad' ? 'toast bad' : 'toast', text});
    box.append(note);
    setTimeout(() => note.remove(), 4500);
  }

  // /version: which version is answering, and whether this server hands out demo sign-ins.
  let versionInfo = null;
  async function version() {
    if (!versionInfo) {
      const r = await api('/version');
      versionInfo = r.ok ? r.data : {version: '?', demo_sign_in: false};
    }
    return versionInfo;
  }

  async function footer(node, text) {
    const v = await version();
    node.textContent = `Porter ${v.version} · ${text}`;
  }

  // The band across the top: the shop, the page, and who is signed in.
  function topbar(node, {section, home, who, onSignOut}) {
    const parts = [
      el('a', {class: 'brand', href: home},
        el('span', {class: 'mark', text: 'W&R', 'aria-hidden': 'true'}),
        el('span', {},
          el('span', {class: 'brand-name', text: 'Wickmere & Rook'}),
          el('span', {class: 'brand-sub', text: section}))),
      who ? el('div', {class: 'who'},
        el('span', {class: 'pill'}, el('span', {class: 'avatar', text: who.initials}), who.label),
        el('button', {class: 'btn ghost small', type: 'button', text: 'Sign out', onclick: onSignOut})) : null];
    node.replaceChildren(...parts.filter(Boolean));
  }

  // "1 tool call", "2 tool calls"
  const count = (n, word) => `${n.toLocaleString('en-GB')} ${word}${n === 1 ? '' : 's'}`;

  // The sign-in card. On a laptop (demo sign-in on) it offers demo accounts. Everywhere it takes a
  // pasted token, which is how a deployed desk is signed in to. onSignedIn(token, identity) runs
  // once the service has confirmed the token belongs to the right kind of person.
  async function signIn({role, root, message, onSignedIn}) {
    const staff = role === 'staff';
    const v = await version();
    const error = el('p', {class: 'error', role: 'alert', hidden: true});
    const fail = text => { error.textContent = text; error.hidden = false; };

    async function finish(token) {
      const who = await api('/v1/me', {token});
      if (!who.ok) return fail(who.data.detail || 'That token did not work.');
      if (who.data.role !== role) {
        return fail(staff ? 'That is a customer token. The returns desk needs a staff token.'
                          : 'That is a staff token. Staff use the returns desk, at /staff.');
      }
      onSignedIn(token, who.data);
    }
    async function demo(body) {
      const r = await api('/dev/token', {method: 'POST', body});
      if (!r.ok) return fail(r.data.detail || 'Demo sign-in is not available here.');
      finish(r.data.token);
    }

    const accounts = staff
      ? [{name: 'alice', note: 'Returns team', initials: 'A', body: {staff: 'alice'}}]
      : [{name: 'Customer 12381', note: 'Norway · 6 orders', initials: '81', body: {customer_id: 12381}},
         {name: 'Customer 12490', note: 'France · 10 orders', initials: '90', body: {customer_id: 12490}}];
    const other = el('input', {class: 'field', 'aria-label': staff ? 'Staff name' : 'Customer number',
                               placeholder: staff ? 'another staff name, e.g. bob' : 'another customer number',
                               inputmode: staff ? 'text' : 'numeric', autocomplete: 'off'});
    const pasted = el('textarea', {class: 'field', 'aria-label': 'Sign-in token', placeholder: 'eyJhbGciOi…',
                                   spellcheck: 'false', autocomplete: 'off'});

    const demoPart = v.demo_sign_in ? [
      el('div', {class: 'section-label', text: 'Demo accounts'}),
      el('div', {class: 'accounts'}, accounts.map(a =>
        el('button', {class: 'account', type: 'button', onclick: () => demo(a.body)},
          el('span', {class: 'avatar', text: a.initials}),
          el('span', {}, el('b', {text: a.name}), el('small', {text: a.note})),
          el('span', {class: 'go', text: '→', 'aria-hidden': 'true'})))),
      el('form', {class: 'row', style: 'margin-top:10px', onsubmit: e => {
        e.preventDefault();
        const value = other.value.trim();
        if (value) demo(staff ? {staff: value.toLowerCase()} : {customer_id: Number(value)});
      }}, other, el('button', {class: 'btn ghost', text: 'Continue'})),
      el('p', {class: 'note'}, 'Demo accounts appear only where the service runs with ',
        el('code', {text: 'PORTER_DEV_LOGIN=true'}), ': a laptop, never a deployment.'),
    ] : [
      el('p', {class: 'note', text: 'This desk signs people in by token. Paste the one you were given.'}),
    ];

    const tokenPart = el('details', {class: 'token', open: !v.demo_sign_in},
      el('summary', {text: 'Have a token? Paste it'}),
      el('form', {onsubmit: e => { e.preventDefault(); const t = pasted.value.trim(); if (t) finish(t); }},
        el('p', {class: 'note'}, 'Made with the server’s secret, e.g. ',
          el('code', {text: staff ? 'python -m porter.auth staff alice' : 'python -m porter.auth customer 12381'})),
        el('div', {style: 'margin-top:8px'}, pasted),
        el('button', {class: 'btn', style: 'margin-top:10px', text: 'Use this token'})));

    root.replaceChildren(el('div', {class: 'card auth'},
      el('h1', {text: staff ? 'Returns desk' : 'Order help'}),
      el('p', {class: 'lead', text: staff
        ? 'Sign in as a member of staff to review the return requests customers have made.'
        : 'Sign in to see your orders and ask Porter, our order assistant, about them.'}),
      message ? el('p', {class: 'message', text: message}) : null,
      ...demoPart, tokenPart, error,
      el('div', {class: 'switch'}, ...(staff
        ? ['Not staff? ', el('a', {href: '/', text: 'Go to the order desk'})]
        : ['Staff? ', el('a', {href: '/staff', text: 'Open the returns desk'})]))));
  }

  return {store, el, uuid, api, money, day, when, count, toast, version, footer, topbar, signIn};
})();
