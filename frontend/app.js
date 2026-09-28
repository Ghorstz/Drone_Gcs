const $ = (id) => document.getElementById(id);

let ws = null;
let authenticated = false;
let reconnectTimer = null;
let manualEnabled = false;
let controlState = { x: 0, y: 0, z: 500, r: 0 };
let lastTelemetry = null;
let mavlinkConnected = false;
let controlOccupied = false;
let leasePending = false;
let leaseTimer = null;
let reconnectAttempt = 0;
let lastMessageAt = 0;
let commandTimeoutMs = 25000;
let pendingCommand = null;
let commandSequence = 0;
const stickResets = [];
const localLogs = [];

const track = [];
const mapState = {
  zoom: 1,
  panX: 0,
  panY: 0,
  dragging: false,
  lastX: 0,
  lastY: 0
};

function token() {
  return localStorage.getItem("purayControlToken") || $("tokenInput").value.trim();
}

function setBadge(id, ok, text) {
  const el = $(id);
  el.textContent = text;
  el.className = `badge ${ok ? "ok" : "bad"}`;
  const online = authenticated && mavlinkConnected;
  $("connectionSummary").classList.toggle("online", online);
  $("connectionText").textContent = online ? "Connected" :
    ws?.readyState === WebSocket.OPEN ? "View only" : "Offline";
  if (!mavlinkConnected) {
    $("headerGps").textContent = "--";
    $("headerBattery").textContent = "--";
  }
}

function updateControls() {
  const ready = authenticated && mavlinkConnected && ws?.readyState === WebSocket.OPEN;
  const blocked = controlOccupied && !manualEnabled;
  $("manualToggle").disabled = !ready || blocked || leasePending ||
    (Boolean(pendingCommand) && !manualEnabled);
  $("manualModeBtn").disabled = $("manualToggle").disabled;
  $("manualModeBtn").classList.toggle("active", manualEnabled || leasePending);
  $("manualModeBtn").setAttribute("aria-pressed", String(manualEnabled || leasePending));
  $("autoModeBtn").classList.toggle("active", !manualEnabled && !leasePending);
  $("autoModeBtn").setAttribute("aria-pressed", String(!manualEnabled && !leasePending));
  for (const button of document.querySelectorAll("[data-command], [data-mode-command], #setModeBtn")) {
    button.disabled = !ready || blocked ||
      (Boolean(pendingCommand) && button.dataset.command !== "EMERGENCY_STOP");
  }
}

function resetManual() {
  manualEnabled = false;
  $("manualToggle").checked = false;
  controlState = { x: 0, y: 0, z: 500, r: 0 };
  for (const reset of stickResets) reset();
}

function renderControl(control) {
  controlOccupied = Boolean(control.occupied);
  const owned = authenticated && mavlinkConnected && !document.hidden &&
    !leasePending && Boolean(control.enabled);
  if (!owned) resetManual();
  manualEnabled = owned;
  $("manualToggle").checked = owned;
  setBadge("controlBadge", owned, leasePending ? "CONTROL: REQUEST PENDING" : owned ? "CONTROL: YOU" :
    controlOccupied ? "CONTROL: OTHER SESSION" : "CONTROL: AVAILABLE");
  updateControls();
}

function commandFeedback(status, message, label = pendingCommand?.label || "Command") {
  const el = $("commandStatus");
  el.dataset.status = status;
  el.textContent = `${label}: ${status.toUpperCase()} - ${message}`;
}

function finishCommand(status, message) {
  if (!pendingCommand) return;
  commandFeedback(status, message);
  clearTimeout(pendingCommand.timer);
  pendingCommand = null;
  updateControls();
}

function resetConnection() {
  authenticated = false;
  mavlinkConnected = false;
  controlOccupied = false;
  leasePending = false;
  clearTimeout(leaseTimer);
  lastTelemetry = null;
  resetManual();
  finishCommand("timeout", "Connection ended; outcome unknown. Command was not retried.");
  setBadge("authBadge", false, "AUTH: NOT AUTHENTICATED");
  setBadge("linkBadge", false, "MAVLINK: UNKNOWN");
  setBadge("controlBadge", false, "CONTROL: UNKNOWN");
  updateControls();
}

