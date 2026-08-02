const $ = (id) => document.getElementById(id);
const curlInput = $("curlInput");
const customName = $("customName");
const qualitySelect = $("qualitySelect");
const resultsEl = $("results");
const jobList = $("jobList");
let parsedItems = [];

const statusLabels = {
  queued: "等待中", resolving: "解析來源", downloading: "下載中", merging: "合併中",
  completed: "已完成", failed: "失敗", cancelled: "已取消",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function setStatus(text, tone = "idle") {
  $("status").textContent = text;
  $("status").dataset.tone = tone;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = response.status === 204 ? {} : await response.json();
  if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
  return data;
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("healthLamp").classList.add("is-online");
    $("healthText").textContent = "本機服務已連線";
    const tools = [health.tools.yt_dlp && "yt-dlp", health.tools.ffmpeg && "ffmpeg"].filter(Boolean);
    $("toolText").textContent = tools.length ? `可用工具：${tools.join(" / ")}` : "尚未安裝串流下載工具";
    $("pairState").textContent = health.paired ? "已有擴充功能完成配對" : "尚未配對擴充功能";
  } catch (error) {
    $("healthLamp").classList.remove("is-online");
    $("healthText").textContent = "本機服務無回應";
    $("toolText").textContent = error.message;
  }
}

async function loadSettings() {
  try {
    const settings = await api("/api/settings");
    $("outputDir").value = settings.output_dir || "";
    $("settingsState").textContent = "設定已載入";
  } catch (error) {
    $("settingsState").textContent = error.message;
  }
}

function renderSources(items) {
  parsedItems = items;
  if (!items.length) {
    resultsEl.innerHTML = "";
    return;
  }
  resultsEl.innerHTML = items.map((item) => {
    if (item.error) return `<div class="source-card"><div class="source-card__error">#${item.index} ${escapeHtml(item.error)}</div></div>`;
    return `<article class="source-card">
      <div>
        <div class="source-card__title">${escapeHtml(item.source_url || item.url)}</div>
        <div class="source-card__meta">${escapeHtml(item.kind)} · ${escapeHtml(item.selected_quality || "source")} · ${escapeHtml(item.output)}</div>
      </div>
      <button class="button button--primary" data-queue="${item.index}">加入佇列</button>
    </article>`;
  }).join("");
  resultsEl.querySelectorAll("[data-queue]").forEach((button) => {
    button.addEventListener("click", () => queueCurlJob(Number(button.dataset.queue), button));
  });
}

async function parseCurl() {
  if (!curlInput.value.trim()) return setStatus("請先貼上 cURL", "warn");
  setStatus("分析中…", "busy");
  $("parseBtn").disabled = true;
  try {
    const data = await api("/api/parse", {
      method: "POST",
      body: JSON.stringify({
        curl_text: curlInput.value.trim(), custom_name: customName.value.trim(), quality: qualitySelect.value,
      }),
    });
    renderSources(data.items || []);
    setStatus(`找到 ${data.count || 0} 筆來源`, "done");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    $("parseBtn").disabled = false;
  }
}

async function queueCurlJob(index, button) {
  button.disabled = true;
  try {
    await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        curl_text: curlInput.value.trim(), index, filename: customName.value.trim(), quality: qualitySelect.value,
      }),
    });
    button.textContent = "已加入";
    await loadJobs();
  } catch (error) {
    button.disabled = false;
    button.textContent = "重試";
    setStatus(error.message, "error");
  }
}

function renderJobs(jobs) {
  $("jobCount").textContent = `${jobs.length} 筆紀錄`;
  $("activeCount").textContent = `${jobs.filter((job) => ["queued", "resolving", "downloading", "merging"].includes(job.status)).length} 執行中`;
  if (!jobs.length) {
    jobList.innerHTML = '<div class="job-empty">尚無下載工作。播放影片後，可由擴充功能直接加入。</div>';
    return;
  }
  jobList.innerHTML = jobs.map((job) => {
    const progress = Number.isFinite(job.progress) ? Math.round(job.progress) : 0;
    const active = ["queued", "resolving", "downloading", "merging"].includes(job.status);
    const detail = job.error || job.output || `${job.downloader || "等待分派"} · ${job.quality}`;
    return `<article class="job">
      <div class="job__state" data-state="${escapeHtml(job.status)}">${escapeHtml(statusLabels[job.status] || job.status)}</div>
      <div><div class="job__source">${escapeHtml(job.page_title || job.source)}</div><div class="job__detail">${escapeHtml(detail)}</div></div>
      <div class="job__progress"><div class="progress"><i style="width:${progress}%"></i></div><div class="progress-label"><span>${Number.isFinite(job.progress) ? `${progress}%` : "計算中"}</span><span>${escapeHtml(job.speed || job.eta || "")}</span></div></div>
      ${active ? `<button class="button button--quiet" data-cancel="${job.id}">取消</button>` : ""}
    </article>`;
  }).join("");
  jobList.querySelectorAll("[data-cancel]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await api(`/api/jobs/${button.dataset.cancel}/cancel`, { method: "POST", body: "{}" }); await loadJobs(); }
      catch (error) { setStatus(error.message, "error"); }
    });
  });
}

async function loadJobs() {
  try {
    const data = await api("/api/jobs");
    renderJobs(data.jobs || []);
  } catch (error) {
    jobList.innerHTML = `<div class="job-empty">${escapeHtml(error.message)}</div>`;
  }
}

$("parseBtn").addEventListener("click", parseCurl);
$("clearBtn").addEventListener("click", () => {
  curlInput.value = ""; customName.value = ""; qualitySelect.value = "best"; renderSources([]); setStatus("已清除");
});
$("pairCodeBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/pairing-code", { method: "POST", body: "{}" });
    $("pairCode").textContent = String(data.code).split("").join(" ");
    $("pairState").textContent = "請在十分鐘內輸入擴充功能";
  } catch (error) { $("pairState").textContent = error.message; }
});
$("saveSettingsBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/settings", { method: "PUT", body: JSON.stringify({ output_dir: $("outputDir").value }) });
    $("outputDir").value = data.output_dir; $("settingsState").textContent = "下載位置已儲存";
  } catch (error) { $("settingsState").textContent = error.message; }
});
$("openFolderBtn").addEventListener("click", async () => {
  try { await api("/api/settings/open-output", { method: "POST", body: "{}" }); $("settingsState").textContent = "已開啟下載資料夾"; }
  catch (error) { $("settingsState").textContent = error.message; }
});

renderSources([]);
renderJobs([]);
Promise.all([loadHealth(), loadSettings(), loadJobs()]);
setInterval(loadHealth, 10000);
setInterval(loadJobs, 1000);
