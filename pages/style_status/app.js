const bridge = window.AstrBotPluginPage;
let context = null;

// 当前活动标签
let currentTab = "style";

async function init() {
  try {
    context = await bridge.ready();
    initTabs();
    loadAllData();
  } catch (err) {
    console.error("初始化失败:", err);
  }
}

// 初始化标签页切换
function initTabs() {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const tabName = tab.dataset.tab;
      switchTab(tabName);
    });
  });
  
  // 刷新按钮
  document.getElementById("refreshBtn").addEventListener("click", loadAllData);
  
  // 重试按钮
  document.querySelectorAll(".retry-btn").forEach(btn => {
    btn.addEventListener("click", loadAllData);
  });
}

// 切换标签页
function switchTab(tabName) {
  currentTab = tabName;
  
  // 更新标签样式
  document.querySelectorAll(".tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  
  // 更新内容显示
  document.querySelectorAll(".tab-content").forEach(content => {
    content.classList.toggle("active", content.id === `tab-${tabName}`);
  });
}

// 加载所有数据
async function loadAllData() {
  await Promise.all([loadStyleStatus(), loadBalance()]);
}

// ==================== 风格学习 ====================

async function loadStyleStatus() {
  showLoading("style");
  try {
    const result = await bridge.apiGet("style_status");
    if (result.success && Object.keys(result.data || {}).length > 0) {
      renderSessions(result.data);
    } else {
      showEmpty("style");
    }
  } catch (err) {
    showError("style");
    console.error("加载失败:", err);
  }
}

function renderSessions(data) {
  const container = document.getElementById("style-sessions");
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
          <div class="session-info">
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

  hideAll("style");
  container.style.display = "grid";
}

// ==================== 状态切换 ====================

function showLoading(tab) {
  document.getElementById(`${tab}-loading`).style.display = "block";
  document.getElementById(`${tab}-error`).style.display = "none";
  if (document.getElementById(`${tab}-empty`)) {
    document.getElementById(`${tab}-empty`).style.display = "none";
  }
}

function showError(tab) {
  document.getElementById(`${tab}-loading`).style.display = "none";
  document.getElementById(`${tab}-error`).style.display = "block";
  if (document.getElementById(`${tab}-empty`)) {
    document.getElementById(`${tab}-empty`).style.display = "none";
  }
}

function showEmpty(tab) {
  document.getElementById(`${tab}-loading`).style.display = "none";
  document.getElementById(`${tab}-error`).style.display = "none";
  if (document.getElementById(`${tab}-empty`)) {
    document.getElementById(`${tab}-empty`).style.display = "block";
  }
}

function hideAll(tab) {
  document.getElementById(`${tab}-loading`).style.display = "none";
  document.getElementById(`${tab}-error`).style.display = "none";
  if (document.getElementById(`${tab}-empty`)) {
    document.getElementById(`${tab}-empty`).style.display = "none";
  }
}

// ==================== 余额查询 ====================

async function loadBalance() {
  showLoading("balance");
  try {
    const result = await bridge.apiGet("balance");
    if (result.success && result.data && result.data.length > 0) {
      renderBalance(result.data);
    } else {
      showEmpty("balance");
    }
  } catch (err) {
    showError("balance");
    console.error("余额查询失败:", err);
  }
}

function renderBalance(data) {
  const container = document.getElementById("balance-list");
  
  container.innerHTML = data.map(item => {
    const className = item.success ? "success" : "error";
    return `
      <div class="balance-item ${className}">
        <div class="balance-name">${escapeHtml(item.name)}</div>
        <div class="balance-value">${escapeHtml(item.value)}</div>
      </div>
    `;
  }).join("");

  hideAll("balance");
  document.getElementById("balance-content").style.display = "block";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// 启动
init();
