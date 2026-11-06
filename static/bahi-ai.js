/* Bahi AI advisor — chat UI. Runs on the full page (bahi-perfumes.com/apps/bahi-ai) and,
   with CFG.embed, inside the floating chat window on every store page (theme snippet
   bahi-ai-widget.liquid puts this markup into the page). Both share the conversation
   through localStorage. The window and the snippet talk through 'bahi-ai:*' DOM events. */
(function () {
  'use strict';

  var CFG = window.BAHI_AI || {};
  var root = document.getElementById('bahi-ai');
  if (!root || root.getAttribute('data-ready')) return;
  root.setAttribute('data-ready', '1');

  var STORE_KEY = 'bahi-ai:chat:v1';
  var SID_KEY = 'bahi-ai:sid';
  var PENDING_KEY = 'bahi-ai:pending'; // answer still being written, so another page can pick it up
  var MAX_STORED = 40;     // messages kept in this browser
  var MAX_SEND = 16;       // messages sent to the server per question
  var MAX_CHARS = 800;
  var POLL_MS = 600;
  var MAX_WAIT_MS = 150000; // the server gives up on an answer after ~2 minutes
  var pollUntil = 0;

  var STARTERS = [
    'ساعدني ألاقي عطري المناسب',
    'عايز عطر مناسب للشغل',
    'بدوّر على هدية لحد غالي عليّا',
    'إيه هي العائلات العطرية الرئيسية؟'
  ];

  var products = {};
  (CFG.products || []).forEach(function (p) { products[p.handle] = p; });

  var log = root.querySelector('[data-bahi-log]');
  var form = root.querySelector('[data-bahi-form]');
  var input = root.querySelector('[data-bahi-input]');
  var sendBtn = root.querySelector('[data-bahi-send]');
  var newBtn = root.querySelector('[data-bahi-new]');
  var EMBED = !!CFG.embed;

  // ------------------------------------------------------------------ storage (cookies don't survive the proxy)
  function load(key, fallback) {
    try { var v = window.localStorage.getItem(key); return v ? JSON.parse(v) : fallback; }
    catch (e) { return fallback; }
  }
  function store(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }
  function randomId() {
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  var sid = load(SID_KEY, null);
  if (typeof sid !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(sid)) { sid = randomId(); store(SID_KEY, sid); }

  function loadMessages() {
    var list = load(STORE_KEY, []);
    if (!Array.isArray(list)) return [];
    return list.filter(function (m) {
      return m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string';
    });
  }
  var messages = loadMessages();
  function save() { messages = messages.slice(-MAX_STORED); store(STORE_KEY, messages); }
  function setPending(job) {
    try {
      if (job) window.localStorage.setItem(PENDING_KEY, JSON.stringify({ job: job, t: Date.now() }));
      else window.localStorage.removeItem(PENDING_KEY);
    } catch (e) { /* private mode */ }
  }

  var busy = false;
  var live = null;         // {el, cards} of the answer being written

  // ------------------------------------------------------------------ text rendering
  function esc(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function inline(s) {          // s is already escaped
    return s
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener nofollow">$1</a>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  }
  function markdown(text) {
    var lines = esc(text).split(/\n/);
    var html = [], para = [], list = null;
    function flushPara() { if (para.length) { html.push('<p>' + para.join('<br>') + '</p>'); para = []; } }
    function flushList() { if (list) { html.push('<' + list.tag + '>' + list.items.join('') + '</' + list.tag + '>'); list = null; } }
    lines.forEach(function (raw) {
      var line = raw.replace(/^#{1,6}\s+/, '').trimEnd();
      var ul = line.match(/^\s*[-*•]\s+(.*)$/);
      var ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        flushPara();
        var tag = ul ? 'ul' : 'ol';
        if (!list || list.tag !== tag) { flushList(); list = { tag: tag, items: [] }; }
        list.items.push('<li>' + inline((ul || ol)[1]) + '</li>');
      } else if (!line.trim()) {
        flushPara(); flushList();
      } else {
        flushList();
        para.push(inline(line));
      }
    });
    flushPara(); flushList();
    return html.join('');
  }

  var MARKER = /\[\[\s*(product|choices)\s*:\s*([^\]]*)\]\]/g;

  // Split an answer into text blocks, product handles and quick-reply options.
  function parse(text, streaming) {
    if (streaming) text = text.replace(/\[\[[^\]]*\]?$/, '').replace(/\[$/, '');
    var parts = [], choices = [], last = 0, m;
    MARKER.lastIndex = 0;
    while ((m = MARKER.exec(text))) {
      if (m.index > last) parts.push({ type: 'text', value: text.slice(last, m.index) });
      if (m[1] === 'product') {
        var h = m[2].trim().toLowerCase();
        if (products[h] && products[h].title) parts.push({ type: 'product', value: h });
      } else {
        choices = m[2].split('|').map(function (s) { return s.trim(); })
          .filter(function (s) { return s && s.length <= 60; }).slice(0, 4);
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push({ type: 'text', value: text.slice(last) });
    return { parts: parts, choices: choices };
  }

  // ------------------------------------------------------------------ product card
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function productCard(handle) {
    var p = products[handle];
    var card = el('div', 'bahi-ai__card');
    var media = el('a', 'bahi-ai__card-media');
    media.href = p.url;
    if (p.image) {
      var img = el('img');
      img.src = p.image; img.alt = p.title; img.loading = 'lazy'; img.width = 200; img.height = 200;
      media.appendChild(img);
    }
    if (CFG.offerPct > 0) media.appendChild(el('span', 'bahi-ai__badge', 'خصم ' + CFG.offerPct + '%'));
    card.appendChild(media);

    var body = el('div', 'bahi-ai__card-body');
    var name = el('a', 'bahi-ai__card-name', p.title);
    name.href = p.url;
    body.appendChild(name);
    if (p.family) body.appendChild(el('p', 'bahi-ai__card-family', p.family));

    var price = el('p', 'bahi-ai__card-price');
    body.appendChild(price);

    var variants = (p.variants || []);
    var chosen = variants.filter(function (v) { return v.available; })[0] || variants[0];
    var sizes = el('div', 'bahi-ai__sizes');
    sizes.setAttribute('role', 'group');
    sizes.setAttribute('aria-label', 'الحجم');
    var actions = el('div', 'bahi-ai__card-actions');
    var add = el('button', 'bahi-ai__add', 'أضف للسلة');
    add.type = 'button';
    var details = el('a', 'bahi-ai__details', 'التفاصيل');
    details.href = p.url;

    function paint() {
      price.textContent = '';
      if (!chosen) return;
      if (CFG.offerPct > 0 && chosen.sale && chosen.sale !== chosen.price) {
        price.appendChild(el('s', 'bahi-ai__was', chosen.price));
        price.appendChild(document.createTextNode(' ' + chosen.sale));
      } else {
        price.appendChild(document.createTextNode(chosen.price));
      }
      Array.prototype.forEach.call(sizes.children, function (b) {
        b.setAttribute('aria-pressed', String(Number(b.getAttribute('data-id')) === chosen.id));
      });
      add.disabled = !chosen.available;
      add.textContent = chosen.available ? 'أضف للسلة' : 'غير متوفر حاليًا';
      add.removeAttribute('data-done');
    }
    variants.forEach(function (v) {
      var b = el('button', 'bahi-ai__size', v.title);
      b.type = 'button';
      b.setAttribute('data-id', v.id);
      if (!v.available) b.classList.add('is-out');
      b.addEventListener('click', function () { chosen = v; paint(); });
      sizes.appendChild(b);
    });
    if (variants.length > 1) body.appendChild(sizes);

    add.addEventListener('click', function () {
      if (!chosen) return;
      if (add.getAttribute('data-done')) { go(CFG.cartUrl || '/cart'); return; }
      if (CFG.dev) { add.textContent = 'متاح على المتجر الحقيقي فقط'; return; }
      add.disabled = true;
      add.textContent = 'جارٍ الإضافة…';
      fetch((CFG.cartAddUrl || '/cart/add') + '.js', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ items: [{ id: chosen.id, quantity: 1 }] })
      }).then(function (r) {
        if (!r.ok) throw new Error('cart ' + r.status);
        return fetch((CFG.cartUrl || '/cart') + '.js', { headers: { 'Accept': 'application/json' } });
      }).then(function (r) { return r.json(); }).then(function (cart) {
        add.disabled = false;
        add.setAttribute('data-done', '1');
        add.textContent = '✓ أُضيف — إتمام الطلب';
        cartChanged(cart, chosen.id);
      }).catch(function () {
        add.disabled = false;
        add.textContent = 'حاول تاني';
      });
    });

    actions.appendChild(add);
    actions.appendChild(details);
    body.appendChild(actions);
    card.appendChild(body);
    paint();
    return card;
  }

  // ------------------------------------------------------------------ store page integration
  // Tell the floating window's snippet (close, leaving the page, ...).
  function tellHost(type) { document.dispatchEvent(new CustomEvent('bahi-ai:' + type)); }

  function go(url) {
    if (EMBED) tellHost('leave');
    window.location.href = url;
  }

  // Tell the theme the cart changed so its cart count / drawer refresh.
  function cartChanged(cart, variantId) { themeCartEvent(document, cart, variantId); }
  function themeCartEvent(doc, cart, variantId) {
    // Horizon listens for Shopify's standard cart event (importmap: @shopify/events).
    import('@shopify/events').then(function (m) {
      var E = m.CartLinesUpdateEvent;
      var d = E.createPromise();
      doc.dispatchEvent(new E({ action: 'add', context: 'product',
        lines: [{ merchandiseId: String(variantId), quantity: 1 }], promise: d.promise }));
      d.resolve({ cart: E.createCartFromAjaxResponse(cart),
        detail: { items: cart.items, itemCount: cart.item_count, source: 'bahi-ai', sourceId: 'bahi-ai', didError: false } });
    }).catch(function () {
      doc.dispatchEvent(new CustomEvent('cart:update', { bubbles: true,
        detail: { resource: cart, sourceId: 'bahi-ai', data: { itemCount: cart.item_count, source: 'bahi-ai' } } }));
    });
  }

  // ------------------------------------------------------------------ messages
  function nearBottom() { return log.scrollHeight - log.scrollTop - log.clientHeight < 120; }
  function toBottom() { log.scrollTop = log.scrollHeight; }

  function fillAnswer(container, text, streaming, cards) {
    var parsed = parse(text, streaming);
    container.textContent = '';
    var seen = {};
    parsed.parts.forEach(function (part) {
      if (part.type === 'text') {
        if (!part.value.trim()) return;
        var t = el('div', 'bahi-ai__text');
        t.innerHTML = markdown(part.value.trim());
        container.appendChild(t);
      } else if (!seen[part.value]) {
        seen[part.value] = true;
        // Reuse a card already built while streaming so its photo doesn't reload.
        var card = cards && cards[part.value];
        if (!card) { card = productCard(part.value); if (cards) cards[part.value] = card; }
        container.appendChild(card);
      }
    });
    return parsed.choices;
  }

  function bubble(role) {
    var row = el('div', 'bahi-ai__msg bahi-ai__msg--' + role);
    var b = el('div', 'bahi-ai__bubble');
    row.appendChild(b);
    log.appendChild(row);
    return b;
  }

  function choicesRow(options) {
    var wrap = el('div', 'bahi-ai__chips');
    options.forEach(function (o) {
      var c = el('button', 'bahi-ai__chip', o);
      c.type = 'button';
      c.addEventListener('click', function () { send(o); });
      wrap.appendChild(c);
    });
    log.appendChild(wrap);
  }

  function welcome() {
    var w = el('div', 'bahi-ai__welcome');
    var hi = CFG.customerName ? 'أهلاً ' + CFG.customerName + ' 👋' : 'أهلاً بيك 👋';
    w.appendChild(el('p', 'bahi-ai__hello', hi));
    w.appendChild(el('p', 'bahi-ai__intro',
      'أنا باهي. اسألني عن أي حاجة في العطور: ذوقك، النوتات، الفرق بين العطور، أو إيه العطر من باهي اللي يناسبك.'));
    log.appendChild(w);
    choicesRow(STARTERS);
  }

  function render() {
    log.textContent = '';
    live = null;
    if (!messages.length) welcome();
    var lastChoices = [];
    messages.forEach(function (m, i) {
      var b = bubble(m.role);
      if (m.role === 'user') { b.textContent = m.content; return; }
      var ch = fillAnswer(b, m.content, false, null);
      if (i === messages.length - 1) lastChoices = ch;
    });
    if (!busy && lastChoices.length) choicesRow(lastChoices);
    newBtn.hidden = !messages.length;
    toBottom();
  }

  function startLive() {
    var b = bubble('assistant');
    b.classList.add('is-live');
    live = { el: b, cards: {} };
    paintLive('', '');
    toBottom();
  }

  function paintLive(text, activity) {
    if (!live) return;
    var stick = nearBottom();
    fillAnswer(live.el, text, true, live.cards);
    if (!text.trim() || activity) {
      var t = el('div', 'bahi-ai__typing');
      t.appendChild(el('span', 'bahi-ai__dots'));
      if (activity) t.appendChild(el('span', 'bahi-ai__activity', activity));
      live.el.appendChild(t);
    }
    if (stick) toBottom();
  }

  function setBusy(v) {
    busy = v;
    sendBtn.disabled = v;
    root.classList.toggle('is-busy', v);
  }

  function notice(text) {
    var n = el('div', 'bahi-ai__notice', text);
    log.appendChild(n);
    toBottom();
  }

  // ------------------------------------------------------------------ server
  function api(path) { return (CFG.apiBase || '') + path; }

  function send(text) {
    text = (text || '').trim();
    if (!text || busy) return;
    if (text.length > MAX_CHARS) text = text.slice(0, MAX_CHARS);
    messages.push({ role: 'user', content: text });
    save();
    input.value = '';
    autosize();
    setBusy(true);
    render();
    startLive();

    fetch(api('/api/chat'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ messages: messages.slice(-MAX_SEND), sid: sid, name: CFG.customerName || '' })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (!res.ok || !res.d.job) throw { userMessage: res.d.error };
      pollUntil = Date.now() + MAX_WAIT_MS;
      setPending(res.d.job);
      poll(res.d.job, 0);
    }).catch(function (err) { fail(err && err.userMessage, text); });
  }

  function poll(id, failures) {
    fetch(api('/api/chat/' + encodeURIComponent(id)), { headers: { 'Accept': 'application/json' }, cache: 'no-store' })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (!res.ok) throw { userMessage: res.d.error, final: true };
        var d = res.d;
        if (d.status === 'running') {
          if (Date.now() > pollUntil) throw { final: true };  // never spin for ever
          paintLive(d.text || '', d.activity || '');
          setTimeout(function () { poll(id, 0); }, POLL_MS);
        } else {
          finish(d.text || '');
        }
      })
      .catch(function (err) {
        if (!(err && err.final) && failures < 4) {
          setTimeout(function () { poll(id, failures + 1); }, POLL_MS * (failures + 2));
        } else {
          fail(err && err.userMessage, null);
        }
      });
  }

  function finish(text) {
    setPending(null);
    messages = loadMessages();  // another page may have finished this answer already
    var last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant' || last.content !== text) messages.push({ role: 'assistant', content: text });
    save();
    setBusy(false);
    render();
    input.focus({ preventScroll: true });
  }

  // The question didn't go through: take it back out of the history and offer it again.
  function fail(message, text) {
    setPending(null);
    if (text != null && messages.length && messages[messages.length - 1].role === 'user') {
      messages.pop();
      save();
      input.value = text;
      autosize();
    }
    setBusy(false);
    render();
    notice(message || 'حصلت مشكلة في الاتصال 🙏 اتأكد من النت وجرّب تاني.');
  }

  // ------------------------------------------------------------------ composer
  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  }
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
  });
  form.addEventListener('submit', function (e) { e.preventDefault(); send(input.value); });
  newBtn.addEventListener('click', function () {
    if (busy) return;
    messages = [];
    save();
    render();
    input.focus();
  });

  // "اسأل باهي" buttons in the guide under the chat (full page only): back up to the chat
  // and ask that question (or leave it in the box if an answer is still being written).
  root.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-bahi-ask]');
    if (!b) return;
    var q = b.getAttribute('data-bahi-ask');
    var panel = root.querySelector('.bahi-ai__panel');
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (busy) { input.value = q; autosize(); return; }
    send(q);
  });

  // An answer started on another page (or before the customer moved to another page) is
  // still being written on the server: keep showing it here.
  function resumePending() {
    var p = load(PENDING_KEY, null);
    var last = messages[messages.length - 1];
    if (!p || typeof p.job !== 'string' || !(Date.now() - p.t < MAX_WAIT_MS) || !last || last.role !== 'user') return false;
    setBusy(true);
    render();
    startLive();
    pollUntil = p.t + MAX_WAIT_MS;
    poll(p.job, 0);
    return true;
  }

  // The floating window and the full page (other tabs too) share one conversation.
  window.addEventListener('storage', function (e) {
    if (busy || (e.key !== STORE_KEY && e.key !== PENDING_KEY && e.key !== null)) return;
    messages = loadMessages();
    if (!resumePending()) render();
  });

  if (EMBED) {
    var closeBtn = root.querySelector('[data-bahi-close]');
    var expand = root.querySelector('[data-bahi-expand]');
    if (closeBtn) closeBtn.addEventListener('click', function () { tellHost('close'); });
    if (expand) {
      expand.href = CFG.pageUrl || '/apps/bahi-ai';
      expand.addEventListener('click', function () { tellHost('leave'); });
    }
    // The snippet says when the window is shown, so the cursor lands in the box (not on
    // phones, where that would pop the keyboard over the welcome message).
    document.addEventListener('bahi-ai:shown', function () {
      if (!matchMedia('(pointer: coarse)').matches) input.focus({ preventScroll: true });
    });
  }

  if (!resumePending()) render();
  if (EMBED) tellHost('ready');
})();