function connectWebSocket() {
  clearTimeout(reconnectTimer);
  const oldSocket = ws;
  ws = null; // Invalidate the old callbacks before closing that socket.
  if (oldSocket) oldSocket.close();
  resetConnection();
  setBadge("backendBadge", false, "BACKEND: CONNECTING");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${proto}//${location.host}/ws`);
  ws = socket;
  lastMessageAt = Date.now();

  socket.onopen = () => {
    if (ws !== socket) return;
    setBadge("backendBadge", true, "BACKEND: CONNECTED");
    setBadge("authBadge", false, "AUTH: AUTHENTICATING");
    socket.send(JSON.stringify({ type: "auth", token: token() }));
  };

  socket.onmessage = (ev) => {
    if (ws !== socket) return;
    lastMessageAt = Date.now();
    reconnectAttempt = 0;
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg.type === "auth_ok") {
      authenticated = true;
      commandTimeoutMs = (msg.command_timeout_s || 25) * 1000;
      setBadge("authBadge", true, "AUTH: AUTHENTICATED");
      updateControls();
    } else if (msg.type === "auth_failed") {
      authenticated = false;
      resetManual();
      setBadge("authBadge", false, "AUTH: REJECTED");
      updateControls();
      addLocalLog("WARN", msg.message);
    } else if (msg.type === "control_lease") {
      leasePending = false;
      clearTimeout(leaseTimer);
      renderControl(msg);
    } else if (msg.type === "command_status") {
      if (!pendingCommand || msg.request_id !== pendingCommand.id) return;
      if (msg.status === "pending") commandFeedback(msg.status, msg.message);
      else finishCommand(msg.status, msg.message);
    } else if (msg.type === "error") {
      addLocalLog("WARN", msg.message);
    } else if (msg.type === "telemetry") {
      lastTelemetry = msg;
      renderTelemetry(msg);
      renderControl(msg.control || {});
    } else if (msg.type === "emergency_action") {
      addLocalLog("WARN", `Emergency action: ${msg.action}`);
    }
  };

  socket.onclose = () => {
    if (ws !== socket) return;
    ws = null;
    resetConnection();
    setBadge("backendBadge", false, "BACKEND: DISCONNECTED");
    const delay = Math.min(10000, 1000 * 2 ** reconnectAttempt++);
    reconnectTimer = setTimeout(() => {
      if (ws === null) connectWebSocket();
    }, delay);
  };
  socket.onerror = () => {
    if (ws === socket) socket.close();
  };
  updateControls();
}

// Detect half-open connections as well as explicit close events.
setInterval(() => {
  if (ws && Date.now() - lastMessageAt > 10000) ws.close();
}, 1000);

function send(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN || !authenticated) {
    addLocalLog("WARN", "Control message not sent: backend is not authenticated");
    return false;
  }
  try {
    ws.send(JSON.stringify(obj));
    return true;
  } catch (_) {
    ws.close();
    return false;
  }
}

function sendCommand(obj) {
  if (pendingCommand) {
    if (obj.command !== "EMERGENCY_STOP") return;
    finishCommand("timeout", "Superseded by emergency request; outcome unknown");
  }
  const id = `${Date.now()}-${++commandSequence}`;
  pendingCommand = { id, label: obj.command || `SET MODE ${obj.mode}`, timer: null };
  commandFeedback("pending", "Sending request");
  if (!send({ ...obj, request_id: id })) {
    finishCommand("rejected", "Request was not sent");
    return;
  }
  pendingCommand.timer = setTimeout(() => {
    if (pendingCommand?.id === id) finishCommand("timeout", "No result received; outcome unknown. Not retried.");
  }, commandTimeoutMs);
  updateControls();
}

function fmt(v, suffix = "") {
  return v === null || v === undefined
    ? "--"
    : `${v}${suffix}`;
}

