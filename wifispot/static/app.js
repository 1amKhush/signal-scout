"use strict";

// ---------------------------------------------------------------- DOM helpers
// Every string from the network (SSIDs, labels) goes in as a text node, never as HTML.

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

function build(el, attrs, kids) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}
const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
const s = (tag, attrs, ...kids) => build(document.createElementNS(SVGNS, tag), attrs, kids);

async function api(path, method = "GET", body) {
  const opt = { method };
  if (method !== "GET") {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body || {});
  }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const sigs = {};
function changed(key, value) {
  const v = JSON.stringify(value);
  if (sigs[key] === v) return false;
  sigs[key] = v;
  return true;
}

// ---------------------------------------------------------------- formatting

const minus = (x) => String(x).replace("-", "−");
const fmt = {
  dbm: (v) => (v == null ? "—" : `${minus(Math.round(v))} dBm`),
  ms: (v) => (v == null ? "—" : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`),
  pct: (v) => (v == null ? "—" : `${(v * 100).toFixed(v > 0 && v < 0.1 ? 1 : 0)}%`),
  mbps: (v) => (v == null ? "—" : `${v >= 100 ? Math.round(v) : v.toFixed(1)} Mb/s`),
  bps: (v) => (v == null ? "—" : v >= 1e6 ? `${(v / 1e6).toFixed(1)} Mb/s` : `${Math.round(v / 1e3)} kb/s`),
  age: (sec) => (sec == null ? "—" : sec < 60 ? `${Math.round(sec)} s ago`
    : sec < 5400 ? `${Math.round(sec / 60)} min ago` : `${(sec / 3600).toFixed(1)} h ago`),
  dur: (sec) => (sec < 60 ? `${Math.round(sec)} s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`),
  score: (v) => (v == null ? "—" : Math.round(v)),
};

function signalStatus(dbm) {
  if (dbm == null) return ["neutral", "No signal", "–"];
  if (dbm >= -55) return ["good", "Excellent", "✓"];
  if (dbm >= -67) return ["good", "Good", "✓"];
  if (dbm >= -75) return ["warning", "Fair", "!"];
  return ["critical", "Weak", "✕"];
}
function latencyStatus(ms) {
  if (ms == null) return ["neutral", "No replies", "–"];
  if (ms < 20) return ["good", "Fast", "✓"];
  if (ms < 50) return ["warning", "Slow", "!"];
  return ["critical", "Very slow", "✕"];
}
function lossStatus(loss) {
  if (loss == null) return ["neutral", "No data", "–"];
  if (loss < 0.01) return ["good", "Clean", "✓"];
  if (loss < 0.03) return ["warning", "Some loss", "!"];
  return ["critical", "Heavy loss", "✕"];
}
const badge = ([st, label, icon]) => h("span", { class: "status", "data-s": st, "data-icon": icon }, label);

function median(xs) {
  const v = xs.filter((x) => x != null).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = (v.length - 1) / 2;
  return (v[Math.floor(m)] + v[Math.ceil(m)]) / 2;
}

// ---------------------------------------------------------------- colour

const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
};
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// One-hue sequential blue ramp for signal strength; weak recedes toward the surface in both themes.
const BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
  "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
const ramp = () => (isDark() ? BLUE.slice(0, 11).reverse() : BLUE.slice(1));
const SIG_LO = -85, SIG_HI = -45;
const signalT = (dbm) => Math.max(0, Math.min(1, (dbm - SIG_LO) / (SIG_HI - SIG_LO)));
const hexRGB = (hx) => [1, 3, 5].map((i) => parseInt(hx.slice(i, i + 2), 16));
function rampRGB(t) {
  const r = ramp(), x = t * (r.length - 1), i = Math.min(Math.floor(x), r.length - 2), f = x - i;
  const a = hexRGB(r[i]), b = hexRGB(r[i + 1]);
  return a.map((v, k) => Math.round(v + (b[k] - v) * f));
}
const rampColor = (t) => `rgb(${rampRGB(t).join(",")})`;
const inkOn = (rgb) => (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] > 150 ? "#0b0b0b" : "#ffffff");

// ---------------------------------------------------------------- tooltip

const tip = {
  show(e, rows) {
    const el = $("tip");
    el.replaceChildren(...rows);
    el.hidden = false;
    const w = el.offsetWidth, hh = el.offsetHeight;
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + w > innerWidth - 8) x = e.clientX - w - 14;
    if (y + hh > innerHeight - 8) y = e.clientY - hh - 14;
    el.style.left = `${Math.max(8, x)}px`;
    el.style.top = `${Math.max(8, y)}px`;
  },
  hide() { $("tip").hidden = true; },
};
const tipRow = (value, name, color) => h("div", { class: "t-row" },
  color ? h("span", { class: "line", style: `width:12px;height:2px;background:${color};display:inline-block` }) : null,
  h("b", {}, value), h("span", { class: "t-name" }, name));

// ---------------------------------------------------------------- time-series chart

function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

class TimeChart {
  constructor(el, { height = 150, log = false, domain, format, label }) {
    Object.assign(this, { el, height, log, domain, format });
    this.legend = h("div", { class: "legend" });
    this.svg = s("svg", { role: "img", "aria-label": label });
    el.append(h("div", { class: "chart" }, this.legend, this.svg));
    this.svg.addEventListener("pointermove", (e) => { this.hover = e; this.draw(); });
    this.svg.addEventListener("pointerleave", () => { this.hover = null; tip.hide(); this.draw(); });
  }

  update(data) { this.data = data; this.draw(); }

  draw() {
    const d = this.data;
    if (!d) return;
    const W = this.svg.clientWidth || 600, H = this.height;
    const m = { l: 48, r: 14, t: 14, b: 22 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
    const [t0, t1] = d.x;
    const X = (t) => m.l + ((t - t0) / (t1 - t0)) * pw;
    const vals = d.series.flatMap((sr) => sr.points.map((p) => p[1])).filter((v) => v != null);
    let Y, ticks;
    if (this.log) {
      const hi = [10, 20, 50, 100, 200, 500, 1000, 2000].find((c) => c >= Math.max(...vals, 1)) || 2000;
      const lo = 1, L = (v) => Math.log(Math.max(v, lo));
      Y = (v) => m.t + (1 - (L(v) - L(lo)) / (L(hi) - L(lo))) * ph;
      ticks = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000].filter((v) => v <= hi);
    } else {
      let [lo, hi] = this.domain(vals);
      const step = niceStep((hi - lo) / 4);
      lo = Math.floor(lo / step) * step;
      hi = Math.ceil(hi / step) * step;
      Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * ph;
      ticks = [];
      for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
    }
    const out = [];
    for (const v of ticks) {
      out.push(s("line", { class: "grid-line", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
      out.push(s("text", { class: "axis-text", x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, minus(v)));
    }
    for (let k = 0; k <= Math.floor((t1 - t0) / 60); k++) {
      const t = t1 - k * 60;
      out.push(s("text", { class: "axis-text", x: X(t), y: H - 6, "text-anchor": k === 0 ? "end" : "middle" },
        k === 0 ? "now" : `−${k} min`));
    }
    for (const ev of d.events || []) {
      out.push(s("line", { class: "base-line", x1: X(ev.t), x2: X(ev.t), y1: m.t, y2: m.t + ph }));
      out.push(s("text", { class: "axis-text", x: X(ev.t) + 3, y: m.t + 8 }, ev.label));
    }
    for (const t of d.lost || []) {
      out.push(s("line", { x1: X(t), x2: X(t), y1: m.t - 10, y2: m.t - 2, style: "stroke:var(--critical);stroke-width:2" }));
    }
    for (const sr of d.series) {
      let path = "", pen = false;
      for (const [t, v] of sr.points) {
        if (v == null) { pen = false; continue; }
        path += `${pen ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`;
        pen = true;
      }
      out.push(s("path", { d: path, fill: "none", stroke: sr.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round" }));
    }

    if (this.hover && d.series[0].points.length) {
      const r = this.svg.getBoundingClientRect();
      const px = (this.hover.clientX - r.left) * (W / r.width);
      const tt = t0 + ((px - m.l) / pw) * (t1 - t0);
      const near = (pts) => pts.reduce((b, p) => (Math.abs(p[0] - tt) < Math.abs(b[0] - tt) ? p : b), pts[0]);
      const tn = near(d.series[0].points)[0];
      out.push(s("line", { class: "base-line", x1: X(tn), x2: X(tn), y1: m.t, y2: m.t + ph }));
      const rows = [h("div", { class: "t-head" }, `${Math.max(0, Math.round(t1 - tn))} s ago`)];
      for (const sr of d.series) {
        const p = sr.points.length ? near(sr.points) : null;
        const v = p && Math.abs(p[0] - tn) <= 1.5 ? p[1] : null;
        if (v != null) out.push(s("circle", { cx: X(tn), cy: Y(v), r: 4, fill: sr.color, stroke: css("--surface"), "stroke-width": 2 }));
        rows.push(tipRow(v == null ? "—" : this.format(v), sr.name, sr.color));
      }
      const lostHere = (d.lost || []).filter((t) => Math.abs(t - tn) <= 1).length;
      if (lostHere) rows.push(tipRow("lost", "router probe(s)", css("--critical")));
      tip.show(this.hover, rows);
    }
    this.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    this.svg.setAttribute("height", H);
    this.svg.replaceChildren(...out);

    const keys = [];
    if (d.series.length > 1) {
      for (const sr of d.series) keys.push(h("span", { class: "key" }, h("span", { class: "line", style: `background:${sr.color}` }), sr.name));
    }
    if (d.lostLabel) keys.push(h("span", { class: "key" }, h("span", { class: "tick" }), d.lostLabel));
    if (changed(`legend-${this.el.id}`, keys.map((k) => k.textContent))) this.legend.replaceChildren(...keys);
  }
}

// ---------------------------------------------------------------- app state

const state = { tab: "now", live: null, survey: null, aps: null, mapView: null, interp: true, dragging: false, editing: null };
const charts = {};

function setTab(tab) {
  state.tab = tab;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  for (const sec of document.querySelectorAll(".tab")) sec.hidden = sec.id !== `tab-${tab}`;
  for (const k of Object.keys(sigs)) delete sigs[k];
  render();
  if (tab === "networks") loadAps();
}

function render() {
  if (state.live) renderHeader();
  if (state.survey) renderPhone();
  if (state.tab === "now" && state.live) renderNow();
  if (state.tab === "networks" && state.aps) renderNetworks();
  if (state.tab === "survey" && state.survey) renderSurvey();
}

// ---------------------------------------------------------------- header

function renderHeader() {
  const c = state.live.connection, last = state.live.link.at(-1) || {};
  const parts = c.ssid != null
    ? [c.ssid || "(hidden network)", c.bssid, c.channel && `ch ${c.channel} · ${c.band} GHz${c.width ? ` · ${c.width} MHz` : ""}`, fmt.dbm(last.signal)]
    : ["Not connected to Wi-Fi"];
  $("conn").textContent = `${state.live.iface}: ${parts.filter(Boolean).join("  ·  ")}`;
}

function renderPhone() {
  const p = state.survey.phone;
  const chip = $("phone-chip");
  const label = p.connected ? `Phone ${p.acc != null ? `±${Math.round(p.acc)} m` : "· no current location"}` : p.url ? "Phone not connected" : "Pair phone";
  if (changed("chip", [label, p.connected])) {
    chip.replaceChildren(h("span", { class: "dot", style: `background:${p.connected ? "var(--good)" : "var(--muted)"}` }), label);
  }
  const status = $("pair-status");
  if (p.connected) {
    status.replaceChildren(badge(["good", "Phone connected", "✓"]), " ",
      p.acc != null ? `Location ±${Math.round(p.acc)} m (${qualityText(p.quality)})` : "Waiting for the phone to share its location…");
  } else {
    status.textContent = p.last_seen_age != null ? `Phone last seen ${fmt.age(p.last_seen_age)}.` : "Waiting for the phone to open the link…";
  }
}

const qualityText = (q) => ({ good: "good enough to tell nearby spots apart", coarse: "coarse; fine for spots far apart",
  insufficient: "too coarse to place spots", none: "no fix" }[q]);

// ---------------------------------------------------------------- Now tab

function tile(label, value, unit, ...foot) {
  return h("div", { class: "tile" }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, value, unit ? h("span", { class: "unit" }, unit) : null),
    h("div", { class: "foot" }, ...foot.filter(Boolean).map((f) => (typeof f === "string" ? h("span", {}, f) : f))));
}

function renderNow() {
  const L = state.live, last = L.link.at(-1) || {}, l60 = L.last60;
  const inet = L.internet ? L.internet.series.filter((b) => b.t >= L.t - 60) : [];
  const inetRtt = median(inet.map((b) => b.rtt));
  const inetN = inet.reduce((a, b) => a + b.n, 0), inetLost = inet.reduce((a, b) => a + b.lost, 0);
  const routerLabel = l60.latency_target === "gateway" ? "Router latency" : "Latency (router ignores ping)";

  fill($("tiles"),
    tile("Signal", last.signal != null ? minus(Math.round(last.signal)) : "—", "dBm",
      badge(signalStatus(last.signal)), l60.signal_sd != null ? `±${l60.signal_sd.toFixed(1)} dB over 60 s` : ""),
    tile(routerLabel, l60.rtt_ms != null ? fmt.ms(l60.rtt_ms).replace(" ms", "") : "—", "ms",
      badge(latencyStatus(l60.rtt_ms)), l60.rtt_p90_ms != null ? `p90 ${fmt.ms(l60.rtt_p90_ms)}` : ""),
    tile("Packet loss", l60.loss != null ? fmt.pct(l60.loss).replace("%", "") : "—", "%",
      badge(lossStatus(l60.loss)), `${l60.ping_n} probes, 60 s`),
    tile("Link rate", last.bitrate != null ? Math.round(last.bitrate) : "—", "Mb/s",
      L.connection.band ? `${L.connection.band} GHz · ch ${L.connection.channel}` : ""),
    L.internet ? tile("Internet latency", inetRtt != null ? fmt.ms(inetRtt).replace(" ms", "") : "—", "ms",
      `to ${L.internet.target}`, inetN ? `${fmt.pct(inetLost / inetN)} lost` : "") : null,
  );

  renderSettled();

  const x = [L.t - 300, L.t];
  if (!charts.signal) {
    charts.signal = new TimeChart($("chart-signal"), { height: 150, label: "Signal strength over the last 5 minutes",
      domain: (v) => { const lo = Math.min(...v, -70), hi = Math.max(...v, -50); const c = (lo + hi) / 2; return [Math.min(lo - 3, c - 10), Math.max(hi + 3, c + 10)]; },
      format: (v) => fmt.dbm(v) });
    charts.latency = new TimeChart($("chart-latency"), { height: 170, log: true, label: "Latency over the last 5 minutes", format: fmt.ms });
    charts.traffic = new TimeChart($("chart-traffic"), { height: 120, label: "Traffic over the last 5 minutes",
      domain: (v) => [0, Math.max(1, ...v)], format: (v) => fmt.mbps(v) });
  }
  const events = [];
  for (let i = 1; i < L.link.length; i++) {
    const a = L.link[i - 1].bssid, b = L.link[i].bssid;
    if (a && b && a !== b) events.push({ t: L.link[i].t, label: "roamed" });
  }
  charts.signal.update({ x, events, series: [{ name: "Signal", color: css("--series-1"), points: L.link.map((p) => [p.t, p.signal]) }] });
  const series = [];
  if (L.gateway) series.push({ name: "Router", color: css("--series-1"), points: L.gateway.series.map((b) => [b.t + 0.5, b.rtt]) });
  if (L.internet) series.push({ name: "Internet", color: css("--series-2"), points: L.internet.series.map((b) => [b.t + 0.5, b.rtt]) });
  if (series.length) {
    charts.latency.update({ x, series, lostLabel: "Lost router probe",
      lost: L.gateway ? L.gateway.series.filter((b) => b.lost).map((b) => b.t + 0.5) : [] });
  }
  charts.traffic.update({ x, series: [
    { name: "Down", color: css("--series-1"), points: L.link.map((p) => [p.t, p.rx != null ? p.rx / 1e6 : null]) },
    { name: "Up", color: css("--series-2"), points: L.link.map((p) => [p.t, p.tx != null ? p.tx / 1e6 : null]) }] });

  const r = L.radio, c = L.connection;
  const rows = [
    ["Network", c.ssid != null ? c.ssid || "(hidden)" : "—"], ["Access point", c.bssid || "—"],
    ["Channel", c.channel ? `${c.channel} (${c.band} GHz${c.width ? `, ${c.width} MHz` : ""})` : "—"],
    ["Security", c.security || "—"], ["Link quality", r.quality != null ? `${r.quality}/70` : "—"],
    ["Noise floor", r.noise_dbm != null ? fmt.dbm(r.noise_dbm) : "not reported by driver"],
    ["Transmit power", r.tx_power_dbm != null ? `${r.tx_power_dbm} dBm` : "—"],
    ["Missed beacons", r.missed_beacon ?? "—"], ["Excessive retries", r.tx_excessive_retries ?? "—"],
    ["Power saving", r.power_save == null ? "—" : r.power_save ? "on (can add latency spikes)" : "off"],
    ["Router", L.gateway ? `${L.gateway.target}${L.gateway.answering ? "" : ` (${L.gateway.error || "not answering ping"})`}` : "no default route"],
  ];
  if (changed("radio", rows)) {
    $("radio").replaceChildren(...rows.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, String(v)))));
  }
}

function diffList(items) {
  return h("ul", {}, items.map((d) => h("li", { class: d.better ? "up" : "down" },
    h("span", { class: "mk", "aria-hidden": "true" }, d.better ? "↑" : "↓"),
    h("span", {}, `${d.better ? "Better" : "Worse"}: ${d.text}`))));
}

function renderSettled() {
  const st = state.live.settled, el = $("settled");
  if (!st) { if (changed("settled", null)) el.replaceChildren(); return; }
  const sig = [st.pending, st.pending && Math.round(st.window_s), st.consistent, st.differences, st.vs_baseline, Math.round(st.spot_age / 60)];
  if (!changed("settled", sig)) return;
  const head = h("div", { class: "row-between" },
    h("h2", {}, `Monitoring your chosen spot: ${st.label}`),
    h("button", { type: "button", onclick: () => choose(null) }, "Stop comparing"));
  if (st.pending) {
    el.replaceChildren(h("div", { class: "card rec" }, head,
      h("p", { class: "small" }, `Collecting live measurements here before comparing with the survey (${Math.round(st.window_s)} of 15 s). Keep the laptop where you will use it.`)));
    return;
  }
  el.replaceChildren(h("div", { class: "card rec" },
    head,
    h("div", { class: "meta" },
      badge(st.consistent ? ["good", "Matches the survey", "✓"] : ["warning", "Worse than when surveyed", "!"]),
      h("span", {}, `Last ${Math.round(st.window_s)} s here compared with the survey ${fmt.age(st.spot_age)}`)),
    st.differences.length ? diffList(st.differences) : h("p", { class: "small" }, "All measurements are within noise of the surveyed values."),
    !st.consistent ? h("p", { class: "caveat small" }, "Conditions may have changed, or the laptop is not quite where it was measured. Re-measure here if this persists.") : null,
    st.vs_baseline && st.vs_baseline.length ? [h("p", { class: "small", style: "margin-top:8px" }, `Compared with where you started (${st.baseline_label}):`), diffList(st.vs_baseline)] : null,
  ));
}

// ---------------------------------------------------------------- Networks tab

async function loadAps() {
  try { state.aps = await api("/api/aps"); renderNetworks(); } catch (e) { /* the live poll shows offline state */ }
}

const BANDS = {
  "2.4": { lo: 2400, hi: 2485, chans: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], f: (c) => 2407 + 5 * c },
  "5": { lo: 5150, hi: 5895, chans: [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165, 169, 173, 177], f: (c) => 5000 + 5 * c },
  "6": { lo: 5925, hi: 7125, chans: Array.from({ length: 59 }, (_, i) => 1 + 4 * i), f: (c) => 5950 + 5 * c },
};

function apRole(ap, conn) {
  if (ap.bssid === conn.bssid) return "me";
  if (conn.ssid && ap.ssid === conn.ssid) return "same";
  return "other";
}

function renderNetworks() {
  const { aps, connection: conn, relations, age } = state.aps;
  const nets = new Set(aps.map((a) => a.ssid)).size;
  const rel = Object.values(relations);
  const strong = Object.entries(relations).filter(([b]) => (aps.find((a) => a.bssid === b) || {}).signal_dbm >= -70).length;
  const me = aps.find((a) => a.bssid === conn.bssid);
  const nSame = rel.filter((r) => r === "same").length, nOver = rel.filter((r) => r === "overlap").length;
  const aps_ = (n) => `${n} access point${n === 1 ? "" : "s"}`;
  $("aps-summary").textContent = `${nets} network${nets === 1 ? "" : "s"} from ${aps_(aps.length)} visible. ` + (me
    ? `You are on ${me.ssid || "(hidden)"} via ${me.bssid}, channel ${me.channel} (${me.band} GHz). `
      + `Sharing that channel: ${aps_(nSame)}; overlapping it: ${aps_(nOver)}`
      + (strong ? `. ${strong} of these ${strong === 1 ? "is" : "are"} strong (≥ −70 dBm), which means more competition for airtime.` : ".")
    : "Not connected.");
  $("aps-age").textContent = age != null
    ? `From NetworkManager's scan cache, refreshed ${fmt.age(age)}. Signal levels in this view are approximate (≈), converted from NetworkManager's percentages.`
    : "";

  const bands = ["2.4", "5", "6"].filter((b) => aps.some((a) => a.band === b));
  const wrap = $("band-charts");
  if (changed("bands", [bands, aps, conn.bssid, innerWidth, isDark()])) {
    wrap.replaceChildren(...bands.map((b) => {
      const card = h("div", { class: "card" }, h("h2", {}, `${b} GHz`, h("span", { class: "sub" }, "each access point spans its channel width; height = signal")));
      card.append(h("div", { class: "legend" },
        h("span", { class: "key" }, h("span", { class: "line", style: `background:${css("--series-1")}` }), "Your connection"),
        h("span", { class: "key" }, h("span", { class: "line", style: `background:${css("--series-2")}` }), "Same network, other access points"),
        h("span", { class: "key" }, h("span", { class: "line", style: `background:${css("--muted")}` }), "Other networks")));
      const holder = h("div", { class: "chart" });
      card.append(holder);
      requestAnimationFrame(() => drawBand(holder, b, aps.filter((a) => a.band === b), conn));
      return card;
    }));
  }

  if (changed("aps-table", [aps, conn.bssid, relations])) {
    const relText = { same: "Same channel", overlap: "Overlaps" };
    $("aps-table").replaceChildren(
      h("thead", {}, h("tr", {}, ["Network", "Access point", "Band · channel", "Width", "Signal (≈)", "Security", "Relation to yours"].map((t) => h("th", {}, t)))),
      h("tbody", {}, aps.map((a) => h("tr", { class: a.bssid === conn.bssid ? "me" : null },
        h("td", {}, a.ssid || h("span", { class: "muted" }, "(hidden)")),
        h("td", {}, a.bssid),
        h("td", {}, `${a.band} GHz · ${a.channel}`),
        h("td", {}, a.width ? `${a.width} MHz` : "—"),
        h("td", {}, h("span", { class: "bars" }, h("i", { style: `width:${a.signal_pct}%` })), `≈ ${fmt.dbm(a.signal_dbm)}`),
        h("td", {}, a.security),
        h("td", {}, a.bssid === conn.bssid ? "Connected" : relText[relations[a.bssid]] || "—"),
      ))));
  }
}

function drawBand(holder, band, aps, conn) {
  const B = BANDS[band];
  const W = holder.clientWidth || 800, H = 220;
  const m = { l: 48, r: 14, t: 22, b: 26 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
  const X = (f) => m.l + ((f - B.lo) / (B.hi - B.lo)) * pw;
  const yLo = -95, yHi = -25;
  const Y = (v) => m.t + (1 - (Math.max(yLo, Math.min(yHi, v)) - yLo) / (yHi - yLo)) * ph;
  const out = [];
  for (let v = -90; v <= -30; v += 10) {
    out.push(s("line", { class: "grid-line", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    out.push(s("text", { class: "axis-text", x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, minus(v)));
  }
  out.push(s("line", { class: "base-line", x1: m.l, x2: W - m.r, y1: m.t + ph, y2: m.t + ph }));
  let lastX = -Infinity;
  for (const c of B.chans) {
    const x = X(B.f(c));
    if (x - lastX < 24) continue;
    lastX = x;
    out.push(s("text", { class: "axis-text", x, y: H - 8, "text-anchor": "middle" }, c));
  }
  const colors = { me: css("--series-1"), same: css("--series-2"), other: css("--muted") };
  const order = { other: 0, same: 1, me: 2 };
  const sorted = [...aps].sort((a, b) => order[apRole(a, conn)] - order[apRole(b, conn)] || a.signal_pct - b.signal_pct);
  for (const a of sorted) {
    const role = apRole(a, conn), x0 = X(a.span[0]), x1 = X(a.span[1]), yt = Y(a.signal_dbm), yb = m.t + ph;
    const e = Math.min((x1 - x0) / 4, 8);
    const d = `M${x0},${yb} C${x0},${yt} ${x0},${yt} ${x0 + e},${yt} L${x1 - e},${yt} C${x1},${yt} ${x1},${yt} ${x1},${yb}`;
    const sw = role === "other" ? 1.25 : 2;
    const path = s("path", { d, fill: colors[role], "fill-opacity": role === "other" ? 0.05 : 0.1, stroke: colors[role], "stroke-width": sw, "pointer-events": "all" });
    path.addEventListener("pointermove", (ev) => {
      path.setAttribute("stroke-width", sw + 1.5);
      tip.show(ev, [h("div", { class: "t-head" }, a.ssid || "(hidden)"),
        tipRow(`≈ ${fmt.dbm(a.signal_dbm)}`, `${a.signal_pct}%`), tipRow(`ch ${a.channel}`, `${a.width || "?"} MHz · ${a.span[0]}–${a.span[1]} MHz`),
        tipRow(a.bssid, a.security)]);
    });
    path.addEventListener("pointerleave", () => { path.setAttribute("stroke-width", sw); tip.hide(); });
    out.push(path);
    if (role === "me") {
      out.push(s("text", { x: (x0 + x1) / 2, y: yt - 6, "text-anchor": "middle", style: "fill:var(--text);font-size:12px;font-weight:600" },
        `${a.ssid || "(hidden)"} (you)`));
    }
  }
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": `${band} GHz channel occupancy` }, ...out);
  holder.replaceChildren(svg);
}

// ---------------------------------------------------------------- survey tab

function renderSurvey() {
  const sv = state.survey;
  renderWizard(sv);
  renderRecommendation(sv);
  renderMap(sv);
  renderSpotsTable(sv);
}

function renderWizard(sv) {
  const n = sv.spots.length, job = sv.job, p = sv.phone;
  const label = $("spot-label"), btn = $("measure-btn"), cancel = $("cancel-btn"), prog = $("progress");
  btn.disabled = !!job;
  cancel.hidden = !job;
  prog.hidden = !job;
  if (job) {
    const frac = job.phase === "measuring" ? 1 - job.remaining / job.duration : 1;
    prog.firstElementChild.style.width = `${Math.round(frac * 100)}%`;
  }
  const sig = [n, job && job.phase, job && Math.ceil(job.remaining), job && job.label, p.connected, p.acc && Math.round(p.acc)];
  if (changed("wizard", sig)) {
    const still = `Keep the laptop${p.connected ? " and phone" : ""} still.`;
    let content;
    if (job && job.phase === "measuring") {
      content = [h("h2", {}, `Measuring “${job.label}”…`), h("p", {}, `${still} ${Math.ceil(job.remaining)} s left.`)];
    } else if (job) {
      content = [h("h2", {}, `Scanning nearby access points at “${job.label}”…`), h("p", {}, `${still} A few seconds more.`)];
    } else if (n === 0) {
      content = [h("h2", {}, "Find a better spot"), h("ol", { class: "steps" },
        h("li", {}, "Measure where you are now. This is your baseline."),
        h("li", {}, `Carry the laptop to 2–4 candidate spots. At each one, set it down and press Measure (${sv.window_s} s, keep it still).`),
        h("li", {}, "See which spot is measurably better, and why."))];
    } else if (n === 1) {
      content = [h("h2", {}, "Baseline recorded"), h("p", {}, "Now carry the laptop to a candidate spot, set it down, and measure again.")];
    } else {
      content = [h("h2", {}, `${n} spots measured`), h("p", {}, "Measure another candidate, or follow the recommendation below. 3–5 spots give a confident answer.")];
    }
    $("wizard-text").replaceChildren(...content);
    label.placeholder = `Name this spot (optional), default “${n === 0 ? "Start" : `Spot ${n + 1}`}”`;
    $("phone-hint").textContent = p.connected
      ? `Phone connected${p.acc != null ? ` · ±${Math.round(p.acc)} m: ${qualityText(p.quality)}` : " · waiting for location"}.`
      : "Optional: pair a phone to place spots by location.";
  }
}

function confidenceBadge(c) {
  return badge({ high: ["good", "High confidence", "✓"], medium: ["warning", "Medium confidence", "!"], low: ["serious", "Low confidence", "?"] }[c]);
}

function renderRecommendation(sv) {
  const rec = sv.recommendation, el = $("recommendation");
  const spot = rec.spot_id && sv.spots.find((x) => x.id === rec.spot_id);
  const sig = [rec.status, rec.decision, rec.spot_id, rec.label, rec.confidence, rec.reasons, rec.caveats, rec.gain,
    sv.chosen_id, spot && spot.hint, spot && spot.position, rec.age_range && rec.age_range.map((a) => Math.round(a / 60))];
  if (!changed("rec", sig)) return;
  if (rec.status !== "ok") {
    el.replaceChildren(sv.spots.length ? h("div", { class: "card muted" }, rec.message) : "");
    return;
  }
  const chosen = sv.chosen_id === rec.spot_id;
  const [a0, a1] = rec.age_range;
  el.replaceChildren(h("div", { class: "card rec" },
    h("div", { class: "muted small" }, "Recommendation"),
    h("div", { class: "headline" }, rec.decision === "move" ? `Move to ${rec.label}` : `Stay at ${rec.label}`),
    h("p", {}, rec.decision === "move"
      ? `It measured clearly better than ${rec.reference_label}${rec.gain != null ? ` (+${Math.round(rec.gain)} points)` : ""}.`
      : "No surveyed spot is clearly better than where you started."),
    h("div", { class: "meta" }, confidenceBadge(rec.confidence),
      spot ? h("span", {}, `Score ${fmt.score(spot.score)} (likely ${fmt.score(spot.score_range[0])}–${fmt.score(spot.score_range[1])})`) : null,
      h("span", {}, `Based on measurements ${fmt.dur(a0)}${fmt.dur(a1) !== fmt.dur(a0) ? `–${fmt.dur(a1)}` : ""} old`)),
    rec.reasons.length ? [h("div", { class: "small muted" }, `Compared with ${rec.reference_label}:`), diffList(rec.reasons)]
      : h("p", { class: "small" }, `All measurements are within noise of ${rec.reference_label}.`),
    rec.caveats.map((c) => h("p", { class: "caveat" }, c)),
    spot && spot.hint ? h("p", { class: "caveat" }, `A stronger access point of this network was visible here (channel ${spot.hint.channel}, ≈ ${fmt.dbm(spot.hint.signal_dbm)} vs ≈ ${fmt.dbm(spot.hint.current_dbm)} for the one you were on). Reconnecting to Wi-Fi may move you to it.`) : null,
    spot && spot.position && spot.position.quality !== "insufficient"
      ? h("p", { class: "small muted" }, `Located by phone to ±${Math.round(spot.position.acc)} m. The phone page shows the direction to it.`) : null,
    h("div", { class: "row-between" },
      chosen ? badge(["good", "You chose this spot; see the Now tab", "✓"]) : h("span"),
      chosen ? h("button", { type: "button", onclick: () => choose(null) }, "Stop comparing")
        : h("button", { class: "primary", type: "button", onclick: () => choose(rec.spot_id) }, "I'm here now: monitor this spot")),
  ));
}

async function choose(id) {
  try {
    await api("/api/survey/choose", "POST", { spot_id: id });
    await refresh();
    if (id) setTab("now");
  } catch (e) { alert(e.message); }
}

// ---------------------------------------------------------------- survey map

function hatchDefs() {
  return s("defs", {}, s("pattern", { id: "hatch", width: 7, height: 7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    s("line", { x1: 0, y1: 0, x2: 0, y2: 7, style: "stroke:var(--grid);stroke-width:1" })));
}

function idwImage(samples, rect, radius) {
  const cell = 4, gw = Math.ceil(rect.w / cell), gh = Math.ceil(rect.h / cell);
  const cv = document.createElement("canvas");
  cv.width = gw; cv.height = gh;
  const ctx = cv.getContext("2d"), img = ctx.createImageData(gw, gh);
  for (let j = 0; j < gh; j++) {
    for (let i = 0; i < gw; i++) {
      const x = rect.x + (i + 0.5) * cell, y = rect.y + (j + 0.5) * cell;
      let num = 0, den = 0, dmin = Infinity;
      for (const p of samples) {
        const d2 = (x - p.x) ** 2 + (y - p.y) ** 2;
        dmin = Math.min(dmin, d2);
        const w = 1 / Math.max(d2, 1);
        num += w * p.v; den += w;
      }
      dmin = Math.sqrt(dmin);
      if (dmin > radius) continue;   // too far from any measurement: stays "unknown"
      const alpha = dmin < radius / 2 ? 1 : 1 - (dmin - radius / 2) / (radius / 2);
      const [r, g, b] = rampRGB(signalT(num / den));
      const k = 4 * (j * gw + i);
      img.data[k] = r; img.data[k + 1] = g; img.data[k + 2] = b; img.data[k + 3] = Math.round(120 * alpha);
    }
  }
  ctx.putImageData(img, 0, 0);
  return s("image", { href: cv.toDataURL(), x: rect.x, y: rect.y, width: rect.w, height: rect.h, preserveAspectRatio: "none" });
}

function medianNN(pts) {
  if (pts.length < 2) return null;
  return median(pts.map((p, i) => Math.min(...pts.filter((_, j) => j !== i).map((q) => Math.hypot(p.x - q.x, p.y - q.y)))));
}

function spotTip(e, sp, idx) {
  const m = sp.metrics;
  tip.show(e, [h("div", { class: "t-head" }, `#${idx + 1} ${sp.label} · measured ${fmt.age(sp.age)}`),
    tipRow(m.signal_dbm != null ? `${fmt.dbm(m.signal_dbm)} ±${(m.signal_sd || 0).toFixed(1)}` : "—", "signal"),
    tipRow(fmt.ms(m.rtt_ms), `latency (${sp.latency_target === "gateway" ? "router" : "internet"})`),
    tipRow(fmt.pct(m.loss), "loss"), tipRow(fmt.mbps(m.bitrate_mbps), "link rate"),
    tipRow(fmt.score(sp.score), "score"),
    sp.position ? tipRow(`±${Math.round(sp.position.acc)} m`, "phone position") : null]);
}

