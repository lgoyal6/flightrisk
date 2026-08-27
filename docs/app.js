// Draws docs/data/*.json, which scripts/make_page_data.py generates from the
// same feature matrix and report CSVs the backtest writes. Nothing here is
// fitted, smoothed or rounded up: the page can only be as good as the run.

const el = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pct = (x, d = 1) => `${(x * 100).toFixed(d)}%`;

const state = { u: null, band: 'all banks', bucket: 7, models: null, split: 'backtest', pick: null };

// A fixed pixel height. Neither chart has anything that grows vertically with
// the viewport, so scaling height with width would only add empty box.
function fitCanvas(canvas, h0) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w0 = canvas.clientWidth || 1200;
  canvas.width = Math.round(w0 * dpr);
  canvas.height = Math.round(h0 * dpr);
  canvas.style.height = h0 + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w0, h0);
  return { ctx, w: w0, h: h0 };
}

// Text drawn straight over a dashed line still shows the dashes between the
// glyph strokes, so a number sitting on the base-rate line reads as struck
// through. Painting the paper behind it first is the only thing that clears it.
function labelOnPaper(ctx, text, x, y, align = 'center') {
  const w = ctx.measureText(text).width;
  const left = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
  const prev = ctx.fillStyle;
  ctx.fillStyle = css('--paper');
  ctx.fillRect(left - 3, y - 11, w + 6, 14);
  ctx.fillStyle = prev;
  ctx.textAlign = align;
  ctx.fillText(text, x, y);
}

// ---------------------------------------------------------- figure 1: the U

function drawU() {
  const band = state.u[state.band];
  if (!band) return;
  const { ctx, w, h } = fitCanvas(el('plot-u'), 300);
  const pad = { l: 58, r: 22, t: 20, b: 54 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const bars = band.buckets;
  const top = Math.max(...bars.map((b) => b.rate)) * 1.15;
  const Y = (v) => pad.t + ih - (v / top) * ih;

  ctx.strokeStyle = css('--hair');
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, pad.t + ih); ctx.lineTo(pad.l + iw, pad.t + ih);
  ctx.stroke();

  ctx.font = "11px 'Courier New', monospace";
  ctx.fillStyle = css('--faint');
  ctx.textAlign = 'right';
  const stepY = top > 0.15 ? 0.05 : 0.02;
  for (let v = 0; v <= top; v += stepY) {
    ctx.fillText(pct(v, 0), pad.l - 8, Y(v) + 3);
    if (v > 0) {
      ctx.strokeStyle = css('--grid');
      ctx.beginPath(); ctx.moveTo(pad.l, Y(v)); ctx.lineTo(pad.l + iw, Y(v)); ctx.stroke();
    }
  }

  const slot = iw / bars.length;
  const bw = slot * 0.62;
  bars.forEach((b, i) => {
    const x = pad.l + slot * i + (slot - bw) / 2;
    const y = Y(b.rate);
    ctx.fillStyle = css('--ox');
    ctx.fillRect(x, y, bw, pad.t + ih - y);
    // The selected bucket is outlined rather than recoloured, so the
    // selection survives being read without colour.
    if (i === state.bucket) {
      ctx.strokeStyle = css('--ink');
      ctx.lineWidth = 2;
      ctx.strokeRect(x - 2, y - 2, bw + 4, pad.t + ih - y + 4);
    }
  });

  // The base rate is the line every bar is being compared against.
  ctx.save();
  ctx.strokeStyle = css('--bad');
  ctx.lineWidth = 1.4;
  ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(pad.l, Y(band.base_rate)); ctx.lineTo(pad.l + iw, Y(band.base_rate)); ctx.stroke();
  ctx.restore();
  // Centred over the shortest bar, which is the only stretch of this line
  // guaranteed to be clear: every bar taller than the base rate covers it.
  const low = bars.reduce((best, b, i) => (b.rate < bars[best].rate ? i : best), 0);
  ctx.font = "12px 'Times New Roman', serif";
  const caption = `base rate ${pct(band.base_rate)}: the average bank`;
  const half = ctx.measureText(caption).width / 2;
  ctx.textAlign = 'center';
  ctx.fillStyle = css('--bad');
  ctx.fillText(
    caption,
    Math.min(Math.max(pad.l + slot * (low + 0.5), pad.l + half), pad.l + iw - half),
    Y(band.base_rate) - 8,
  );

  // Bar labels last, so the base-rate dashes do not run through the numbers on
  // the buckets that sit just under the line.
  ctx.textAlign = 'center';
  bars.forEach((b, i) => {
    const x = pad.l + slot * i + (slot - bw) / 2;
    ctx.fillStyle = css('--sub');
    ctx.font = "12px 'Times New Roman', serif";
    labelOnPaper(ctx, `${b.lift.toFixed(2)}x`, x + bw / 2, Y(b.rate) - 7);
    ctx.fillStyle = css('--faint');
    ctx.font = "10px 'Courier New', monospace";
    ctx.fillText(b.label, x + bw / 2, pad.t + ih + 16);
  });

  ctx.textAlign = 'center';
  ctx.fillStyle = css('--faint');
  ctx.font = "11px 'Courier New', monospace";
  ctx.fillText("this quarter's deposit growth", pad.l + iw / 2, h - 8);
}

