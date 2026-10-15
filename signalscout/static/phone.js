"use strict";

const token = location.pathname.split("/").pop();
const $ = (id) => document.getElementById(id);
const USABLE_ACC = 20;   // must match analysis.USABLE_ACC_M on the laptop

let fix = null;
let moving = false;
let sharing = false;
let wakeLock = null;
const motion = [];

// Movement from the accelerometer: the spread of |a| over the last 1.5 s.
function onMotion(e) {
  const a = e.accelerationIncludingGravity;
  if (!a || a.x == null) return;
  const now = performance.now();
  motion.push([now, Math.hypot(a.x, a.y, a.z)]);
  while (motion.length && now - motion[0][0] > 1500) motion.shift();
  if (motion.length >= 5) {
    const mean = motion.reduce((s, m) => s + m[1], 0) / motion.length;
    const sd = Math.sqrt(motion.reduce((s, m) => s + (m[1] - mean) ** 2, 0) / motion.length);
    moving = sd > 0.6;
  }
}

async function keepAwake() {
  try { wakeLock = await navigator.wakeLock.request("screen"); } catch { /* not supported; the screen may sleep */ }
}

async function startSharing() {
  if (!("geolocation" in navigator)) {
    $("acc").textContent = "Not available in this browser";
    return;
  }
  if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
    try { await DeviceMotionEvent.requestPermission(); } catch { /* movement detection just stays off */ }
  }
  addEventListener("devicemotion", onMotion);
  navigator.geolocation.watchPosition(
    (p) => {
      fix = { lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy, ts: p.timestamp };
      renderFix();
    },
    (err) => {
      $("acc").textContent = err.code === 1 ? "Permission denied" : "No fix yet";
      $("acc-note").textContent = err.code === 1
        ? "Allow location for this page in the browser's site settings, then reload."
        : "Still searching. Indoors this can take a while or never become precise.";
    },
    { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 },
  );
  sharing = true;
  $("start").hidden = true;
  keepAwake();
}

function renderFix() {
  if (!fix) return;
  const acc = Math.round(fix.acc);
  $("acc").textContent = `±${acc} m`;
  $("acc").className = fix.acc <= USABLE_ACC ? "ok" : "bad";
  $("acc-note").textContent = fix.acc <= 10 ? "Good: precise enough to tell nearby spots apart."
    : fix.acc <= USABLE_ACC ? "Coarse: fine for spots several metres apart."
      : "Too coarse to place spots (typical indoors). Measurements still work; spots just won't be placed on the map.";
}

function bearingText(deg) {
  return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][Math.round(deg / 45) % 8];
}

function navigation(target) {
  if (!target) return "The laptop has no location for this spot; find it by its name.";
  if (!fix) return "Share location to get directions.";
  if (target.acc > USABLE_ACC || fix.acc > USABLE_ACC) {
    return `Location is too imprecise (±${Math.round(Math.max(target.acc, fix.acc))} m) to guide you; find the spot by its name.`;
  }
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (target.lat - fix.lat) * rad, dLon = (target.lon - fix.lon) * rad;
  const x = dLon * Math.cos(((target.lat + fix.lat) / 2) * rad), dist = Math.hypot(x, dLat) * R;
  const unc = target.acc + fix.acc;
  if (dist <= unc) return `You are within the position uncertainty (±${Math.round(unc)} m) of this spot. Use its name to find the exact place.`;
  const brg = (Math.atan2(x, dLat) / rad + 360) % 360;
  return `About ${Math.round(dist)} m to the ${bearingText(brg)} (±${Math.round(unc)} m).`;
}

function render(st) {
  $("link").textContent = "Connected";
  $("link").className = "ok";
  const job = st.job;
  $("measure").disabled = !!job;
  $("survey").textContent = job
    ? (job.phase === "measuring" ? `Measuring “${job.label}”: keep still, ${Math.ceil(job.remaining)} s left.` : "Scanning nearby access points: keep still a few seconds more.")
    : st.spots === 0 ? "Ready. Start with where you are now." : `${st.spots} spot${st.spots > 1 ? "s" : ""} measured. Move to the next candidate.`;
  const rec = st.recommendation;
  $("rec-card").hidden = !rec;
  if (rec) {
    $("rec").textContent = rec.decision === "move" ? `Move to ${rec.label}` : `Stay at ${rec.label}`;
    $("nav").textContent = `${rec.confidence[0].toUpperCase()}${rec.confidence.slice(1)} confidence. ${navigation(rec.position)}`;
  }
}

async function post(path, body) {
  const res = await fetch(`/api/p/${token}/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let refreshing = false;
function refreshFix() {
  if (refreshing) return;
  refreshing = true;
  navigator.geolocation.getCurrentPosition(
    (p) => { fix = { lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy, ts: p.timestamp }; refreshing = false; renderFix(); },
    () => { refreshing = false; },
    { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 },
  );
}

async function report() {
  if (sharing && fix && Date.now() - fix.ts > 10000) refreshFix();
  const body = { moving };
  if (sharing && fix) body.fix = { ...fix, age_ms: Date.now() - fix.ts };
  try {
    render(await post("report", body));
  } catch (e) {
    $("link").textContent = e.message.includes("no longer valid") ? "Link expired: scan the QR code again" : "Can't reach the laptop";
    $("link").className = "bad";
  }
}

$("start").addEventListener("click", startSharing);
$("measure").addEventListener("click", async () => {
  $("measure").disabled = true;
  try { render(await post("measure", {})); } catch (e) { alert(e.message); }
});
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && sharing) keepAwake(); });
setInterval(report, 1000);
report();