function spotNode(sp, idx, x, y, sv) {
  const sig = sp.metrics.signal_dbm;
  const rgb = sig != null ? rampRGB(signalT(sig)) : null;
  const fill = rgb ? `rgb(${rgb.join(",")})` : css("--muted");
  const rec = sv.recommendation.status === "ok" && sv.recommendation.spot_id === sp.id;
  const opacity = { fresh: 1, aging: 0.8, stale: 0.45 }[sp.freshness];
  const g = s("g", { class: "node", transform: `translate(${x},${y})`, opacity, tabindex: 0, role: "img",
    "aria-label": `${sp.label}: ${fmt.dbm(sig)}, score ${fmt.score(sp.score)}` });
  g.append(s("circle", { r: 22, fill: "transparent" }));   // generous hit target
  if (rec) g.append(s("circle", { r: 19, fill: "none", style: "stroke:var(--text);stroke-width:2" }));
  g.append(s("circle", { r: 14, fill, style: "stroke:var(--surface);stroke-width:2" }));
  g.append(s("text", { y: 4, "text-anchor": "middle", style: `fill:${rgb ? inkOn(rgb) : "#fff"};font-size:11px;font-weight:700` }, idx + 1));
  g.append(s("text", { class: "map-label", y: 34, "text-anchor": "middle" }, `${rec ? "★ " : ""}${sp.label}`));
  g.append(s("text", { class: "map-sub", y: 48, "text-anchor": "middle" },
    `${fmt.dbm(sig)} · ${fmt.score(sp.score)}${sp.freshness === "stale" ? " · stale" : ""}${sv.chosen_id === sp.id ? " · you're here" : ""}`));
  g.addEventListener("pointermove", (e) => { if (!state.dragging) spotTip(e, sp, idx); });
  g.addEventListener("pointerleave", () => tip.hide());
  return g;
}

