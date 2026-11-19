const APP = "http://127.0.0.1:47811";
const $ = (id) => document.getElementById(id);

function size(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i < 2 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
}

function toast(text) {
  const t = $("toast");
  t.textContent = text; t.style.display = "block";
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.style.display = "none"), 2600);
}

async function appOnline() {
  try {
    const r = await fetch(APP + "/ping", { cache: "no-store" });
    return (await r.json()).ok;
  } catch { return false; }
}

function grab(payload) {
  return new Promise((res) => chrome.runtime.sendMessage({ type: "grab", payload }, res));
}

async function send(payload, btn) {
  btn.disabled = true;
  const r = await grab(payload);
  btn.disabled = false;
  if (r && r.ok) toast("✓ Sent to MediaGrab – downloading best quality");
  else { toast("MediaGrab app isn't running"); $("warn").style.display = "block"; }
}

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("pageTitle").textContent = tab.title || tab.url;
  $("pageVideo").onclick = (e) => send({ url: tab.url, mode: "video", referer: tab.url, title: tab.title }, e.target);
  $("pageAudio").onclick = (e) => send({ url: tab.url, mode: "audio", referer: tab.url, title: tab.title }, e.target);

  const online = await appOnline();
  $("status").textContent = online ? "● app connected" : "● app not running";
  $("status").className = "pill " + (online ? "on" : "off");
  $("warn").style.display = online ? "none" : "block";

  const key = "tab_" + tab.id;
  const items = (await chrome.storage.session.get(key))[key] || [];
  $("detHead").textContent = `Detected media (${items.length})`;
  const list = $("list");
  if (!items.length) {
    list.innerHTML = `<div class="empty">Nothing detected yet. Press play on the video, then reopen this.
      For YouTube, Instagram, Facebook, TikTok etc. just use the <b>This page</b> buttons above.</div>`;
    return;
  }
  for (const it of items) {
    const el = document.createElement("div");
    el.className = "item";
    const cls = it.kind.endsWith("stream") ? "stream" : it.kind;
    const note = it.kind.endsWith("stream") ? "all qualities – best is picked" : size(it.size);
    el.innerHTML = `
      <div class="meta"><span class="tag ${cls}"></span><span class="note" style="color:var(--muted);font-size:11px"></span></div>
      <div class="url"></div>
      <div class="row small">
        <button data-m="video">⬇ Best video</button>
        <button data-m="audio" class="alt">♪ Audio only</button>
      </div>`;
    el.querySelector(".tag").textContent = it.kind;
    el.querySelector(".note").textContent = note;
    el.querySelector(".url").textContent = it.url;
    el.querySelector(".url").title = it.url;
    if (it.kind === "Audio") el.querySelector('[data-m="video"]').remove();
    el.querySelectorAll("button").forEach((b) => {
      b.onclick = () => send({ url: it.url, mode: b.dataset.m, referer: tab.url, title: tab.title }, b);
    });
    list.appendChild(el);
  }
})();
