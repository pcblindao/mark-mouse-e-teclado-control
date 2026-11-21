"use strict";

const el = (id) => document.getElementById(id);
const canvas = el("screen");
const context = canvas.getContext("2d");
const named = new Set([
  "Alt", "AltGraph", "Backspace", "CapsLock", "Control", "Delete", "ArrowDown",
  "End", "Enter", "Escape", "Home", "Insert", "ArrowLeft", "Meta", "PageDown",
  "PageUp", "ArrowRight", "Shift", "Tab", "ArrowUp", " ",
  ...Array.from({length: 12}, (_, i) => "F" + (i + 1)),
]);
const mouseNames = ["left", "middle", "right"];
const buttons = new Set();
const keys = new Map();
let socket, clipboardEnabled = false, pendingClip, retryCopy;
let ready = false, waitingMonitor = 0, epoch = 0, queued, drawing = false;
let frames = 0, pingAt = 0, pingId = 0, latency;
let streamOptions = false, highFps = false;
let position = {x: 0.5, y: 0.5}, waitingMove = false;

function status(text) {
  el("status").textContent = text;
}

function send(message) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function canInput() {
  return ready && socket?.readyState === WebSocket.OPEN;
}

function releaseInput() {
  for (const k of new Set(keys.values())) send({t: "key", k, d: "up"});
  for (const b of buttons) send({t: "click", b, d: "up", ...position});
  keys.clear();
  buttons.clear();
}

function finishClipboard(error, text) {
  if (!pendingClip) return;
  const {resolve, reject} = pendingClip;
  pendingClip = undefined;
  if (error) reject(new Error(error));
  else resolve(text);
}

function resetViewer() {
  ready = clipboardEnabled = false;
  waitingMonitor = 0;
  queued = retryCopy = undefined;
  epoch++;
  frames = pingAt = 0;
  latency = undefined;
  keys.clear();
  buttons.clear();
  finishClipboard("Sessão encerrada.");
  el("textDialog").close();
  el("text").value = "";
  el("monitor").replaceChildren();
  el("paste").disabled = el("copy").disabled = true;
  el("stats").textContent = "";
  context.clearRect(0, 0, canvas.width, canvas.height);
}

el("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const pin = el("pin").value;
  el("pin").value = "";
  el("connect").disabled = true;
  status("Conectando…");
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(pin ? {pin} : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Falha de acesso.");
    const url = new URL("/ws", location.href);
    url.protocol = "wss:";
    const ws = new WebSocket(url, ["markcontrol", result.token]);
    result.token = "";
    socket = ws;
    ws.binaryType = "blob";
    ws.onopen = () => {
      el("login").hidden = true;
      el("viewer").hidden = false;
      status("Conectado. Clique na tela para controlar.");
    };
    ws.onmessage = (event) => {
      if (socket === ws) receive(event.data);
    };
    ws.onclose = () => {
      if (socket !== ws) return;
      socket = undefined;
      resetViewer();
      el("viewer").hidden = true;
      el("login").hidden = false;
      el("connect").disabled = false;
      el("pin").focus();
      status("Sessão encerrada ou indisponível.");
    };
  } catch (error) {
    status(error.message);
    el("connect").disabled = false;
    el("pin").focus();
  }
});