function renderMap(sv) {
  const holder = $("map"), lay = sv.map, geo = lay.geo;
  const geoOk = !!geo;
  if (state.mapView === "geo" && !geoOk) state.mapView = null;
  const view = state.mapView || (geoOk && geo.resolvable ? "geo" : "graph");
  for (const b of document.querySelectorAll("#map-views button")) {
    b.setAttribute("aria-pressed", String(b.dataset.view === view));
    if (b.dataset.view === "geo") {
      b.disabled = !geoOk;
      b.title = geoOk ? "" : "Needs phone positions (±20 m or better) at 2 or more spots";
    }
  }
  const interpOk = view === "geo" ? geoOk && geo.interpolation : lay.canvas.interpolation;
  $("interp").disabled = !interpOk;
  $("interp-label").textContent = interpOk ? "Show interpolated signal (an estimate between measured spots)"
    : view === "geo" ? "Interpolated signal: needs 4+ phone-placed spots spaced well beyond their uncertainty"
      : "Interpolated signal: place 4 or more spots on the canvas first";
  if (state.dragging) return;
  const width = holder.clientWidth;
  const sig = [view, state.interp, width, isDark(), lay, sv.recommendation.spot_id, sv.chosen_id,
    sv.spots.map((x) => [x.id, x.label, x.canvas, x.freshness, x.metrics.signal_dbm, x.score])];
  if (!changed("map", sig)) return;
  if (!sv.spots.length) {
    holder.replaceChildren(h("p", { class: "muted small" }, "Measured spots will appear here."));
    $("map-legend").replaceChildren();
    $("map-note").textContent = "";
    return;
  }
  if (view === "geo") drawGeo(holder, sv, interpOk && state.interp);
  else drawGraph(holder, sv, interpOk && state.interp);

  const r = ramp();
  fill($("map-legend"),
    h("span", { class: "key" }, h("span", { class: "ramp", style: `background:linear-gradient(90deg,${r.join(",")})` }),
      `median signal, ${minus(SIG_LO)} to ${minus(SIG_HI)} dBm`),
    h("span", { class: "key" }, h("span", { class: "hatch" }), "not measured (unknown)"),
    view === "geo" ? h("span", { class: "key" }, h("span", { class: "ring" }), "phone position uncertainty") : null,
    h("span", { class: "key" }, "★ ringed: recommended"),
    h("span", { class: "key" }, "faded: measured over 5 min ago"));
  const notes = [];
  if (view === "geo") {
    if (geo.note) notes.push(geo.note);
    if (lay.unplaced_geo.length) {
      notes.push(`Not on this map: ${lay.unplaced_geo.map((u) => `${u.label} (${u.acc != null ? `phone ±${Math.round(u.acc)} m` : "no phone fix"})`).join(", ")}.`);
    }
  } else {
    notes.push("Positions here are your rough placement, not measurements. Lines show the order you visited spots.");
  }
  $("map-note").textContent = notes.join(" ");
}