function renderU() {
  const band = state.u[state.band];
  if (!band) return;
  state.bucket = Math.min(state.bucket, band.buckets.length - 1);
  const b = band.buckets[state.bucket];
  el('u-bucket').textContent = b.label;
  el('u-n').textContent = b.n.toLocaleString('en-US');
  el('u-rate').textContent = pct(b.rate, 2);
  el('u-lift').textContent = `${b.lift.toFixed(2)}x`;
  el('cap-what').textContent =
    `${state.band}, ${band.n.toLocaleString('en-US')} bank-quarters`;
  drawU();

  const banner = el('u-banner');
  const ends = (band.buckets[0].lift + band.buckets[band.buckets.length - 1].lift) / 2;
  const middle = Math.min(...band.buckets.map((x) => x.lift));
  if (b.lift >= 1.3) {
    banner.className = 'banner alarm';
    banner.textContent =
      `Banks that moved ${b.label} this quarter dropped 5% or more next quarter ` +
      `${b.lift.toFixed(2)}x as often as the average bank.`;
  } else if (b.lift <= 0.8) {
    banner.className = 'banner calm';
    banner.textContent =
      `The safe part of the curve. Moving ${b.label} is ${b.lift.toFixed(2)}x the base rate, ` +
      `and both ends of this chart are above ${ends.toFixed(1)}x.`;
  } else {
    banner.className = 'banner';
    banner.textContent =
      `${b.lift.toFixed(2)}x the base rate. The bottom of the curve is ${middle.toFixed(2)}x ` +
      `and both ends are far above it.`;
  }
}

// ---------------------------------------------------- figure 2: the ranking

function currentModels() {
  const rows = state.models[state.split === 'backtest' ? 'backtest' : 'out_of_time'].slice();
  if (state.split === 'backtest') rows.push(state.models.shuffled);
  return rows.sort((a, b) => b.lift_at_1pct - a.lift_at_1pct);
}

function isControl(m) { return m.model.startsWith('labels shuffled'); }