function receive(data) {
  if (data instanceof Blob) {
    if (waitingMonitor) return;
    queued = {data, epoch};
    if (!drawing) void draw();
    return;
  }

  const message = JSON.parse(data);
  if (message.t === "info") {
    if (waitingMonitor && message.monitor !== waitingMonitor
        && waitingMonitor <= message.monitors) return;
    releaseInput();
    ready = false;
    waitingMonitor = 0;
    queued = undefined;
    epoch++;
    streamOptions = message.features?.stream_resolution === true;
    highFps = message.features?.high_fps === true;
    canvas.width = message.frame_size?.[0] || message.w;
    canvas.height = message.frame_size?.[1] || message.h;
    const select = el("monitor");
    select.replaceChildren();
    for (let i = 1; i <= message.monitors; i++) {
      select.add(new Option("Monitor " + i, i));
    }
    select.value = String(message.monitor);
    select.disabled = false;
    clipboardEnabled = message.clipboard;
    el("paste").disabled = el("copy").disabled = !clipboardEnabled;
    updateQuality(false);
  } else if (message.t === "pong" && message.n === pingId && pingAt) {
    latency = Math.round(performance.now() - pingAt);
    pingAt = 0;
  } else if (message.t === "clip") {
    finishClipboard(null, message.s);
  } else if (message.t === "clip_ok") {
    status("Texto disponível na área de transferência do remoto.");
  } else if (message.t === "error") {
    if (message.op === "clip_get") finishClipboard("Área de transferência indisponível ou acima de 100 KiB.");
    else if (message.op === "clip_set") status("O agente não aceitou o texto da área de transferência.");
    else if (message.op === "quality") {
      waitingMonitor = 0;
      el("monitor").disabled = false;
      status("Monitor indisponível.");
    } else status("O sistema remoto recusou essa entrada.");
  }
}

async function draw() {
  drawing = true;
  try {
    while (queued) {
      const frame = queued;
      queued = undefined;
      try {
        const bitmap = await createImageBitmap(frame.data);
        try {
          if (frame.epoch !== epoch || !socket || waitingMonitor) continue;
          context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
          ready = true;
          frames++;
        } finally {
          bitmap.close();
        }
      } catch {
        status("Não foi possível decodificar um quadro.");
      }
    }
  } finally {
    drawing = false;
  }
}

function updateQuality(changeMonitor) {
  const monitor = Number(el("monitor").value);
  if (!monitor) return;
  if (changeMonitor) {
    releaseInput();
    ready = false;
    waitingMonitor = monitor;
    epoch++;
    el("monitor").disabled = true;
  }
  const message = {t: "quality", q: Number(el("quality").value), fps: Math.min(Number(el("fps").value), highFps ? 60 : 20), monitor};
  if (streamOptions) {
    message.t = "stream";
    message.height = el("resolution").value === "auto" ? (el("scale").value === "actual" ? 0 : Math.max(240, Math.min(2160, Math.round(innerHeight - 140)))) : Number(el("resolution").value);
  }
  send(message);
}

el("monitor").addEventListener("change", () => updateQuality(true));
el("quality").addEventListener("change", () => updateQuality(false));
el("scale").addEventListener("change", () => {
  el("viewport").classList.toggle("actual", el("scale").value === "actual");
  updateQuality(false);
});
el("fps").addEventListener("change", () => updateQuality(false));
el("resolution").addEventListener("change", () => updateQuality(false));
el("disconnect").addEventListener("click", () => {
  releaseInput();
  socket?.close(1000);
});
window.addEventListener("blur", releaseInput);
canvas.addEventListener("blur", releaseInput);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) releaseInput();
});

function at(event) {
  const box = canvas.getBoundingClientRect();
  const clamp = (v) => Math.min(1, Math.max(0, v));
  const scale = Math.min(box.width / canvas.width, box.height / canvas.height);
  const w = canvas.width * scale, h = canvas.height * scale;
  return {
    x: clamp((event.clientX - box.left - (box.width - w) / 2) / w),
    y: clamp((event.clientY - box.top - (box.height - h) / 2) / h),
  };
}