function drawGraph(holder, sv, interp) {
  const W = holder.clientWidth, trayH = 86, H = Math.round(Math.min(520, Math.max(320, W * 0.55)));
  const area = { x: 1, y: 1, w: W - 2, h: H - trayH - 12 }, trayY = H - trayH;
  const unplaced = sv.spots.filter((x) => !x.canvas);
  const step = Math.min(110, (W - 80) / Math.max(1, unplaced.length));
  const pos = new Map();
  let k = 0;
  for (const sp of sv.spots) {
    pos.set(sp.id, sp.canvas ? { x: area.x + sp.canvas.x * area.w, y: area.y + sp.canvas.y * area.h }
      : { x: 60 + step * k++, y: trayY + 26 });
  }
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Spot graph" }, hatchDefs());
  svg.append(s("rect", { x: area.x, y: area.y, width: area.w, height: area.h, rx: 8, fill: "url(#hatch)", style: "stroke:var(--grid)" }));
  const placed = sv.spots.filter((x) => x.canvas && x.metrics.signal_dbm != null).map((x) => ({ ...pos.get(x.id), v: x.metrics.signal_dbm }));
  if (interp && placed.length >= 4) svg.append(idwImage(placed, area, medianNN(placed) * 0.75));
  svg.append(s("text", { class: "map-zone", x: area.x + 10, y: area.y + 18 },
    sv.spots.some((x) => x.canvas) ? "Your space (approximate, as you placed it)" : "Your space: drag spots here to place them roughly (optional)"));
  svg.append(s("text", { class: "map-zone", x: 10, y: trayY - 2 }, unplaced.length ? "Not placed, in visit order" : ""));

  const edges = [];
  for (let i = 1; i < sv.spots.length; i++) {
    const a = sv.spots[i - 1].id, b = sv.spots[i].id, pa = pos.get(a), pb = pos.get(b);
    const line = s("line", { x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y, style: "stroke:var(--axis);stroke-width:1.5" });
    edges.push({ a, b, line });
    svg.append(line);
  }
  sv.spots.forEach((sp, i) => {
    const p = pos.get(sp.id), g = spotNode(sp, i, p.x, p.y, sv);
    makeDraggable(g, sp, p, svg, area, trayY, edges);
    svg.append(g);
  });
  holder.replaceChildren(svg);
}

