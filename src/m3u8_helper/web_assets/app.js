const curlInput = document.getElementById("curlInput");
const customName = document.getElementById("customName");
const qualitySelect = document.getElementById("qualitySelect");
const parseBtn = document.getElementById("parseBtn");
const clearBtn = document.getElementById("clearBtn");
const resultsEl = document.getElementById("results");
const statusEl = document.getElementById("status");
const countBadge = document.getElementById("countBadge");

function setStatus(text, tone = "idle") {
  statusEl.textContent = text;
  statusEl.dataset.tone = tone;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatVariantLabel(variant) {
  const bits = [];
  if (variant.label) bits.push(variant.label);
  if (variant.resolution && variant.resolution !== variant.label) bits.push(variant.resolution);
  if (variant.bandwidth) bits.push(`${Math.round(Number(variant.bandwidth) / 1000)} kbps`);
  return bits.join(" · ");
}

function renderVariantBlock(item) {
  if (!item.variants || !item.variants.length) {
    if (item.selected_quality === "source") {
      return `
        <div class="variant-panel variant-panel--plain">
          <div class="variant-panel__title">畫質分析</div>
          <p>這個連結看起來是固定畫質或單一 media playlist，沒有解析到可切換的 master variants。</p>
        </div>
      `;
    }
    return "";
  }

  const rows = item.variants
    .map((variant) => {
      const selected = variant.url === item.url;
      return `
        <li class="variant-row${selected ? " is-selected" : ""}">
          <div>
            <strong>${escapeHtml(formatVariantLabel(variant) || "variant")}</strong>
            <div class="variant-row__url">${escapeHtml(variant.url)}</div>
          </div>
          <span class="variant-chip">${selected ? "目前使用" : "可選"}</span>
        </li>
      `;
    })
    .join("");

  return `
    <div class="variant-panel">
      <div class="variant-panel__title">可用畫質變體</div>
      <ul class="variant-list">${rows}</ul>
    </div>
  `;
}

function renderResults(items) {
  countBadge.textContent = `${items.length} 筆`;
  if (!items.length) {
    resultsEl.innerHTML = `
      <div class="placeholder">
        <div class="placeholder__frame"></div>
        <p>尚未有可顯示的結果。</p>
      </div>
    `;
    return;
  }

  resultsEl.innerHTML = items
    .map((item) => {
      if (item.error) {
        return `
          <div class="result-card">
            <div class="result-card__header">
              <span class="result-card__title">#${item.index}</span>
              <span class="badge">error</span>
            </div>
            <p>${escapeHtml(item.error)}</p>
          </div>
        `;
      }

      return `
        <div class="result-card">
          <div class="result-card__header">
            <span class="result-card__title">#${item.index}</span>
            <span class="badge">${escapeHtml(item.kind)}</span>
          </div>
          <div class="kv">
            <span>來源 URL</span>
            <div>${escapeHtml(item.source_url || item.url)}</div>
            <span>URL</span>
            <div>${escapeHtml(item.url)}</div>
            <span>畫質</span>
            <div>${escapeHtml(item.selected_quality || "source")}${item.is_master ? " (master)" : ""}</div>
            <span>檔名</span>
            <div>${escapeHtml(item.output)}</div>
          </div>
          ${renderVariantBlock(item)}
          <pre>${escapeHtml(item.command)}</pre>
          <div class="result-note" id="note-${item.index}"></div>
          <div class="result-actions">
            <button data-copy="${escapeHtml(item.command)}">複製指令</button>
            <button data-copy="${escapeHtml(item.url)}">複製 URL</button>
            <button class="btn-run" data-run="${item.index}">立即下載</button>
          </div>
        </div>
      `;
    })
    .join("");

  resultsEl.querySelectorAll("button[data-copy]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = btn.getAttribute("data-copy");
      if (!text) return;
      navigator.clipboard.writeText(text).then(() => {
        const original = btn.textContent;
        btn.textContent = "已複製";
        setTimeout(() => (btn.textContent = original), 1200);
      });
    });
  });

  resultsEl.querySelectorAll("button[data-run]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const index = Number(btn.getAttribute("data-run"));
      if (!index) return;
      runDownload(index, btn);
    });
  });
}

async function parseCurl() {
  const curlText = curlInput.value.trim();
  if (!curlText) {
    setStatus("請先貼上 curl", "warn");
    return;
  }
  setStatus("解析中...", "busy");

  try {
    const response = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        curl_text: curlText,
        custom_name: customName.value.trim(),
        quality: qualitySelect.value,
      }),
    });

    if (!response.ok) {
      throw new Error("解析失敗，請稍後再試");
    }

    const data = await response.json();
    renderResults(data.items || []);
    setStatus("完成", "done");
  } catch (error) {
    setStatus("發生錯誤", "error");
    resultsEl.innerHTML = `
      <div class="result-card">
        <div class="result-card__header">
          <span class="result-card__title">錯誤</span>
          <span class="badge">error</span>
        </div>
        <p>${escapeHtml(error.message || "解析失敗")}</p>
      </div>
    `;
  }
}

async function runDownload(index, buttonEl) {
  setStatus("下載中...", "busy");
  buttonEl.disabled = true;
  const noteEl = document.getElementById(`note-${index}`);
  if (noteEl) {
    noteEl.textContent = "下載中...";
  }

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        curl_text: curlInput.value.trim(),
        custom_name: customName.value.trim(),
        quality: qualitySelect.value,
        index,
      }),
    });

    const data = await response.json();
    if (!response.ok || data.status !== "ok") {
      throw new Error(data.message || "下載失敗");
    }

    if (noteEl) {
      noteEl.textContent = `完成：${data.output || ""}`.trim();
    }
    setStatus("下載完成", "done");
  } catch (error) {
    if (noteEl) {
      noteEl.textContent = `錯誤：${error.message || "下載失敗"}`;
    }
    setStatus("下載失敗", "error");
  } finally {
    buttonEl.disabled = false;
  }
}

parseBtn.addEventListener("click", parseCurl);
clearBtn.addEventListener("click", () => {
  curlInput.value = "";
  customName.value = "";
  qualitySelect.value = "best";
  renderResults([]);
  setStatus("已清除", "idle");
});

renderResults([]);
setStatus("待命", "idle");
