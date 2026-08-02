const MEDIA_PATH = /\.(m3u8|mpd|mp4|webm|m4a)(?:$|[?#])/i;
const MEDIA_CONTENT = /(?:application\/(?:vnd\.apple\.mpegurl|x-mpegurl|dash\+xml)|audio\/|video\/)/i;
const SEGMENT_PATH = /\.(?:ts|m4s|aac|cmfv|cmfa|key)(?:$|[?#])/i;
const SEGMENT_CONTENT = /(?:mp2t|iso\.segment)/i;
const OBSERVED_TYPES = ["media", "xmlhttprequest", "other", "sub_frame"];
const SAFE_HEADERS = new Set(["user-agent", "referer", "origin", "cookie", "accept", "accept-language"]);

function pendingKey(requestId) { return `pending:${requestId}`; }
function candidatesKey(tabId) { return `candidates:${tabId}`; }

function headersToObject(headers = []) {
  const result = {};
  for (const header of headers) {
    const name = String(header.name || "");
    if (!SAFE_HEADERS.has(name.toLowerCase()) || typeof header.value !== "string") continue;
    result[name] = header.value;
  }
  return result;
}

function mediaKind(url, contentType = "") {
  const path = new URL(url).pathname.toLowerCase();
  if (path.endsWith(".m3u8") || /mpegurl/i.test(contentType)) return "HLS";
  if (path.endsWith(".mpd") || /dash\+xml/i.test(contentType)) return "DASH";
  if (path.endsWith(".mp4") || /video\/mp4/i.test(contentType)) return "MP4";
  if (path.endsWith(".webm") || /webm/i.test(contentType)) return "WEBM";
  if (path.endsWith(".m4a")) return "M4A";
  return contentType.startsWith("audio/") ? "AUDIO" : "MEDIA";
}

function guessedQuality(url) {
  const match = String(url).match(/(?:^|[^0-9])(2160|1440|1080|720|480|360)p?(?:[^0-9]|$)/i);
  return match ? `${match[1]}p` : "自動";
}

async function updateBadge(tabId, count) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color: "#f36b35", tabId });
    await chrome.action.setBadgeText({ text: count ? String(Math.min(count, 99)) : "", tabId });
  } catch (_) {
    // Tab may already be gone.
  }
}

async function addCandidate(details, headers, contentType = "") {
  if (details.tabId < 0) return;
  const key = candidatesKey(details.tabId);
  const stored = await chrome.storage.session.get(key);
  const items = Array.isArray(stored[key]) ? stored[key] : [];
  const normalized = new URL(details.url);
  normalized.hash = "";
  const dedupeKey = normalized.toString();
  const existing = items.findIndex((item) => item.dedupeKey === dedupeKey);
  const candidate = {
    id: crypto.randomUUID(),
    dedupeKey,
    url: details.url,
    headers,
    kind: mediaKind(details.url, contentType),
    quality: guessedQuality(details.url),
    detectedAt: Date.now(),
    initiator: details.initiator || "",
  };
  if (existing >= 0) items[existing] = { ...items[existing], ...candidate, id: items[existing].id };
  else items.unshift(candidate);
  const trimmed = items.slice(0, 50);
  await chrome.storage.session.set({ [key]: trimmed });
  await updateBadge(details.tabId, trimmed.length);
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const headers = headersToObject(details.requestHeaders);
    chrome.storage.session.set({ [pendingKey(details.requestId)]: { headers, details } });
    if (MEDIA_PATH.test(details.url)) addCandidate(details, headers);
  },
  { urls: ["<all_urls>"], types: OBSERVED_TYPES },
  ["requestHeaders", "extraHeaders"],
);

chrome.webRequest.onHeadersReceived.addListener(
  async (details) => {
    const contentTypeHeader = (details.responseHeaders || []).find((header) => header.name.toLowerCase() === "content-type");
    const contentType = contentTypeHeader?.value || "";
    if (
      !MEDIA_CONTENT.test(contentType) ||
      MEDIA_PATH.test(details.url) ||
      SEGMENT_PATH.test(details.url) ||
      SEGMENT_CONTENT.test(contentType)
    ) return;
    const key = pendingKey(details.requestId);
    const stored = await chrome.storage.session.get(key);
    const pending = stored[key];
    await addCandidate(details, pending?.headers || {}, contentType);
  },
  { urls: ["<all_urls>"], types: OBSERVED_TYPES },
  ["responseHeaders", "extraHeaders"],
);

async function clearPending(details) {
  await chrome.storage.session.remove(pendingKey(details.requestId));
}
chrome.webRequest.onCompleted.addListener(clearPending, { urls: ["<all_urls>"] });
chrome.webRequest.onErrorOccurred.addListener(clearPending, { urls: ["<all_urls>"] });

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status === "loading") {
    await chrome.storage.session.remove(candidatesKey(tabId));
    await updateBadge(tabId, 0);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(candidatesKey(tabId)));
