/* ==========================================================================
   app.js - the small amount of behaviour the lighter ZYRO needs (14 Nov 2026)

   1. the command palette  - Ctrl/Cmd + K: type three letters, press Enter,
                             you are on the screen you wanted. No scrolling.
   2. the progress line    - a thin line across the top while a page loads
   3. the page transition  - the page lifts and fades instead of blinking
   4. back to the top      - a round button that appears once you have scrolled
   5. two-key shortcuts    - g then d / i / p / c / v / r / s
   6. small niceties       - "/" jumps to the page's search box, Escape closes

   Nothing here is required for a screen to work: no JavaScript means every
   link, every form and every table behaves exactly as before.
   ========================================================================== */
(function () {
  'use strict';

  // Not every embedded browser offers matchMedia; ask politely and fall back.
  function media(query) {
    try { return window.matchMedia && window.matchMedia(query).matches; } catch (e) { return false; }
  }

  var reduceMotion = media('(prefers-reduced-motion: reduce)');
  var isTouch = media('(pointer: coarse)');

  /* ---------------------------------------------------------------- 1. palette */

  var palette = document.getElementById('cmdk');
  var backdrop = document.getElementById('cmdk-backdrop');
  var input = document.getElementById('cmdk-input');
  var list = document.getElementById('cmdk-list');
  var nav = Array.isArray(window.ZYRO_NAV) ? window.ZYRO_NAV : [];

  // Screens you can jump to are only half the story; these are the things you
  // do most often, so "new inv" + Enter lands on the blank invoice form.
  var ACTIONS = Array.isArray(window.ZYRO_ACTIONS) && window.ZYRO_ACTIONS.length ? window.ZYRO_ACTIONS : [
    { label: 'New sales invoice', url: '/sales/invoices/new', group: 'Create', icon: 'invoice' },
    { label: 'New customer receipt', url: '/customers/receipts/new', group: 'Create', icon: 'receipt' },
    { label: 'New vendor bill', url: '/vendors/bills/new', group: 'Create', icon: 'bill' },
    { label: 'New vendor payment', url: '/vendors/payments/new', group: 'Create', icon: 'payment' },
    { label: 'New journal entry', url: '/accounting/journal-entries/new', group: 'Create', icon: 'journal' },
    { label: 'New customer', url: '/customers/new', group: 'Create', icon: 'customers' },
    { label: 'New vendor', url: '/vendors/new', group: 'Create', icon: 'vendor' },
    { label: 'Trial balance', url: '/reports/trial-balance', group: 'Create', icon: 'trial' }
  ];
  var RECENT_KEY = 'zyro.recent';
  var selected = 0;
  var visible = [];

  function recents() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch (e) { return []; }
  }
  function remember(item) {
    try {
      var kept = recents().filter(function (r) { return r.url !== item.url; });
      kept.unshift({ label: item.label, url: item.url, icon: item.icon });
      localStorage.setItem(RECENT_KEY, JSON.stringify(kept.slice(0, 5)));
    } catch (e) { /* private mode: no recents, nothing breaks */ }
  }

  function score(text, query) {
    // a forgiving match: the letters of the query appear in order
    text = text.toLowerCase();
    query = query.toLowerCase();
    if (!query) return 1;
    var ti = 0, first = -1, hits = 0;
    for (var qi = 0; qi < query.length; qi++) {
      var c = query[qi];
      if (c === ' ') continue;
      var found = text.indexOf(c, ti);
      if (found === -1) return 0;
      if (first === -1) first = found;
      hits++;
      ti = found + 1;
    }
    // earlier and tighter matches win
    return 1000 - first * 3 - (ti - first - hits) * 2;
  }

  function render(query) {
    var groups = [];
    if (!query) {
      var recent = recents();
      var recentItems = recent.map(function (r) {
        return nav.filter(function (n) { return n.url === r.url; })[0] || r;
      }).filter(Boolean);
      if (recentItems.length) groups.push({ title: 'Recent', items: recentItems });
    }
    var pool = nav.concat(ACTIONS);
    var matched = pool
      .map(function (n) { return { item: n, s: score(n.label + ' ' + (n.group || ''), query) }; })
      .filter(function (r) { return r.s > 0; })
      .sort(function (a, b) { return b.s - a.s; })
      .map(function (r) { return r.item; });

    if (query) {
      if (matched.length) groups.push({ title: 'Jump to', items: matched });
    } else {
      var byGroup = {};
      nav.forEach(function (n) {
        var g = n.group || 'Zyro';
        (byGroup[g] = byGroup[g] || []).push(n);
      });
      Object.keys(byGroup).forEach(function (g) { groups.push({ title: g, items: byGroup[g] }); });
      groups.push({ title: 'Create', items: ACTIONS });
    }

    list.innerHTML = '';
    visible = [];
    selected = 0;

    if (!groups.length) {
      var empty = document.createElement('div');
      empty.className = 'cmdk-empty';
      empty.textContent = 'Nothing matches "' + query + '". Try "invoice", "customer" or "journal".';
      list.appendChild(empty);
      return;
    }

    groups.forEach(function (group) {
      var title = document.createElement('div');
      title.className = 'cmdk-group-title';
      title.textContent = group.title;
      list.appendChild(title);

      group.items.forEach(function (item) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'cmdk-item';
        button.innerHTML =
          '<svg class="ico" aria-hidden="true"><use href="#i-' + (item.icon || 'chevron-right') + '"/></svg>' +
          '<span></span>' +
          '<span class="cmdk-hint">' + (item.group || '') + '</span>';
        button.querySelector('span').textContent = item.label;
        button.addEventListener('click', function () { go(item); });
        button.addEventListener('mousemove', function () { select(visible.indexOf(item)); });
        list.appendChild(button);
        visible.push(item);
      });
    });
  }

  function select(index) {
    var buttons = list.querySelectorAll('.cmdk-item');
    if (!buttons.length) return;
    selected = Math.max(0, Math.min(index, buttons.length - 1));
    for (var i = 0; i < buttons.length; i++) buttons[i].classList.toggle('sel', i === selected);
    var active = buttons[selected];
    if (active) {
      var box = active.getBoundingClientRect();
      var panel = list.getBoundingClientRect();
      if (box.top < panel.top) list.scrollTop -= (panel.top - box.top) + 8;
      if (box.bottom > panel.bottom) list.scrollTop += (box.bottom - panel.bottom) + 8;
      active.dataset.index = selected;
    }
  }

  function go(item) {
    if (!item) return;
    remember(item);
    closePalette();
    navigate(item.url);
  }

  function openPalette() {
    if (!palette) return;
    palette.classList.add('open');
    backdrop.classList.add('open');
    palette.setAttribute('aria-hidden', 'false');
    input.value = '';
    render('');
    setTimeout(function () { input.focus(); }, 20);
  }

  function closePalette() {
    if (!palette) return;
    palette.classList.remove('open');
    backdrop.classList.remove('open');
    palette.setAttribute('aria-hidden', 'true');
    input.blur();
  }

  if (palette) {
    input.addEventListener('input', function () { render(input.value.trim()); });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown') { event.preventDefault(); select(selected + 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); select(selected - 1); }
      else if (event.key === 'Enter') { event.preventDefault(); go(visible[selected]); }
      else if (event.key === 'Escape') { event.preventDefault(); closePalette(); }
      else if (event.key === 'Home') { event.preventDefault(); select(0); }
      else if (event.key === 'End') { event.preventDefault(); select(visible.length - 1); }
    });
    backdrop.addEventListener('click', closePalette);
    document.querySelectorAll('[data-cmdk-open]').forEach(function (button) {
      button.addEventListener('click', function (event) { event.preventDefault(); openPalette(); });
    });
  }

  /* --------------------------------------------------------- 2/3. progress + fade */

  var progress = document.getElementById('lp-progress');
  var leaving = false;

  function startProgress() {
    if (!progress) return;
    progress.classList.remove('done');
    progress.classList.add('on');
    progress.style.width = '18%';
    setTimeout(function () { if (progress.classList.contains('on')) progress.style.width = '62%'; }, 180);
    setTimeout(function () { if (progress.classList.contains('on')) progress.style.width = '86%'; }, 900);
  }

  function navigate(url) {
    if (reduceMotion) { window.location.href = url; return; }
    leaving = true;
    document.body.classList.add('is-leaving');
    startProgress();
    setTimeout(function () { window.location.href = url; }, 130);
  }

  window.addEventListener('beforeunload', function () {
    if (progress) { progress.classList.add('done'); }
  });
  window.addEventListener('pageshow', function () {
    document.body.classList.remove('is-leaving');
    if (progress) { progress.classList.remove('on'); progress.style.width = '0'; }
  });

  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[href]');
    if (!link) return;
    var href = link.getAttribute('href');
    if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) return;
    if (link.target === '_blank' || link.hasAttribute('download')) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    if (href.indexOf(window.location.origin) === 0) href = href.slice(window.location.origin.length);
    if (href.charAt(0) !== '/') return;                    // external: leave it alone
    if (!reduceMotion) {
      leaving = true;
      document.body.classList.add('is-leaving');
    }
    startProgress();
  }, true);

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (form && form.hasAttribute('data-no-progress')) return;
    startProgress();
    if (!reduceMotion) document.body.classList.add('is-leaving');
  }, true);

  document.addEventListener('DOMContentLoaded', function () {
    if (progress) { progress.classList.add('done'); }
  });

  /* ------------------------------------------------------------- 4. back to top */

  var backtop = document.getElementById('lp-backtop');
  if (backtop) {
    var onScroll = function () {
      var y = window.scrollY || document.documentElement.scrollTop;
      backtop.classList.toggle('on', y > 420);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    backtop.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
    });
  }

  /* --------------------------------------------------------------- 5/6. keys */

  var pendingG = 0;

  document.addEventListener('keydown', function (event) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) ||
                 document.activeElement.isContentEditable;

    // Ctrl/Cmd + K from anywhere
    if ((event.metaKey || event.ctrlKey) && (event.key === 'k' || event.key === 'K')) {
      event.preventDefault();
      if (palette && palette.classList.contains('open')) closePalette(); else openPalette();
      return;
    }

    if (event.key === 'Escape') {
      if (palette && palette.classList.contains('open')) { closePalette(); return; }
      if (typing) { document.activeElement.blur(); }
      return;
    }

    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

    // "/" - jump to this page's search box, wherever it is
    if (event.key === '/') {
      var search = document.querySelector('input[type="search"], input[name="q"], .lp-list-search input');
      if (search) { event.preventDefault(); search.focus(); search.select && search.select(); }
      return;
    }

    // g then a letter
    if (event.key === 'g' || event.key === 'G') { pendingG = Date.now(); return; }
    if (!pendingG || Date.now() - pendingG > 1100) return;
    var shortcuts = { d: '#i-dashboard', i: '#i-invoice', p: '#i-product', c: '#i-customers', v: '#i-vendor', r: '#i-trial', s: '#i-receipt' };
    var letter = event.key.toLowerCase();
    if (!shortcuts[letter]) return;
    var target = nav.filter(function (n) { return n.icon === shortcuts[letter].replace('#i-', ''); })[0];
    if (target) { event.preventDefault(); navigate(target.url); }
    pendingG = 0;
  });
})();

