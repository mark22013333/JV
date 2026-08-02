const API = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
let token = "";
let activeTab = null;
let candidates = [];

const labels = { queued:"等待中", resolving:"解析中", downloading:"下載中", merging:"合併中", completed:"已完成", failed:"失敗", cancelled:"已取消" };
const escapeHtml = (value) => String(value ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");

async function api(path, options = {}, needsToken = true) {
  const headers = { "Content-Type":"application/json", ...(options.headers || {}) };
  if (needsToken && token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API}${path}`, { ...options, headers });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
  return data;
}

function toast(message) { $("toast").textContent = message; setTimeout(() => { if ($("toast").textContent === message) $("toast").textContent = ""; }, 2500); }

function safeUrl(url) {
  try { const parsed = new URL(url); return { host:parsed.host, path:parsed.pathname }; }
  catch (_) { return { host:"未知來源", path:"" }; }
}

function renderCandidates() {
  if (!candidates.length) {
    $("candidateList").innerHTML = '<div class="empty">播放影片後，偵測到的串流會出現在這裡。</div>';
    return;
  }
  $("candidateList").innerHTML = candidates.map((candidate, index) => {
    const display = safeUrl(candidate.url);
    return `<article class="candidate"><div><div class="candidate__top"><span class="tag">${escapeHtml(candidate.kind)}</span><span class="candidate__host">${escapeHtml(display.host)}</span></div><div class="candidate__path">${escapeHtml(display.path)}</div><div class="candidate__time">${escapeHtml(candidate.quality)} · ${new Date(candidate.detectedAt).toLocaleTimeString("zh-TW", {hour:"2-digit",minute:"2-digit",second:"2-digit"})}</div></div><button data-download="${index}" ${token ? "" : "disabled"}>下載</button></article>`;
  }).join("");
  $("candidateList").querySelectorAll("[data-download]").forEach((button) => button.addEventListener("click", () => createJob(Number(button.dataset.download), button)));
}

async function loadCandidates() {
  [activeTab] = await chrome.tabs.query({ active:true, currentWindow:true });
  if (!activeTab?.id) return renderCandidates();
  const key = `candidates:${activeTab.id}`;
  const stored = await chrome.storage.session.get(key);
  candidates = Array.isArray(stored[key]) ? stored[key] : [];
  renderCandidates();
}

async function checkConnection() {
  try {
    await api("/api/health", {}, false);
    $("connection").textContent = token ? "已配對" : "服務在線";
    $("connection").classList.add("online");
    $("pairPanel").hidden = Boolean(token);
  } catch (_) {
    $("connection").textContent = "服務離線";
    $("connection").classList.remove("online");
    $("pairPanel").hidden = false;
  }
}

async function pair() {
  const code = $("pairCode").value.trim();
  if (!/^\d{6}$/.test(code)) { $("pairMessage").textContent = "請輸入六位數配對碼。"; return; }
  $("pairBtn").disabled = true;
  try {
    const data = await api("/api/pair", { method:"POST", body:JSON.stringify({ code }) }, false);
    token = data.token;
    await chrome.storage.local.set({ apiToken:token });
    $("pairPanel").hidden = true;
    await checkConnection(); await loadJobs(); renderCandidates();
  } catch (error) { $("pairMessage").textContent = error.message; }
  finally { $("pairBtn").disabled = false; }
}

async function createJob(index, button) {
  const candidate = candidates[index];
  if (!candidate) return;
  button.disabled = true;
  try {
    await api("/api/jobs", { method:"POST", body:JSON.stringify({
      url:candidate.url, headers:candidate.headers, filename:$("filename").value.trim(), quality:$("quality").value,
      page_title:activeTab?.title || "",
    }) });
    button.textContent = "已加入"; toast("已加入下載佇列"); await loadJobs();
  } catch (error) { button.disabled = false; button.textContent = "重試"; toast(error.message); }
}

function renderJobs(jobs) {
  $("jobSummary").textContent = `${jobs.length} 筆`;
  if (!jobs.length) { $("jobList").innerHTML = '<div class="empty">尚無工作</div>'; return; }
  $("jobList").innerHTML = jobs.slice(0, 8).map((job) => {
    const active = ["queued","resolving","downloading","merging"].includes(job.status);
    const progress = Number.isFinite(job.progress) ? Math.round(job.progress) : 0;
    return `<article class="job ${active ? "active" : escapeHtml(job.status)}"><div class="job__top"><strong>${escapeHtml(labels[job.status] || job.status)}</strong><span class="job__state">${Number.isFinite(job.progress) ? `${progress}%` : (job.speed || "")}</span></div><div class="job__source">${escapeHtml(job.page_title || job.error || job.source)}</div><div class="bar"><i style="width:${progress}%"></i></div></article>`;
  }).join("");
}

async function loadJobs() {
  if (!token) return renderJobs([]);
  try { const data = await api("/api/jobs"); renderJobs(data.jobs || []); }
  catch (error) { if (/配對|權杖|授權/.test(error.message)) { token=""; await chrome.storage.local.remove("apiToken"); await checkConnection(); renderCandidates(); } }
}

$("pairBtn").addEventListener("click", pair);
$("pairCode").addEventListener("keydown", (event) => { if (event.key === "Enter") pair(); });
$("clearBtn").addEventListener("click", async () => {
  if (!activeTab?.id) return;
  await chrome.storage.session.remove(`candidates:${activeTab.id}`); candidates=[]; renderCandidates();
  await chrome.action.setBadgeText({ text:"", tabId:activeTab.id });
});
$("openToolBtn").addEventListener("click", () => chrome.tabs.create({ url:`${API}/` }));

(async () => {
  const stored = await chrome.storage.local.get("apiToken"); token = stored.apiToken || "";
  await Promise.all([checkConnection(), loadCandidates()]);
  await loadJobs();
  setInterval(loadJobs, 1000);
})();
