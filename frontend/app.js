const $ = (id) => document.getElementById(id);

let ws = null;
let authenticated = false;
let reconnectTimer = null;
let manualEnabled = false;
let controlState = { x: 0, y: 0, z: 500, r: 0 };
let lastTelemetry = null;

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

function setLinkBadge(ok, text) {
  const el = $("linkBadge");
  el.textContent = text;
  el.className = `badge ${ok ? "ok" : "bad"}`;
}

function connectWebSocket() {
  clearTimeout(reconnectTimer);

  if (ws) {
    try {
      ws.close();
    } catch (_) {}
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    authenticated = false;
    setLinkBadge(false, "AUTHENTICATING");

    ws.send(
      JSON.stringify({
        type: "auth",
        token: token()
      })
    );
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.type === "auth_ok") {
      authenticated = true;
      setLinkBadge(true, "UI CONNECTED");
      return;
    }

    if (msg.type === "error") {
      addLocalLog("WARN", msg.message);
      return;
    }

    if (msg.type === "telemetry") {
      lastTelemetry = msg;
      renderTelemetry(msg);
      return;
    }

    if (msg.type === "emergency_action") {
      addLocalLog("WARN", `Emergency action: ${msg.action}`);
    }
  };

  ws.onclose = () => {
    authenticated = false;
    setLinkBadge(false, "UI DISCONNECTED");

    reconnectTimer = setTimeout(connectWebSocket, 1500);
  };

  ws.onerror = () => {
    ws.close();
  };
}

function send(obj) {
  if (
    !ws ||
    ws.readyState !== WebSocket.OPEN ||
    !authenticated
  ) {
    addLocalLog(
      "WARN",
      "Control message not sent: UI WebSocket is not authenticated"
    );
    return;
  }

  ws.send(JSON.stringify(obj));
}

function fmt(v, suffix = "") {
  return v === null || v === undefined
    ? "--"
    : `${v}${suffix}`;
}

function renderTelemetry(msg) {
  const t = msg.telemetry || {};

  setLinkBadge(
    Boolean(msg.mavlink_connected),
    msg.mavlink_connected
      ? "MAVLINK ONLINE"
      : "MAVLINK OFFLINE"
  );

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
  const host = $("analyticsCards");

  const crops = [
    "gabi",
    "saging",
    "palay"
  ];

  host.innerHTML = crops
    .map((crop) => {
      const d = data[crop] || {};

      const rows = [
        ["Healthy", d.healthy],
        ["Wilting", d.wilting],
        ["Disease", d.disease],
        ["Pest", d.pest]
      ]
        .map(([label, value]) => {
          const pct =
            typeof value === "number"
              ? Math.max(
                  0,
                  Math.min(100, value)
                )
              : 0;

          const text =
            typeof value === "number"
              ? `${value.toFixed(1)}%`
              : "--";

          return `
            <div class="metric">
              <span>${label}</span>

              <div class="bar">
                <i style="width:${pct}%"></i>
              </div>

              <b>${text}</b>
            </div>
          `;
        })
        .join("");

      return `
        <article class="crop-card">
          <h3>${crop}</h3>
          ${rows}
        </article>
      `;
    })
    .join("");
}

function renderLogs(logs) {
  const host = $("systemLog");

  host.innerHTML = logs
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

  host.scrollTop = host.scrollHeight;
}

function addLocalLog(level, message) {
  const host = $("systemLog");

  const el = document.createElement("div");

  el.className = `log-line ${level}`;

  el.textContent =
    `[local] ${level}: ${message}`;

  host.appendChild(el);

  host.scrollTop = host.scrollHeight;
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

$("manualToggle").addEventListener(
  "change",
  (e) => {
    manualEnabled =
      e.target.checked;

    send({
      type: "take_control",
      enabled: manualEnabled
    });

    if (!manualEnabled) {
      controlState = {
        x: 0,
        y: 0,
        z: 500,
        r: 0
      };
    }
  }
);

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

      send({
        type: "command",
        command: command
      });
    }
  );
}

$("setModeBtn").addEventListener(
  "click",
  () => {
    send({
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
      18,
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

resizeMap();

connectWebSocket();