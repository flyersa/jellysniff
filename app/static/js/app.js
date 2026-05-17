// Top-level function declarations (no IIFE!). Alpine 3 evaluates x-data /
// x-text expressions in a stricter scope that does NOT fall back to window
// properties — only true global bindings (function declarations / var at
// script top level) are resolvable by bare reference.

// Global image-error fallback. Replaces inline `onerror="..."` handlers
// (which CSP3 forbids even with a script-src nonce). Mark images with
// data-img-fallback="hide" or "fade" and they'll silently disappear / dim
// on a 404 or load failure.
document.addEventListener('error', function (e) {
  const t = e.target;
  if (t && t.tagName === 'IMG') {
    const mode = t.dataset.imgFallback;
    if (mode === 'hide') t.style.visibility = 'hidden';
    else if (mode === 'fade') t.style.opacity = '0';
  }
}, true);

function _parseDate(s) {
  if (!s) return null;
  // SQLite gives us "YYYY-MM-DD HH:MM:SS[.fff]" which we treat as UTC.
  const iso = String(s).includes('T') ? s : s.replace(' ', 'T');
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  const d = new Date(hasTz ? iso : iso + 'Z');
  return isNaN(d.getTime()) ? null : d;
}

function fmtDuration(seconds) {
  seconds = Math.round(seconds || 0);
  if (seconds < 60) return seconds + 's';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h === 0) return m + 'm';
  if (h >= 100) return h + 'h';
  return h + 'h ' + (m < 10 ? '0' + m : m) + 'm';
}

function fmtDate(s) {
  const d = _parseDate(s);
  if (!d) return '—';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const yest = new Date(now); yest.setDate(now.getDate() - 1);
  const sameYest = d.toDateString() === yest.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return 'today ' + hh + ':' + mm;
  if (sameYest) return 'yest ' + hh + ':' + mm;
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
         String(d.getDate()).padStart(2, '0') + ' ' + hh + ':' + mm;
}

function fmtDateRelative(s) {
  const d = _parseDate(s);
  if (!d) return '—';
  const now = new Date();
  const diffSec = (now - d) / 1000;
  if (diffSec < 0) return fmtDate(s);
  if (diffSec < 60)        return 'just now';
  if (diffSec < 3600)      return Math.floor(diffSec / 60) + 'm ago';
  if (diffSec < 6 * 3600)  return Math.floor(diffSec / 3600) + 'h ago';
  const sameDay = d.toDateString() === now.toDateString();
  const yest = new Date(now); yest.setDate(now.getDate() - 1);
  const sameYest = d.toDateString() === yest.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay)  return 'today ' + hh + ':' + mm;
  if (sameYest) return 'yest ' + hh + ':' + mm;
  if ((now - d) < 6 * 86400 * 1000) {
    const dn = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.getDay()];
    return `${dn} ${hh}:${mm}`;
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
  if (sameYear) return `${mon} ${d.getDate()} ${hh}:${mm}`;
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function fmtDateFull(s) {
  const d = _parseDate(s);
  if (!d) return '';
  const Y = d.getFullYear(); const M = String(d.getMonth()+1).padStart(2,'0');
  const D = String(d.getDate()).padStart(2,'0'); const h = String(d.getHours()).padStart(2,'0');
  const m = String(d.getMinutes()).padStart(2,'0'); const sec = String(d.getSeconds()).padStart(2,'0');
  return `${Y}-${M}-${D} ${h}:${m}:${sec}`;
}

function fmtBitrate(bps) {
  bps = bps || 0;
  if (bps < 1000) return bps + ' bps';
  if (bps < 1_000_000) return (bps / 1000).toFixed(0) + ' Kbps';
  if (bps < 1_000_000_000) return (bps / 1_000_000).toFixed(1) + ' Mbps';
  return (bps / 1_000_000_000).toFixed(2) + ' Gbps';
}

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

function nowPlaying(endpoint) {
  return {
    endpoint, data: null, _t: null,
    fmtBitrate, fmtTime,
    start() { this.refresh(); this._t = setInterval(() => this.refresh(), 5000); },
    stop()  { if (this._t) clearInterval(this._t); this._t = null; },
    async refresh() {
      try {
        const r = await fetch(this.endpoint, { cache: 'no-store' });
        if (r.ok) this.data = await r.json();
      } catch (e) { /* swallowed: network blip, paused tab, etc. */ }
    },
  };
}

function sortBy(arr, key, dir) {
  const m = dir === 'desc' ? -1 : 1;
  return [...arr].sort((a, b) => {
    const va = a[key]; const vb = b[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * m;
    return String(va).localeCompare(String(vb)) * m;
  });
}

function chartOpts(opts) {
  opts = opts || {};
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: opts.legend ? {
        position: opts.legendPosition || 'top',
        labels: { color: '#a1a1aa', font: { family: 'Inter', size: 11 }, boxWidth: 10, boxHeight: 10 }
      } : { display: false },
      tooltip: {
        backgroundColor: '#18181b', titleColor: '#fafafa', bodyColor: '#d4d4d8',
        borderColor: '#3f3f46', borderWidth: 1, padding: 8,
      },
    },
    scales: opts.legend ? {} : {
      x: { ticks: { color: '#71717a', font: { size: 10 } }, grid: { color: 'rgba(63,63,70,0.2)' } },
      y: {
        ticks: {
          color: '#71717a', font: { size: 10 },
          callback: function (v) { return opts.yLabel ? v + (opts.yLabel === 'min' ? '' : ' ' + opts.yLabel) : v; },
        },
        grid: { color: 'rgba(63,63,70,0.2)' },
        title: opts.yLabel ? { display: true, text: opts.yLabel, color: '#71717a', font: { size: 10 } } : undefined,
      },
    },
  };
}

// Mirror to window so inline component methods that wrote `window.fmtDuration`
// etc. keep working without further edits.
window.fmtDuration     = fmtDuration;
window.fmtDate         = fmtDate;
window.fmtDateRelative = fmtDateRelative;
window.fmtDateFull     = fmtDateFull;
window.fmtBitrate      = fmtBitrate;
window.fmtTime         = fmtTime;
window.sortBy          = sortBy;
window.chartOpts       = chartOpts;
window.nowPlaying      = nowPlaying;
window._parseDate      = _parseDate;
