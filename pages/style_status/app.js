const bridge = window.AstrBotPluginPage;
let context = null;

async function init() {
  try {
    context = await bridge.ready();
    await loadStyleStatus();
  } catch (err) {
    showError();
    console.error("初始化失败:", err);
  }
}

async function loadStyleStatus() {
  showLoading();
  try {
    const result = await bridge.apiGet("style_status");
    if (result.success && Object.keys(result.data || {}).length > 0) {
      renderSessions(result.data);
    } else {
      showEmpty();
    }
  } catch (err) {
    showError();
    console.error("加载失败:", err);
  }
}

function renderSessions(data) {
  const container = document.getElementById("sessions");
  const entries = Object.values(data);

  container.innerHTML = entries.map(session => {
    const universalPreview = session.universal_preview.length > 0
      ? session.universal_preview.map(item => `<div class="preview-item">${escapeHtml(item)}</div>`).join("")
      : `<div class="preview-item empty-preview">暂无数据</div>`;

    const contextualPreview = session.contextual_preview.length > 0
      ? session.contextual_preview.map(item => `<div class="preview-item contextual">${escapeHtml(item)}</div>`).join("")
      : `<div class="preview-item contextual empty-preview">暂无数据</div>`;

    const specificPreview = session.specific_preview.length > 0
      ? session.specific_preview.map(item => `<div class="preview-item specific">${escapeHtml(item)}</div>`).join("")
      : `<div class="preview-item specific empty-preview">暂无数据</div>`;

    const lastUpdated = session.last_updated
      ? new Date(session.last_updated * 1000).toLocaleString("zh-CN")
      : "未知";

    return `
      <div class="session-card">
        <div class="session-header">
          <div>
            <div class="session-name">群组: ${escapeHtml(session.display_name)}</div>
            <div class="session-id">${escapeHtml(session.session_id)}</div>
          </div>
        </div>

        <div class="stats">
          <div class="stat">
            <div class="stat-value">${session.universal_count}</div>
            <div class="stat-label">通用表征</div>
          </div>
          <div class="stat">
            <div class="stat-value">${session.contextual_count}</div>
            <div class="stat-label">情境表征</div>
          </div>
          <div class="stat">
            <div class="stat-value">${session.specific_count}</div>
            <div class="stat-label">特定表征</div>
          </div>
          <div class="stat">
            <div class="stat-value">${session.history_count}</div>
            <div class="stat-label">聊天记录</div>
          </div>
        </div>

        <div class="preview-section">
          <div class="preview-title">通用表征 (最近3条)</div>
          <div class="preview-list">${universalPreview}</div>
        </div>

        <div class="preview-section">
          <div class="preview-title">情境表征 (最近3条)</div>
          <div class="preview-list">${contextualPreview}</div>
        </div>

        <div class="preview-section">
          <div class="preview-title">特定表征 (最近3条)</div>
          <div class="preview-list">${specificPreview}</div>
        </div>

        <div class="last-updated">最后更新: ${lastUpdated}</div>
      </div>
    `;
  }).join("");

  document.getElementById("loading").style.display = "none";
  document.getElementById("error").style.display = "none";
  document.getElementById("empty").style.display = "none";
  container.style.display = "grid";
}

function showLoading() {
  document.getElementById("loading").style.display = "block";
  document.getElementById("error").style.display = "none";
  document.getElementById("empty").style.display = "none";
  document.getElementById("sessions").style.display = "none";
}

function showError() {
  document.getElementById("loading").style.display = "none";
  document.getElementById("error").style.display = "block";
  document.getElementById("empty").style.display = "none";
  document.getElementById("sessions").style.display = "none";
}

function showEmpty() {
  document.getElementById("loading").style.display = "none";
  document.getElementById("error").style.display = "none";
  document.getElementById("empty").style.display = "block";
  document.getElementById("sessions").style.display = "none";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// 绑定按钮事件
document.getElementById("refreshBtn").addEventListener("click", loadStyleStatus);
document.getElementById("retryBtn")?.addEventListener("click", loadStyleStatus);

// 启动
init();