function makeDraggable(g, sp, p, svg, area, trayY, edges) {
  let drag = null;
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    g.setPointerCapture(e.pointerId);
    drag = { sx: e.clientX, sy: e.clientY, moved: false };
    state.dragging = true;
    g.classList.add("dragging");
    tip.hide();
  });
  g.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const k = svg.viewBox.baseVal.width / svg.getBoundingClientRect().width;
    const x = Math.max(16, Math.min(area.x + area.w - 16, p.x + (e.clientX - drag.sx) * k));
    const y = Math.max(16, Math.min(trayY + 26, p.y + (e.clientY - drag.sy) * k));
    drag.moved = drag.moved || Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 3;
    drag.x = x; drag.y = y;
    g.setAttribute("transform", `translate(${x},${y})`);
    for (const ed of edges) {
      if (ed.a === sp.id) { ed.line.setAttribute("x1", x); ed.line.setAttribute("y1", y); }
      if (ed.b === sp.id) { ed.line.setAttribute("x2", x); ed.line.setAttribute("y2", y); }
    }
  });
  const end = async () => {
    if (!drag) return;
    const d = drag;
    drag = null;
    g.classList.remove("dragging");
    if (d.moved) {
      const inArea = d.y <= area.y + area.h;
      const canvas = inArea ? { x: (d.x - area.x) / area.w, y: (d.y - area.y) / area.h } : null;
      try { await api(`/api/survey/spots/${sp.id}`, "PATCH", { canvas }); } catch (err) { alert(err.message); }
    }
    state.dragging = false;
    delete sigs.map;
    await refresh();
  };
  g.addEventListener("pointerup", end);
  g.addEventListener("pointercancel", end);
}

