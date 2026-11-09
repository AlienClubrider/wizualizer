/* wizualizer viewer: plays back a trace (docs/trace-format.md). No network, no dependencies. */
(() => {
  'use strict';
  const T = JSON.parse(document.getElementById('trace').textContent);
  const steps = T.steps, frames = T.frames, fns = T.functions, files = T.files, cov = T.coverage;
  const N = steps.length;
  const $ = (id) => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const narrow = () => window.matchMedia('(max-width: 860px)').matches;

  function h(tag, attrs, parent, text) {
    const svg = ['svg', 'g', 'rect', 'path', 'text', 'line', 'polygon', 'marker', 'defs', 'title', 'tspan'].includes(tag);
    const node = svg ? document.createElementNS(NS, tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }
  const clip = (s, n) => (s.length <= n ? s : s.slice(0, Math.max(1, n - 1)) + '…');
  const fnOfStep = (s) => fns[frames[s.frame].fn];
  const fnIdOfStep = (s) => frames[s.frame].fn;

  // ---- derived model ------------------------------------------------------
  const lineStatus = {}; // file -> {line: status}
  if (cov) for (const [f, m] of Object.entries(cov.files)) lineStatus[f] = m;
  const changed = {}; // file -> Set(lines)
  for (const [f, e] of Object.entries(files)) if (e.changed && e.changed.length) changed[f] = new Set(e.changed);
  const isChangedLine = (file, line) => !!(changed[file] && changed[file].has(line));

  const stackAt = new Array(N), snapAt = new Array(N), prevSnapAt = new Array(N), prevInFrame = new Array(N);
  {
    let stack = [];
    const lastSnap = {}, lastIdx = {};
    steps.forEach((s, i) => {
      if (s.kind === 'call' && !stack.includes(s.frame)) stack = [...stack, s.frame];
      stackAt[i] = stack;
      const prev = lastSnap[s.frame];
      let snap = prev;
      if (s.kind === 'call') snap = s.args; else if (s.kind === 'line') snap = s.locals;
      snapAt[i] = snap || {};
      prevSnapAt[i] = s.kind === 'line' ? prev || null : null;
      lastSnap[s.frame] = snap;
      prevInFrame[i] = lastIdx[s.frame] ?? -1;
      if (s.kind === 'call' || s.kind === 'line') lastIdx[s.frame] = i;
      if (s.kind === 'return') {
        const k = stack.indexOf(s.frame);
        if (k >= 0) stack = stack.slice(0, k);
      }
    });
  }

  // Actors: drop "#1" when a class only ever had one instance.
  const actorName = (() => {
    const counts = {};
    for (const f of Object.values(frames)) {
      const m = /^(.*)#(\d+)$/.exec(f.actor);
      if (m) counts[m[1]] = Math.max(counts[m[1]] || 0, +m[2]);
    }
    return (a) => {
      const m = /^(.*)#(\d+)$/.exec(a);
      return m && counts[m[1]] === 1 ? m[1] : a;
    };
  })();

  // Sequence messages
  const actors = [], actorIdx = {}, messages = [], msgAt = new Array(N).fill(-1);
  const addActor = (a) => { if (!(a in actorIdx)) { actorIdx[a] = actors.length; actors.push(a); } };
  {
    let last = -1;
    const open = {};
    steps.forEach((s, i) => {
      const f = frames[s.frame];
      const p = f.parent != null ? frames[f.parent] : null;
      if (s.kind === 'call') {
        addActor(p ? p.actor : f.actor);
        addActor(f.actor);
        if (p) {
          const fn = fns[f.fn];
          const args = Object.entries(s.args || {}).filter(([k]) => k !== 'self' && k !== 'cls').map(([, v]) => clip(v, 18));
          const label = s.resume ? `resume ${fn.name}` : `${fn.name}(${args.join(', ')})`;
          const level = Object.values(open).filter((o) => o.actor === f.actor).length;
          open[s.frame] = { actor: f.actor, start: messages.length, level };
          messages.push({ step: i, from: p.actor, to: f.actor, label, kind: 'call', frame: s.frame });
          last = messages.length - 1;
        }
      } else if (s.kind === 'return' && p) {
        const label = s.raised ? `raises ${s.type}` : (s.yield ? `yield ${s.value}` : `→ ${s.value}`);
        messages.push({ step: i, from: f.actor, to: p.actor, label, kind: s.raised ? 'raise' : 'ret', frame: s.frame });
        if (open[s.frame]) { open[s.frame].end = messages.length - 1; open[s.frame].done = true; }
        last = messages.length - 1;
      }
      msgAt[i] = last;
    });
    messages.activations = Object.values(open);
  }

  // ---- flowchart ------------------------------------------------------------
  const flowCache = {};
  const ROW = 46, NH = 30, CH = 7.25, INDENT = 24;

  function nodeLookup(fc) {
    const memo = {};
    return (line) => {
      if (line in memo) return memo[line];
      let best = null, span = 1e9;
      for (const n of fc.nodes) {
        if (n.kind === 'start' || n.kind === 'end') continue;
        const [a, b] = n.lines;
        if (line >= a && line <= b && b - a <= span) { best = n.id; span = b - a; }
      }
      return (memo[line] = best);
    };
  }

  function assignLanes(edges) {
    const lanes = [];
    for (const e of edges) {
      const lo = Math.min(e.r1, e.r2), hi = Math.max(e.r1, e.r2);
      let k = 0;
      while (lanes[k] && lanes[k].some(([a, b]) => !(hi < a || lo > b))) k++;
      (lanes[k] = lanes[k] || []).push([lo, hi]);
      e.lane = k;
    }
    return lanes.length;
  }

  function buildFlow(fnId) {
    if (flowCache[fnId]) return flowCache[fnId];
    const fn = fns[fnId], fc = fn.flowchart;
    const wrap = h('div');
    if (!fc) {
      h('p', { class: 'pad muted' }, wrap, 'No flowchart: source not available for this function.');
      return (flowCache[fnId] = { wrap, nodeFor: () => null, fc: { nodes: [], edges: [] }, nodes: [], edges: {} });
    }
    const file = fn.file;
    const status = (n) => {
      if (n.kind === 'start' || n.kind === 'end') return null;
      let out = null;
      for (let l = n.lines[0]; l <= n.lines[1]; l++) {
        if (!isChangedLine(file, l)) continue;
        const st = (lineStatus[file] || {})[l];
        if (st === 'not_run') return 'miss';
        out = 'chg';
      }
      return out;
    };
    const edgesAll = fc.edges.map((e) => ({ ...e, r1: e.from, r2: e.to }));
    const back = edgesAll.filter((e) => e.to <= e.from);
    const fwd = edgesAll.filter((e) => e.to > e.from + 1);
    const nBack = assignLanes(back);
    const nFwd = assignLanes(fwd);
    const leftPad = 16 + nBack * 9;
    const pos = fc.nodes.map((n, i) => {
      const hex = n.kind === 'decision' || n.kind === 'loop';
      const w = Math.min(Math.max(n.label.length * CH + (hex ? 40 : 26), 64), 440);
      const st = status(n);
      return { x: leftPad + n.depth * INDENT, y: 12 + i * ROW, w, hex, st, badge: st === 'miss' ? 70 : st ? 18 : 0 };
    });
    const maxRight = Math.max(...pos.map((p) => p.x + p.w + p.badge));
    const laneBase = maxRight + 18;
    const W = laneBase + nFwd * 10 + 24, H = 12 + fc.nodes.length * ROW;
    const svg = h('svg', { class: 'fc', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': `Flowchart of ${fn.qualname}` }, wrap);
    const defs = h('defs', {}, svg);
    for (const [id, cls] of [['ah', ''], ['ah-hot', 'hot']]) {
      const m = h('marker', { id: `${id}-${fnId.replace(/[^\w]/g, '_')}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse', class: cls }, defs);
      h('path', { d: 'M0,0 L10,5 L0,10 z' }, m);
    }
    const mk = (hot) => `url(#${hot ? 'ah-hot' : 'ah'}-${fnId.replace(/[^\w]/g, '_')})`;
    const edgeEls = {};
    const gEdges = h('g', {}, svg);
    for (const e of edgesAll) {
      const a = pos[e.from], b = pos[e.to];
      const acy = a.y + NH / 2, bcy = b.y + NH / 2;
      let d, lx, ly, anchor = 'start';
      if (e.to === e.from + 1) {
        const ax = a.x + 16, bx = b.x + 16, ay = a.y + NH, by = b.y;
        d = ax === bx ? `M${ax},${ay} V${by}` : `M${ax},${ay} V${(ay + by) / 2} H${bx} V${by}`;
        lx = ax - 3; ly = ay + 11; anchor = 'end';
      } else if (e.to > e.from) {
        const lane = laneBase + e.lane * 10;
        d = `M${a.x + a.w + a.badge},${acy} H${lane} V${bcy} H${b.x + b.w + b.badge + 2}`;
        lx = lane + 3; ly = acy + 12;
      } else {
        const lane = leftPad - 9 - e.lane * 9;
        d = `M${a.x},${acy} H${lane} V${bcy} H${b.x - 2}`;
        lx = null;
      }
      const p = h('path', { d, class: 'edge', 'marker-end': mk(false) }, gEdges);
      edgeEls[`${e.from}>${e.to}`] = p;
      if (e.label && lx !== null) h('text', { x: lx, y: ly, class: 'elabel', 'text-anchor': anchor }, gEdges, e.label);
    }
    const nodeEls = [];
    fc.nodes.forEach((n, i) => {
      const p = pos[i];
      const g = h('g', { class: `node k-${n.kind}` + (p.st ? ` ${p.st}` : ''), 'data-id': n.id, tabindex: 0 }, svg);
      h('title', {}, g, n.kind === 'start' || n.kind === 'end' ? n.label : `${n.label}  (line ${n.lines[0]})`);
      if (p.hex) {
        const x = p.x, y = p.y, w = p.w, c = y + NH / 2;
        h('polygon', { class: 'shape', points: `${x},${c} ${x + 11},${y} ${x + w - 11},${y} ${x + w},${c} ${x + w - 11},${y + NH} ${x + 11},${y + NH}` }, g);
      } else {
        const r = n.kind === 'start' || n.kind === 'end' ? NH / 2 : 5;
        h('rect', { class: 'shape', x: p.x, y: p.y, width: p.w, height: NH, rx: r }, g);
      }
      if (p.st) h('rect', { class: 'stripe', x: p.x + (p.hex ? 9 : 2), y: p.y + 5, width: 4, height: NH - 10, rx: 2 }, g);
      h('text', { class: 'lbl', x: p.x + (p.hex ? 18 : 13), y: p.y + NH / 2 + 4 }, g, n.label);
      if (p.st === 'miss') h('text', { class: 'badge miss', x: p.x + p.w + 6, y: p.y + NH / 2 + 4 }, g, 'never ran');
      else if (p.st) h('text', { class: 'badge chg', x: p.x + p.w + 6, y: p.y + NH / 2 + 4 }, g, 'Δ');
      g.addEventListener('click', () => jumpToNode(fnId, n.id));
      g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') jumpToNode(fnId, n.id); });
      nodeEls.push(g);
    });
    const nodeFor = nodeLookup(fc);
    const start = fc.nodes.find((n) => n.kind === 'start').id;
    const end = fc.nodes.find((n) => n.kind === 'end').id;
    return (flowCache[fnId] = { wrap, svg, W, H, fc, pos, nodes: nodeEls, edges: edgeEls, edgeMarker: mk, nodeFor, start, end, scale: null });
  }

  function nodeAtStep(i, flow) {
    const s = steps[i];
    if (s.kind === 'call') return flow.start;
    if (s.kind === 'return') return flow.end;
    return flow.nodeFor(s.line);
  }

  // ---- state ------------------------------------------------------------------
  let cur = 0, playing = false, timer = null, pinnedFn = '', shownFlow = null;
  const zoom = { flow: null, seq: 1 };

  // ---- render: flowchart --------------------------------------------------------
  function renderFlow() {
    const s = steps[cur];
    const fnId = pinnedFn || fnIdOfStep(s);
    const flow = buildFlow(fnId);
    const host = $('flow');
    if (shownFlow !== flow) {
      host.replaceChildren(flow.wrap);
      shownFlow = flow;
      applyZoom('flow');
    }
    if (!flow.svg) return;
    const visited = new Set();
    for (let j = 0; j <= cur; j++) if (fnIdOfStep(steps[j]) === fnId) { const n = nodeAtStep(j, flow); if (n !== null) visited.add(n); }
    const here = fnIdOfStep(s) === fnId ? nodeAtStep(cur, flow) : null;
    flow.nodes.forEach((g, k) => {
      g.classList.toggle('visited', visited.has(k) && k !== here);
      g.classList.toggle('cur', k === here);
      g.classList.toggle('boom', k === here && (s.kind === 'exception' || (s.kind === 'return' && s.raised)));
    });
    for (const p of Object.values(flow.edges)) { p.classList.remove('hot'); p.setAttribute('marker-end', flow.edgeMarker(false)); }
    if (here !== null && prevInFrame[cur] >= 0) {
      const from = nodeAtStep(prevInFrame[cur], flow);
      const p = flow.edges[`${from}>${here}`];
      if (p) { p.classList.add('hot'); p.setAttribute('marker-end', flow.edgeMarker(true)); p.parentNode.appendChild(p); }
    }
    if (here !== null) {
      const ps = flow.pos[here], sc = flow.scale || 1;
      keepInView(host, ps.x * sc, ps.y * sc, ps.w * sc, NH * sc);
    }
  }

  function keepInView(box, x, y, w, hgt) {
    if (!box.clientHeight) return;
    if (y < box.scrollTop + 8 || y + hgt > box.scrollTop + box.clientHeight - 8) box.scrollTop = Math.max(0, y - box.clientHeight / 2 + hgt / 2);
    if (x < box.scrollLeft || x + Math.min(w, box.clientWidth) > box.scrollLeft + box.clientWidth) box.scrollLeft = Math.max(0, x - 16);
  }

  function applyZoom(which) {
    const target = which === 'flow' ? shownFlow : seqView;
    if (!target || !target.svg) return;
    const box = $(which);
    if (which === 'flow' && target.scale === null) {
      const fit = box.clientWidth ? (box.clientWidth - 8) / target.W : 1;
      target.scale = Math.max(0.55, Math.min(1, fit));
    }
    const sc = which === 'flow' ? target.scale : zoom.seq;
    target.svg.setAttribute('width', target.W * sc);
    target.svg.setAttribute('height', target.H * sc);
    if (target.head) { target.head.setAttribute('width', target.W * sc); target.head.setAttribute('height', target.HH * sc); }
  }

  function changeZoom(which, dir) {
    const f = dir > 0 ? 1.2 : 1 / 1.2;
    if (which === 'flow' && shownFlow) shownFlow.scale = Math.max(0.3, Math.min(3, (shownFlow.scale || 1) * f));
    else zoom.seq = Math.max(0.3, Math.min(3, zoom.seq * f));
    applyZoom(which);
    render();
  }

  // ---- render: sequence ------------------------------------------------------------
  let seqView = null;
  function buildSeq() {
    const COL = 156, ROWH = 26, HH = 40, PAD = 80;
    const host = $('seq');
    const W = PAD * 2 + Math.max(0, actors.length - 1) * COL + 60;
    const H = 20 + messages.length * ROWH + 20;
    const ax = (a) => PAD + actorIdx[a] * COL;
    const head = h('svg', { class: 'sq sq-head', viewBox: `0 0 ${W} ${HH}`, width: W, height: HH }, host);
    for (const a of actors) {
      const g = h('g', { class: 'actor' }, head);
      const full = actorName(a);
      const name = full.length <= 19 ? full : full.slice(0, 9) + '…' + full.slice(-9);
      const w = Math.min(COL - 10, name.length * 7.4 + 20);
      h('rect', { x: ax(a) - w / 2, y: 6, width: w, height: 28, rx: 6 }, g);
      h('text', { x: ax(a), y: 24, 'text-anchor': 'middle' }, g, name);
      h('title', {}, g, a);
    }
    const svg = h('svg', { class: 'sq', viewBox: `0 0 ${W} ${H}`, width: W, height: H }, host);
    const defs = h('defs', {}, svg);
    for (const [id, color] of [['sa', 'var(--muted)'], ['sa-cur', 'var(--cur)'], ['sa-raise', 'var(--miss)']]) {
      const m = h('marker', { id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
      h('path', { d: 'M0,0 L10,5 L0,10 z', style: `fill:${color}` }, m);
    }
    for (const a of actors) h('line', { class: 'life', x1: ax(a), y1: 0, x2: ax(a), y2: H }, svg);
    const rowY = (k) => 20 + k * ROWH + ROWH / 2;
    for (const act of messages.activations) {
      const y1 = rowY(act.start), y2 = act.done ? rowY(act.end) : H - 6;
      h('rect', { class: 'act', x: ax(act.actor) - 4 + act.level * 5, y: y1, width: 8, height: Math.max(6, y2 - y1), rx: 2 }, svg);
    }
    const els = messages.map((m, k) => {
      const g = h('g', { class: `msg ${m.kind === 'call' ? 'call' : m.kind === 'raise' ? 'ret raise' : 'ret'}` }, svg);
      const y = rowY(k), x1 = ax(m.from), x2 = ax(m.to);
      const marker = m.kind === 'raise' ? 'sa-raise' : 'sa';
      let shape;
      if (x1 === x2) {
        shape = h('path', { d: `M${x1 + 4},${y - 6} h26 v12 h-24`, 'marker-end': `url(#${marker})` }, g);
        h('text', { x: x1 + 36, y: y + 4 }, g, clip(m.label, 34));
        h('path', { class: 'hit', d: `M${x1},${y} h160` }, g);
      } else {
        const dir = x2 > x1 ? 1 : -1;
        shape = h('line', { x1: x1 + dir * 5, y1: y, x2: x2 - dir * 5, y2: y, 'marker-end': `url(#${marker})` }, g);
        const chars = Math.floor((Math.abs(x2 - x1) - 12) / 6.6);
        h('text', { x: (x1 + x2) / 2, y: y - 5, 'text-anchor': 'middle' }, g, clip(m.label, Math.max(6, chars)));
        h('line', { class: 'hit', x1, y1: y, x2, y2: y }, g);
      }
      h('title', {}, g, m.label);
      g.addEventListener('click', () => go(m.step));
      return { g, shape, marker, y };
    });
    seqView = { svg, head, W, H, HH, els, rowY };
    applyZoom('seq');
  }

  function renderSeq() {
    if (!seqView) buildSeq();
    const k = msgAt[cur];
    seqView.els.forEach((e, j) => {
      e.g.classList.toggle('future', j > k);
      e.g.classList.toggle('cur', j === k);
      e.shape.setAttribute('marker-end', `url(#${j === k ? 'sa-cur' : e.marker})`);
    });
    if (k >= 0) keepInView($('seq'), 0, (seqView.els[k].y - 13) * zoom.seq + seqView.HH * zoom.seq, 0, 26 * zoom.seq);
  }

  // ---- render: variables + stack ------------------------------------------------------
  function renderVars() {
    const s = steps[cur], fn = fnOfStep(s);
    $('frame-name').textContent = `${fn.qualname} · ${fn.file}:${s.line}`;
    const tbl = $('vars');
    tbl.replaceChildren();
    const snap = snapAt[cur], prev = prevSnapAt[cur];
    const names = Object.keys(snap);
    if (!names.length && s.kind !== 'return' && s.kind !== 'exception') h('tr', { class: 'empty' }, tbl).appendChild(h('td', { colspan: 2 }, null, 'No local variables yet'));
    for (const name of names) {
      const isNew = prev && !(name in prev);
      const isChanged = prev && name in prev && prev[name] !== snap[name];
      const tr = h('tr', { class: isNew || isChanged ? 'changed' : '' }, tbl);
      const tn = h('td', { class: 'n' }, tr, name);
      if (isNew) h('span', { class: 'tag' }, tn, 'new');
      const tv = h('td', {}, tr);
      if (isChanged) h('span', { class: 'was' }, tv, prev[name]);
      tv.appendChild(document.createTextNode(snap[name]));
    }
    if (s.kind === 'return') {
      const tr = h('tr', { class: s.raised ? 'exc' : 'ret' }, tbl);
      h('td', { class: 'n' }, tr, s.raised ? 'raises' : s.yield ? 'yields' : 'returns');
      h('td', {}, tr, s.raised ? `${s.type}: ${s.message}` : s.value);
    }
    if (s.kind === 'exception') {
      const tr = h('tr', { class: 'exc' }, tbl);
      h('td', { class: 'n' }, tr, 'raised');
      h('td', {}, tr, `${s.type}: ${s.message}`);
    }
    const ol = $('stack');
    ol.replaceChildren();
    const st = stackAt[cur];
    for (let k = st.length - 1; k >= 0; k--) {
      const fid = st[k], f = frames[fid], ffn = fns[f.fn];
      let line = ffn.line;
      for (let j = cur; j >= 0; j--) if (steps[j].frame === fid) { line = steps[j].line; break; }
      const li = h('li', { class: k === st.length - 1 ? 'top' : '' }, ol, ffn.qualname);
      h('small', {}, li, `${ffn.file}:${line}`);
    }
  }

  // ---- render: source ------------------------------------------------------------------
  const codeCache = {};
  let shownFile = null, peek = null;
  function buildCode(file) {
    if (codeCache[file]) return codeCache[file];
    const wrap = h('div');
    const lines = (files[file] ? files[file].source : '').split('\n');
    if (lines.length && lines[lines.length - 1] === '') lines.pop();
    const rows = lines.map((text, i) => {
      const no = i + 1, st = (lineStatus[file] || {})[no];
      const cls = ['ln'];
      if (isChangedLine(file, no)) cls.push(st === 'not_run' ? 'miss' : 'chg');
      const row = h('div', { class: cls.join(' ') }, wrap);
      h('span', { class: 'no' }, row, String(no));
      h('span', { class: 'mk' }, row);
      h('span', { class: 'tx' }, row, text || ' ');
      return row;
    });
    return (codeCache[file] = { wrap, rows, lastCur: null });
  }

  function renderCode() {
    const s = steps[cur];
    const file = peek ? peek.file : fnOfStep(s).file;
    const line = peek ? peek.line : s.line;
    const view = buildCode(file);
    const host = $('code');
    if (shownFile !== file) { host.replaceChildren(view.wrap); shownFile = file; }
    $('code-file').textContent = `${file}:${line}`;
    if (view.lastCur) view.lastCur.classList.remove('cur');
    const row = view.rows[line - 1];
    if (row) {
      if (!peek) { row.classList.add('cur'); view.lastCur = row; }
      else { row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash'); }
      const y = row.getBoundingClientRect().top - host.getBoundingClientRect().top + host.scrollTop;
      keepInView(host, 0, y, 0, row.offsetHeight);
    }
  }

  // ---- PR coverage ---------------------------------------------------------------------
  const isJava = T.meta.language === 'java';
  const STATUS_TEXT = { ran: 'ran', not_run: 'never ran', import_time: isJava ? 'class load' : 'import', neutral: 'no code' };
  function renderMeter() {
    const m = $('cov-meter');
    m.replaceChildren();
    if (!cov) {
      h('span', { class: 'label', style: 'grid-column: span 2' }, m, 'No --base given, so PR changes are not marked.');
      return;
    }
    const s = cov.summary;
    const pct = s.percent === null ? '—' : `${s.percent}%`;
    h('span', { class: `big ${s.not_run ? 'bad' : 'good'}` }, m, pct);
    const lab = h('span', { class: 'label' }, m);
    h('b', {}, lab, `${s.ran} of ${s.measured}`);
    lab.appendChild(document.createTextNode(' changed lines ran in this test'));
    if (s.not_run) { lab.appendChild(document.createTextNode(' · ')); h('span', { class: 'miss' }, lab, `${s.not_run} never ran`); }
    const bar = h('span', { class: 'bar' }, m);
    if (s.measured) {
      h('i', { class: 'r', style: `width:${(100 * s.ran) / s.measured}%` }, bar);
      h('i', { class: 'n', style: `width:${(100 * s.not_run) / s.measured}%` }, bar);
    }
  }

  function buildCoverage() {
    const host = $('cov');
    host.replaceChildren();
    if (!cov) { h('p', { class: 'muted' }, host, 'Run with --base <ref> (for example --base main) to see which of the PR\'s changed lines this test ran.'); return; }
    const s = cov.summary;
    const sum = h('div', { class: 'cov-sum' }, host);
    h('span', { class: 'big', style: `color:var(${s.not_run ? '--miss' : '--ran'})` }, sum, s.percent === null ? '—' : `${s.percent}%`);
    h('span', {}, sum, `${s.ran} of ${s.measured} changed lines ran · ${s.not_run} never ran`);
    const bar = h('span', { class: 'bar' }, sum);
    if (s.measured) { h('i', { class: 'r', style: `width:${(100 * s.ran) / s.measured}%` }, bar); h('i', { class: 'n', style: `width:${(100 * s.not_run) / s.measured}%` }, bar); }
    const d = T.meta.diff || {};
    h('p', { class: 'cov-note' }, host,
      `Changes since ${d.base || 'base'} (${d.merge_base || '?'}…${d.head || '?'}). Tap a line to jump to it. ` +
      (isJava ? '"class load" lines (static fields and blocks) run when the class loads, not in the test. '
        : '"import" lines run when the module loads, not in the test. ') +
      (cov.skipped_test_files.length ? `Test files are not counted: ${cov.skipped_test_files.join(', ')}.` : ''));
    for (const [file, statuses] of Object.entries(cov.files)) {
      const rank = { not_run: 0, ran: 1, import_time: 2, neutral: 3 };
      const entries = Object.entries(statuses).map(([l, st]) => [+l, st]).sort((a, b) => rank[a[1]] - rank[b[1]] || a[0] - b[0]);
      const nr = entries.filter(([, st]) => st === 'not_run').length;
      const head = h('div', { class: 'cov-file' }, host);
      h('b', {}, head, file);
      h('span', {}, head, nr ? `${nr} never ran` : 'all ran');
      const src = (files[file] ? files[file].source : '').split('\n');
      for (const [line, st] of entries) {
        const row = h('button', { class: `cov-row ${st}`, type: 'button' }, host);
        h('span', { class: `st ${st}` }, row, STATUS_TEXT[st] || st);
        h('span', { class: 'no' }, row, String(line));
        h('code', {}, row, (src[line - 1] || '').trim());
        row.addEventListener('click', () => openLine(file, line, st));
      }
    }
  }

  function openLine(file, line, st) {
    if (st === 'ran') {
      const j = steps.findIndex((s) => s.kind === 'line' && s.line === line && fnOfStep(s).file === file);
      if (j >= 0) { peek = null; go(j); if (narrow()) setView('flow'); else setSide('code'); return; }
    }
    peek = { file, line };
    setSide('code');
    if (narrow()) setView('code');
    renderCode();
  }

  // ---- caption, counter -----------------------------------------------------------------
  function renderCaption() {
    const s = steps[cur], fn = fnOfStep(s), cap = $('caption');
    cap.replaceChildren();
    const where = `${fn.qualname} · ${fn.file.split('/').pop()}:${s.line}`;
    h('span', { class: 'k' }, cap, where + '  ');
    if (s.kind === 'call') h('span', {}, cap, s.resume ? 'resumes' : 'called');
    else if (s.kind === 'return') h('span', { class: s.raised ? 'x' : 'v' }, cap, s.raised ? `✖ raises ${s.type}` : `↩ ${s.value}`);
    else if (s.kind === 'exception') h('span', { class: 'x' }, cap, `✖ ${s.type}: ${s.message}`);
    else {
      const prev = prevSnapAt[cur], snap = snapAt[cur];
      const ch = prev ? Object.keys(snap).filter((k) => prev[k] !== snap[k]) : [];
      if (ch.length) h('span', { class: 'v' }, cap, ch.slice(0, 3).map((k) => `${k} = ${clip(snap[k], 40)}`).join('   '));
      if (isChangedLine(fn.file, s.line)) h('span', { class: 'v' }, cap, '   Δ PR line');
    }
    $('counter').textContent = `step ${cur + 1} / ${N}`;
    $('scrub').value = cur;
  }

  function render() {
    renderCaption();
    renderFlow();
    renderVars();
    renderSeq();
    renderCode();
  }

  // ---- navigation -------------------------------------------------------------------------
  function go(i) {
    cur = Math.max(0, Math.min(N - 1, i));
    peek = null;
    render();
  }
  function setPlaying(on) {
    playing = on && cur < N - 1;
    const b = $('b-play');
    b.textContent = playing ? '❚❚' : '▶';
    b.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    clearTimeout(timer);
    if (playing) tick();
  }
  function tick() {
    const speed = +$('speed').value || 1;
    timer = setTimeout(() => {
      if (!playing) return;
      if (cur >= N - 1) { setPlaying(false); return; }
      go(cur + 1);
      tick();
    }, 650 / speed);
  }
  function jumpToNode(fnId, nodeId) {
    const flow = buildFlow(fnId);
    const match = (j) => fnIdOfStep(steps[j]) === fnId && nodeAtStep(j, flow) === nodeId;
    for (let j = cur + 1; j < N; j++) if (match(j)) return go(j);
    for (let j = 0; j <= cur; j++) if (match(j)) return go(j);
  }
  function nextChange() {
    for (let j = cur + 1; j < N; j++) {
      const s = steps[j];
      if (s.kind === 'line' && isChangedLine(fnOfStep(s).file, s.line)) return go(j);
    }
    const b = $('b-next-change');
    b.textContent = 'no more Δ';
    setTimeout(() => (b.textContent = 'next Δ'), 1200);
  }

  // ---- views (phone tabs) and side tabs -----------------------------------------------------
  function setView(view) {
    const panelView = view === 'cov' ? 'code' : view;
    document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('on', p.dataset.view === panelView));
    if (view === 'cov' || view === 'code') setSide(view);
    if (view === 'flow' && shownFlow) { shownFlow.scale = shownFlow.scale; applyZoom('flow'); }
    render();
  }
  function setSide(side) {
    $('code').hidden = side !== 'code';
    $('cov').hidden = side !== 'cov';
    $('code-file').hidden = side !== 'code';
    document.querySelectorAll('.desk-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.side === side));
  }

  // ---- wire up ------------------------------------------------------------------------------
  function init() {
    const m = T.meta;
    $('test-id').textContent = m.test;
    $('test-id').title = m.test;
    const oc = $('outcome');
    oc.textContent = m.outcome + (m.truncated ? ' · truncated' : '');
    oc.className = `chip ${m.outcome}`;
    renderMeter();
    buildCoverage();

    const sel = $('fn-select');
    h('option', { value: '' }, sel, 'Follow the current call');
    const seen = [];
    for (const s of steps) { const id = fnIdOfStep(s); if (!seen.includes(id)) seen.push(id); }
    for (const id of seen) h('option', { value: id }, sel, `${fns[id].qualname}  (${fns[id].file.split('/').pop()}:${fns[id].line})`);
    sel.addEventListener('change', () => { pinnedFn = sel.value; render(); });

    const scrub = $('scrub');
    scrub.max = N - 1;
    scrub.addEventListener('input', () => { setPlaying(false); go(+scrub.value); });
    $('b-first').addEventListener('click', () => { setPlaying(false); go(0); });
    $('b-back').addEventListener('click', () => { setPlaying(false); go(cur - 1); });
    $('b-fwd').addEventListener('click', () => { setPlaying(false); go(cur + 1); });
    $('b-play').addEventListener('click', () => setPlaying(!playing));
    $('b-next-change').addEventListener('click', () => { setPlaying(false); nextChange(); });
    $('speed').addEventListener('change', () => { if (playing) { clearTimeout(timer); tick(); } });
    $('cov-meter').addEventListener('click', () => (narrow() ? setView('cov') : setSide('cov')));
    document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
    document.querySelectorAll('.desk-tabs button').forEach((b) => b.addEventListener('click', () => { setSide(b.dataset.side); render(); }));
    document.querySelectorAll('[data-zoom]').forEach((b) => b.addEventListener('click', () => changeZoom(b.dataset.zoom, +b.dataset.dir)));
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey) return;
      const k = e.key;
      if (k === ' ' || k === 'k') { e.preventDefault(); setPlaying(!playing); }
      else if (k === 'ArrowRight' || k === 'l') { setPlaying(false); go(cur + 1); }
      else if (k === 'ArrowLeft' || k === 'j') { setPlaying(false); go(cur - 1); }
      else if (k === 'Home') go(0);
      else if (k === 'End') go(N - 1);
      else if (k === 'n') nextChange();
    });
    const startAt = new URLSearchParams(location.hash.slice(1)).get('step');
    setSide('code');
    go(startAt ? +startAt : 0);
  }
  init();
  window.wiz = { go, setView, setSide, setPlaying, get step() { return cur; }, steps: N };
})();