function drawModels() {
  const rows = currentModels();
  const { ctx, w, h } = fitCanvas(el('plot-m'), Math.max(230, rows.length * 34 + 82));
  const pad = { l: 232, r: 74, t: 16, b: 62 };
  const iw = w - pad.l - pad.r;
  const top = Math.max(...rows.map((m) => m.lift_at_1pct), 1.2) * 1.06;
  const X = (v) => pad.l + (v / top) * iw;
  const rowH = (h - pad.t - pad.b) / rows.length;
  const barH = Math.min(rowH * 0.62, 22);

  // From 2 up: a gridline at 0 is the axis, and its label would sit under the
  // break-even marker.
  for (let v = 2; v <= top; v += 2) {
    ctx.strokeStyle = css('--grid');
    ctx.beginPath(); ctx.moveTo(X(v), pad.t); ctx.lineTo(X(v), pad.t + rows.length * rowH); ctx.stroke();
    ctx.fillStyle = css('--faint');
    ctx.font = "11px 'Courier New', monospace";
    ctx.textAlign = 'center';
    ctx.fillText(`${v}x`, X(v), h - 40);
  }

  rows.forEach((m, i) => {
    const y = pad.t + i * rowH + (rowH - barH) / 2;
    const bw = Math.max(X(m.lift_at_1pct) - pad.l, 1);
    ctx.fillStyle = css('--ox');
    ctx.fillRect(pad.l, y, bw, barH);
    // Baselines and the control are hatched, so "this is not the model" is
    // legible without relying on a colour difference.
    if (m.is_baseline || isControl(m)) {
      ctx.save();
      ctx.beginPath(); ctx.rect(pad.l, y, bw, barH); ctx.clip();
      ctx.strokeStyle = css('--paper');
      ctx.lineWidth = 1.6;
      for (let k = -barH; k < bw + barH; k += 7) {
        ctx.beginPath(); ctx.moveTo(pad.l + k, y + barH); ctx.lineTo(pad.l + k + barH, y); ctx.stroke();
      }
      ctx.restore();
    }
    if (i === state.pick) {
      ctx.strokeStyle = css('--ink');
      ctx.lineWidth = 2;
      ctx.strokeRect(pad.l - 2, y - 2, bw + 4, barH + 4);
    }
    ctx.textAlign = 'right';
    ctx.font = "12px 'Times New Roman', serif";
    ctx.fillStyle = i === state.pick ? css('--ink') : css('--sub');
    ctx.fillText(m.model.replace('baseline: ', ''), pad.l - 10, y + barH / 2 + 4);
  });

  ctx.save();
  ctx.strokeStyle = css('--bad');
  ctx.lineWidth = 1.4;
  ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(X(1), pad.t); ctx.lineTo(X(1), pad.t + rows.length * rowH); ctx.stroke();
  ctx.restore();

  // After the break-even line, so the three bars that end beside it do not get
  // their number struck through by the dashes.
  ctx.textAlign = 'left';
  ctx.font = "12px 'Times New Roman', serif";
  ctx.fillStyle = css('--sub');
  rows.forEach((m, i) => {
    const y = pad.t + i * rowH + (rowH - barH) / 2;
    labelOnPaper(ctx, `${m.lift_at_1pct.toFixed(2)}x`, X(m.lift_at_1pct) + 8, y + barH / 2 + 4, 'left');
  });

  // Both captions get their own line under the ticks. Anywhere inside the plot
  // they land either on a bar or on a tick label.
  ctx.textAlign = 'left';
  ctx.fillStyle = css('--bad');
  ctx.font = "12px 'Times New Roman', serif";
  ctx.fillText('dashed: 1x, no better than working the list at random', pad.l, h - 16);

  ctx.textAlign = 'right';
  ctx.fillStyle = css('--faint');
  ctx.font = "11px 'Courier New', monospace";
  ctx.fillText('hatched: a baseline or the control, not a model', w - pad.r, h - 16);
  return { rows, pad, rowH };
}

function renderModels() {
  const rows = currentModels();
  if (state.pick === null || state.pick >= rows.length) {
    // Opens on the plain structured model, not on whichever variant scored
    // highest. The macro arm wins by 0.04x, inside the fold spread, and
    // opening on it would quietly claim the macro features earned something.
    const real = (m) => !m.is_baseline && !isControl(m);
    state.pick = rows.findIndex((m) => real(m) && !m.model.includes('+'));
    if (state.pick < 0) state.pick = rows.findIndex(real);
    if (state.pick < 0) state.pick = 0;
  }
  drawModels();
  const m = rows[state.pick];
  el('m-name').textContent = m.model;
  el('m-prec').textContent = pct(m.precision_at_1pct);
  el('m-lift').textContent = `${m.lift_at_1pct.toFixed(2)}x`;
  el('m-auc').textContent = m.roc_auc_within_quarter.toFixed(3);
  el('cap-n').textContent =
    `${m.base_rate ? pct(m.base_rate) + ' of bank-quarters were drawdowns' : ''}`;

  const b = el('m-banner');
  if (isControl(m)) {
    b.className = 'banner calm';
    b.textContent =
      `The control: same pipeline, labels shuffled first. Lift ${m.lift_at_1pct.toFixed(2)}x and ` +
      `within-quarter AUC ${m.roc_auc_within_quarter.toFixed(3)}. It finds nothing, which is the point.`;
  } else if (m.roc_auc_within_quarter < 0.5) {
    b.className = 'banner alarm';
    b.textContent =
      `Worse than random at ordering a quarter (AUC ${m.roc_auc_within_quarter.toFixed(3)}) and still ` +
      `${m.lift_at_1pct.toFixed(2)}x at the top. The U inverts the relationship across the middle.`;
  } else if (m.lift_at_1pct < 2) {
    b.className = 'banner alarm';
    b.textContent =
      `${m.lift_at_1pct.toFixed(2)}x. Barely better than working the list at random, and under the ` +
      `naive baseline on this split.`;
  } else {
    b.className = 'banner calm';
    b.textContent =
      `Of the top 1% it flags, ${pct(m.precision_at_1pct)} really did lose 5% of deposits next quarter, ` +
      `against a base rate of ${pct(m.base_rate)}.`;
  }
}