function drawGeo(holder, sv, interp) {
  const geo = sv.map.geo, W = holder.clientWidth, H = Math.round(Math.min(520, Math.max(320, W * 0.6))), pad = 44;
  const byId = new Map(sv.spots.map((x, i) => [x.id, [x, i]]));
  const ext = geo.points.flatMap((p) => [[p.x - p.acc, p.y - p.acc], [p.x + p.acc, p.y + p.acc]]);
  if (geo.phone) ext.push([geo.phone.x - geo.phone.acc, geo.phone.y - geo.phone.acc], [geo.phone.x + geo.phone.acc, geo.phone.y + geo.phone.acc]);
  const xs = ext.map((e) => e[0]), ys = ext.map((e) => e[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const k = Math.min((W - 2 * pad) / Math.max(x1 - x0, 1), (H - 2 * pad) / Math.max(y1 - y0, 1));
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const X = (x) => W / 2 + (x - cx) * k, Y = (y) => H / 2 - (y - cy) * k;   // north up

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Geographic survey map" }, hatchDefs());
  svg.append(s("rect", { x: 1, y: 1, width: W - 2, height: H - 2, rx: 8, fill: "url(#hatch)", style: "stroke:var(--grid)" }));
  const pts = geo.points.filter((p) => byId.get(p.id)[0].metrics.signal_dbm != null)
    .map((p) => ({ x: X(p.x), y: Y(p.y), v: byId.get(p.id)[0].metrics.signal_dbm }));
  if (interp && pts.length >= 4) svg.append(idwImage(pts, { x: 1, y: 1, w: W - 2, h: H - 2 }, geo.radius_m * k));
  const gstep = niceStep(Math.max(x1 - x0, y1 - y0) / 5);
  for (let gx = Math.ceil((cx - W / 2 / k) / gstep) * gstep; gx < cx + W / 2 / k; gx += gstep) {
    svg.append(s("line", { class: "grid-line", x1: X(gx), x2: X(gx), y1: 0, y2: H, "stroke-opacity": 0.7 }));
  }
  for (let gy = Math.ceil((cy - H / 2 / k) / gstep) * gstep; gy < cy + H / 2 / k; gy += gstep) {
    svg.append(s("line", { class: "grid-line", x1: 0, x2: W, y1: Y(gy), y2: Y(gy), "stroke-opacity": 0.7 }));
  }
  for (const p of geo.points) {
    svg.append(s("circle", { cx: X(p.x), cy: Y(p.y), r: p.acc * k, style: "fill:var(--wash);stroke:var(--muted);stroke-width:1" }));
  }
  if (geo.phone) {
    const px = X(geo.phone.x), py = Y(geo.phone.y);
    svg.append(s("circle", { cx: px, cy: py, r: geo.phone.acc * k, style: "fill:none;stroke:var(--text-2);stroke-width:1" }));
    svg.append(s("circle", { cx: px, cy: py, r: 5, style: "fill:var(--text);stroke:var(--surface);stroke-width:2" }));
    svg.append(s("text", { class: "map-sub", x: px, y: py - 26, "text-anchor": "middle" }, `phone now ±${Math.round(geo.phone.acc)} m`));
  }
  for (const p of geo.points) {
    const [sp, i] = byId.get(p.id);
    svg.append(spotNode(sp, i, X(p.x), Y(p.y), sv));
  }
  const bar = niceStep((W / 5) / k);
  svg.append(s("line", { x1: 14, x2: 14 + bar * k, y1: H - 16, y2: H - 16, style: "stroke:var(--text-2);stroke-width:2" }));
  svg.append(s("text", { class: "map-sub", x: 14, y: H - 22 }, `${bar} m`));
  svg.append(s("text", { class: "map-label", x: W - 18, y: 24, "text-anchor": "middle" }, "N"));
  svg.append(s("path", { d: `M${W - 18},30 l-5,12 l5,-3 l5,3 z`, style: "fill:var(--text-2)" }));
  holder.replaceChildren(svg);
}

// ---------------------------------------------------------------- spots table

function renderSpotsTable(sv) {
  if (state.editing) return;
  const rec = sv.recommendation.status === "ok" ? sv.recommendation.spot_id : null;
  const sig = [sv.spots.map((x) => [x.id, x.label, x.canvas, x.position, x.freshness, Math.floor(x.age / 30)]), rec, sv.chosen_id, !!sv.job];
  if (!changed("spots", sig)) return;
  const posText = (sp) => {
    if (sp.position) {
      const q = sp.position.quality;
      return [`phone ±${Math.round(sp.position.acc)} m`, q === "insufficient" ? h("span", { class: "sub" }, "too coarse to place") : null];
    }
    return sp.canvas ? "placed by you" : "—";
  };
  $("spots-table").replaceChildren(
    h("thead", {}, h("tr", {}, ["#", "Spot", "Measured", "Signal", "Latency", "Loss", "Link rate", "Access point", "Position", "Score", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, sv.spots.map((sp, i) => {
      const m = sp.metrics;
      const nameCell = h("td", {}, sp.label, sp.id === rec ? h("span", { class: "sub" }, "★ recommended") : null,
        sv.chosen_id === sp.id ? h("span", { class: "sub" }, "you're here") : null);
      return h("tr", { class: sp.id === rec ? "me" : null },
        h("td", {}, i + 1),
        nameCell,
        h("td", {}, fmt.age(sp.age), sp.freshness !== "fresh" ? h("span", { class: "sub" }, sp.freshness) : null,
          sp.moved ? h("span", { class: "sub" }, "phone moved during window") : null),
        h("td", {}, fmt.dbm(m.signal_dbm), m.signal_sd != null ? h("span", { class: "sub" }, `±${m.signal_sd.toFixed(1)} dB, ${m.signal_n} samples`) : null),
        h("td", {}, fmt.ms(m.rtt_ms), m.rtt_p90_ms != null ? h("span", { class: "sub" }, `p90 ${fmt.ms(m.rtt_p90_ms)}${sp.latency_target !== "gateway" ? ", internet" : ""}`) : null),
        h("td", {}, fmt.pct(m.loss), h("span", { class: "sub" }, `${m.ping_n} probes`)),
        h("td", {}, fmt.mbps(m.bitrate_mbps)),
        h("td", {}, sp.ap ? `ch ${sp.ap.channel} · ${sp.ap.band} GHz` : sp.bssid || "not connected",
          sp.roamed ? h("span", { class: "sub" }, "roamed during window") : null,
          sp.hint ? h("span", { class: "sub" }, `stronger AP visible: ch ${sp.hint.channel}`) : null),
        h("td", {}, posText(sp)),
        h("td", {}, fmt.score(sp.score), sp.score != null ? h("span", { class: "sub" }, `${fmt.score(sp.score_range[0])}–${fmt.score(sp.score_range[1])}`) : null),
        h("td", { style: "white-space:nowrap" },
          h("button", { class: "icon", type: "button", title: "Rename", "aria-label": `Rename ${sp.label}`, onclick: () => startRename(nameCell, sp) }, "✎"), " ",
          h("button", { class: "icon", type: "button", title: "Delete", "aria-label": `Delete ${sp.label}`, disabled: !!sv.job, onclick: () => deleteSpot(sp) }, "✕")),
      );
    })));
}

function startRename(cell, sp) {
  state.editing = sp.id;
  const input = h("input", { value: sp.label, maxlength: 60, "aria-label": "Spot name" });
  const done = async (save) => {
    if (state.editing !== sp.id) return;
    state.editing = null;
    if (save && input.value.trim() && input.value.trim() !== sp.label) {
      try { await api(`/api/survey/spots/${sp.id}`, "PATCH", { label: input.value }); } catch (e) { alert(e.message); }
    }
    delete sigs.spots;
    await refresh();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); });
  input.addEventListener("blur", () => done(true));
  cell.replaceChildren(input);
  input.focus();
  input.select();
}

async function deleteSpot(sp) {
  if (!confirm(`Delete “${sp.label}” and its measurements?`)) return;
  try { await api(`/api/survey/spots/${sp.id}`, "DELETE"); } catch (e) { alert(e.message); }
  await refresh();
}

// ---------------------------------------------------------------- polling & wiring

async function refresh() {
  try {
    const [live, survey] = await Promise.all([api("/api/live"), api("/api/survey")]);
    state.live = live;
    state.survey = survey;
    $("offline").hidden = true;
  } catch (e) {
    $("offline").hidden = false;
    return;
  }
  render();
}

let apTimer = 0;
setInterval(() => {
  refresh();
  if (state.tab === "networks" && ++apTimer % 5 === 0) loadAps();
}, 1000);

for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));

