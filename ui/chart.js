// Options-analysis pages for chart.html and the local app (ui/index.html): hand-drawn SVG, no libraries.
// OAChart.render(root, data) draws the ticker page (header, KPIs, expected-move cone, term structure, strategy
// screens, smile, open interest, dealer gamma, earnings, summaries, option chain, unusual activity, data notes) or
// the market page (thermometer, VIX history, SPX term structure and gamma, skew board, expected moves) from the JSON
// that scripts/chart.py builds. Every number shown comes from that JSON; this file only lays it out.
(function (global) {
  "use strict";

  const I18N = global.OA_I18N;
  const t = (key, ...a) => I18N.t(key, ...a);
  const tv = (prefix, value) => I18N.tv(prefix, value);
  const VIS_KEY = "oa-vis";
  const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5"];
  const TERM_TICKS = [1, 7, 14, 30, 60, 90, 180, 365, 730, 1095, 1825, 2555, 3650];
  const STRAT_ORDER = ["short_put", "covered_call", "long_call", "long_put", "earnings_straddle"];
  const FLAG_ORDER = ["vix_20_or_more", "term_inverted", "front_stress", "skew_steep", "negative_gamma", "realized_above_implied"];
  let uid = 0;

  // ------------------------------------------------------------------ formatting
  const isNum = v => typeof v === "number" && isFinite(v);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MINUS = "−", DASH = "–";
  const sign = v => (v > 0 ? "+" : v < 0 ? MINUS : "");
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function grp(v, d) {
    if (!isNum(v)) return DASH;
    const s = Math.abs(v).toFixed(d), [i, f] = s.split(".");
    return (v < 0 && +s !== 0 ? MINUS : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (f ? "." + f : "");
  }
  const fmtPx = v => grp(v, 2);                                    // prices and premiums: 2 decimals
  const fmtStrike = v => isNum(v) ? grp(v, 2).replace(/\.?0+$/, "") : DASH;
  const fmtInt = v => grp(v, 0);
  function fmtPct(v, d = 1, signed = false) {
    if (!isNum(v)) return DASH;
    const r = +(v * 100).toFixed(d);
    return (signed ? sign(r) : r < 0 ? MINUS : "") + Math.abs(r).toFixed(d) + "%";
  }
  const fmtIV = v => fmtPct(v, 1);                                 // IV: percent, 1 decimal
  function fmtPts(v, signed = true) {                              // vol points: 1 decimal
    if (!isNum(v)) return DASH;
    const r = +(v * 100).toFixed(1);
    return (signed ? sign(r) : r < 0 ? MINUS : "") + Math.abs(r).toFixed(1) + " " + t("u_pts");
  }
  const fmtDelta = v => isNum(v) ? (v < 0 ? MINUS : "") + Math.abs(v).toFixed(2) : DASH;
  const fmtRatio = (v, d = 2) => isNum(v) ? (v < 0 ? MINUS : "") + Math.abs(v).toFixed(d) : DASH;
  const fmtPctl = v => isNum(v) ? v.toFixed(0) : DASH;
  function fmtCompact(v) {
    if (!isNum(v)) return DASH;
    const a = Math.abs(v);
    for (const [d, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
      if (a >= d) return (v < 0 ? MINUS : "") + (a / d).toFixed(a / d >= 100 ? 0 : 1) + s;
    }
    return (v < 0 ? MINUS : "") + String(Math.round(a));
  }
  const fmtUsd = v => isNum(v) ? (v < 0 ? MINUS : "") + "$" + fmtCompact(Math.abs(v)) : DASH;
  const fmtUsdFull = v => isNum(v) ? (v < 0 ? MINUS : "") + "$" + grp(Math.abs(v), 0) : DASH;
  const pctCls = v => !isNum(v) || v === 0 ? "" : v > 0 ? "pos" : "neg";
  const dayOf = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 864e5;
  const isoOf = day => new Date(day * 864e5).toISOString().slice(0, 10);
  function fmtDate(iso, year = false) {
    if (!iso) return DASH;
    try {
      return new Date(iso.slice(0, 10) + "T12:00:00Z").toLocaleDateString(I18N.locale(),
        year ? { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" });
    } catch (e) { return iso.slice(0, 10); }
  }
  function fmtMonth(iso) {                                         // "Nov 25": month and two-digit year
    try {
      return new Date(iso.slice(0, 10) + "T12:00:00Z").toLocaleDateString(I18N.locale(), { month: "short", year: "2-digit", timeZone: "UTC" });
    } catch (e) { return iso.slice(0, 7); }
  }
  const hhmm = iso => iso ? String(iso).slice(11, 16) : DASH;
  function niceTicks(lo, hi, n) {
    const raw = (hi - lo) / Math.max(1, n), mag = 10 ** Math.floor(Math.log10(raw || 1));
    const step = [1, 2, 2.5, 5, 10].find(m => m * mag >= raw) * mag, out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return { ticks: out, step };
  }
  const tickLabel = (v, step) => v.toFixed(Math.min(4, (String(+step.toPrecision(6)).split(".")[1] || "").length));
  const fmtTick = (v, step) => grp(v, Math.min(4, (String(+step.toPrecision(6)).split(".")[1] || "").length));
  const measure = (() => {
    const ctx = global.document.createElement("canvas").getContext("2d");
    return (s, px = 11, weight = 400) => {
      if (!ctx) return String(s).length * px * 0.6;
      ctx.font = `${weight} ${px}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      return ctx.measureText(String(s)).width;
    };
  })();
  const minOf = a => a.reduce((m, v) => (isNum(v) && v < m ? v : m), Infinity);
  const maxOf = a => a.reduce((m, v) => (isNum(v) && v > m ? v : m), -Infinity);

  // An English sentence from Python, or its translation when it carries an id that the current language has a
  // text for (plan 9.5); market warnings carry the member key.
  function said(text, id, prefix) {
    if (id && id.id && I18N.has(prefix + id.id)) {
      const s = t(prefix + id.id, ...(id.args || []));
      return id.key ? `${id.key}: ${s}` : s;
    }
    return text;
  }
  const notesOf = (b, prefix = "note_") => ((b && b.notes) || []).map((n, i) => said(n, (b.note_ids || [])[i], prefix));
  function reasonOf(b) {
    if (!b) return "";
    if (b.reason_id && I18N.has("reason_" + b.reason_id)) return t("reason_" + b.reason_id, b.n_obs ?? b.n, b.need);
    return b.reason || t("unavailable");
  }
  const tagsLabel = tags => (tags || []).map(x => tv("tag_", x)).join(" · ");

  function loadVis() {
    try { return JSON.parse(global.localStorage.getItem(VIS_KEY)) || {}; } catch (e) { return {}; }
  }
  function saveVis(vis) {
    try { global.localStorage.setItem(VIS_KEY, JSON.stringify(vis)); } catch (e) { /* storage blocked */ }
  }

  // ------------------------------------------------------------------ small HTML pieces
  const card = (title, body, cls = "", extra = "") =>
    `<section class="oa-card ${cls}">${title ? `<h3>${title}</h3>` : ""}${extra}${body}</section>`;
  const empty = reason => `<p class="oa-empty">${esc(t("unavailable"))}${reason ? ": " + esc(reason) : ""}</p>`;
  const list = (title, items) => items.length ? `<h4>${esc(title)}</h4><ul class="oa-list">${items.map(x => `<li>${x}</li>`).join("")}</ul>` : "";
  const kv = pairs => `<dl class="oa-kv">${pairs.filter(p => p).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
  function chipsHtml(items, vis) {
    return items.map(it => `<button type="button" class="oa-chip" data-k="${esc(it.key)}" aria-pressed="${vis[it.key] ?? it.on ?? true}"` +
      `${it.title ? ` title="${esc(it.title)}"` : ""}><span class="sw ${it.sw || ""}" style="color:${it.color}"></span>${esc(it.label)}</button>`).join("");
  }
  const isOn = (vis, key, dflt = true) => vis[key] ?? dflt;
  const segHtml = (cls, items, cur) => `<div class="oa-seg ${cls}" role="group">${items.map(([k, label]) =>
    `<button type="button" data-v="${esc(k)}" class="${k === cur ? "on" : ""}" aria-pressed="${k === cur}">${esc(label)}</button>`).join("")}</div>`;

  // ------------------------------------------------------------------ chart frame, scales, axes, hover
  function frame(W, H, m) { return { W, H, L: m.l, R: W - m.r, T: m.t, B: H - m.b }; }
  function lin(lo, hi, a, b) { const k = (b - a) / ((hi - lo) || 1); const f = v => a + (v - lo) * k; f.lo = lo; f.hi = hi; return f; }
  function sqrtScale(lo, hi, a, b) {
    const s = v => Math.sqrt(Math.max(0, v)), sl = s(lo), k = (b - a) / ((s(hi) - sl) || 1);
    const f = v => a + (s(v) - sl) * k; f.lo = lo; f.hi = hi; return f;
  }
  function padRange(lo, hi, frac = 0.08) {
    if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
    if (hi - lo < 1e-12) { const d = Math.abs(lo) * 0.05 || 1; return [lo - d, hi + d]; }
    const d = (hi - lo) * frac;
    return [lo - d, hi + d];
  }
  // horizontal grid lines with labels on the left (side "l") or right ("r")
  function yGrid(F, y, fmt, n = 5, side = "l") {
    const { ticks, step } = niceTicks(y.lo, y.hi, n);
    return ticks.map(v => {
      const yy = y(v).toFixed(1);
      const lab = side === "l" ? `<text x="${F.L - 6}" y="${(+yy + 4).toFixed(1)}" text-anchor="end">${esc(fmt(v, step))}</text>`
        : `<text x="${F.R + 6}" y="${(+yy + 4).toFixed(1)}">${esc(fmt(v, step))}</text>`;
      return `<line class="grid" x1="${F.L}" x2="${F.R}" y1="${yy}" y2="${yy}"/>${lab}`;
    }).join("");
  }
  // x tick labels that do not overlap
  function xTicks(F, x, ticks, fmt) {
    let last = -Infinity, out = "";
    for (const v of ticks) {
      const xx = x(v), s = fmt(v), w = measure(s);
      if (xx - w / 2 < last + 6 || xx < F.L - 1 || xx > F.R + 1 || xx - w / 2 < 1 || xx + w / 2 > F.W - 1) continue;
      out += `<line class="axis" x1="${xx.toFixed(1)}" x2="${xx.toFixed(1)}" y1="${F.B}" y2="${F.B + 4}"/>` +
        `<text x="${xx.toFixed(1)}" y="${F.B + 16}" text-anchor="middle">${esc(s)}</text>`;
      last = xx + w / 2;
    }
    return out;
  }
  const pathOf = pts => pts.length ? "M" + pts.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join("L") : "";
  function svgOpen(F, aria) {
    return `<svg viewBox="0 0 ${F.W} ${F.H}" width="${F.W}" height="${F.H}" role="img" tabindex="0" aria-label="${esc(aria)}">` +
      `<rect class="hit" x="${F.L}" y="${F.T}" width="${Math.max(0, F.R - F.L)}" height="${Math.max(0, F.B - F.T)}" fill="transparent"/>`;
  }
  const svgClose = F => `<line class="xh" x1="0" x2="0" y1="${F.T}" y2="${F.B}" visibility="hidden"/></svg>`;

  // Crosshair and readout over items sorted by pixel x; arrow keys move it, Escape leaves. The hit rectangle is
  // drawn first so markers with <title> stay reachable; the svg itself listens.
  function bindHover(box, readout, xs, show, idle) {
    const svg = box.querySelector("svg");
    if (!svg || !xs.length) { if (readout) readout.innerHTML = idle ? idle() : ""; return; }
    const xh = svg.querySelector(".xh");
    let cur = null;
    const point = i => {
      cur = clamp(i, 0, xs.length - 1);
      xh.setAttribute("x1", xs[cur]); xh.setAttribute("x2", xs[cur]); xh.setAttribute("visibility", "visible");
      readout.innerHTML = show(cur);
    };
    const leave = () => { cur = null; xh.setAttribute("visibility", "hidden"); readout.innerHTML = idle ? idle() : ""; };
    const nearest = px => {
      let lo = 0, hi = xs.length - 1;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] < px) lo = m; else hi = m; }
      return Math.abs(xs[lo] - px) <= Math.abs(xs[hi] - px) ? lo : hi;
    };
    const fromEvent = e => {
      const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * (svg.viewBox.baseVal.width / r.width);
      const hit = svg.querySelector(".hit");
      const x0 = +hit.getAttribute("x"), x1 = x0 + +hit.getAttribute("width");
      if (px < x0 - 8 || px > x1 + 8) { leave(); return; }
      point(nearest(px));
    };
    svg.addEventListener("pointermove", fromEvent);
    svg.addEventListener("pointerdown", fromEvent);
    svg.addEventListener("pointerleave", leave);
    svg.addEventListener("blur", leave);
    svg.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Escape") { leave(); return; }
      const step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -10, PageDown: 10, Home: -xs.length, End: xs.length }[e.key];
      if (step == null) return;
      e.preventDefault();
      point((cur ?? (step > 0 ? -1 : xs.length)) + step);
    });
    leave();
  }
  const width = box => Math.max(280, Math.floor(box.clientWidth || 600));
  const chartHeight = (W, ratio, lo, hi) => Math.round(clamp(W * ratio, lo, hi));
  const sw = color => `<span class="sw" style="background:${color}"></span>`;

  // ------------------------------------------------------------------ header, notices, KPIs
  function headerHtml(d) {
    const m = d.meta || {};
    const badges = [];
    if (m.mode === "ticker") {
      badges.push(`<span class="oa-badge">${esc(tv("kind_", m.kind))}</span>`);
      if (m.kind_source === "cboe_fallback") badges.push(`<span class="oa-badge warn">${esc(tv("ksrc_", m.kind_source))}</span>`);
    }
    badges.push(`<span class="oa-badge">${esc(t("badge_delayed"))}</span>`);
    if (m.overnight_quotes) badges.push(`<span class="oa-badge warn">${esc(t("badge_overnight"))}</span>`);
    if (m.closed === false) badges.push(`<span class="oa-badge warn">${esc(t("badge_intraday"))}</span>`);
    if (m.stale && m.stale.is_stale) badges.push(`<span class="oa-badge warn">${esc(t("badge_stale"))}</span>`);
    const title = m.mode === "market" ? esc(t("app_title_market")) : esc(m.ticker);
    const name = m.mode === "market" ? "" : (m.name ? `<span class="oa-name">${esc(m.name)}</span>` : "");
    return `<div class="oa-head"><h1 class="oa-ticker">${title}</h1>${name}${badges.join("")}</div>
      <div class="oa-sub">${esc(t("as_of"))} ${esc(m.as_of)} · ${esc(t("data_time", hhmm(m.quote_time)))} · ${esc(t("source"))}: ${esc(m.data_source)}</div>`;
  }

  function noticesHtml(d) {
    const m = d.meta || {}, n = d.data_notes || {}, c = m.connection;
    const out = [];
    (n.warnings || []).forEach((w, i) => out.push(said(w, (n.warning_ids || [])[i], "warn_")));
    if (!m.fmp_configured) out.push(t(m.mode === "market" ? "notice_fmp_missing_market" : "notice_fmp_missing"));
    if (c && String(c.route || "").startsWith("third-party")) {
      out.push(t("notice_proxy", c.base || "") + (c.tls_verify === false ? " " + t("notice_tls") : ""));
    }
    for (const e of n.errors || []) out.push(t("notice_error", e));
    return out.length ? `<div class="oa-notices" role="status">${out.map(x => `<div class="oa-warn">${esc(x)}</div>`).join("")}</div>` : "";
  }

  const tile = (key, val, sub = "", cls = "", title = "", extra = "") =>
    `<div class="oa-stat ${extra}"${title ? ` title="${esc(title)}"` : ""}><div class="k">${esc(key)}</div><div class="v ${cls}">${val}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;

  function emRow(label, k) {
    const s = k.sub || {};
    if (!isNum(k.value)) return `<div class="em"><span>${esc(label)}</span><b>${DASH}</b></div>`;
    const tip = t("em_tip", fmtPct(s.straddle_pct), s.range_1sd ? fmtPx(s.range_1sd[0]) : DASH, s.range_1sd ? fmtPx(s.range_1sd[1]) : DASH);
    return `<div class="em" title="${esc(tip)}"><span>${esc(label)}</span><b>±${esc(fmtPct(k.value))}</b><span class="oa-sub">${esc(fmtDate(s.expiration))} · ${esc(t("dte_n", s.dte))}</span></div>`;
  }

  function tickerKpis(d) {
    const ks = Object.fromEntries((d.kpis || []).map(k => [k.id, k]));
    const out = [];
    const p = ks.price || {}, ps = p.sub || {};
    out.push(tile(t("kpi_price"), esc(fmtPx(p.value)),
      `<span class="${pctCls(ps.change_pct)}">${esc(fmtPct(ps.change_pct, 2, true))}</span>${ps.basis && ps.basis !== "close" ? " · " + esc(tv("basis_", ps.basis)) : ""}`));
    out.push(`<div class="oa-stat"><div class="k">${esc(t("kpi_em"))}</div>${emRow(t("kpi_em_next_week"), ks.em_next_week || {})}${emRow(t("kpi_em_monthly"), ks.em_monthly || {})}</div>`);
    const e = ks.earnings || {}, es = e.sub || {};
    if (es.applies === false) out.push(tile(t("kpi_earnings"), DASH, esc(t("no_earnings_kind", tv("kind_", d.meta.kind)))));
    else if (e.value) {
      const when = es.event_today ? t("earnings_today_short") : t("in_tdays", es.trading_days);
      const cmp = isNum(es.implied_window) ? t("implied_vs_past", fmtPct(es.implied_window), fmtPct(es.hist_mean_abs)) : "";
      out.push(tile(t("kpi_earnings"), esc(fmtDate(e.value, true)), esc([when, cmp].filter(Boolean).join(" · ")), "sm"));
    } else out.push(tile(t("kpi_earnings"), DASH, esc(es.reason || t("unknown"))));
    const iv = ks.iv30 || {}, is = iv.sub || {};
    let ivsub = "";
    if (is.iv_rank) ivsub = t("ivr_sub", fmtPctl(is.iv_rank.value), is.iv_rank.n_obs);
    else if (is.iv_index) ivsub = t("ivx_sub", is.iv_index.symbol, fmtPctl(is.iv_index.pctl_1y));
    else if (is.building) ivsub = t("ivr_building_short", is.building.n ?? 0, is.building.need ?? 60);
    out.push(tile(t("kpi_iv30"), esc(fmtIV(iv.value)), esc(ivsub)));
    const h = ks.iv_hv || {}, hs = h.sub || {};
    out.push(tile(t("kpi_iv_hv"), esc(fmtPts(h.value)), esc(isNum(hs.hv20) ? t("hv20_sub", fmtIV(hs.hv20), fmtRatio(hs.ratio)) : (hs.reason || ""))));
    const r = ks.rr25 || {}, rs = r.sub || {};
    out.push(tile(t("kpi_rr25"), esc(fmtPts(r.value)), esc([rs.label ? tv("skew_", rs.label) : "", isNum(rs.pctl) ? t("pctl_sub", fmtPctl(rs.pctl)) : ""].filter(Boolean).join(" · "))));
    const pc = ks.pcr || {};
    out.push(tile(t("kpi_pcr"), esc(fmtRatio(pc.value)), esc(t("pcr_vol_sub", fmtRatio((pc.sub || {}).volume)))));
    if (ks.gex) out.push(gexTile(ks.gex));
    return `<div class="oa-stats">${out.join("")}</div>`;
  }

  function gexTile(g) {
    const s = g.sub || {};
    const sub = isNum(s.zero_gamma) ? t("zero_gamma_sub", fmtStrike(Math.round(s.zero_gamma * 100) / 100), fmtPct(s.zero_gamma_dist_pct, 1, true)) : t("no_zero_gamma");
    return tile(t("kpi_gex"), `<span class="${pctCls(g.value)}">${esc(fmtUsd(g.value))}</span>`, esc(`${sub} · ${tv("regime_", s.regime)}`));
  }

  function marketKpis(d) {
    const ks = Object.fromEntries((d.kpis || []).map(k => [k.id, k]));
    const out = [];
    const v = ks.vix || {}, vs = v.sub || {};
    out.push(tile(t("kpi_vix"), esc(fmtRatio(v.value)), esc([tv("band_", vs.band), isNum(vs.pctl_1y) ? t("pctl1y_sub", fmtPctl(vs.pctl_1y)) : "",
      isNum(vs.change_pct) ? fmtPct(vs.change_pct, 1, true) : ""].filter(Boolean).join(" · "))));
    const r = ks.vix_vix3m || {}, rs = r.sub || {};
    out.push(tile(t("kpi_vix_vix3m"), esc(fmtRatio(r.value, 3)), esc(isNum(rs.pctl_1y) ? t("pctl1y_sub", fmtPctl(rs.pctl_1y)) : "")));
    const iv = ks.spx_iv30 || {};
    out.push(tile(t("kpi_spx_iv30"), esc(fmtIV(iv.value)), esc(isNum((iv.sub || {}).hv20) ? t("hv20_only", fmtIV(iv.sub.hv20)) : "")));
    const ts = ks.ratio_30_90 || {}, tss = ts.sub || {};
    out.push(tile(t("kpi_ratio_30_90"), esc(fmtRatio(ts.value, 3)), esc([tss.shape ? tv("shape_", tss.shape) : "", isNum(tss.pctl) ? t("pctl_sub", fmtPctl(tss.pctl)) : ""].filter(Boolean).join(" · "))));
    const rr = ks.rr25 || {};
    out.push(tile(t("kpi_rr25_spx"), esc(fmtPts(rr.value)), esc(rr.sub && rr.sub.label ? tv("skew_", rr.sub.label) : "")));
    if (ks.gex) out.push(gexTile(ks.gex));
    const em = ks.em_next_week || {}, es = em.sub || {};
    out.push(tile(t("kpi_spx_em"), isNum(em.value) ? "±" + esc(fmtPct(em.value)) : DASH, esc(es.expiration ? `${fmtDate(es.expiration)} (${t("dte_n", es.dte)})` : "")));
    const pc = ks.pcr || {};
    out.push(tile(t("kpi_pcr_spx"), esc(fmtRatio(pc.value)), esc(t("pcr_vol_sub", fmtRatio((pc.sub || {}).volume)))));
    return `<div class="oa-stats">${out.join("")}</div>`;
  }

  // ------------------------------------------------------------------ chart 1: expected-move cone
  function drawCone(st, box, readout) {
    const d = st.data.cone || {};
    if (!d.available) { box.innerHTML = empty(d.reason); readout.innerHTML = ""; return; }
    const vis = st.vis, W = width(box), H = chartHeight(W, 0.5, 240, 360);
    const asOf = dayOf(d.as_of);
    const past = (d.closes || []).map(([dt, v]) => ({ x: dayOf(dt) - asOf, v, d: dt }));
    const bands = (d.bands || []).map(b => ({ x: b.dte, b }));
    const xs = [...past.map(p => p.x), 0, ...bands.map(b => b.x)];
    const ys = [...past.map(p => p.v), d.spot, ...bands.flatMap(b => [b.b.low, b.b.high])];
    const F = frame(W, H, { l: 10, r: 62, t: 14, b: 24 });
    const x = lin(Math.min(...xs), Math.max(...xs) * 1.02 + 0.5, F.L, F.R);
    const [lo, hi] = padRange(minOf(ys), maxOf(ys), 0.06);
    const y = lin(lo, hi, F.B, F.T);
    let s = svgOpen(F, t("aria_cone")) + yGrid(F, y, fmtTick, 5, "r");
    const span = x.hi - x.lo, step = span > 120 ? 28 : span > 60 ? 14 : 7;
    const ticks = [];
    for (let v = Math.ceil(x.lo / step) * step; v <= x.hi; v += step) ticks.push(v);
    s += xTicks(F, x, ticks.includes(0) ? ticks : [...ticks, 0].sort((a, b) => a - b), v => fmtDate(isoOf(asOf + v)));
    if (isOn(vis, "cone:band") && bands.length) {
      const up = [[x(0), y(d.spot)], ...bands.map(b => [x(b.x), y(b.b.high)])];
      const dn = [[x(0), y(d.spot)], ...bands.map(b => [x(b.x), y(b.b.low)])];
      s += `<path d="${pathOf([...up, ...dn.reverse()])}Z" fill="var(--cone)"/>`;
      s += `<path d="${pathOf(up)}" fill="none" stroke="var(--cone-edge)" stroke-width="1.5"/><path d="${pathOf(dn.reverse())}" fill="none" stroke="var(--cone-edge)" stroke-width="1.5"/>`;
    }
    s += `<line class="spot" x1="${x(0).toFixed(1)}" x2="${x(0).toFixed(1)}" y1="${F.T}" y2="${F.B}"/>`;
    if (isOn(vis, "cone:closes") && past.length) {
      s += `<path d="${pathOf(past.map(p => [x(p.x), y(p.v)]))}" fill="none" stroke="var(--text)" stroke-width="1.6"/>`;
    }
    s += `<circle cx="${x(0).toFixed(1)}" cy="${y(d.spot).toFixed(1)}" r="3.5" fill="var(--text)"/>`;
    // key expiries: markers and labels at the band ends
    let lastLab = -Infinity;
    for (const b of bands.filter(b => (b.b.tags || []).length)) {
      const xx = x(b.x), lab = tagsLabel(b.b.tags), w = measure(lab, 10.5, 600);
      s += `<g><title>${esc(`${b.b.expiration} · ±${fmtPct(b.b.move_1sd_pct)} · ${fmtPx(b.b.low)}–${fmtPx(b.b.high)}`)}</title>` +
        `<line x1="${xx.toFixed(1)}" x2="${xx.toFixed(1)}" y1="${y(b.b.high).toFixed(1)}" y2="${y(b.b.low).toFixed(1)}" stroke="var(--cone-edge)" stroke-dasharray="3 3"/>` +
        `<circle cx="${xx.toFixed(1)}" cy="${y(b.b.high).toFixed(1)}" r="3" fill="var(--cone-edge)"/><circle cx="${xx.toFixed(1)}" cy="${y(b.b.low).toFixed(1)}" r="3" fill="var(--cone-edge)"/></g>`;
      if (xx - w / 2 > lastLab + 4 && xx + w / 2 < F.R + 30) {
        s += `<text class="lbl halo" x="${xx.toFixed(1)}" y="${(y(b.b.high) - 7).toFixed(1)}" text-anchor="middle">${esc(lab)}</text>`;
        lastLab = xx + w / 2;
      }
    }
    box.innerHTML = s + svgClose(F);
    const items = [...past.map(p => ({ x: x(p.x), kind: "c", p })), ...bands.map(b => ({ x: x(b.x), kind: "b", b: b.b }))].sort((a, b) => a.x - b.x);
    bindHover(box, readout, items.map(i => i.x), i => {
      const it = items[i];
      if (it.kind === "c") return `<span><b>${esc(it.p.d)}</b></span><span>${esc(t("close"))} <b>${esc(fmtPx(it.p.v))}</b></span>`;
      const b = it.b;
      return `<span><b>${esc(b.expiration)}</b> (${esc(t("dte_n", b.dte))})${b.tags.length ? " · " + esc(tagsLabel(b.tags)) : ""}</span>` +
        `<span>${esc(t("move_1sd"))} <b>±${esc(fmtPct(b.move_1sd_pct))}</b></span><span>${esc(t("range_1sd"))} <b>${esc(fmtPx(b.low))}–${esc(fmtPx(b.high))}</b></span>`;
    }, () => `<span>${esc(t("spot"))} <b>${esc(fmtPx(d.spot))}</b></span>${d.proxy ? `<span>${esc(t("cone_proxy", d.proxy))}</span>` : ""}${!past.length ? `<span>${esc(d.closes_reason || "")}</span>` : ""}`);
  }

  function emTableHtml(rows, withKey = false) {
    if (!rows || !rows.length) return "";
    const head = `<tr>${withKey ? `<th>${esc(t("th_key"))}</th>` : ""}<th${withKey ? ' class="l"' : ""}>${esc(t("th_expiration"))}</th><th class="l">${esc(t("th_tags"))}</th><th>${esc(t("th_dte"))}</th>` +
      `<th>${esc(t("th_straddle_pct"))}</th><th>${esc(t("th_move_1sd"))}</th><th>${esc(t("th_range_1sd"))}</th>${withKey ? "" : `<th>${esc(t("th_atm_iv"))}</th>`}</tr>`;
    const body = rows.map(r => `<tr>${withKey ? `<td>${esc(r.key)}</td>` : ""}<td${withKey ? ' class="l"' : ""}>${esc(r.expiration)}${!withKey && r.settlement === "AM" ? ` <span class="oa-sub">${esc(tv("settle_", "AM"))}</span>` : ""}</td>` +
      `<td class="l">${esc(tagsLabel(r.tags))}</td><td>${esc(r.dte)}</td><td>${esc(fmtPct(r.straddle_pct))}</td><td>±${esc(fmtPct(r.move_1sd_pct))}</td>` +
      `<td>${r.range_1sd ? esc(fmtPx(r.range_1sd[0])) + "–" + esc(fmtPx(r.range_1sd[1])) : DASH}</td>${withKey ? "" : `<td>${esc(fmtIV(r.atm_iv))}${r.atm_iv_source === "cboe_fallback" ? "*" : ""}</td>`}</tr>`).join("");
    return `<div class="oa-tablewrap"><table class="oa-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
  }

  // ------------------------------------------------------------------ chart 2: term structure
  function drawTerm(st, box, readout, d) {
    if (!d || !d.available || !(d.points || []).length) { box.innerHTML = empty(d && d.reason); readout.innerHTML = ""; return; }
    const vis = st.vis, W = width(box), H = chartHeight(W, 0.5, 240, 340);
    const pts = d.points;
    const maxD = Math.max(...pts.map(p => p[0]), ...(d.cm || []).map(c => c[0]), (d.event && d.event.dte) || 0);
    const F = frame(W, H, { l: 46, r: 14, t: 16, b: 26 });
    const x = sqrtScale(0, maxD * 1.03 + 1, F.L, F.R);
    const ivs = [...pts.map(p => p[1]), ...(isOn(vis, "term:cboe", false) ? pts.map(p => p[3]) : []), ...(d.cm || []).map(c => c[1])];
    const [lo, hi] = padRange(minOf(ivs), maxOf(ivs), 0.1);
    const y = lin(lo, hi, F.B, F.T);
    let s = svgOpen(F, t("aria_term")) + yGrid(F, y, v => fmtPct(v, 0), 5);
    s += xTicks(F, x, TERM_TICKS.filter(v => v <= x.hi), v => v >= 365 && v % 365 === 0 ? t("years_n", v / 365) : t("days_n", v));
    const px = p => [x(p[0]), y(p[1])];
    const byExp = new Map(pts.map(p => [p[2], p]));
    if (isOn(vis, "term:kinks")) {
      for (const k of d.kinks || []) {
        const a = byExp.get(k.from), b = byExp.get(k.to);
        if (!a || !b) continue;
        s += `<g><title>${esc(t("kink_title", k.from, k.to, fmtIV(k.fwd_vol)))}</title><path d="${pathOf([px(a), px(b)])}" stroke="var(--event)" stroke-width="7" opacity=".35" stroke-linecap="round"/></g>`;
      }
    }
    if (d.event && isNum(d.event.dte)) {
      const xx = x(Math.max(0, d.event.dte)).toFixed(1);
      s += `<line x1="${xx}" x2="${xx}" y1="${F.T}" y2="${F.B}" stroke="var(--event)" stroke-dasharray="4 3"/>` +
        `<text class="ev halo" x="${(+xx + 4).toFixed(1)}" y="${F.T + 10}">${esc(t("earnings_marker", fmtDate(d.event.date)))}</text>`;
    }
    s += `<path d="${pathOf(pts.map(px))}" fill="none" stroke="var(--line)" stroke-width="1.6"/>`;
    s += pts.map(p => { const [a, b] = px(p); return `<circle cx="${a.toFixed(1)}" cy="${b.toFixed(1)}" r="2.6" fill="var(--line)"/>`; }).join("");
    if (isOn(vis, "term:cboe", false)) {
      s += pts.filter(p => isNum(p[3])).map(p => `<circle cx="${x(p[0]).toFixed(1)}" cy="${y(p[3]).toFixed(1)}" r="2.6" fill="none" stroke="var(--s2)"/>`).join("");
    }
    if (isOn(vis, "term:cm")) {
      for (const [dd, v, ex] of d.cm || []) {
        const a = x(dd), b = y(v);
        s += `<g><title>${esc(t("cm_title", dd, fmtIV(v)) + (ex ? " · " + t("extrapolated") : ""))}</title><path d="M${a.toFixed(1)},${(b - 5).toFixed(1)}l5,5l-5,5l-5,-5Z" fill="${ex ? "var(--card)" : "var(--s3)"}" stroke="var(--s3)" stroke-width="1.4"/></g>`;
      }
    }
    for (const dr of d.dropped || []) {
      if (!isNum(dr.dte)) continue;
      const a = x(Math.max(0, dr.dte));
      s += `<g><title>${esc(t("dropped_title", dr.expiration, dr.reason || ""))}</title><path d="M${a.toFixed(1)},${F.B - 7}l4,7h-8Z" fill="none" stroke="var(--faint)"/></g>`;
    }
    box.innerHTML = s + svgClose(F);
    const fv = new Map((d.forward_vols || []).map(f => [f.from, f]));
    const items = pts.map(p => ({ x: x(p[0]), p })).sort((a, b) => a.x - b.x);
    bindHover(box, readout, items.map(i => i.x), i => {
      const p = items[i].p, f = fv.get(p[2]);
      return `<span><b>${esc(p[2])}</b> (${esc(t("dte_n", p[0]))})</span><span>${esc(t("atm_iv"))} <b>${esc(fmtIV(p[1]))}</b></span>` +
        `<span>${esc(t("cboe_iv"))} <b>${esc(fmtIV(p[3]))}</b></span>` +
        (f ? `<span>${esc(t("fwd_vol_to", f.to))} <b>${f.negative_variance ? esc(t("negative_variance")) : esc(fmtIV(f.fwd_vol))}</b></span>` : "");
    }, () => `<span>${esc(t("shape"))} <b>${esc(tv("shape_", d.shape))}</b></span><span>30/90 <b>${esc(fmtRatio(d.ratio_30_90, 3))}</b></span>` +
      `<span>7/30 <b>${esc(fmtRatio(d.ratio_7_30, 3))}</b></span>` +
      (d.cm || []).map(([dd, v]) => `<span>${esc(t("cm_n", dd))} <b>${esc(fmtIV(v))}</b></span>`).join(""));
  }

  // ------------------------------------------------------------------ chart 4: smile
  function smileItems(d) {
    return (d.expiries || []).map((e, i) => ({ key: "smile:" + e.labels.join("+"), label: `${fmtDate(e.expiration)} · ${e.labels.map(l => l === "event" ? t("tag_event") : l).join(", ")}`,
      color: `var(${SERIES[i % SERIES.length]})`, e }));
  }
  function drawSmile(st, box, readout) {
    const d = st.data.smile || {};
    if (!d.available) { box.innerHTML = empty(t("no_smile")); readout.innerHTML = ""; return; }
    const vis = st.vis, W = width(box), H = chartHeight(W, 0.55, 240, 340);
    const items = smileItems(d).filter(it => isOn(vis, it.key));
    const lines = items.map(it => ({ it, pts: it.e.points.map(p => [p[0], p[0] < 1 ? (p[2] ?? p[1]) : (p[1] ?? p[2])]).filter(p => isNum(p[1])) }));
    const kfs = lines.flatMap(l => l.pts.map(p => p[0])), ivs = lines.flatMap(l => l.pts.map(p => p[1]));
    const F = frame(W, H, { l: 46, r: 14, t: 12, b: 26 });
    const x = lin(kfs.length ? minOf(kfs) : 0.7, kfs.length ? maxOf(kfs) : 1.3, F.L, F.R);
    const [lo, hi] = padRange(ivs.length ? minOf(ivs) : 0.1, ivs.length ? maxOf(ivs) : 0.5, 0.08);
    const y = lin(lo, hi, F.B, F.T);
    let s = svgOpen(F, t("aria_smile")) + yGrid(F, y, v => fmtPct(v, 0), 5);
    const { ticks } = niceTicks(x.lo - 1, x.hi - 1, Math.max(3, Math.floor((F.R - F.L) / 70)));
    s += xTicks(F, x, ticks.map(v => v + 1), v => (v === 1 ? "F" : fmtPct(v - 1, 0, true)));
    s += `<line class="spot" x1="${x(1).toFixed(1)}" x2="${x(1).toFixed(1)}" y1="${F.T}" y2="${F.B}"/>`;
    for (const l of lines) s += `<path d="${pathOf(l.pts.map(p => [x(p[0]), y(p[1])]))}" fill="none" stroke="${l.it.color}" stroke-width="1.7"/>`;
    box.innerHTML = s + svgClose(F);
    const grid = [...new Set(kfs.map(v => Math.round(v * 400) / 400))].sort((a, b) => a - b);
    bindHover(box, readout, grid.map(v => x(v)), i => {
      const k = grid[i];
      return `<span>${esc(t("moneyness"))} <b>${esc(fmtPct(k - 1, 1, true))}</b></span>` + lines.map(l => {
        let best = null;
        for (const p of l.pts) if (!best || Math.abs(p[0] - k) < Math.abs(best[0] - k)) best = p;
        return best && Math.abs(best[0] - k) < 0.02 ? `<span>${sw(l.it.color)}${esc(fmtDate(l.it.e.expiration))} <b>${esc(fmtIV(best[1]))}</b></span>` : "";
      }).join("");
    }, () => `<span>${esc(t("smile_hint"))}</span>`);
  }

  // ------------------------------------------------------------------ chart 5: open interest / volume by strike
  function drawOI(st, box, readout) {
    const d = st.data.oi || {};
    if (!d.available) { box.innerHTML = empty(d.reason); readout.innerHTML = ""; return; }
    const win = st.oiWin, meas = st.oiMeasure;
    const rows = (d.windows || {})[win] || [];
    if (!rows.length) { box.innerHTML = empty(t("no_rows_window")); readout.innerHTML = ""; return; }
    const ci = meas === "vol" ? 3 : 1, pi = meas === "vol" ? 4 : 2;
    const W = width(box), H = chartHeight(W, 0.5, 240, 340);
    const F = frame(W, H, { l: 52, r: 12, t: 16, b: 26 });
    const ks = rows.map(r => r[0]);
    const x = lin(Math.min(...ks, d.spot), Math.max(...ks, d.spot), F.L + 4, F.R - 4);
    const mc = Math.max(1, ...rows.map(r => r[ci])), mp = Math.max(1, ...rows.map(r => r[pi]));
    const y = lin(-mp * 1.08, mc * 1.08, F.B, F.T);
    let minStep = Infinity;
    for (let i = 1; i < ks.length; i++) minStep = Math.min(minStep, ks[i] - ks[i - 1]);
    const bw = clamp(isFinite(minStep) ? (x(ks[0] + minStep) - x(ks[0])) * 0.8 : 6, 1, 14);
    let s = svgOpen(F, t("aria_oi")) + yGrid(F, y, v => fmtCompact(Math.abs(v)), 6);
    s += xTicks(F, x, niceTicks(x.lo, x.hi, Math.max(3, Math.floor((F.R - F.L) / 80))).ticks, v => fmtStrike(v));
    s += `<line class="zero" x1="${F.L}" x2="${F.R}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>`;
    for (const r of rows) {
      const xx = (x(r[0]) - bw / 2).toFixed(1);
      if (r[ci] > 0) s += `<rect x="${xx}" y="${y(r[ci]).toFixed(1)}" width="${bw.toFixed(1)}" height="${(y(0) - y(r[ci])).toFixed(1)}" fill="var(--call)" opacity=".8"/>`;
      if (r[pi] > 0) s += `<rect x="${xx}" y="${y(0).toFixed(1)}" width="${bw.toFixed(1)}" height="${(y(-r[pi]) - y(0)).toFixed(1)}" fill="var(--put)" opacity=".8"/>`;
    }
    s += `<text class="callc" x="${F.L + 4}" y="${F.T + 10}">${esc(t(meas === "vol" ? "calls_vol" : "calls_oi"))}</text><text class="putc" x="${F.L + 4}" y="${F.B - 4}">${esc(t(meas === "vol" ? "puts_vol" : "puts_oi"))}</text>`;
    const sx = x(d.spot).toFixed(1);
    s += `<line class="spot" x1="${sx}" x2="${sx}" y1="${F.T}" y2="${F.B}"/><text class="lbl halo" x="${(+sx + 4).toFixed(1)}" y="${F.T + 22}">${esc(t("spot"))} ${esc(fmtPx(d.spot))}</text>`;
    const mpn = d.max_pain && (win === "next" ? d.max_pain.next : d.max_pain.monthly);
    if (mpn && isNum(mpn.strike) && mpn.strike >= x.lo && mpn.strike <= x.hi) {
      const mx = x(mpn.strike).toFixed(1);
      s += `<g><title>${esc(t("max_pain_title", mpn.expiration, fmtStrike(mpn.strike)))}</title><line x1="${mx}" x2="${mx}" y1="${F.T}" y2="${F.B}" stroke="var(--faint)" stroke-dasharray="5 4"/>` +
        `<text class="halo" x="${(+mx + 4).toFixed(1)}" y="${F.T + 34}">${esc(t("max_pain_short", fmtStrike(mpn.strike)))}</text></g>`;
    }
    if (meas === "oi") {
      const kl = d.key_levels || {};
      for (const [side, arr] of [["calls", kl.calls || []], ["puts", kl.puts || []]]) {
        const placed = [], dir = side === "calls" ? -1 : 1;   // labels on neighbouring strikes stack outwards
        arr.forEach(([k, oi], i) => {
          if (k < x.lo || k > x.hi) return;
          const xx = x(k), lab = (side === "calls" ? "C" : "P") + (i + 1), w = measure(lab, 10.5, 600) + 4;
          let yy = side === "calls" ? y(Math.min(oi, mc)) - 4 : y(-Math.min(oi, mp)) + 12;
          while (placed.some(q => Math.abs(q.x - xx) < (q.w + w) / 2 && Math.abs(q.y - yy) < 11)) yy += 11 * dir;
          yy = clamp(yy, F.T + 9, F.B - 2);
          placed.push({ x: xx, y: yy, w });
          s += `<g><title>${esc(t("key_level_title", t(side === "calls" ? "call" : "put"), fmtStrike(k), fmtInt(oi), d.key_levels.window_dte))}</title>` +
            `<text class="lbl halo ${side === "calls" ? "callc" : "putc"}" x="${xx.toFixed(1)}" y="${yy.toFixed(1)}" text-anchor="middle">${esc(lab)}</text></g>`;
        });
      }
    }
    box.innerHTML = s + svgClose(F);
    bindHover(box, readout, rows.map(r => x(r[0])), i => {
      const r = rows[i];
      return `<span>${esc(t("strike"))} <b>${esc(fmtStrike(r[0]))}</b></span><span>${sw("var(--call)")}${esc(t("call"))} OI <b>${esc(fmtInt(r[1]))}</b> · ${esc(t("vol_short"))} <b>${esc(fmtInt(r[3]))}</b></span>` +
        `<span>${sw("var(--put)")}${esc(t("put"))} OI <b>${esc(fmtInt(r[2]))}</b> · ${esc(t("vol_short"))} <b>${esc(fmtInt(r[4]))}</b></span>`;
    }, () => `<span>${esc(t("oi_hint"))}</span>`);
  }

  // ------------------------------------------------------------------ chart 6: dealer gamma
  function drawGex(st, box, readout, d) {
    if (!d || !d.available) { box.innerHTML = empty(d && d.reason); readout.innerHTML = ""; return; }
    const vis = st.vis, W = width(box), H = chartHeight(W, 0.45, 240, 330);
    const F = frame(W, H, { l: 58, r: 58, t: 16, b: 26 });
    const prof = d.profile || [], bars = d.by_strike || [];
    const xsAll = [...prof.map(p => p[0]), ...bars.map(b => b[0])];
    const x = lin(minOf(xsAll), maxOf(xsAll), F.L, F.R);
    const showBars = isOn(vis, "gex:bars") && bars.length, showProf = isOn(vis, "gex:profile") && prof.length;
    const bmax = Math.max(1, ...bars.map(b => Math.abs(b[1])));
    const yb = lin(-bmax * 1.1, bmax * 1.1, F.B, F.T);
    const pv = prof.map(p => p[1]);
    const pm = Math.max(1, Math.abs(minOf(pv)), Math.abs(maxOf(pv)));
    const yp = lin(-pm * 1.1, pm * 1.1, F.B, F.T);
    let s = svgOpen(F, t("aria_gex"));
    if (showBars) s += yGrid(F, yb, v => fmtUsd(v), 5, "l");
    if (showProf) s += (showBars ? "" : yGrid(F, yp, v => fmtUsd(v), 5, "l")) + niceTicks(yp.lo, yp.hi, 5).ticks.map(v =>
      showBars ? `<text x="${F.R + 6}" y="${(yp(v) + 4).toFixed(1)}" style="fill:var(--line)">${esc(fmtUsd(v))}</text>` : "").join("");
    s += xTicks(F, x, niceTicks(x.lo, x.hi, Math.max(3, Math.floor((F.R - F.L) / 80))).ticks, v => fmtStrike(v));
    s += `<line class="zero" x1="${F.L}" x2="${F.R}" y1="${yb(0).toFixed(1)}" y2="${yb(0).toFixed(1)}"/>`;
    if (showBars) {
      let minStep = Infinity;
      for (let i = 1; i < bars.length; i++) minStep = Math.min(minStep, bars[i][0] - bars[i - 1][0]);
      const bw = clamp(isFinite(minStep) ? (x(bars[0][0] + minStep) - x(bars[0][0])) * 0.8 : 4, 1, 12);
      for (const [k, v] of bars) {
        const top = v > 0 ? yb(v) : yb(0), h = Math.abs(yb(v) - yb(0));
        s += `<rect x="${(x(k) - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="var(${v > 0 ? "--gex-pos" : "--gex-neg"})" opacity=".75"/>`;
      }
    }
    if (showProf) s += `<path d="${pathOf(prof.map(p => [x(p[0]), yp(p[1])]))}" fill="none" stroke="var(--line)" stroke-width="1.8"/>`;
    const vline = (v, label, color, dash, row) => {
      if (!isNum(v) || v < x.lo || v > x.hi) return "";
      const xx = x(v).toFixed(1);
      return `<line x1="${xx}" x2="${xx}" y1="${F.T}" y2="${F.B}" stroke="${color}" ${dash ? `stroke-dasharray="${dash}"` : ""}/>` +
        `<text class="lbl halo" x="${(+xx + 4).toFixed(1)}" y="${F.T + 10 + row * 12}">${esc(label)}</text>`;
    };
    s += vline(d.spot, `${t("spot")} ${fmtPx(d.spot)}`, "var(--text)", "2 3", 0);
    s += vline(d.zero_gamma, t("zero_gamma_short", fmtStrike(Math.round(d.zero_gamma * 100) / 100)), "var(--event)", "5 3", 1);
    s += vline(d.call_wall, t("call_wall_short", fmtStrike(d.call_wall)), "var(--call)", "1 3", 2);
    s += vline(d.put_wall, t("put_wall_short", fmtStrike(d.put_wall)), "var(--put)", "1 3", 3);
    box.innerHTML = s + svgClose(F);
    const barAt = new Map(bars.map(b => [b[0], b[1]]));
    const items = (showProf ? prof.map(p => ({ x: x(p[0]), p: p[0], v: p[1] })) : bars.map(b => ({ x: x(b[0]), p: b[0], v: null })));
    bindHover(box, readout, items.map(i => i.x), i => {
      const it = items[i];
      let near = null;
      for (const b of bars) if (!near || Math.abs(b[0] - it.p) < Math.abs(near[0] - it.p)) near = b;
      return `<span>${esc(t("price"))} <b>${esc(fmtPx(it.p))}</b></span>` + (it.v != null ? `<span>${sw("var(--line)")}${esc(t("gex_profile"))} <b>${esc(fmtUsd(it.v))}</b></span>` : "") +
        (near ? `<span>${esc(t("gex_strike", fmtStrike(near[0])))} <b>${esc(fmtUsd(barAt.get(near[0])))}</b></span>` : "");
    }, () => `<span>${esc(t("net_gex"))} <b>${esc(fmtUsd(d.net_gex_1pct))}</b></span><span>${esc(t("regime"))} <b>${esc(tv("regime_", d.regime))}</b></span>` +
      `<span>${esc(t("zero_gamma"))} <b>${isNum(d.zero_gamma) ? esc(fmtPx(d.zero_gamma)) + " (" + esc(fmtPct(d.zero_gamma_dist_pct, 1, true)) + ")" : DASH}</b></span>`);
  }

  // ------------------------------------------------------------------ earnings: implied vs past reactions
  function drawEarnings(st, box, readout) {
    const d = st.data.earnings || {}, r = d.reactions || {}, ev = d.event_move || {};
    const items = r.items || [];
    if (!items.length) { box.innerHTML = empty(r.reason || t("no_reactions")); readout.innerHTML = ""; return; }
    const W = width(box), H = chartHeight(W, 0.42, 200, 260);
    const F = frame(W, H, { l: 46, r: Math.round(clamp(W * 0.22, 70, 104)), t: 12, b: 26 });
    const imp = ev.implied_window_1sd, mv = items.map(i => Math.abs(i[1]));
    const m = Math.max(...mv, isNum(imp) ? imp : 0, isNum(r.mean_abs) ? r.mean_abs : 0) * 1.15 || 0.05;
    const y = lin(-m, m, F.B, F.T);
    const n = items.length, slot = (F.R - F.L) / n, x = i => F.L + slot * (i + 0.5);
    let s = svgOpen(F, t("aria_earnings")) + yGrid(F, y, v => fmtPct(v, 0, true), 4);
    s += `<line class="zero" x1="${F.L}" x2="${F.R}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>`;
    items.forEach(([dt, v], i) => {
      const top = v > 0 ? y(v) : y(0), h = Math.abs(y(v) - y(0)), bw = Math.min(26, slot * 0.6);
      s += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" fill="var(${v >= 0 ? "--up" : "--down"})" opacity=".8"/>`;
    });
    // the two reference lines carry their labels in the right margin, pushed apart when the values are close
    const refs = [[imp, "var(--event)", "6 3", t("implied_line", fmtPct(imp))], [r.mean_abs, "var(--muted)", "2 3", t("past_mean_line", fmtPct(r.mean_abs))]]
      .filter(a => isNum(a[0])).sort((a, b) => b[0] - a[0]);
    let labY = -Infinity;
    for (const [v, color, dash, label] of refs) {
      s += [v, -v].map(w => `<line x1="${F.L}" x2="${F.R}" y1="${y(w).toFixed(1)}" y2="${y(w).toFixed(1)}" stroke="${color}" stroke-dasharray="${dash}"/>`).join("");
      const ly = Math.max(y(v) + 4, labY + 12);
      labY = ly;
      s += `<text class="lbl" x="${F.R + 4}" y="${ly.toFixed(1)}" style="fill:${color}">${esc(label)}</text>`;
    }
    const step = Math.ceil(n / Math.max(1, Math.floor((F.R - F.L) / 60)));
    items.forEach(([dt], i) => { if (i % step === 0) s += `<text x="${x(i).toFixed(1)}" y="${F.B + 16}" text-anchor="middle">${esc(fmtMonth(dt))}</text>`; });
    box.innerHTML = s + svgClose(F);
    bindHover(box, readout, items.map((_, i) => x(i)), i => `<span><b>${esc(items[i][0])}</b></span><span>${esc(t("reaction"))} <b class="${pctCls(items[i][1])}">${esc(fmtPct(items[i][1], 1, true))}</b></span>`,
      () => `<span>${esc(t("reactions_n", r.n, tv("method_", r.method)))}</span>`);
  }

  // ------------------------------------------------------------------ strategy screens (sortable tables)
  const COLS = {
    expiration: { th: "th_expiration", l: true, f: r => `${esc(r.expiration)} <span class="oa-sub">${esc(t("dte_n", r.dte))}</span>`, v: r => r.expiration },
    strike: { th: "th_strike", f: r => esc(fmtStrike(r.strike)), v: r => r.strike },
    bidask: { th: "th_bid_ask", f: r => `${esc(fmtPx(r.bid))} / ${esc(fmtPx(r.ask))}`, v: r => r.mid },
    mid: { th: "th_mid", f: r => esc(fmtPx(r.mid)), v: r => r.mid },
    spread: { th: "th_spread", f: r => esc(fmtPct(r.spread_pct, 0)), v: r => r.spread_pct },
    iv: { th: "th_iv", f: r => esc(fmtIV(r.iv)), v: r => r.iv },
    delta: { th: "th_delta", f: r => esc(fmtDelta(r.delta)), v: r => r.delta },
    otm: { th: "th_otm", f: r => `${esc(fmtPct(r.otm_pct, 1, true))}<br><span class="oa-sub">${isNum(r.em_units) ? esc(fmtRatio(r.em_units, 1)) + "σ" : ""}</span>`, v: r => r.otm_pct },
    p_itm: { th: "th_p_itm", f: r => esc(fmtPct(r.p_itm, 0)), v: r => r.p_itm },
    p_touch: { th: "th_p_touch", f: r => esc(fmtPct(r.p_touch, 0)), v: r => r.p_touch },
    p_profit: { th: "th_p_profit", f: r => esc(fmtPct(r.p_profit, 0)), v: r => r.p_profit },
    breakeven: { th: "th_breakeven", f: r => `${esc(fmtPx(r.breakeven))}<br><span class="oa-sub">${esc(fmtPct(r.breakeven_move_pct, 1, true))}</span>`, v: r => r.breakeven_move_pct },
    breakeven_ask: { th: "th_breakeven_ask", f: r => esc(fmtPx(r.breakeven_ask)), v: r => r.breakeven_ask },
    yield: { th: "th_yield", f: r => `${esc(fmtPct(r.yield, 2))}<br><span class="oa-sub">${esc(t("ann_short", fmtPct(r.annualized_yield, 0)))}</span>`, v: r => r.annualized_yield },
    yield_bid: { th: "th_yield_bid", f: r => `${esc(fmtPct(r.yield_bid, 2))}<br><span class="oa-sub">${esc(t("ann_short", fmtPct(r.annualized_yield_bid, 0)))}</span>`, v: r => r.annualized_yield_bid },
    cap: { th: "th_upside_cap", f: r => esc(fmtPct(r.upside_cap_pct, 1, true)), v: r => r.upside_cap_pct },
    called: { th: "th_if_called", f: r => esc(fmtPct(r.if_called_return, 2)), v: r => r.if_called_return },
    ydiv: { th: "th_yield_div", f: r => esc(fmtPct(r.yield_with_div, 2)), v: r => r.yield_with_div },
    premium: { th: "th_premium", f: r => esc(fmtUsdFull(r.premium_per_contract)), v: r => r.premium_per_contract },
    capital: { th: "th_capital", f: r => `${esc(fmtUsdFull(r.capital))}<br><span class="oa-sub">${esc(t("of_account", fmtPct(r.pct_of_example_account, 1)))}</span>`, v: r => r.capital },
    max_loss: { th: "th_max_loss", f: r => esc(fmtUsdFull(r.max_loss)), v: r => r.max_loss },
    theta: { th: "th_theta", f: r => esc(fmtPct(r.theta_pct_per_day, 1, true)), v: r => r.theta_pct_per_day },
    liq: { th: "th_liquidity", c: true, f: r => `<span title="${esc(t("liq_title", fmtInt(r.open_interest), fmtInt(r.volume)))}">${esc(tv("liq_", r.liquidity))}</span>`, v: r => r.liquidity },
    earn: { th: "th_earnings", c: true, f: r => esc(r.earnings_before_expiry == null ? t("unknown") : r.earnings_before_expiry ? t("yes") : t("no")), v: r => r.earnings_before_expiry == null ? 0.5 : +r.earnings_before_expiry },
    exdiv: { th: "th_ex_div", f: r => r.ex_div_date ? `${esc(r.ex_div_date)}<br><span class="oa-sub">${esc(fmtPx(r.dividend))}</span>` : DASH, v: r => r.ex_div_date || "" },
    style: { th: "th_style", c: true, f: r => esc([tv("style_", r.exercise_style), r.settlement || ""].filter(Boolean).join(" · ")), v: r => r.exercise_style },
  };
  const STRAT_COLS = {
    short_put: ["expiration", "strike", "bidask", "spread", "iv", "delta", "otm", "p_itm", "p_touch", "p_profit", "breakeven", "yield", "yield_bid", "capital", "max_loss", "liq", "earn", "style"],
    covered_call: ["expiration", "strike", "bidask", "spread", "iv", "delta", "cap", "yield", "called", "ydiv", "p_itm", "p_profit", "breakeven", "exdiv", "liq", "earn", "style"],
    long_call: ["expiration", "strike", "bidask", "spread", "iv", "delta", "otm", "premium", "breakeven", "breakeven_ask", "p_itm", "p_profit", "theta", "liq", "earn", "style"],
    long_put: ["expiration", "strike", "bidask", "spread", "iv", "delta", "otm", "premium", "breakeven", "breakeven_ask", "p_itm", "p_profit", "theta", "liq", "earn", "style"],
  };

  function sortRows(rows, cols, sort) {
    if (!sort || !COLS[sort.k]) return rows;
    const v = COLS[sort.k].v, dir = sort.dir;
    return [...rows].sort((a, b) => {
      const x = v(a), y = v(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }

  function tableHtml(id, colKeys, rows, sort) {
    const head = colKeys.map(k => {
      const c = COLS[k], on = sort && sort.k === k;
      return `<th class="sort ${c.l ? "l" : c.c ? "c" : ""}" data-t="${id}" data-k="${k}" tabindex="0"${on ? ` aria-sort="${sort.dir > 0 ? "ascending" : "descending"}"` : ""}>${esc(t(c.th))}</th>`;
    }).join("");
    const body = sortRows(rows, colKeys, sort).map(r => `<tr>${colKeys.map(k => `<td class="${COLS[k].l ? "l" : COLS[k].c ? "c" : ""}">${COLS[k].f(r)}</td>`).join("")}</tr>`).join("");
    return `<div class="oa-tablewrap"><table class="oa-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function stratLabel(name, kind) { return name === "short_put" && kind === "index" ? t("strat_short_put_index") : tv("strat_", name); }

  function strategiesBody(st) {
    const d = st.data.strategies || {};
    if (!d.available) return empty(d.reason);
    const kind = d.kind, tabs = STRAT_ORDER.filter(n => !(n === "covered_call" && kind === "index") && !(n === "earnings_straddle" && kind !== "stock"));
    if (!tabs.includes(st.stratTab)) st.stratTab = tabs[0];
    const tabBar = `<div class="oa-tabs" role="tablist">${tabs.map(n => `<button type="button" role="tab" class="oa-tab${n === st.stratTab ? " on" : ""}" aria-selected="${n === st.stratTab}" data-tab="${n}">${esc(stratLabel(n, kind))}</button>`).join("")}</div>`;
    let body = "";
    if (st.stratTab === "earnings_straddle") body = straddleHtml(d.earnings_straddle);
    else {
      const sc = (d.screens || {})[st.stratTab] || {};
      if (!sc.available) body = empty(sc.reason);
      else {
        const all = st.stratAll[st.stratTab];
        const rows = all ? sc.all : sc.rows;
        const rule = t("screen_rule", fmtDelta(sc.delta_band && sc.delta_band[0]), fmtDelta(sc.delta_band && sc.delta_band[1]), sc.dte_window && sc.dte_window[0], sc.dte_window && sc.dte_window[1], fmtDelta(sc.target_delta));
        body = `<p class="oa-small" style="margin:0 0 6px">${esc(rule)} · ${esc(t("candidates_n", sc.candidates))} · ` +
          `<button type="button" class="oa-link" data-all="${st.stratTab}">${esc(all ? t("show_per_expiry") : (sc.candidates || 0) <= (sc.all || []).length ? t("show_all_candidates", sc.candidates || 0) : t("show_candidates", (sc.all || []).length))}</button></p>` +
          (rows.length ? tableHtml("strat", STRAT_COLS[st.stratTab].filter(c => c !== "earn" || kind === "stock"), rows, st.sort.strat) : empty(t("no_rows"))) +
          (notesOf(sc).length ? `<ul class="oa-list">${notesOf(sc).map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "");
      }
    }
    const notes = notesOf(d);
    return tabBar + body + (notes.length ? `<ul class="oa-list">${notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "");
  }

  function straddleHtml(s) {
    if (!s || !s.available) return empty(s && s.reason);
    return kv([
      [t("th_expiration"), esc(s.expiration)], [t("th_strike"), esc(fmtStrike(s.strike))],
      [t("straddle_cost"), `${esc(fmtPx(s.cost))} (${esc(t("per_contract", fmtUsdFull(s.cost_per_contract)))})`],
      [t("breakevens"), `<span class="pos">${esc(fmtPct(s.be_up, 2, true))}</span> / <span class="neg">${esc(fmtPct(s.be_down, 2, true))}</span>`],
      [t("event_share"), esc(fmtPct(s.event_variance_share, 0))],
      [t("p_beyond_rn"), esc(fmtPct(s.p_beyond_be_rn, 1))],
      [t("hold_days"), esc(t("hold_days_v", s.hold_days, s.k_pre, s.k_post))],
      [t("hist_beyond"), isNum(s.hist_share_beyond_be) ? esc(t("hist_beyond_v", fmtPct(s.hist_share_beyond_be, 1), s.n)) : DASH],
    ]) + (notesOf(s).length ? `<ul class="oa-list">${notesOf(s).map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "");
  }

  // ------------------------------------------------------------------ summaries, option chain, flow, notes
  function earningsSummary(d) {
    const e = d.earnings || {};
    if (!e.applies) return "";
    if (!e.available) return kv([[t("next_earnings"), esc(e.reason || t("unknown"))]]);
    const ev = e.event_move || {}, c = e.comparison || {}, r = e.reactions || {};
    return kv([
      [t("next_earnings"), `${esc(e.next_date)}${e.event_today ? " · " + esc(t("earnings_today_short")) : ""} · ${esc(t("in_tdays", e.trading_days_to_event))}`],
      [t("timing"), esc(tv("timing_", e.timing))],
      [t("date_check"), esc(tv("date_check_", e.date_check))],
      [t("event_move"), ev.available ? `±${esc(fmtPct(ev.move_1sd))} (${esc(tv("method_", ev.method))}) · ${esc(t("abs_move", fmtPct(ev.expected_abs_move)))}` : esc(ev.reason || DASH)],
      [t("implied_window"), ev.available ? `±${esc(fmtPct(ev.implied_window_1sd))}` : DASH],
      [t("past_reactions"), r.available ? esc(t("past_v", fmtPct(r.mean_abs), fmtPct(r.median_abs), fmtPct(r.max_abs), r.n)) + (r.small_sample ? " · " + esc(t("small_sample")) : "") : esc(r.reason || DASH)],
      [t("implied_to_past"), esc(fmtRatio(c.ratio_implied_to_mean))],
      [t("share_above"), isNum(c.share_above_implied_window_1sd) ? esc(t("share_above_v", fmtPct(c.share_above_implied_window_1sd, 0), fmtPct(c.normal_benchmark_share, 0))) : DASH],
      [t("event_share"), esc(fmtPct(ev.event_variance_share, 0))],
    ]) + (notesOf(e).length ? `<ul class="oa-list">${notesOf(e).map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "");
  }

  function skewTermSummary(d) {
    const sk = d.skew || {}, tm = d.term || {}, v = d.volatility || {};
    const rank = v.iv_rank || {}, rx = v.iv_rank_ex_event || {}, ix = v.iv_index || {}, rv = v.realized || {};
    const pairs = [
      [t("shape"), `${esc(tv("shape_", tm.shape))} · 30/90 ${esc(fmtRatio(tm.ratio_30_90, 3))} · 7/30 ${esc(fmtRatio(tm.ratio_7_30, 3))}`],
      [t("cm_iv"), (tm.cm || []).map(([dd, vv, ex]) => `${esc(t("cm_n", dd))} ${esc(fmtIV(vv))}${ex ? "*" : ""}`).join(" · ") || DASH],
      [t("iv30_ex_event"), esc(fmtIV(v.cm_iv_30d_ex_event))],
      [t("iv30_cboe"), esc(fmtIV(v.iv30_cboe))],
      [t("iv_rank"), rank.available ? esc(t("ivr_v", fmtPctl(rank.value), fmtPctl(rank.pctl), rank.n_obs, tv("window_", rank.window_label))) : esc(reasonOf(rank))],
      [t("iv_rank_ex"), rx.available ? esc(t("ivr_v", fmtPctl(rx.value), fmtPctl(rx.pctl), rx.n_obs, tv("window_", rx.window_label))) : esc(reasonOf(rx))],
      [t("iv_index"), ix.available ? esc(t("ivx_v", ix.symbol, fmtRatio(ix.close), ix.date, fmtPctl(ix.pctl_1y))) : esc(ix.reason || DASH)],
      [t("realized"), rv.available ? esc(t("hv_v", fmtIV(rv.hv10), fmtIV(rv.hv20), fmtIV(rv.hv60), fmtPctl(rv.hv20_pctl_1y))) + (rv.source ? ` <span class="oa-sub">(${esc(rv.source)})</span>` : "") : esc(rv.reason || DASH)],
      [t("vrp"), `${esc(fmtPts(v.vrp))} · IV/HV ${esc(fmtRatio(v.iv_hv_ratio))}`],
      [t("rr25"), `${esc(fmtPts(sk.rr25_30d))} · ${esc(tv("skew_", sk.label))}${isNum(sk.rr25_pctl) ? " · " + esc(t("pctl_sub", fmtPctl(sk.rr25_pctl))) : ""}`],
      [t("bf25"), esc(fmtPts(sk.bf25_30d))],
    ];
    const by = (sk.by_expiry || []).slice(0, 12);
    const table = by.length ? `<details><summary class="oa-small">${esc(t("skew_by_expiry"))}</summary><div class="oa-tablewrap"><table class="oa-table"><thead><tr>` +
      `<th>${esc(t("th_expiration"))}</th><th>${esc(t("th_put25"))}</th><th>${esc(t("th_atm_iv"))}</th><th>${esc(t("th_call25"))}</th><th>RR25</th><th>BF25</th></tr></thead><tbody>` +
      by.map(r => `<tr><td>${esc(r.expiration)} <span class="oa-sub">${esc(t("dte_n", r.dte))}</span></td><td>${esc(fmtIV(r.put25_iv))}</td><td>${esc(fmtIV(r.atm_iv_cboe))}</td><td>${esc(fmtIV(r.call25_iv))}</td><td>${esc(fmtPts(r.rr25))}</td><td>${esc(fmtPts(r.bf25))}</td></tr>`).join("") +
      `</tbody></table></div></details>` : "";
    return kv(pairs) + table;
  }

  function positioningSummary(d) {
    const p = d.positioning || {};
    if (!p.available) return empty(p.reason);
    const pcr = p.pcr || {}, kl = p.key_levels || {}, mp = p.max_pain || {}, oc = p.oi_change || {};
    const lv = arr => (arr || []).map(([k, oi]) => `${esc(fmtStrike(k))} <span class="oa-sub">(${esc(fmtCompact(oi))})</span>`).join(", ") || DASH;
    const ocItems = xs => (xs || []).slice(0, 5).map(i => `${esc(i.type)} ${esc(fmtStrike(i.strike))} <span class="${pctCls(i.oi_change)}">${esc((i.oi_change > 0 ? "+" : "") + fmtInt(i.oi_change))}</span>`).join(", ") || DASH;
    return kv([
      [t("pcr_all"), `${esc(t("oi_short"))} ${esc(fmtRatio((pcr.all || {}).oi))} · ${esc(t("vol_short"))} ${esc(fmtRatio((pcr.all || {}).volume))}`],
      [t("pcr_30d"), `${esc(t("oi_short"))} ${esc(fmtRatio((pcr.d30 || {}).oi))} · ${esc(t("vol_short"))} ${esc(fmtRatio((pcr.d30 || {}).volume))}`],
      [t("call_levels", kl.window_dte), lv(kl.calls)],
      [t("put_levels", kl.window_dte), lv(kl.puts)],
      [t("far_otm"), (p.far_otm || []).map(f => `${esc(f.type)} ${esc(fmtStrike(f.strike))} <span class="oa-sub">(${esc(fmtCompact(f.oi))})</span>`).join(", ") || DASH],
      [t("max_pain"), [mp.next, mp.monthly].filter(Boolean).map(m => `${esc(fmtStrike(m.strike))} <span class="oa-sub">(${esc(m.expiration)})</span>`).join(" · ") + " · " + esc(t("weak_evidence"))],
      [t("oi_change"), oc.available ? `${esc(t("since", oc.base_date))}: ${esc(t("up_short"))} ${ocItems(oc.top_increases)}; ${esc(t("down_short"))} ${ocItems(oc.top_decreases)}` : esc(oc.reason || DASH)],
    ]) + `<p class="oa-small">${esc(said((d.data_notes || {}).oi_note || "", { id: "" }, ""))}</p>`;
  }

  function chainBody(st) {
    const d = st.data.chain || {};
    if (!d.available) return empty(t("no_chain"));
    const ex = d.expiries;
    if (st.chainIdx >= ex.length) st.chainIdx = 0;
    const e = ex[st.chainIdx];
    const sel = `<select class="oa-select" data-chain="1" aria-label="${esc(t("th_expiration"))}">${ex.map((x, i) => `<option value="${i}"${i === st.chainIdx ? " selected" : ""}>${esc(x.expiration)} · ${esc(t("dte_n", x.dte))} · ${esc(tagsLabel(x.tags))}</option>`).join("")}</select>`;
    const S = d.spot;
    let spotDone = false;
    const rows = e.rows.map(r => {
      const k = r[0], atm = isNum(e.atm_strike) && Math.abs(k - e.atm_strike) < 1e-9;
      let cls = atm ? "atm" : "";
      if (!spotDone && k > S) { spotDone = true; }
      const c = r.slice(1, 7), p = r.slice(7, 13);
      const cell = (v, f, itm) => `<td class="${itm ? "itm" : ""}">${esc(f(v))}</td>`;
      const citm = k < S, pitm = k > S;
      return `<tr class="${cls}">${cell(c[0], fmtPx, citm)}${cell(c[1], fmtPx, citm)}${cell(c[2], fmtIV, citm)}${cell(c[3], fmtDelta, citm)}${cell(c[4], fmtInt, citm)}${cell(c[5], fmtInt, citm)}` +
        `<td class="k">${esc(fmtStrike(k))}</td>${cell(p[0], fmtPx, pitm)}${cell(p[1], fmtPx, pitm)}${cell(p[2], fmtIV, pitm)}${cell(p[3], fmtDelta, pitm)}${cell(p[4], fmtInt, pitm)}${cell(p[5], fmtInt, pitm)}</tr>`;
    }).join("");
    const h = ["th_bid", "th_ask", "th_iv", "th_delta", "th_oi", "th_vol"].map(k => `<th>${esc(t(k))}</th>`).join("");
    return `<div class="oa-toolbar">${sel}<span class="oa-small" style="margin:0">${esc(t("chain_note", fmtPx(e.forward), fmtStrike(e.atm_strike), e.root, tv("settle_", e.settlement), fmtPct(d.range_pct, 0)))}</span></div>` +
      `<div class="oa-tablewrap" style="max-height:520px;overflow:auto"><table class="oa-table"><thead><tr><th class="c" colspan="6">${esc(t("calls"))}</th><th></th><th class="c" colspan="6">${esc(t("puts"))}</th></tr>` +
      `<tr>${h}<th class="c">${esc(t("th_strike"))}</th>${h}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function flowBody(st) {
    const d = st.data.flow || {};
    if (!d.available) return empty(d.reason);
    const rows = st.flowAll ? d.rows : d.rows.slice(0, 5);
    if (!rows.length) return `<p class="oa-small">${esc(t("no_flow", (d.rules || {}).min_volume, (d.rules || {}).min_vol_oi))}</p>`;
    const body = rows.map(r => `<tr><td class="l">${esc(r.type)} ${esc(fmtStrike(r.strike))} <span class="oa-sub">${esc(r.expiration)} · ${esc(t("dte_n", r.dte))}</span></td>` +
      `<td>${esc(fmtInt(r.volume))}</td><td>${esc(fmtInt(r.open_interest))}</td><td>${esc(fmtRatio(r.vol_oi, 1))}</td><td>${esc(fmtUsd(r.premium))}</td><td>${esc(fmtPx(r.price))}</td><td>${esc(fmtIV(r.iv))}</td><td>${esc(fmtDelta(r.delta))}</td><td>${esc(hhmm(r.last_trade_time))}</td></tr>`).join("");
    return `<p class="oa-small" style="margin:0 0 6px">${esc(t("flow_rule", (d.rules || {}).min_volume, (d.rules || {}).min_vol_oi))} · <b>${esc(d.direction_note || "")}</b></p>` +
      `<div class="oa-tablewrap"><table class="oa-table"><thead><tr><th>${esc(t("th_contract"))}</th><th>${esc(t("th_vol"))}</th><th>${esc(t("th_oi"))}</th><th>${esc(t("th_vol_oi"))}</th><th>${esc(t("th_premium_total"))}</th><th>${esc(t("th_price"))}</th><th>${esc(t("th_iv"))}</th><th>${esc(t("th_delta"))}</th><th>${esc(t("th_last"))}</th></tr></thead><tbody>${body}</tbody></table></div>` +
      (d.rows.length > 5 ? `<p class="oa-small"><button type="button" class="oa-link" data-flow="1">${esc(st.flowAll ? t("show_less") : t("show_all_n", d.rows.length))}</button></p>` : "");
  }

  function notesHtml(d) {
    const n = d.data_notes || {}, h = d.history || {};
    const notes = (n.notes || []).map((x, i) => esc(said(x, (n.note_ids || [])[i], "note_")));
    const hist = d.meta.mode === "ticker"
      ? (h.available ? t("history_v", h.days, h.first_date || DASH, h.last_date || DASH, h.saved_this_run ? t("saved_yes") : t("saved_no")) : (h.reason || ""))
      : (h && h.days ? Object.entries(h.days).map(([k, v]) => t("member_days", k, v)).join(", ") : "");
    const body = `<div class="oa-notes">${list(t("dn_skipped"), (n.skipped || []).map(s => `<b>${esc(s.module)}</b>: ${esc(s.reason)}`))}` +
      `${list(t("dn_restricted"), (n.restricted || []).map(esc))}${list(t("dn_notes"), notes)}` +
      `${list(t("dn_history"), hist ? [esc(hist), ...(h.data_dir ? [`<span class="oa-sub">${esc(h.data_dir)}</span>`] : [])] : [])}` +
      `${list(t("dn_approx"), ["approx_surface", "approx_rn", "approx_gex", "approx_oi", "approx_delay"].map(k => esc(t(k))))}</div>`;
    return card(esc(t("t_notes")), body);
  }

  // ------------------------------------------------------------------ market page pieces
  function thermoHtml(d) {
    const th = d.thermometer || {};
    if (!th.available) return card(esc(t("t_thermo")), empty(th.reason));
    const fmt = {
      vix_20_or_more: v => fmtRatio(v), term_inverted: v => fmtRatio(v, 3), front_stress: v => fmtRatio(v, 3),
      skew_steep: v => fmtPts(v), negative_gamma: v => fmtUsd(v), realized_above_implied: v => fmtIV(v),
    };
    const rows = FLAG_ORDER.filter(k => (th.flags || {})[k]).map(k => {
      const f = th.flags[k], dot = f.fired === true ? "on" : f.fired === false ? "" : "na";
      const val = f.fired == null ? `<span class="oa-sub">${esc(f.reason || t("not_computed"))}</span>` : `${esc(fmt[k](f.value))} <span class="oa-sub">${esc(t("op_" + k))} ${esc(fmt[k](f.threshold))}</span>`;
      return `<tr><td class="l"><span class="oa-dot ${dot}"></span>${esc(tv("flag_", k))}</td><td>${val}</td><td class="${f.fired ? "fired" : ""}">${esc(f.fired === true ? t("fired") : f.fired === false ? t("not_fired") : DASH)}</td></tr>`;
    }).join("");
    const label = th.label ? `<p class="oa-thermo-label ${esc(th.label)}">${esc(tv("thermo_", th.label))}${th.label_uncertain ? ` <span class="oa-sub">${esc(t("label_uncertain"))}</span>` : ""}</p>` : `<p class="oa-thermo-label">${DASH}</p>`;
    return card(esc(t("t_thermo")), `${label}<p class="oa-small" style="margin:0 0 8px">${esc(t("thermo_count", th.n_flags, th.n_available))}${(th.missing || []).length ? " · " + esc(t("thermo_missing", th.missing.map(m => tv("flag_", m)).join(", "))) : ""}</p>` +
      `<div class="oa-tablewrap"><table class="oa-table"><thead><tr><th>${esc(t("th_flag"))}</th><th>${esc(t("th_value_threshold"))}</th><th>${esc(t("th_fired"))}</th></tr></thead><tbody>${rows}</tbody></table></div>` +
      `<p class="oa-small">${esc(t("thermo_note"))}</p>`);
  }

  function drawVix(st, box, readout) {
    const d = st.data.vix_history || [], vis = st.vis;
    if (d.length < 2) { box.innerHTML = empty(t("no_vix_history")); readout.innerHTML = ""; return; }
    const W = width(box), H = chartHeight(W, 0.45, 220, 320);
    const F = frame(W, H, { l: 40, r: 12, t: 12, b: 26 });
    const n = d.length, x = lin(0, n - 1, F.L, F.R);
    const vals = [...d.map(r => r[1]), ...(isOn(vis, "vix:vix3m") ? d.map(r => r[2]) : [])];
    const [lo, hi] = padRange(Math.min(minOf(vals), 12), maxOf(vals), 0.06);
    const y = lin(lo, hi, F.B, F.T);
    let s = svgOpen(F, t("aria_vix")) + yGrid(F, y, (v, step) => tickLabel(v, step), 5);
    for (const b of [15, 20, 30]) if (b > lo && b < hi) s += `<line x1="${F.L}" x2="${F.R}" y1="${y(b).toFixed(1)}" y2="${y(b).toFixed(1)}" stroke="var(--event)" stroke-dasharray="2 4" opacity=".6"/>`;
    const months = [];
    d.forEach((r, i) => { if (i === 0 || r[0].slice(5, 7) !== d[i - 1][0].slice(5, 7)) months.push(i); });
    s += xTicks(F, x, months, i => fmtDate(d[i][0]).replace(/\s?\d+$/, "") || d[i][0].slice(0, 7));
    if (isOn(vis, "vix:vix3m")) s += `<path d="${pathOf(d.filter(r => isNum(r[2])).map(r => [x(d.indexOf(r)), y(r[2])]))}" fill="none" stroke="var(--s2)" stroke-width="1.4"/>`;
    s += `<path d="${pathOf(d.map((r, i) => [x(i), y(r[1])]))}" fill="none" stroke="var(--line)" stroke-width="1.6"/>`;
    box.innerHTML = s + svgClose(F);
    bindHover(box, readout, d.map((_, i) => x(i)), i => {
      const r = d[i];
      return `<span><b>${esc(r[0])}</b></span><span>${sw("var(--line)")}VIX <b>${esc(fmtRatio(r[1]))}</b></span><span>${sw("var(--s2)")}VIX3M <b>${esc(fmtRatio(r[2]))}</b></span>` +
        `<span>VIX/VIX3M <b>${isNum(r[2]) && r[2] ? esc(fmtRatio(r[1] / r[2], 3)) : DASH}</b></span>`;
    }, () => `<span>${esc(t("vix_hint"))}</span>`);
  }

  function drawSkewBoard(st, box, readout) {
    const rows = st.data.skew_board || [], th = st.data.skew_thresholds || {};
    if (!rows.length) { box.innerHTML = empty(t("unavailable")); readout.innerHTML = ""; return; }
    const labOf = r => `${fmtPts(r[1])} · ${tv("skew_", r[3])}`;    // value labels sit right of the bars
    const labW = Math.max(0, ...rows.filter(r => isNum(r[1])).map(r => measure(labOf(r), 11))) + 10;
    const W = width(box), rowH = 30, H = rows.length * rowH + 46;
    const F = frame(W, H, { l: 52, r: Math.round(Math.min(labW, W * 0.5)), t: 20, b: 26 });
    const vals = rows.map(r => r[1]).filter(isNum);
    const x = lin(Math.min(0, minOf(vals), 0) - 0.005, Math.max(maxOf(vals), th.steep_put || 0.08) * 1.1, F.L, F.R);
    let s = svgOpen(F, t("aria_skew_board"));
    const { ticks, step } = niceTicks(x.lo, x.hi, 5);
    s += ticks.map(v => `<line class="grid" x1="${x(v).toFixed(1)}" x2="${x(v).toFixed(1)}" y1="${F.T}" y2="${F.B}"/><text x="${x(v).toFixed(1)}" y="${F.B + 16}" text-anchor="middle">${esc(tickLabel(v * 100, step * 100))}</text>`).join("");
    for (const [k, v] of Object.entries(th)) if (isNum(v) && v > x.lo && v < x.hi && k !== "flat") s += `<line x1="${x(v).toFixed(1)}" x2="${x(v).toFixed(1)}" y1="${F.T}" y2="${F.B}" stroke="var(--event)" stroke-dasharray="4 3"/><text class="ev" x="${(x(v) + 3).toFixed(1)}" y="${F.T - 6}">${esc(tv("skew_", k))}</text>`;
    rows.forEach((row, i) => {
      const [key, rr] = row, yy = F.T + 6 + i * rowH;
      s += `<text class="lbl" x="${F.L - 8}" y="${(yy + 12).toFixed(1)}" text-anchor="end">${esc(key)}</text>`;
      if (isNum(rr)) {
        const a = x(Math.min(0, rr)), b = x(Math.max(0, rr));
        s += `<rect x="${a.toFixed(1)}" y="${yy}" width="${Math.max(1, b - a).toFixed(1)}" height="16" fill="var(--put)" opacity=".75"/>` +
          `<text x="${(b + 4).toFixed(1)}" y="${(yy + 12).toFixed(1)}">${esc(labOf(row))}</text>`;
      }
    });
    s += `<line class="zero" x1="${x(0).toFixed(1)}" x2="${x(0).toFixed(1)}" y1="${F.T}" y2="${F.B}"/>`;
    box.innerHTML = s + svgClose(F);
    readout.innerHTML = `<span>${esc(t("skew_board_hint"))}</span>`;
  }

  function marketTables(d) {
    const gb = (d.gamma_board || []).map(r => `<tr><td class="l">${esc(r.key)}</td>${r.available ? `<td class="${pctCls(r.net_gex_1pct)}">${esc(fmtUsd(r.net_gex_1pct))}</td><td>${esc(tv("regime_", r.regime))}</td>` +
      `<td>${isNum(r.zero_gamma) ? esc(fmtPx(r.zero_gamma)) + ` <span class="oa-sub">${esc(fmtPct(r.zero_gamma_dist_pct, 1, true))}</span>` : DASH}</td><td>${esc(fmtPctl(r.net_gex_pctl))}</td>` : `<td colspan="4" class="l">${esc(r.reason || t("unavailable"))}</td>`}</tr>`).join("");
    const pcr = (d.pcr || []).map(([k, v, o]) => `<tr><td class="l">${esc(k)}</td><td>${esc(fmtRatio(o))}</td><td>${esc(fmtRatio(v))}</td></tr>`).join("");
    return card(esc(t("t_gamma_board")), `<div class="oa-tablewrap"><table class="oa-table"><thead><tr><th>${esc(t("th_key"))}</th><th>${esc(t("net_gex"))}</th><th>${esc(t("regime"))}</th><th>${esc(t("zero_gamma"))}</th><th>${esc(t("th_pctl"))}</th></tr></thead><tbody>${gb}</tbody></table></div>`) +
      card(esc(t("t_pcr_board")), `<div class="oa-tablewrap"><table class="oa-table"><thead><tr><th>${esc(t("th_key"))}</th><th>${esc(t("th_pcr_oi"))}</th><th>${esc(t("th_pcr_vol"))}</th></tr></thead><tbody>${pcr}</tbody></table></div>`);
  }

  function sparksHtml(d) {
    const sp = d.spark || {}, keys = Object.keys(sp);
    if (!keys.length) return "";
    const fmt = { spx_iv30: fmtIV, ratio_30_90: v => fmtRatio(v, 3), rr25: v => fmtPts(v), gex: fmtUsd };
    const one = k => {
      const pts = sp[k], W = 220, H = 48, vals = pts.map(p => p[1]);
      const [lo, hi] = padRange(minOf(vals), maxOf(vals), 0.1);
      const x = lin(0, pts.length - 1, 2, W - 2), y = lin(lo, hi, H - 2, 2);
      return `<div class="oa-spark"><div class="k">${esc(t("spark_" + k))} · <b>${esc((fmt[k] || String)(vals[vals.length - 1]))}</b> <span class="oa-sub">${esc(t("spark_days", pts.length))}</span></div>` +
        `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" role="img" aria-label="${esc(t("spark_" + k))}"><path d="${pathOf(pts.map((p, i) => [x(i), y(p[1])]))}" fill="none" stroke="var(--line)" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg></div>`;
    };
    return card(esc(t("t_spark")), `<div class="oa-sparks">${keys.map(one).join("")}</div>`, "oa-wide");
  }

  // ------------------------------------------------------------------ chart cards
  function chartCard(id, title, opts = {}) {
    const chips = opts.chips ? `<div class="oa-legend">${chipsHtml(opts.chips, opts.vis)}</div>` : "";
    const tools = chips || opts.tools ? `<div class="oa-toolbar">${chips}${opts.tools || ""}</div>` : "";
    const inner = `${tools}<div class="oa-readout"></div><div class="oa-chartbox" data-chart="${id}"></div>${opts.after || ""}`;
    if (opts.collapsed != null) {
      return `<section class="oa-card ${opts.cls || ""}"><details data-chart-wrap="${id}"${opts.collapsed ? "" : " open"}><summary><h3>${title}</h3></summary>${inner}</details></section>`;
    }
    return `<section class="oa-card ${opts.cls || ""}"><h3>${title}</h3>${inner}</section>`;
  }

  function tickerPage(st) {
    const d = st.data, m = d.meta, vis = st.vis;
    const coneChips = [{ key: "cone:closes", label: t("lg_closes"), color: "var(--text)" }, { key: "cone:band", label: t("lg_band"), color: "var(--cone-edge)", sw: "box" }];
    const termChips = [{ key: "term:cm", label: t("lg_cm"), color: "var(--s3)", sw: "dot" }, { key: "term:kinks", label: t("lg_kinks"), color: "var(--event)", sw: "box" },
      { key: "term:cboe", label: t("lg_cboe_iv"), color: "var(--s2)", sw: "dot", on: false }];
    const gexChips = [{ key: "gex:bars", label: t("lg_gex_bars"), color: "var(--gex-pos)", sw: "box" }, { key: "gex:profile", label: t("lg_gex_profile"), color: "var(--line)" }];
    const oiTools = segHtml("oa-oiwin", ["next", "d30", "d45", "all"].map(k => [k, t("win_" + k, (d.oi && d.oi.window_dte || {})[k])]), st.oiWin) +
      segHtml("oa-oimeas", [["oi", t("oi_short")], ["vol", t("vol_short")]], st.oiMeasure);
    const stockGex = m.kind === "stock";
    const gexTitle = `${esc(t("t_gex"))}${stockGex ? ` <span class="oa-badge warn">${esc(t("low_confidence"))}</span>` : ""}`;
    const em = d.em || {};
    const coneAfter = `${emTableHtml(em.rows)}<p class="oa-note">${esc(t("cone_note"))}</p>`;
    const termAfter = `<p class="oa-note">${esc(t("term_note"))}</p>`;
    const earningsCard = d.earnings && d.earnings.applies
      ? chartCard("earnings", esc(t("t_earnings")), { after: earningsSummary(d) })
      : "";
    return `${headerHtml(d)}${noticesHtml(d)}${tickerKpis(d)}
      <div class="oa-grid">
        ${chartCard("cone", esc(t("t_cone")), { chips: coneChips, vis, after: coneAfter })}
        ${chartCard("term", esc(t("t_term")), { chips: termChips, vis, after: termAfter })}
        <section class="oa-card oa-wide" id="oa-strat"><h3>${esc(t("t_strategies"))} <span class="oa-badge">${esc(t("not_advice"))}</span></h3><div class="oa-strat-body"></div></section>
        ${chartCard("smile", esc(t("t_smile")), { chips: smileItems(d.smile || {}).map(it => ({ key: it.key, label: it.label, color: it.color })), vis, after: `<p class="oa-note">${esc(t("smile_note"))}</p>` })}
        ${chartCard("oi", esc(t("t_oi")), { tools: oiTools, after: `<p class="oa-note">${esc(t("oi_note_chart"))}</p>` })}
        ${chartCard("gex", gexTitle, { chips: gexChips, vis, cls: "oa-wide", collapsed: stockGex ? true : null, after: `<p class="oa-note">${esc(t("gex_note"))}</p>` })}
      </div>
      <div class="oa-below">
        ${earningsCard}
        ${card(esc(t("t_skew_term")), skewTermSummary(d))}
        ${card(esc(t("t_positioning")), positioningSummary(d))}
        ${card(esc(t("t_chain")), `<div class="oa-chain-body"></div>`, "oa-wide")}
        <section class="oa-card"><details class="oa-flow"${st.flowOpen ? " open" : ""}><summary><h3>${esc(t("t_flow"))}</h3></summary><div class="oa-flow-body"></div></details></section>
        ${marketContextHtml(d)}
        ${notesHtml(d)}
      </div>
      <p class="oa-disclaimer">${esc(I18N.lang() === "en" ? (m.disclaimer || t("disclaimer")) : t("disclaimer"))}</p>`;
  }

  function marketContextHtml(d) {
    const mc = d.market_context || {};
    if (!mc.available) return card(esc(t("t_market_context")), empty(mc.reason));
    const v = mc.vix || {}, s = mc.spx || {};
    return card(esc(t("t_market_context")), kv([
      ["VIX", v.level != null ? `${esc(fmtRatio(v.level))} · ${esc(tv("band_", v.band))}${isNum(v.pctl_1y) ? " · " + esc(t("pctl1y_sub", fmtPctl(v.pctl_1y))) : ""}` : DASH],
      [t("spx_term"), s.shape ? `${esc(tv("shape_", s.shape))} · 30/90 ${esc(fmtRatio(s.ratio_30_90, 3))}` : DASH],
      [t("spx_skew"), s.skew_label ? `${esc(fmtPts(s.rr25_30d))} · ${esc(tv("skew_", s.skew_label))}` : DASH],
      [t("spx_gamma"), s.gamma_regime ? `${esc(tv("regime_", s.gamma_regime))}${isNum(s.zero_gamma_dist_pct) ? " · " + esc(t("zero_gamma_dist", fmtPct(s.zero_gamma_dist_pct, 1, true))) : ""}` : DASH],
    ]) + (notesOf(mc).length ? `<ul class="oa-list">${notesOf(mc).map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""));
  }

  function marketPage(st) {
    const d = st.data, vis = st.vis;
    const termChips = [{ key: "term:cm", label: t("lg_cm"), color: "var(--s3)", sw: "dot" }, { key: "term:kinks", label: t("lg_kinks"), color: "var(--event)", sw: "box" },
      { key: "term:cboe", label: t("lg_cboe_iv"), color: "var(--s2)", sw: "dot", on: false }];
    const gexChips = [{ key: "gex:bars", label: t("lg_gex_bars"), color: "var(--gex-pos)", sw: "box" }, { key: "gex:profile", label: t("lg_gex_profile"), color: "var(--line)" }];
    const vixChips = [{ key: "vix:vix3m", label: "VIX3M", color: "var(--s2)" }];
    const v = d.vix || {};
    const vixAfter = `<p class="oa-note">${esc(t("vix_note", fmtRatio(v.vix9d_vix, 3), fmtPctl(v.vix9d_vix_pctl_1y)))}</p>`;
    return `${headerHtml(d)}${noticesHtml(d)}${marketKpis(d)}
      <div class="oa-grid">
        ${thermoHtml(d)}
        ${chartCard("vix", esc(t("t_vix")), { chips: vixChips, vis, after: vixAfter })}
        ${chartCard("term", esc(t("t_term_spx")), { chips: termChips, vis })}
        ${chartCard("gex", esc(t("t_gex_spx")), { chips: gexChips, vis, after: `<p class="oa-note">${esc(t("gex_note"))}</p>` })}
        ${chartCard("skew", esc(t("t_skew_board")))}
        ${card(esc(t("t_em_board")), emTableHtml(d.em, true) || empty())}
        ${marketTables(d)}
        ${sparksHtml(d)}
      </div>
      <div class="oa-below">${notesHtml(d)}</div>
      <p class="oa-disclaimer">${esc(I18N.lang() === "en" ? (d.meta.disclaimer || t("disclaimer")) : t("disclaimer"))}</p>`;
  }

  // ------------------------------------------------------------------ render
  const DRAW = {
    cone: drawCone, smile: drawSmile, oi: drawOI, earnings: drawEarnings, vix: drawVix, skew: drawSkewBoard,
    term: (st, box, ro) => drawTerm(st, box, ro, st.data.term), gex: (st, box, ro) => drawGex(st, box, ro, st.data.gex),
  };

  function render(root, data) {
    const st = root._oa || (root._oa = { vis: loadVis(), id: "oa" + (++uid), sort: {}, stratAll: {}, chainIdx: 0, flowAll: false });
    if (st.ro) st.ro.disconnect();
    if (st.data !== data) {
      st.stratTab = null; st.chainIdx = 0; st.flowAll = false; st.sort = {}; st.stratAll = {};
      st.oiWin = "d45"; st.oiMeasure = "oi";
    }
    st.root = root; st.data = data;
    if (!data || !data.meta) { root.innerHTML = empty(t("no_data")); return; }
    root.innerHTML = data.meta.mode === "market" ? marketPage(st) : tickerPage(st);
    const boxes = [...root.querySelectorAll(".oa-chartbox[data-chart]")];
    const drawOne = box => {
      const fn = DRAW[box.dataset.chart];
      const wrap = box.closest("details");
      if (!fn || (wrap && !wrap.open)) return;
      const ro = box.parentElement.querySelector(".oa-readout");
      try { fn(st, box, ro); } catch (e) { box.innerHTML = empty(String(e && e.message || e)); }
      box._w = box.clientWidth;
    };
    const redraw = () => boxes.forEach(drawOne);
    // legend chips: toggle, remember, redraw the chart of that card
    root.querySelectorAll(".oa-chip").forEach(b => b.addEventListener("click", () => {
      const k = b.dataset.k, cur = b.getAttribute("aria-pressed") === "true";
      st.vis[k] = !cur;
      b.setAttribute("aria-pressed", String(!cur));
      saveVis(st.vis);
      const box = b.closest(".oa-card").querySelector(".oa-chartbox");
      if (box) drawOne(box);
    }));
    root.querySelectorAll("details[data-chart-wrap]").forEach(det => det.addEventListener("toggle", () => {
      if (det.open) det.querySelectorAll(".oa-chartbox").forEach(drawOne);
    }));
    const seg = (sel, key) => root.querySelectorAll(`${sel} button`).forEach(b => b.addEventListener("click", () => {
      st[key] = b.dataset.v;
      b.parentElement.querySelectorAll("button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
      drawOne(root.querySelector('.oa-chartbox[data-chart="oi"]'));
    }));
    seg(".oa-oiwin", "oiWin");
    seg(".oa-oimeas", "oiMeasure");
    // strategy tabs, candidate toggle and sortable headers
    const strat = root.querySelector(".oa-strat-body");
    const paintStrat = () => {
      if (!strat) return;
      strat.innerHTML = strategiesBody(st);
      strat.querySelectorAll(".oa-tab").forEach(b => b.addEventListener("click", () => { st.stratTab = b.dataset.tab; st.sort.strat = null; paintStrat(); }));
      strat.querySelectorAll("[data-all]").forEach(b => b.addEventListener("click", () => { st.stratAll[b.dataset.all] = !st.stratAll[b.dataset.all]; paintStrat(); }));
      strat.querySelectorAll("th.sort").forEach(th => {
        const go = () => {
          const k = th.dataset.k, cur = st.sort.strat;
          st.sort.strat = cur && cur.k === k ? (cur.dir > 0 ? { k, dir: -1 } : null) : { k, dir: 1 };
          paintStrat();
        };
        th.addEventListener("click", go);
        th.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      });
    };
    paintStrat();
    const chain = root.querySelector(".oa-chain-body");
    const paintChain = () => {
      if (!chain) return;
      chain.innerHTML = chainBody(st);
      const sel = chain.querySelector("select[data-chain]");
      if (sel) sel.addEventListener("change", () => { st.chainIdx = +sel.value; paintChain(); });
    };
    paintChain();
    const flow = root.querySelector(".oa-flow-body");
    const paintFlow = () => {
      if (!flow) return;
      flow.innerHTML = flowBody(st);
      const b = flow.querySelector("[data-flow]");
      if (b) b.addEventListener("click", () => { st.flowAll = !st.flowAll; paintFlow(); });
    };
    paintFlow();
    const fd = root.querySelector("details.oa-flow");
    if (fd) fd.addEventListener("toggle", () => { st.flowOpen = fd.open; });
    redraw();
    const onSize = () => boxes.forEach(b => { if (b.clientWidth && Math.abs(b.clientWidth - (b._w || 0)) > 2) drawOne(b); });
    if (global.ResizeObserver) { st.ro = new ResizeObserver(onSize); boxes.forEach(b => st.ro.observe(b)); }
    if (!st.onWin) { st.onWin = () => st.onSize && st.onSize(); global.addEventListener("resize", st.onWin); }
    st.onSize = onSize;
  }

  function pageTitle(data) {
    const m = (data && data.meta) || {};
    return m.mode === "market" ? `${t("app_title_market")} · ${m.as_of || ""}` : `${m.ticker || ""} · ${t("app_title")} · ${m.as_of || ""}`;
  }

  // chart.html entry point: language toggle, page title, render, re-render on language change.
  function boot(root, data) {
    const title = () => { global.document.title = pageTitle(data); };
    I18N.bindToggle(global.document.getElementById("oa-lang"));
    I18N.applyStatic(global.document);
    I18N.onChange(() => { title(); render(root, data); });
    title();
    render(root, data);
  }

  global.OAChart = { render, boot, pageTitle };
})(window);