function pickFromClick(ev) {
  const canvas = el('plot-m');
  const { rows, pad, rowH } = drawModels();
  const rect = canvas.getBoundingClientRect();
  const y = ev.clientY - rect.top - pad.t;
  const i = Math.floor(y / rowH);
  if (i >= 0 && i < rows.length) { state.pick = i; renderModels(); }
}

// ---------------------------------------------------------------- wiring

function picker(node, items, current, onPick) {
  node.innerHTML = '';
  items.forEach(({ key, label }) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('aria-pressed', String(key === current()));
    b.addEventListener('click', () => {
      onPick(key);
      [...node.children].forEach((c) => c.setAttribute('aria-pressed', String(c === b)));
    });
    node.appendChild(b);
  });
}

async function main() {
  const [uRes, mRes] = await Promise.all([fetch('./data/ushape.json'), fetch('./data/models.json')]);
  if (!uRes.ok || !mRes.ok) {
    el('u-banner').textContent = 'Could not load the measurements.';
    return;
  }
  state.u = await uRes.json();
  state.models = await mRes.json();

  picker(
    el('bands'),
    Object.keys(state.u).map((k) => ({ key: k, label: k })),
    () => state.band,
    (k) => { state.band = k; renderU(); },
  );
  picker(
    el('splits'),
    [{ key: 'backtest', label: 'Walk-forward backtest' }, { key: 'oot', label: 'Out-of-time holdout' }],
    () => state.split,
    (k) => {
      state.split = k;
      state.pick = null;
      el('cap-split').textContent = k === 'backtest' ? 'walk-forward backtest' : 'out-of-time holdout';
      renderModels();
    },
  );

  const scrub = el('scrub');
  scrub.max = String(state.u[state.band].buckets.length - 1);
  scrub.value = String(state.bucket);
  scrub.addEventListener('input', (e) => { state.bucket = Number(e.target.value); renderU(); });
  el('plot-m').addEventListener('click', pickFromClick);
  window.addEventListener('resize', () => { renderU(); renderModels(); });

  renderU();
  renderModels();
}

main();

// ------------------------------------------------- figure 3: a quarter's output

async function alerts() {
  const res = await fetch('./data/alerts.json');
  if (!res.ok) return;
  const rows = await res.json();

  // The list is built stratified by size, so showing the head of the file
  // would show one size band and imply the whole quarter looked like that.
  const perBand = 4;
  const seen = new Map();
  const shown = rows.filter((r) => {
    const n = seen.get(r.size_stratum) || 0;
    if (n >= perBand) return false;
    seen.set(r.size_stratum, n + 1);
    return true;
  });

  const money = (m) => (m >= 1000 ? `$${(m / 1000).toFixed(1)}B` : `$${m.toFixed(0)}M`);
  const head =
    '<tr><th>size band</th><th>deposits</th><th>score</th><th>pctile in band</th>' +
    '<th class="why">why it was flagged</th></tr>';
  const body = shown
    .map(
      (r) =>
        `<tr><td>${r.size_stratum}</td><td class="num">${money(r.deposits_usd_m)}</td>` +
        `<td class="num">${r.score.toFixed(3)}</td>` +
        `<td class="num">${r.percentile.toFixed(1)}</td>` +
        `<td class="why">${r.reason.replace(/^Flagged because /, '')}</td></tr>`,
    )
    .join('');
  el('alerts').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  el('alert-cap').textContent = `top ${perBand} in each size band, of ${rows.length}`;
}

alerts();