/* ---- Tabs on a document ------------------------------------------------
   Every panel is in the page already; this only decides which one is shown.
   `[data-lp-tabs]` is a strip of .lp-tab buttons followed by .lp-tab-panel
   panels, in the same order. No JavaScript means every panel stays visible,
   which is the right way round to fail. */
(function () {
  document.querySelectorAll('[data-lp-tabs]').forEach(function (tabs) {
    var buttons = Array.prototype.slice.call(tabs.querySelectorAll('.lp-tab'));
    var panels = Array.prototype.slice.call(tabs.querySelectorAll('.lp-tab-panel'));
    if (!buttons.length || buttons.length !== panels.length) return;

    function show(index) {
      buttons.forEach(function (button, i) { button.classList.toggle('on', i === index); });
      panels.forEach(function (panel, i) { panel.hidden = i !== index; });
      buttons.forEach(function (button, i) {
        button.setAttribute('aria-selected', i === index ? 'true' : 'false');
      });
    }

    buttons.forEach(function (button, i) {
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.addEventListener('click', function () { show(i); });
    });
    panels.forEach(function (panel) { panel.setAttribute('role', 'tabpanel'); });

    // Which one starts open: the one the page names, else the first.
    var start = buttons.findIndex(function (button) { return button.classList.contains('on'); });
    show(start === -1 ? 0 : start);
  });
})();