function renderTelemetry(msg) {
  const t = msg.telemetry || {};

  mavlinkConnected = Boolean(msg.mavlink_connected);
  setBadge("linkBadge", mavlinkConnected,
    mavlinkConnected ? "MAVLINK: ONLINE" : "MAVLINK: OFFLINE / STALE");
  updateControls();

  $("headerGps").textContent = mavlinkConnected ? fmt(t.gps_sats) : "--";
  $("headerBattery").textContent = mavlinkConnected ? fmt(t.battery_pct, "%") : "--";
  $("altOverlay").textContent = fmt(t.relative_alt_m, " m");
  $("speedOverlay").textContent = fmt(t.groundspeed_mps, " m/s");
  $("batteryVal").textContent = fmt(t.battery_pct, "%");
  $("gpsVal").textContent = fmt(t.gps_sats);
  $("modeVal").textContent = fmt(t.mode);
  $("armedVal").textContent = t.armed ? "YES" : "NO";
  $("voltageVal").textContent = fmt(t.voltage_v, " V");
  $("headingVal").textContent = fmt(t.heading_deg, "°");

  $("modeOverlay").textContent = `MODE: ${fmt(t.mode)}`;
  $("gpsOverlay").textContent = `GPS: ${fmt(t.gps_sats)}`;
  $("batteryOverlay").textContent = `BAT: ${fmt(
    t.battery_pct,
    "%"
  )}`;

  $("latVal").textContent =
    t.lat == null ? "--" : t.lat.toFixed(6);

  $("lonVal").textContent =
    t.lon == null ? "--" : t.lon.toFixed(6);

  $("altVal").textContent = fmt(
    t.relative_alt_m,
    " m"
  );

  $("speedVal").textContent = fmt(
    t.groundspeed_mps,
    " m/s"
  );

  if (
    typeof t.lat === "number" &&
    typeof t.lon === "number"
  ) {
    const prev = track[track.length - 1];

    if (
      !prev ||
      Math.abs(prev.lat - t.lat) > 1e-7 ||
      Math.abs(prev.lon - t.lon) > 1e-7
    ) {
      track.push({
        lat: t.lat,
        lon: t.lon
      });

      if (track.length > 2000) {
        track.shift();
      }

      drawMap();
    }
  }

  renderAnalytics(msg.crop_health || {});
  renderLogs(msg.logs || []);
  renderSync(msg.sync || {});
}

function renderSync(sync) {
  const panel = $("syncPanel");

  if (sync.show_prompt) {
    panel.classList.remove("hidden");

    $("syncText").textContent =
      `Connected to ${sync.ssid}. ` +
      `Unsynced local flight data is ready.`;

    $("syncBtn").disabled = false;
  } else {
    panel.classList.add("hidden");
  }
}

function renderAnalytics(data) {
  const crop = $("cropSelect").value;
  const values = data[crop] || {};
  const metrics = [
    ["Healthy", "healthy", "#1bcd76"],
    ["Wilting", "wilting", "#f6ce35"],
    ["Disease", "disease", "#ff8b35"],
    ["Pest", "pest", "#ff454b"]
  ];
  const valid = (value) => Number.isFinite(value) && value >= 0 && value <= 100;
  const healthy = values.healthy;
  const allValid = metrics.every(([, key]) => valid(values[key]));
  const total = metrics.reduce((sum, [, key]) => sum + (valid(values[key]) ? values[key] : 0), 0);
  // Only draw a distribution when all four percentages form a complete whole.
  let background = "#20313c";
  if (allValid && Math.abs(total - 100) < 0.5) {
    let offset = 0;
    const segments = metrics.map(([, key, color]) => {
      const start = offset;
      offset += values[key] / total * 100;
      return `${color} ${start}% ${offset}%`;
    });
    background = `conic-gradient(${segments.join(",")})`;
  } else if (valid(healthy)) {
    background = `conic-gradient(#1bcd76 ${healthy}%, #20313c 0)`;
  }
  $("analyticsCards").innerHTML = `
    <div class="health-summary">
      <div class="health-ring" style="background:${background}">
        <div class="health-ring-center"><strong>${valid(healthy) ? `${healthy.toFixed(0)}%` : "--"}</strong><small>${valid(healthy) ? "Healthy" : "No data"}</small></div>
      </div>
      <div class="health-legend">${metrics.map(([label, key, color]) => `
        <div class="health-row"><i class="health-dot" style="background:${color}"></i><span>${label}</span><b>${valid(values[key]) ? `${values[key].toFixed(1)}%` : "--"}</b></div>
      `).join("")}</div>
    </div>
    <p class="health-note">${allValid ? "Latest reported crop-health percentages." : "Waiting for crop-health analysis. No results available yet."}</p>`;
}

