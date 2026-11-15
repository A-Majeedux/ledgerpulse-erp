/* ==========================================================================
   charts.js - modern, animated charts with no outside library (14 Nov 2026)
   --------------------------------------------------------------------------
   A chart on a page is written as:

     <div class="zyro-chart" data-chart="area" data-unit="PKR" data-height="210">
       <script type="application/json">[["2026-06", 125000.5], ["2026-07", 98000]]</script>
     </div>

   and this file draws it. Four shapes:

     area   one line with a soft fill underneath  - trends over time
     bars   columns, red when the value is below zero - profit, comparisons
     donut  a ring split into parts               - where the money sits
     hbar   horizontal ranking bars               - top customers / vendors

   Why hand-written: the program already asks the browser for nothing but the
   page itself. No CDN, no library to keep up to date, nothing to fail when
   the internet is slow - and the charts still animate, still answer the
   pointer with a tooltip, and still redraw when the window is resized.
   ========================================================================== */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var reduceMotion = (function () {
    try { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (e) { return false; }
  })();
  var tip = null;

  /* ------------------------------------------------------------- helpers */

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    }
    return node;
  }

  function number(value) {
    var n = typeof value === 'number' ? value : parseFloat(value);
    return isFinite(n) ? n : 0;
  }

  function compact(value) {
    var n = Math.abs(value);
    var sign = value < 0 ? '-' : '';
    if (n >= 1e9) return sign + (n / 1e9).toFixed(n >= 1e10 ? 0 : 1).replace(/\.0$/, '') + 'B';
    if (n >= 1e6) return sign + (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (n >= 1e3) return sign + (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k';
    return sign + (Math.round(n * 100) / 100).toString();
  }

  function full(value) {
    return number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function label(value) {
    // 2026-06 -> Jun 26, 2026-07-04 -> 04 Jul, "2026-W31" -> W31 26
    var text = String(value || '');
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var m = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return m[3] + ' ' + months[parseInt(m[2], 10) - 1];
    m = text.match(/^(\d{4})-(\d{2})$/);
    if (m) return months[parseInt(m[2], 10) - 1] + ' ’' + m[1].slice(2);
    m = text.match(/^(\d{4})-W(\d{2})$/);
    if (m) return 'W' + m[2] + ' ’' + m[1].slice(2);
    return text;
  }

  function ensureTip() {
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'chart-tip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  function showTip(html, x, y) {
    var node = ensureTip();
    node.innerHTML = html;
    node.style.left = x + 'px';
    node.style.top = y + 'px';
    node.classList.add('on');
  }

  function hideTip() {
    if (tip) tip.classList.remove('on');
  }

  function unitOf(root) {
    return root.getAttribute('data-unit') || '';
  }

  function money(root, value, short) {
    var unit = unitOf(root);
    var text = short ? compact(value) : full(value);
    return unit ? unit + ' ' + text : text;
  }

  function readPoints(root) {
    var holder = root.querySelector('script[type="application/json"]');
    if (!holder) return [];
    try {
      var raw = JSON.parse(holder.textContent.trim() || '[]');
      return raw.map(function (row) {
        if (Array.isArray(row)) return { key: row[0], value: number(row[1]) };
        return { key: row.label || row.key, value: number(row.value) };
      });
    } catch (e) {
      return [];
    }
  }

  function empty(root) {
    root.innerHTML = '<div class="chart-empty">Nothing has been entered for this chart yet.</div>';
  }

  function defs(uid, negative) {
    return '' +
      '<defs>' +
      '<linearGradient id="' + uid + '-area" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="#0EA5A0" stop-opacity="0.26"/>' +
        '<stop offset="100%" stop-color="#0EA5A0" stop-opacity="0"/>' +
      '</linearGradient>' +
      '<linearGradient id="' + uid + '-bar" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="#19C2BA"/><stop offset="100%" stop-color="#0B8F8A"/>' +
      '</linearGradient>' +
      '<linearGradient id="' + uid + '-barneg" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="#F58A72"/><stop offset="100%" stop-color="#C0392B"/>' +
      '</linearGradient>' +
      '<linearGradient id="' + uid + '-hbar" x1="0" y1="0" x2="1" y2="0">' +
        '<stop offset="0%" stop-color="#12B0A9"/><stop offset="100%" stop-color="#7BE0D8"/>' +
      '</linearGradient>' +
      '</defs>';
  }

  /* ---------------------------------------------------------------- area */

  function drawArea(root, points) {
    // One period only: two identical values make a steady line, which reads
    // much better than an invisible single dot.
    if (points.length === 1) {
      points = [points[0], { key: '', value: points[0].value }];
    }
    var width = 680;
    var height = number(root.getAttribute('data-height')) || 210;
    var padL = 42, padR = 10, padT = 14, padB = 26;
    var uid = 'zy' + Math.random().toString(36).slice(2, 8);

    var values = points.map(function (p) { return p.value; });
    var max = Math.max.apply(null, values);
    var min = Math.min.apply(null, values);
    var low = min < 0 ? min : 0;
    if (max === low) max = low + 1;
    var span = max - low;

    var innerW = width - padL - padR;
    var innerH = height - padT - padB;
    var step = points.length > 1 ? innerW / (points.length - 1) : 0;

    function x(i) { return padL + (points.length > 1 ? i * step : innerW / 2); }
    function y(v) { return padT + innerH - ((v - low) / span) * innerH; }

    var svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="trend chart">' + defs(uid);

    // grid + value axis
    for (var g = 0; g <= 3; g++) {
      var gy = padT + (innerH / 3) * g;
      var gv = max - (span / 3) * g;
      svg += '<line class="grid-line" x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (width - padR) + '" y2="' + gy.toFixed(1) + '"/>';
      svg += '<text class="axis-label" x="' + (padL - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end">' + compact(gv) + '</text>';
    }

    // area + line
    var line = points.map(function (p, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p.value).toFixed(1); }).join(' ');
    var areaPath = 'M' + x(0).toFixed(1) + ' ' + y(low).toFixed(1) + ' ' +
      points.map(function (p, i) { return 'L' + x(i).toFixed(1) + ' ' + y(p.value).toFixed(1); }).join(' ') +
      ' L' + x(points.length - 1).toFixed(1) + ' ' + y(low).toFixed(1) + ' Z';

    svg += '<path d="' + areaPath + '" fill="url(#' + uid + '-area)" opacity="' + (reduceMotion ? 1 : 0) + '" class="area-shape"/>';
    svg += '<path class="line-path" d="' + line + '"/>';

    // labels (never more than 6, so they can breathe)
    var every = Math.ceil(points.length / 6);
    points.forEach(function (p, i) {
      if (!p.key) return;
      if (i % every !== 0 && i !== points.length - 1) return;
      svg += '<text class="axis-label" x="' + x(i).toFixed(1) + '" y="' + (height - 8) + '" text-anchor="middle">' + label(p.key) + '</text>';
    });

    // dots, invisible until hovered
    points.forEach(function (p, i) {
      svg += '<circle class="dot" data-i="' + i + '" cx="' + x(i).toFixed(1) + '" cy="' + y(p.value).toFixed(1) + '" r="4"/>';
    });

    // one tall hit area per point
    points.forEach(function (p, i) {
      var half = points.length > 1 ? step / 2 : innerW / 2;
      svg += '<rect class="hit" data-i="' + i + '" x="' + (x(i) - half).toFixed(1) + '" y="' + padT + '" width="' + Math.max(half * 2, 6).toFixed(1) + '" height="' + innerH + '"/>';
    });

    svg += '</svg>';
    root.innerHTML = svg;

    // the line draws itself in
    var path = root.querySelector('.line-path');
    if (path && !reduceMotion && typeof path.getTotalLength === 'function') {
      var len = 0;
      try { len = path.getTotalLength(); } catch (e) { len = 0; }
      if (!len) { len = 0; }
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;
      path.style.transition = 'stroke-dashoffset 1.05s cubic-bezier(0.16, 1, 0.3, 1)';
      requestAnimationFrame(function () { path.style.strokeDashoffset = 0; });
      var shape = root.querySelector('.area-shape');
      if (shape) {
        shape.style.transition = 'opacity .8s ease .25s';
        requestAnimationFrame(function () { shape.style.opacity = 1; });
      }
    }

    // tooltips
    root.querySelectorAll('.hit').forEach(function (hit) {
      hit.addEventListener('mousemove', function (event) {
        var i = parseInt(hit.getAttribute('data-i'), 10);
        var p = points[i];
        root.querySelectorAll('.dot').forEach(function (dot) {
          dot.style.opacity = parseInt(dot.getAttribute('data-i'), 10) === i ? 1 : 0;
        });
        showTip('<span class="tip-key">' + label(p.key) + '</span> &nbsp;<span class="tip-val">' + money(root, p.value) + '</span>',
          event.clientX, event.clientY);
      });
      hit.addEventListener('mouseleave', function () {
        hideTip();
        root.querySelectorAll('.dot').forEach(function (dot) { dot.style.opacity = 0; });
      });
    });
  }

  /* ---------------------------------------------------------------- bars */

  function drawBars(root, points) {
    var width = 680;
    var height = number(root.getAttribute('data-height')) || 210;
    var padL = 44, padR = 10, padT = 14, padB = 28;
    var uid = 'zy' + Math.random().toString(36).slice(2, 8);

    var values = points.map(function (p) { return p.value; });
    var max = Math.max.apply(null, values.concat([0]));
    var min = Math.min.apply(null, values.concat([0]));
    if (max === min) max = min + 1;
    var span = max - min;

    var innerW = width - padL - padR;
    var innerH = height - padT - padB;
    var slot = innerW / points.length;
    var barW = Math.min(46, slot * 0.56);

    function y(v) { return padT + innerH - ((v - min) / span) * innerH; }

    var svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="bar chart">' + defs(uid);

    for (var g = 0; g <= 3; g++) {
      var gy = padT + (innerH / 3) * g;
      var gv = max - (span / 3) * g;
      svg += '<line class="grid-line" x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (width - padR) + '" y2="' + gy.toFixed(1) + '"/>';
      svg += '<text class="axis-label" x="' + (padL - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end">' + compact(gv) + '</text>';
    }

    var zeroY = y(0);
    svg += '<line x1="' + padL + '" y1="' + zeroY.toFixed(1) + '" x2="' + (width - padR) + '" y2="' + zeroY.toFixed(1) + '" stroke="#DCE8ED" stroke-width="1"/>';

    var every = Math.ceil(points.length / 7);
    points.forEach(function (p, i) {
      var cx = padL + slot * i + slot / 2;
      var top = p.value >= 0 ? y(p.value) : zeroY;
      var barH = Math.max(Math.abs(y(p.value) - zeroY), 2);
      svg += '<rect class="bar' + (p.value < 0 ? ' neg' : '') + '" data-i="' + i + '"' +
        ' x="' + (cx - barW / 2).toFixed(1) + '" y="' + top.toFixed(1) + '"' +
        ' width="' + barW.toFixed(1) + '" height="' + barH.toFixed(1) + '"' +
        ' fill="url(#' + uid + (p.value < 0 ? '-barneg' : '-bar') + ')"' +
        ' style="transform-origin: 0px ' + zeroY.toFixed(1) + 'px; transform: scaleY(' + (reduceMotion ? 1 : 0) + '); transition: transform .7s cubic-bezier(0.16, 1, 0.3, 1) ' + (i * 0.05).toFixed(2) + 's"/>';
      if (i % every === 0 || i === points.length - 1) {
        svg += '<text class="axis-label" x="' + cx.toFixed(1) + '" y="' + (height - 8) + '" text-anchor="middle">' + label(p.key) + '</text>';
      }
    });

    svg += '</svg>';
    root.innerHTML = svg;

    requestAnimationFrame(function () {
      root.querySelectorAll('.bar').forEach(function (bar) { bar.style.transform = 'scaleY(1)'; });
    });

    root.querySelectorAll('.bar').forEach(function (bar) {
      bar.addEventListener('mousemove', function (event) {
        var p = points[parseInt(bar.getAttribute('data-i'), 10)];
        showTip('<span class="tip-key">' + label(p.key) + '</span> &nbsp;<span class="tip-val">' + money(root, p.value) + '</span>',
          event.clientX, event.clientY);
      });
      bar.addEventListener('mouseleave', hideTip);
    });
  }

  /* --------------------------------------------------------------- donut */

  function drawDonut(root, points) {
    var size = 240;
    var cx = 120, cy = 112, r = 84, thickness = 24;
    var uid = 'zy' + Math.random().toString(36).slice(2, 8);
    var total = points.reduce(function (sum, p) { return sum + Math.max(p.value, 0); }, 0);
    if (total <= 0) { empty(root); return; }

    var palette = ['#0EA5A0', '#38BDF8', '#8B7CF6', '#F59E0B', '#EF7B63', '#22C55E', '#64748B'];
    var circumference = 2 * Math.PI * r;
    var offset = 0;

    var svg = '<svg viewBox="0 0 ' + size + ' ' + (size + 6) + '" role="img" aria-label="share chart">';
    svg += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="#F1F6F8" stroke-width="' + thickness + '"/>';

    points.forEach(function (p, i) {
      if (p.value <= 0) return;
      var share = p.value / total;
      var dash = share * circumference;
      var color = palette[i % palette.length];
      svg += '<circle class="arc" data-i="' + i + '" cx="' + cx + '" cy="' + cy + '" r="' + r + '"' +
        ' fill="none" stroke="' + color + '" stroke-width="' + thickness + '" stroke-linecap="butt"' +
        ' stroke-dasharray="' + (dash - 2).toFixed(2) + ' ' + (circumference - dash + 2).toFixed(2) + '"' +
        ' stroke-dashoffset="' + (-offset).toFixed(2) + '"' +
        ' transform="rotate(-90 ' + cx + ' ' + cy + ')"' +
        ' style="opacity:' + (reduceMotion ? 1 : 0) + '; transition: opacity .5s ease ' + (0.1 + i * 0.08).toFixed(2) + 's"/>';
      offset += dash;
    });

    svg += '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" style="fill:#17313F;font-size:19px;font-weight:750;letter-spacing:-.03em">' + compact(total) + '</text>';
    svg += '<text x="' + cx + '" y="' + (cy + 15) + '" text-anchor="middle" class="axis-label">total</text>';
    svg += '</svg>';

    var legend = '<div class="chart-legend">' + points.map(function (p, i) {
      return '<span><i style="background:' + palette[i % palette.length] + '"></i>' + (p.key || '—') + '</span>';
    }).join('') + '</div>';

    root.innerHTML = svg + legend;

    requestAnimationFrame(function () {
      root.querySelectorAll('.arc').forEach(function (arc) { arc.style.opacity = 1; });
    });

    root.querySelectorAll('.arc').forEach(function (arc) {
      arc.addEventListener('mousemove', function (event) {
        var p = points[parseInt(arc.getAttribute('data-i'), 10)];
        var share = ((p.value / total) * 100).toFixed(1);
        showTip('<span class="tip-key">' + (p.key || '—') + '</span> &nbsp;<span class="tip-val">' + money(root, p.value) + ' · ' + share + '%</span>',
          event.clientX, event.clientY);
      });
      arc.addEventListener('mouseleave', hideTip);
    });
  }

  /* ---------------------------------------------------------------- hbar */

  function drawHbar(root, points) {
    var uid = 'zy' + Math.random().toString(36).slice(2, 8);
    var width = 680;
    var rowH = 34;
    var labelW = 210;
    var valueW = 110;
    var height = Math.max(points.length * rowH + 8, 80);
    var trackW = width - labelW - valueW - 16;
    var max = Math.max.apply(null, points.map(function (p) { return Math.abs(p.value); })) || 1;

    var svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="ranking chart">' + defs(uid);

    points.forEach(function (p, i) {
      var y = i * rowH + 6;
      var name = String(p.key || '—');
      if (name.length > 28) name = name.slice(0, 27) + '…';
      var w = Math.max((Math.abs(p.value) / max) * trackW, 3);
      svg += '<text class="hbar-label" x="0" y="' + (y + 16) + '">' + escapeXml(name) + '</text>';
      svg += '<rect class="hbar-track" x="' + labelW + '" y="' + (y + 6) + '" width="' + trackW + '" height="14"/>';
      svg += '<rect class="hbar-fill" data-i="' + i + '" x="' + labelW + '" y="' + (y + 6) + '" width="' + w.toFixed(1) + '" height="14"' +
        ' fill="url(#' + uid + '-hbar)" style="transform-origin:' + labelW + 'px 0px; transform: scaleX(' + (reduceMotion ? 1 : 0) + ');' +
        ' transition: transform .8s cubic-bezier(0.16, 1, 0.3, 1) ' + (i * 0.06).toFixed(2) + 's"/>';
      svg += '<text class="hbar-value" x="' + (width - 2) + '" y="' + (y + 17) + '" text-anchor="end">' + compact(p.value) + '</text>';
    });

    svg += '</svg>';
    root.innerHTML = svg;

    requestAnimationFrame(function () {
      root.querySelectorAll('.hbar-fill').forEach(function (rect) { rect.style.transform = 'scaleX(1)'; });
    });

    root.querySelectorAll('.hbar-fill').forEach(function (rect) {
      rect.addEventListener('mousemove', function (event) {
        var p = points[parseInt(rect.getAttribute('data-i'), 10)];
        showTip('<span class="tip-key">' + escapeXml(String(p.key || '—')) + '</span> &nbsp;<span class="tip-val">' + money(root, p.value) + '</span>',
          event.clientX, event.clientY);
      });
      rect.addEventListener('mouseleave', hideTip);
    });
  }

  function escapeXml(text) {
    return String(text).replace(/[<>&"']/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------------------------------------------------------------- start */

  function render(root) {
    if (root.dataset.drawn === '1') return;
    var points = readPoints(root);
    if (!points.length) { empty(root); root.dataset.drawn = '1'; return; }
    var kind = root.getAttribute('data-chart') || 'area';
    try {
      if (kind === 'bars') drawBars(root, points);
      else if (kind === 'donut') drawDonut(root, points);
      else if (kind === 'hbar') drawHbar(root, points);
      else drawArea(root, points);
      root.dataset.drawn = '1';
    } catch (e) {
      empty(root);
    }
  }

  function start() {
    var charts = Array.prototype.slice.call(document.querySelectorAll('.zyro-chart'));
    if (!charts.length) return;
    if (!('IntersectionObserver' in window)) { charts.forEach(render); return; }
    var watcher = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { render(entry.target); watcher.unobserve(entry.target); }
      });
    }, { rootMargin: '120px' });
    charts.forEach(function (chart) { watcher.observe(chart); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

  window.ZyroCharts = { render: render, redraw: function () {
    document.querySelectorAll('.zyro-chart').forEach(function (root) { root.dataset.drawn = '0'; render(root); });
  } };
})();
