// MediaGrab – background service worker (Opera / Opera GX / any Chromium browser)
// Watches network traffic for audio/video files and streaming playlists,
// keeps a per-tab list, and sends chosen items to the MediaGrab Windows app.

const APP = "http://127.0.0.1:47811";

const MEDIA_EXT = /\.(m3u8|mpd|mp4|m4v|webm|mkv|mov|flv|mp3|m4a|aac|ogg|oga|opus|wav|flac)(\?|#|$)/i;
const SEGMENT_EXT = /\.(ts|m4s|cmfv|cmfa|vtt|srt)(\?|#|$)/i;
// Sites where single media requests are just fragments – the page link works better.
const SKIP_HOSTS = /(^|\.)(googlevideo\.com|youtube\.com|ytimg\.com|doubleclick\.net|googlesyndication\.com)$/i;
const MIN_FILE_BYTES = 150 * 1024; // ignore tiny clips (UI sounds, ads, previews)
const MAX_PER_TAB = 60;

const tabs = {}; // tabId -> [{url, kind, size, ct, time}]
let loaded = false;

async function ensureLoaded() {
  if (loaded) return;
  const all = await chrome.storage.session.get(null);
  for (const [k, v] of Object.entries(all)) {
    if (k.startsWith("tab_")) tabs[k.slice(4)] = v;
  }
  loaded = true;
}

function header(headers, name) {
  const h = (headers || []).find(x => x.name.toLowerCase() === name);
  return h ? h.value : "";
}

function classify(url, ct) {
  ct = (ct || "").toLowerCase().split(";")[0].trim();
  const path = url.split("?")[0].toLowerCase();
  if (ct.includes("mpegurl") || path.endsWith(".m3u8")) return "HLS stream";
  if (ct === "application/dash+xml" || path.endsWith(".mpd")) return "DASH stream";
  if (ct === "video/mp2t" || ct === "video/iso.segment" || SEGMENT_EXT.test(url)) return null;
  if (ct.startsWith("video/")) return "Video";
  if (ct.startsWith("audio/")) return "Audio";
  if (MEDIA_EXT.test(url)) {
    return /\.(mp3|m4a|aac|ogg|oga|opus|wav|flac)(\?|#|$)/i.test(url) ? "Audio" : "Video";
  }
  return null;
}

function totalSize(headers) {
  const range = header(headers, "content-range"); // bytes 0-999/123456
  const m = range.match(/\/(\d+)\s*$/);
  if (m) return parseInt(m[1], 10);
  const len = parseInt(header(headers, "content-length"), 10);
  return isNaN(len) ? 0 : len;
}

async function save(tabId) {
  await chrome.storage.session.set({ ["tab_" + tabId]: tabs[tabId] || [] });
  const n = (tabs[tabId] || []).length;
  chrome.action.setBadgeText({ tabId: Number(tabId), text: n ? String(n) : "" });
  chrome.action.setBadgeBackgroundColor({ tabId: Number(tabId), color: "#ff3b5c" });
}

chrome.webRequest.onHeadersReceived.addListener(
  async (d) => {
    if (d.tabId < 0) return;
    if (d.statusCode && d.statusCode >= 400) return;
    let host = "";
    try { host = new URL(d.url).hostname; } catch { return; }
    if (SKIP_HOSTS.test(host)) return;
    const kind = classify(d.url, header(d.responseHeaders, "content-type"));
    if (!kind) return;
    const size = totalSize(d.responseHeaders);
    const isStream = kind.endsWith("stream");
    if (!isStream && size && size < MIN_FILE_BYTES) return;

    await ensureLoaded();
    const list = (tabs[d.tabId] = tabs[d.tabId] || []);
    // HLS sub-playlists: keep only the first (master) playlist per host+path folder
    const key = isStream ? d.url : d.url.split("?")[0];
    if (list.some(x => (x.kind.endsWith("stream") ? x.url : x.url.split("?")[0]) === key)) return;
    if (kind === "HLS stream" && list.some(x => x.kind === "HLS stream" &&
        new URL(x.url).pathname.split("/").slice(0, -1).join("/") ===
        new URL(d.url).pathname.split("/").slice(0, -1).join("/"))) return;

    list.unshift({ url: d.url, kind, size, ct: header(d.responseHeaders, "content-type"), time: Date.now() });
    if (list.length > MAX_PER_TAB) list.length = MAX_PER_TAB;
    await save(d.tabId);
  },
  { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "object", "sub_frame"] },
  ["responseHeaders"]
);

// Clear the list when the tab goes to a new page.
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.url) {
    await ensureLoaded();
    tabs[tabId] = [];
    await save(tabId);
  }
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  delete tabs[tabId];
  await chrome.storage.session.remove("tab_" + tabId);
});

// ---- Send to the Windows app ----
async function sendToApp(payload) {
  const r = await fetch(APP + "/grab", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "App refused");
  return true;
}

async function flash(tabId, ok) {
  chrome.action.setBadgeText({ tabId, text: ok ? "✓" : "!" });
  chrome.action.setBadgeBackgroundColor({ tabId, color: ok ? "#1db954" : "#e0a800" });
  setTimeout(() => save(tabId), 2500);
}

// ---- Right-click menu ----
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    const contexts = ["page", "link", "video", "audio"];
    chrome.contextMenus.create({ id: "mg-video", title: "MediaGrab: download best video", contexts });
    chrome.contextMenus.create({ id: "mg-audio", title: "MediaGrab: download best audio", contexts });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let url = info.srcUrl || info.linkUrl || info.pageUrl;
  if (!url || url.startsWith("blob:") || url.startsWith("data:")) url = info.pageUrl || tab.url;
  try {
    await sendToApp({
      url,
      mode: info.menuItemId === "mg-audio" ? "audio" : "video",
      referer: tab && tab.url,
      title: url === (tab && tab.url) ? tab.title : undefined,
    });
    flash(tab.id, true);
  } catch {
    flash(tab.id, false);
  }
});

// Popup asks the worker to send (so it works even if the popup closes).
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "grab") {
    sendToApp(msg.payload).then(() => reply({ ok: true }), e => reply({ ok: false, error: String(e) }));
    return true;
  }
});
