/* wiz flows page: every test of the project as a flow; open one to record (once) and play it back. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const ONLY_KEY = 'wiz-only-changes';
  let S = null; // latest /api/state
  let only = false;
  let wantOpen = new URLSearchParams(location.search).get('open');
  let timer = null;
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

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Wiz': '1' }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

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
      const f = S.flows.find((x) => x.id === wantOpen);
      if (f && f.cached) { location.href = flowHref(f.id); return; }
      if (f && !busy(f.job) && !(f.job && f.job.status === 'failed')) { await record(f.id); return; }
      if (!f || (f.job && f.job.status === 'failed')) wantOpen = null;
    }
    if (only && S.diff.available && !S.scan.ready && !busy(S.scan.job) && !(S.scan.job && S.scan.job.status === 'failed')) {
      await api('/api/scan', {});
      return refresh();
    }
    render();
    const running = busy(S.scan.job) || S.flows.some((f) => busy(f.job));
    if (running) timer = setTimeout(refresh, 1000);
  }

  const flowHref = (id) => `/flow?id=${encodeURIComponent(id)}`;

  async function record(id) {
    wantOpen = id;
    try { await api('/api/record', { id }); } catch (e) { /* shown by the next refresh */ }
    refresh();
  }

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
      ? `${d.lines} changed line${d.lines === 1 ? '' : 's'} in ${d.files} file${d.files === 1 ? '' : 's'}, ${against}`
      : d.reason;

    // Notice: scan progress or failure, recording failure of the flow being opened
    const n = $('notice');
    const sj = S.scan.job;
    n.hidden = true;
    n.className = 'notice';
    if (box.checked && busy(sj)) {
      n.hidden = false;
      n.replaceChildren(el('span', { class: 'spin' }), `Finding the flows that run changed lines: running the tests once (${secs(sj)}s)…`);
    } else if (box.checked && sj && sj.status === 'failed' && !S.scan.ready) {
      n.hidden = false;
      n.className = 'notice err';
      n.replaceChildren('Could not find the changed flows. ',
        el('button', { class: 'btn', type: 'button', onclick: () => api('/api/scan', {}).then(refresh) }, 'Try again'),
        el('pre', {}, sj.error));
    } else if (wantOpen) {
      const f = S.flows.find((x) => x.id === wantOpen);
      if (f && busy(f.job)) {
        n.hidden = false;
        n.replaceChildren(el('span', { class: 'spin' }), `Recording ${f.name} (${secs(f.job)}s). It opens when done.`,
          f.job.progress ? el('small', { class: 'progress' }, f.job.progress) : null);
      }
    }

    // List
    const q = $('search').value.trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    let shown = S.flows.filter((f) => words.every((w) => `${f.id} ${f.file}`.toLowerCase().includes(w)));
    if (box.checked) shown = S.scan.ready ? shown.filter((f) => f.changed) : [];

    const groups = new Map();
    for (const f of shown) {
      if (!groups.has(f.file)) groups.set(f.file, new Map());
      const g = groups.get(f.file);
      if (!g.has(f.group)) g.set(f.group, []);
      g.get(f.group).push(f);
    }
    const list = $('list');
    const out = [];
    for (const [file, byGroup] of groups) {
      const sec = el('section', { class: 'group' }, el('h2', {}, file));
      for (const [group, flows] of byGroup) {
        const label = group.split('.').pop();
        if (group && byGroup.size > 1) sec.append(el('h3', {}, label));
        for (const f of flows) sec.append(row(f));
      }
      out.push(sec);
    }
    if (!out.length) {
      let why = 'No tests found.';
      if (q) why = 'No flow matches the search.';
      else if (box.checked && S.scan.ready) why = 'No test runs any of the changed lines.';
      else if (box.checked) why = '';
      if (why) out.push(el('div', { class: 'empty' }, why));
    }
    list.replaceChildren(...out);

    const recorded = S.flows.filter((f) => f.cached).length;
    const changed = S.scan.ready ? S.flows.filter((f) => f.changed).length : null;
    $('foot').textContent = `${S.flows.length} flows · ${recorded} recorded`
      + (changed !== null && d.available ? ` · ${changed} touch changed lines` : '')
      + ' · cached in .wiz/';
  }

  function row(f) {
    const chips = [];
    if (f.changed) {
      const c = f.changed;
      if (c.lines) chips.push(el('span', { class: 'chip chg', title: 'Changed lines this test runs' }, `Δ ${c.lines} line${c.lines === 1 ? '' : 's'}`));
      if (c.test_lines) chips.push(el('span', { class: 'chip chg', title: 'The test itself changed' }, 'test changed'));
    }
    let err = null;
    if (busy(f.job)) {
      chips.push(el('span', { class: 'chip busy' }, el('span', { class: 'spin' }),
        f.job.status === 'queued' ? 'queued' : `recording ${secs(f.job)}s`));
    } else if (f.cached) {
      chips.push(el('span', { class: `chip ${f.cached.outcome}` }, f.cached.outcome));
    } else if (f.job && f.job.status === 'failed') {
      chips.push(el('span', { class: 'chip failed' }, 'not recorded'));
      err = failure(f);
    } else {
      chips.push(el('span', { class: 'chip new' }, 'not recorded'));
    }
    const a = el('a', { class: 'flow', href: flowHref(f.id), title: f.id },
      el('span', { class: 'name' }, f.name), ...chips);
    a.addEventListener('click', (ev) => {
      if (f.cached) return; // plain navigation
      ev.preventDefault();
      if (!busy(f.job)) record(f.id); else { wantOpen = f.id; render(); }
    });
    return el('div', { class: 'item' }, a, err);
  }

  // A failed recording: the reason in one line, what to do, and the build's whole output on demand.
  function failure(f) {
    const j = f.job;
    const pre = el('pre', { class: 'log' }, j.has_log ? 'Loading the build output…' : j.error);
    const box = el('details', { class: 'err' },
      el('summary', {}, el('b', {}, 'Recording failed: '), j.reason || 'see details'),
      j.hint ? el('p', { class: 'hint' }, j.hint) : null,
      j.log_path ? el('p', { class: 'muted' }, `Full build output, also saved in ${j.log_path}:`) : null,
      pre);
    box.addEventListener('toggle', async () => {
      if (!box.open || !j.has_log || pre.dataset.loaded) return;
      try {
        const r = await fetch(`/api/log?id=${encodeURIComponent(f.id)}`);
        pre.textContent = await r.text();
        pre.dataset.loaded = '1';
        pre.scrollTop = pre.scrollHeight;
      } catch (e) { pre.textContent = j.error; }
    });
    return box;
  }

  $('search').addEventListener('input', () => S && render());
  $('only').addEventListener('change', (e) => {
    only = e.target.checked;
    try { localStorage.setItem(ONLY_KEY, only ? '1' : '0'); } catch (err) { /* not remembered */ }
    refresh();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== $('search')) { e.preventDefault(); $('search').focus(); }
  });
  window.addEventListener('pageshow', (e) => { if (e.persisted) refresh(); });
  refresh();
})();