function renderLogs(logs) {
  const host = $("systemLog");

  host.innerHTML = [...logs, ...localLogs]
    .sort((a, b) => (a.ts || "").localeCompare(b.ts || ""))
    .slice(-30)
    .map((item) => {
      const time =
        (item.ts || "")
          .split("T")[1]
          ?.slice(0, 8) || "--:--:--";

      return `
        <div class="log-line ${item.level || ""}">
          [${time}]
          ${escapeHtml(item.level || "INFO")}:
          ${escapeHtml(item.message || "")}
        </div>
      `;
    })
    .join("");

  if (!host.innerHTML.trim()) host.innerHTML = '<p class="empty-log">Waiting for system events.</p>';
  host.scrollTop = host.scrollHeight;
}

function addLocalLog(level, message) {
  localLogs.push({ ts: new Date().toISOString(), level, message: `[local] ${message}` });
  if (localLogs.length > 30) localLogs.shift();
  renderLogs(lastTelemetry?.logs || []);
}

function escapeHtml(s) {
  return String(s).replace(
    /[&<>'"]/g,
    (ch) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#039;",
        '"': "&quot;"
      })[ch]
  );
}

function createStick(
  element,
  onMove,
  isThrottleStick = false
) {
  const knob =
    element.querySelector(".knob");

  let activePointer = null;

  function update(clientX, clientY) {
    if (!manualEnabled) return;
    const r =
      element.getBoundingClientRect();

    const cx =
      r.left + r.width / 2;

    const cy =
      r.top + r.height / 2;

    let dx = clientX - cx;
    let dy = clientY - cy;

    const max =
      r.width * 0.36;

    const mag =
      Math.hypot(dx, dy);

    if (mag > max) {
      dx =
        (dx / mag) * max;

      dy =
        (dy / mag) * max;
    }

    knob.style.transform =
      `translate(` +
      `calc(-50% + ${dx}px), ` +
      `calc(-50% + ${dy}px)` +
      `)`;

    onMove(
      dx / max,
      dy / max,
      true
    );
  }

  element.addEventListener(
    "pointerdown",
    (e) => {
      if (!manualEnabled) return;
      activePointer =
        e.pointerId;

      element.setPointerCapture(
        e.pointerId
      );

      update(
        e.clientX,
        e.clientY
      );
    }
  );

  element.addEventListener(
    "pointermove",
    (e) => {
      if (
        e.pointerId ===
        activePointer
      ) {
        update(
          e.clientX,
          e.clientY
        );
      }
    }
  );

  function release(e) {
    if (
      activePointer === null ||
      (
        e.pointerId !== undefined &&
        e.pointerId !== activePointer
      )
    ) {
      return;
    }

    activePointer = null;

    knob.style.transform =
      "translate(-50%,-50%)";

    onMove(
      0,
      0,
      false
    );
  }

  stickResets.push(() => {
    const pointer = activePointer;
    activePointer = null;
    if (pointer !== null && element.hasPointerCapture(pointer)) {
      element.releasePointerCapture(pointer);
    }
    knob.style.transform = "translate(-50%,-50%)";
  });

  element.addEventListener(
    "pointerup",
    release
  );

  element.addEventListener(
    "pointercancel",
    release
  );

  element.addEventListener(
    "lostpointercapture",
    release
  );
}

createStick(
  $("leftStick"),
  (nx, ny, active) => {
    controlState.r =
      Math.round(nx * 1000);

    controlState.z =
      active
        ? Math.round(
            ((1 - ny) / 2) *
              1000
          )
        : 500;
  }
);