$("measure-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/survey/measure", "POST", { label: $("spot-label").value });
    $("spot-label").value = "";
  } catch (err) { alert(err.message); }
  refresh();
});
$("cancel-btn").addEventListener("click", () => api("/api/survey/cancel", "POST").then(refresh));
$("new-survey").addEventListener("click", async () => {
  if (state.survey && state.survey.spots.length && !confirm("Start a new survey? The current one stays saved on disk.")) return;
  try { await api("/api/survey/new", "POST"); } catch (e) { alert(e.message); }
  refresh();
});
$("rescan").addEventListener("click", async () => {
  const b = $("rescan");
  b.disabled = true;
  b.textContent = "Scanning…";
  await api("/api/aps/rescan", "POST").catch(() => {});
  setTimeout(async () => { await loadAps(); b.disabled = false; b.textContent = "Rescan"; }, 4500);
});
for (const b of document.querySelectorAll("#map-views button")) {
  b.addEventListener("click", () => { state.mapView = b.dataset.view; render(); });
}
$("interp").addEventListener("change", (e) => { state.interp = e.target.checked; render(); });

$("phone-chip").addEventListener("click", async () => {
  const dlg = $("pair-dialog");
  dlg.showModal();
  $("qr").replaceChildren();
  $("pair-url").textContent = "Starting the phone server…";
  try {
    const r = await api("/api/phone/pair", "POST");
    $("qr").innerHTML = r.qr_svg;   // SVG generated by our own server from its own URL
    $("pair-url").textContent = r.url;
  } catch (e) {
    $("pair-url").textContent = `Could not start pairing: ${e.message}`;
  }
});
$("pair-close").addEventListener("click", () => $("pair-dialog").close());
$("unpair").addEventListener("click", async () => {
  await api("/api/phone/unpair", "POST").catch(() => {});
  $("pair-dialog").close();
  refresh();
});

let resizeTimer;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { for (const k of Object.keys(sigs)) delete sigs[k]; render(); }, 150);
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { for (const k of Object.keys(sigs)) delete sigs[k]; render(); });

refresh();
