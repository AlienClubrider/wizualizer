/* wiz functions page: the production functions under test, each with its runs. Opening a run
   records its test (once) and plays it back on that function. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const ONLY_KEY = 'wiz-only-changes';
  const PAGE_SIZE = 20;
  let S = null; // latest /api/state
  let only = false;
  let page = 0;
  const params = new URLSearchParams(location.search);
  let wantOpen = params.get('open'); // flow id to record and open
  let wantFn = params.get('fn') || ''; // function key to open it on
  let timer = null;
  const openDetails = new Set(); // flow ids whose failure details are open
  const logs = new Map(); // flow id -> its failed build's output, once fetched
  try { only = localStorage.getItem(ONLY_KEY) === '1'; } catch (e) { /* not remembered */ }

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') n.className = v; else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
    }
    for (const k of kids) if (k !== null && k !== undefined && k !== false) n.append(k);
    return n;
  }
  const busy = (job) => job && (job.status === 'queued' || job.status === 'running');
  const secs = (job) => Math.max(0, Math.round(Date.now() / 1000 - (job.started || job.queued)));
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Wiz': '1' }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  const allRuns = () => S.functions.flatMap((f) => f.runs.map((r) => ({ fn: f, run: r })));
  const findRun = (id) => allRuns().find((x) => x.run.id === id);

  async function refresh() {
    clearTimeout(timer);
    try {
      S = await api('/api/state');
    } catch (e) {
      $('notice').hidden = false;
      $('notice').className = 'notice err';
      $('notice').textContent = 'wiz is not running any more. Start it again with `wiz` in the project.';
      return;
    }
    if (wantOpen) {
      const x = findRun(wantOpen);
      const r = x && x.run;
      if (r && r.cached) { location.href = runHref(r.id, wantFn || (x.fn.known ? x.fn.key : '')); return; }
      if (r && !busy(r.job) && !(r.job && r.job.status === 'failed')) { await record(r.id, wantFn); return; }
      if (!r || (r.job && r.job.status === 'failed')) wantOpen = null;
    }
    if (only && S.diff.available && !S.scan.ready && !busy(S.scan.job) && !(S.scan.job && S.scan.job.status === 'failed')) {
      await api('/api/scan', {});
      return refresh();
    }
    render();
    const running = busy(S.scan.job) || allRuns().some((x) => busy(x.run.job));
    if (running) timer = setTimeout(refresh, 1000);
  }

  const runHref = (id, fnKey) => `/flow?id=${encodeURIComponent(id)}` + (fnKey ? `#fn=${encodeURIComponent(fnKey)}` : '');

  async function record(id, fnKey) {
    wantOpen = id;
    wantFn = fnKey || '';
    try { await api('/api/record', { id }); } catch (e) { /* shown by the next refresh */ }
    refresh();
  }

  // ---- filtering and paging -------------------------------------------------------------------
  function changedOf(fn) {
    const runs = fn.runs.filter((r) => r.changed);
    return runs.length ? { runs: runs.length, lines: Math.max(...runs.map((r) => r.changed.lines || 0)) } : null;
  }

  function visible() {
    const q = $('search').value.trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    const hay = (f) => [f.name, f.signature, f.file, f.via, ...f.runs.flatMap((r) => [r.label, r.id])].join(' ').toLowerCase();
    let fns = S.functions.filter((f) => words.every((w) => hay(f).includes(w)));
    if ($('only').checked) {
      if (!S.scan.ready) return [];
      fns = fns.filter((f) => changedOf(f)).map((f) => ({ ...f, runs: f.runs.filter((r) => r.changed) }));
    }
    return fns;
  }

  // ---- render ---------------------------------------------------------------------------------
  function render() {
    const p = S.project;
    $('proj-name').textContent = p.name;
    $('proj-kind').textContent = p.kind;
    const probs = [...(p.problems || []), ...(p.error ? [p.error] : [])];
    $('problems').hidden = !probs.length;
    $('problems').replaceChildren(...probs.map((t) => el('p', {}, t)));

    // Only-changes switch
    const d = S.diff;
    const box = $('only');
    box.disabled = !d.available;
    box.checked = only && d.available;
    $('only-wrap').classList.toggle('disabled', !d.available);
    const against = d.base === 'HEAD' ? 'uncommitted' : `vs ${d.base} + uncommitted`;
    $('only-sub').textContent = d.available
      ? `${plural(d.lines, 'changed line')} in ${plural(d.files, 'file')}, ${against}`
      : d.reason;

    renderNotice(box.checked);

    const fns = visible();
    const pages = Math.max(1, Math.ceil(fns.length / PAGE_SIZE));
    page = Math.min(page, pages - 1);
    const shown = fns.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    const out = shown.map(card);
    if (!out.length) {
      let why = 'No tests found.';
      if ($('search').value.trim()) why = 'No function matches the search.';
      else if (box.checked && S.scan.ready) why = 'No test runs any of the changed lines.';
      else if (box.checked) why = '';
      if (why) out.push(el('div', { class: 'empty' }, why));
    }
    $('list').replaceChildren(...out);
    renderPager(fns.length, pages);

    const runs = allRuns();
    const recorded = runs.filter((x) => x.run.cached).length;
    const known = S.functions.filter((f) => f.known).length;
    const changed = S.scan.ready ? S.functions.filter((f) => changedOf(f)).length : null;
    $('foot').textContent = `${plural(known, 'function')} · ${plural(S.tests, 'test')} · ${recorded} recorded`
      + (changed !== null && d.available ? ` · ${changed} run changed lines` : '')
      + ' · cached in .wiz/';
  }

  function renderNotice(onlyOn) {
    const n = $('notice');
    const sj = S.scan.job;
    n.hidden = true;
    n.className = 'notice';
    if (onlyOn && busy(sj)) {
      n.hidden = false;
      n.replaceChildren(el('span', { class: 'spin' }), `Finding the functions that run changed lines: running the tests once (${secs(sj)}s)…`);
    } else if (onlyOn && sj && sj.status === 'failed' && !S.scan.ready) {
      n.hidden = false;
      n.className = 'notice err';
      n.replaceChildren('Could not find the changed functions. ',
        el('button', { class: 'btn', type: 'button', onclick: () => api('/api/scan', {}).then(refresh) }, 'Try again'),
        el('pre', {}, sj.error));
    } else if (wantOpen) {
      const x = findRun(wantOpen);
      if (x && busy(x.run.job)) {
        n.hidden = false;
        const what = x.fn.known ? `${x.fn.name} (${x.run.label})` : x.run.label;
        n.replaceChildren(el('span', { class: 'spin' }), `Recording ${what}, ${secs(x.run.job)}s. It opens when done.`,
          x.run.job.progress ? el('small', { class: 'progress' }, x.run.job.progress) : null);
      }
    }
  }

  function renderPager(total, pages) {
    const pg = $('pager');
    pg.hidden = pages <= 1;
    if (pages <= 1) { pg.replaceChildren(); return; }
    const go = (k) => { page = Math.max(0, Math.min(pages - 1, k)); render(); window.scrollTo(0, 0); };
    pg.replaceChildren(
      el('button', { class: 'btn', type: 'button', disabled: page === 0 ? '' : null, onclick: () => go(page - 1), 'aria-label': 'Previous page' }, '‹ Prev'),
      el('span', { class: 'muted' }, `Page ${page + 1} of ${pages} · ${plural(total, 'function')}`),
      el('button', { class: 'btn', type: 'button', disabled: page === pages - 1 ? '' : null, onclick: () => go(page + 1), 'aria-label': 'Next page' }, 'Next ›'));
  }

  function card(f) {
    const head = [];
    if (f.known) {
      const ch = changedOf(f);
      head.push(el('div', { class: 'fn-head' },
        el('span', { class: 'fn-name' }, f.name),
        ch ? el('span', { class: 'chip chg', title: 'Its runs reach changed lines' }, `Δ ${plural(ch.lines, 'line')}`) : null));
      head.push(el('div', { class: 'fn-sig' }, f.signature || f.name,
        f.via ? el('span', { class: 'via' }, f.via) : null));
      head.push(el('div', { class: 'fn-file muted' }, `${f.file}${f.line ? `:${f.line}` : ''}`));
    } else {
      head.push(el('div', { class: 'fn-head' }, el('span', { class: 'fn-name' }, 'Not placed yet')));
      head.push(el('div', { class: 'fn-file muted' }, 'wiz could not tell from these tests which function they call. Open one: the recording finds it.'));
    }
    return el('section', { class: 'fn' + (f.known ? '' : ' unknown') }, el('header', {}, ...head),
      el('div', { class: 'runs' }, ...f.runs.map((r) => runRow(f, r))));
  }

  function runRow(f, r) {
    const chips = [];
    if (r.changed) {
      const c = r.changed;
      if (c.lines) chips.push(el('span', { class: 'chip chg', title: 'Changed lines this run reaches' }, `Δ ${c.lines}`));
      if (c.test_lines) chips.push(el('span', { class: 'chip chg', title: 'The test itself changed' }, 'test changed'));
    }
    let err = null;
    if (busy(r.job)) {
      chips.push(el('span', { class: 'chip busy' }, el('span', { class: 'spin' }),
        r.job.status === 'queued' ? 'queued' : `recording ${secs(r.job)}s`));
    } else if (r.cached) {
      chips.push(el('span', { class: `chip ${r.cached.outcome}` }, r.cached.outcome));
    } else if (r.job && r.job.status === 'failed') {
      chips.push(el('span', { class: 'chip failed' }, 'not recorded'));
      err = failure(r);
    } else {
      chips.push(el('span', { class: 'chip new' }, 'not recorded'));
    }
    const cls = (r.group || '').split('.').pop().replace(/\$/g, '.');
    const source = el('small', { class: 'src', title: r.id }, 'from ', cls ? `${cls} › ${r.test}` : r.test);
    const fnKey = f.known ? f.key : '';
    const a = el('a', { class: 'run', href: runHref(r.id, fnKey) },
      el('span', { class: 'run-main' }, el('span', { class: 'label' }, r.label), source), ...chips);
    a.addEventListener('click', (ev) => {
      if (r.cached) return; // plain navigation
      ev.preventDefault();
      logs.delete(r.id);
      if (!busy(r.job)) record(r.id, fnKey); else { wantOpen = r.id; wantFn = fnKey; render(); }
    });
    return el('div', { class: 'item' }, a, err);
  }

  // A failed recording: the reason in one line, what to do, and the build's whole output on demand.
  function failure(r) {
    const j = r.job;
    const pre = el('pre', { class: 'log' }, j.has_log ? 'Loading the build output…' : j.error);
    const box = el('details', { class: 'err', open: openDetails.has(r.id) ? '' : null },
      el('summary', {}, el('b', {}, 'Recording failed: '), j.reason || 'see details'),
      j.hint ? el('p', { class: 'hint' }, j.hint) : null,
      j.log_path ? el('p', { class: 'muted' }, `Full build output (also in ${j.log_path}):`) : null,
      pre);
    const load = async () => {
      if (!j.has_log || pre.dataset.loaded) return;
      try {
        if (!logs.has(r.id)) logs.set(r.id, await (await fetch(`/api/log?id=${encodeURIComponent(r.id)}`)).text());
        pre.textContent = logs.get(r.id);
        pre.dataset.loaded = '1';
        requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
      } catch (e) { pre.textContent = j.error; }
    };
    box.addEventListener('toggle', () => {
      if (box.open) { openDetails.add(r.id); load(); } else openDetails.delete(r.id);
    });
    if (box.open) load();
    return box;
  }

  $('search').addEventListener('input', () => { page = 0; if (S) render(); });
  $('only').addEventListener('change', (e) => {
    only = e.target.checked;
    page = 0;
    try { localStorage.setItem(ONLY_KEY, only ? '1' : '0'); } catch (err) { /* not remembered */ }
    refresh();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== $('search')) { e.preventDefault(); $('search').focus(); }
  });
  window.addEventListener('pageshow', (e) => { if (e.persisted) refresh(); });
  refresh();
})();