createStick(
  $("rightStick"),
  (nx, ny) => {
    controlState.y =
      Math.round(nx * 1000);

    controlState.x =
      Math.round(-ny * 1000);
  }
);

setInterval(() => {
  if (manualEnabled) {
    send({
      type: "manual",
      ...controlState
    });
  }
}, 100);

setInterval(() => {
  if (authenticated) {
    send({
      type: "operator_heartbeat"
    });
  }
}, 500);

function requestManual(enabled) {
  resetManual();
  leasePending = true;
  if (!send({ type: "take_control", enabled })) leasePending = false;
  clearTimeout(leaseTimer);
  if (leasePending) {
    leaseTimer = setTimeout(() => {
      // An unanswered ownership request is uncertain: reconnect with no lease.
      if (leasePending) connectWebSocket();
    }, 5000);
  }
  updateControls();
}

$("manualToggle").addEventListener("change", (e) => {
  requestManual(e.target.checked);
});

$("manualModeBtn").addEventListener("click", () => {
  if (!$("manualModeBtn").disabled) requestManual(true);
});

$("autoModeBtn").addEventListener("click", () => {
  if (manualEnabled || leasePending) requestManual(false);
});

for (const button of document.querySelectorAll("[data-stick-mode]")) {
  button.addEventListener("click", () => {
    document.body.dataset.stickMode = button.dataset.stickMode;
    for (const option of document.querySelectorAll("[data-stick-mode]")) {
      option.setAttribute("aria-pressed", String(option === button));
    }
  });
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden && (manualEnabled || leasePending)) requestManual(false);
});

for (
  const btn of
  document.querySelectorAll(
    "[data-command]"
  )
) {
  btn.addEventListener(
    "click",
    () => {
      const command =
        btn.dataset.command;

      if (
        command ===
        "EMERGENCY_STOP"
      ) {
        const ok = confirm(
          "Emergency action: airborne motor kill is blocked; the server will request LAND. Continue?"
        );

        if (!ok) return;
      }

      sendCommand({
        type: "command",
        command: command
      });
    }
  );
}

for (const button of document.querySelectorAll("[data-mode-command]")) {
  button.addEventListener("click", () => {
    sendCommand({ type: "set_mode", mode: button.dataset.modeCommand });
  });
}

$("setModeBtn").addEventListener(
  "click",
  () => {
    sendCommand({
      type: "set_mode",
      mode:
        $("modeSelect").value
    });
  }
);

$("saveTokenBtn").addEventListener(
  "click",
  () => {
    const value =
      $("tokenInput")
        .value
        .trim();

    if (value) {
      localStorage.setItem(
        "purayControlToken",
        value
      );
    }

    connectWebSocket();
  }
);

$("tokenInput").value =
  localStorage.getItem(
    "purayControlToken"
  ) || "";

$("syncBtn").addEventListener(
  "click",
  async () => {
    $("syncBtn").disabled = true;

    try {
      const res =
        await fetch(
          "/api/sync",
          {
            method: "POST",

            headers: {
              "X-Control-Token":
                token()
            }
          }
        );

      const data =
        await res.json();

      if (!res.ok) {
        throw new Error(
          data.detail ||
          "Sync failed"
        );
      }

      addLocalLog(
        "INFO",
        `Google Drive sync complete: ${data.archive}`
      );

      $("syncPanel")
        .classList
        .add("hidden");

    } catch (err) {
      addLocalLog(
        "WARN",
        err.message
      );

    } finally {
      $("syncBtn").disabled =
        false;
    }
  }
);


// =====================================
// OFFLINE GPS MAP
// =====================================

const canvas =
  $("mapCanvas");

const ctx =
  canvas.getContext("2d");

function resizeMap() {
  const dpr =
    window.devicePixelRatio || 1;

  const rect =
    canvas.getBoundingClientRect();

  canvas.width =
    Math.max(
      1,
      Math.floor(
        rect.width * dpr
      )
    );

  canvas.height =
    Math.max(
      1,
      Math.floor(
        rect.height * dpr
      )
    );

  ctx.setTransform(
    dpr,
    0,
    0,
    dpr,
    0,
    0
  );

  drawMap();
}