canvas.addEventListener("contextmenu", (event) => event.preventDefault());
canvas.addEventListener("mousedown", (event) => {
  const b = mouseNames[event.button];
  if (!canInput() || !b) return;
  event.preventDefault();
  canvas.focus({preventScroll: true});
  position = at(event);
  buttons.add(b);
  send({t: "click", b, d: "down", ...position});
});
window.addEventListener("mouseup", (event) => {
  const b = mouseNames[event.button];
  if (!buttons.delete(b)) return;
  position = at(event);
  send({t: "click", b, d: "up", ...position});
});
window.addEventListener("mousemove", (event) => {
  if (!canInput() || (event.target !== canvas && !buttons.size)) return;
  position = at(event);
  if (waitingMove) return;
  waitingMove = true;
  requestAnimationFrame(() => {
    waitingMove = false;
    if (canInput() && socket.bufferedAmount < 4096) send({t: "move", ...position});
  });
});
canvas.addEventListener("wheel", (event) => {
  if (!canInput()) return;
  event.preventDefault();
  position = at(event);
  const step = (delta) => -Math.sign(delta) * Math.min(20,
    Math.ceil(Math.abs(delta) / (event.deltaMode === 0 ? 100 : 1)));
  send({t: "move", ...position});
  send({t: "scroll", dx: step(event.deltaX), dy: step(event.deltaY)});
}, {passive: false});

document.addEventListener("keydown", (event) => {
  if (el("textDialog").open || socket?.readyState !== WebSocket.OPEN) return;
  const key = event.key;
  if (clipboardEnabled && event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey
      && ["c", "v"].includes(key.toLowerCase())) {
    event.preventDefault();
    if (!event.repeat) void (key.toLowerCase() === "v" ? pasteRemote() : copyRemote());
    return;
  }
  if (!canInput() || document.activeElement !== canvas) return;
  if (!named.has(key) && !/^[!-~]$/.test(key)) return;
  event.preventDefault();
  const code = event.code || key;
  if (keys.has(code)) return;
  const k = /^[A-Z]$/.test(key) ? key.toLowerCase() : key;
  if (![...keys.values()].includes(k)) send({t: "key", k, d: "down"});
  keys.set(code, k);
});

document.addEventListener("keyup", (event) => {
  const code = event.code || event.key;
  const k = keys.get(code);
  if (!keys.delete(code)) return;
  event.preventDefault();
  if (![...keys.values()].includes(k)) send({t: "key", k, d: "up"});
});

el("typeText").addEventListener("click", () => {
  if (canInput()) el("textDialog").showModal();
});
el("sendText").addEventListener("click", () => {
  const input = el("text");
  if (canInput() && input.value) send({t: "text", s: input.value});
  input.value = "";
  el("textDialog").close();
  canvas.focus();
});
el("textDialog").addEventListener("close", () => { el("text").value = ""; });

async function pasteRemote() {
  if (!clipboardEnabled) return;
  const owner = socket;
  try {
    const text = await navigator.clipboard.readText();
    if (owner !== socket || !clipboardEnabled) return;
    if (new TextEncoder().encode(text).length > 100 * 1024) {
      status("A área de transferência excede 100 KiB.");
      return;
    }
    send({t: "clip_set", s: text});
  } catch {
    if (owner === socket) status("O navegador não autorizou a leitura da área de transferência.");
  }
}

async function copyRemote() {
  if (!clipboardEnabled || pendingClip) return;
  const owner = socket;
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Área de transferência indisponível neste navegador.");
    const text = retryCopy ?? await new Promise((resolve, reject) => {
      pendingClip = {resolve, reject};
      if (!send({t: "clip_get"})) finishClipboard("Sessão encerrada.");
    });
    if (owner !== socket || !clipboardEnabled) return;
    try {
      await navigator.clipboard.writeText(text);
      retryCopy = undefined;
      status("Texto copiado do remoto.");
    } catch {
      if (owner !== socket) return;
      retryCopy = text;
      status("Clique novamente em Copiar do remoto para autorizar a escrita local.");
    }
  } catch (error) {
    if (owner === socket) status(error.message);
  }
}

el("paste").addEventListener("click", pasteRemote);
el("copy").addEventListener("click", copyRemote);

setInterval(() => {
  if (socket?.readyState !== WebSocket.OPEN) return;
  const now = performance.now();
  if (!pingAt || now - pingAt > 10000) {
    pingAt = now;
    pingId = (pingId + 1) % 2147483648;
    send({t: "ping", n: pingId});
  }
  const rtt = latency === undefined ? "—" : latency;
  el("stats").textContent = frames + " FPS · " + rtt + " ms";
  frames = 0;
}, 1000);