function project(
  p,
  origin
) {
  const lat0 =
    origin.lat *
    Math.PI /
    180;

  const metersPerDegLat =
    111320;

  const metersPerDegLon =
    111320 *
    Math.cos(lat0);

  return {
    x:
      (p.lon -
        origin.lon) *
      metersPerDegLon,

    y:
      -(p.lat -
        origin.lat) *
      metersPerDegLat
  };
}

function drawMap() {
  const rect =
    canvas.getBoundingClientRect();

  const w =
    rect.width;

  const h =
    rect.height;

  ctx.clearRect(
    0,
    0,
    w,
    h
  );

  ctx.fillStyle =
    "#071117";

  ctx.fillRect(
    0,
    0,
    w,
    h
  );

  ctx.strokeStyle =
    "#17303a";

  ctx.lineWidth = 1;

  for (
    let x = 0;
    x < w;
    x += 40
  ) {
    ctx.beginPath();

    ctx.moveTo(
      x,
      0
    );

    ctx.lineTo(
      x,
      h
    );

    ctx.stroke();
  }

  for (
    let y = 0;
    y < h;
    y += 40
  ) {
    ctx.beginPath();

    ctx.moveTo(
      0,
      y
    );

    ctx.lineTo(
      w,
      y
    );

    ctx.stroke();
  }

  if (!track.length) {
    ctx.fillStyle =
      "#8fa8b5";

    ctx.font =
      "14px system-ui";

    ctx.fillText(
      "Waiting for GPS position…",
      60,
      28
    );

    return;
  }

  const origin =
    track[0];

  const pts =
    track.map(
      (p) =>
        project(
          p,
          origin
        )
    );

  const scale =
    2.0 *
    mapState.zoom;

  const cx =
    w / 2 +
    mapState.panX;

  const cy =
    h / 2 +
    mapState.panY;

  ctx.strokeStyle =
    "#41a7ff";

  ctx.lineWidth = 2;

  ctx.beginPath();

  pts.forEach(
    (p, i) => {
      const x =
        cx +
        p.x * scale;

      const y =
        cy +
        p.y * scale;

      if (i === 0) {
        ctx.moveTo(
          x,
          y
        );
      } else {
        ctx.lineTo(
          x,
          y
        );
      }
    }
  );

  ctx.stroke();

  const last =
    pts[
      pts.length - 1
    ];

  ctx.fillStyle =
    "#35d07f";

  ctx.beginPath();

  ctx.arc(
    cx +
      last.x *
        scale,

    cy +
      last.y *
        scale,

    6,
    0,
    Math.PI * 2
  );

  ctx.fill();

  ctx.fillStyle =
    "#8fa8b5";

  ctx.font =
    "11px ui-monospace";

  ctx.fillText(
    `scale ${(1 / scale).toFixed(2)} m/px • wheel to zoom, drag to pan`,
    12,
    h - 12
  );
}

canvas.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();

    mapState.zoom *=
      e.deltaY < 0
        ? 1.15
        : 0.87;

    mapState.zoom =
      Math.max(
        0.1,
        Math.min(
          30,
          mapState.zoom
        )
      );

    drawMap();
  },
  {
    passive: false
  }
);

canvas.addEventListener(
  "pointerdown",
  (e) => {
    mapState.dragging =
      true;

    mapState.lastX =
      e.clientX;

    mapState.lastY =
      e.clientY;

    canvas.setPointerCapture(
      e.pointerId
    );
  }
);

canvas.addEventListener(
  "pointermove",
  (e) => {
    if (
      !mapState.dragging
    ) {
      return;
    }

    mapState.panX +=
      e.clientX -
      mapState.lastX;

    mapState.panY +=
      e.clientY -
      mapState.lastY;

    mapState.lastX =
      e.clientX;

    mapState.lastY =
      e.clientY;

    drawMap();
  }
);

canvas.addEventListener(
  "pointerup",
  () => {
    mapState.dragging =
      false;
  }
);

canvas.addEventListener(
  "pointercancel",
  () => {
    mapState.dragging =
      false;
  }
);

window.addEventListener(
  "resize",
  resizeMap
);

function showView(view) {
  if (!["home", "live", "map", "health", "controls", "logs", "settings"].includes(view)) view = "home";
  // Release the manual lease when navigating away from visible flight controls.
  if (manualEnabled || leasePending) requestManual(false);
  document.body.dataset.view = view;
  for (const button of document.querySelectorAll("[data-view-target]")) {
    const active = button.dataset.viewTarget === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  window.scrollTo({ top: 0, behavior: "instant" });
  requestAnimationFrame(resizeMap);
}
for (const button of document.querySelectorAll("[data-view-target]")) {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    showView(button.dataset.viewTarget);
    document.body.classList.remove("nav-open");
    $("mobileMenuBtn").setAttribute("aria-expanded", "false");
    $("mobileMenuBtn").setAttribute("aria-label", "Open navigation");
  });
}
$("mobileMenuBtn").addEventListener("click", () => {
  const open = document.body.classList.toggle("nav-open");
  $("mobileMenuBtn").setAttribute("aria-expanded", String(open));
  $("mobileMenuBtn").setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
});
document.addEventListener("click", (event) => {
  if (document.body.classList.contains("nav-open") && event.target === document.body) {
    document.body.classList.remove("nav-open");
    $("mobileMenuBtn").setAttribute("aria-expanded", "false");
    $("mobileMenuBtn").setAttribute("aria-label", "Open navigation");
  }
});
$("cropSelect").addEventListener("change", () => renderAnalytics(lastTelemetry?.crop_health || {}));
$("mapZoomIn").addEventListener("click", () => { mapState.zoom = Math.min(30, mapState.zoom * 1.3); drawMap(); });
$("mapZoomOut").addEventListener("click", () => { mapState.zoom = Math.max(0.1, mapState.zoom / 1.3); drawMap(); });
$("mapCenter").addEventListener("click", () => {
  const latest = track.length ? project(track[track.length - 1], track[0]) : { x: 0, y: 0 };
  mapState.panX = -latest.x * 2 * mapState.zoom;
  mapState.panY = -latest.y * 2 * mapState.zoom;
  drawMap();
});

const cameraFeed = $("cameraFeed");
function cameraAvailability(available) {
  $("cameraStage").classList.toggle("has-frame", available);
  $("captureBtn").disabled = !available;
  $("cameraStatus").innerHTML = `<i></i> ${available ? "LIVE" : "OFFLINE"}`;
}
cameraFeed.addEventListener("load", () => cameraAvailability(true));
cameraFeed.addEventListener("error", () => cameraAvailability(false));
// MJPEG streams may not fire load until the stream ends; check decoded frames.
setInterval(() => cameraAvailability(cameraFeed.naturalWidth > 0), 1000);
$("fullscreenBtn").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if ($("cameraStage").requestFullscreen) await $("cameraStage").requestFullscreen();
    else $("cameraNotice").textContent = "Fullscreen is unavailable in this browser.";
  } catch (_) { $("cameraNotice").textContent = "Unable to open fullscreen."; }
});
$("captureBtn").addEventListener("click", () => {
  if (!cameraFeed.naturalWidth) return;
  const photo = document.createElement("canvas");
  photo.width = cameraFeed.naturalWidth;
  photo.height = cameraFeed.naturalHeight;
  photo.getContext("2d").drawImage(cameraFeed, 0, 0);
  photo.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `puray-capture-${Date.now()}.jpg`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("cameraNotice").textContent = "Photo download requested.";
  }, "image/jpeg", 0.92);
});
function updateClock() {
  $("headerClock").textContent = new Date().toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
updateClock();
setInterval(updateClock, 30000);
renderAnalytics({});
if (typeof ResizeObserver !== "undefined") new ResizeObserver(resizeMap).observe(canvas);
resizeMap();
connectWebSocket();
