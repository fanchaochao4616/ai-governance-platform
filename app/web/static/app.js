/* Content moderation annotation platform UI */
const API = "/api/v1";
const TOKEN_KEY = "auth_token";
let currentJobId = null;
let pollTimer = null;
let currentUser = null;

function toast(msg, err = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = err ? "show err" : "show";
  setTimeout(() => (el.className = ""), 3500);
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401 && !path.startsWith("/auth/login")) {
    setToken("");
    currentUser = null;
    showLogin(true);
    throw new Error("未登录或会话已过期");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function showLogin(show) {
  const ov = document.getElementById("login-overlay");
  const shell = document.getElementById("app-shell");
  if (ov) ov.hidden = !show;
  if (shell) shell.style.visibility = show ? "hidden" : "visible";
}

function renderUserChip(user) {
  currentUser = user;
  const name = user?.display_name || user?.username || "—";
  const role = user?.role === "admin" ? "管理员" : user?.role || "用户";
  const nameEl = document.getElementById("user-name");
  const roleEl = document.getElementById("user-role");
  const av = document.getElementById("user-avatar");
  if (nameEl) nameEl.textContent = name;
  if (roleEl) roleEl.textContent = role;
  if (av) av.textContent = (name || "用").slice(0, 1);
}

async function bootstrapAuth() {
  const token = getToken();
  if (!token) {
    showLogin(true);
    return false;
  }
  try {
    const me = await api("/auth/me");
    renderUserChip(me);
    showLogin(false);
    return true;
  } catch (_) {
    setToken("");
    showLogin(true);
    return false;
  }
}

document.getElementById("form-login")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const errEl = document.getElementById("login-error");
  if (errEl) {
    errEl.hidden = true;
    errEl.textContent = "";
  }
  try {
    // login must not require existing token
    const res = await fetch(API + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: fd.get("username"),
        password: fd.get("password"),
      }),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || res.statusText);
    }
    const data = await res.json();
    setToken(data.token);
    renderUserChip(data.user);
    showLogin(false);
    e.target.reset();
    loadJobs().catch(() => {});
  } catch (err) {
    if (errEl) {
      errEl.hidden = false;
      errEl.textContent = err.message || "登录失败";
    }
  }
});

document.getElementById("user-chip")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const dd = document.getElementById("user-dropdown");
  if (!dd) return;
  dd.hidden = !dd.hidden;
});

document.addEventListener("click", () => {
  const dd = document.getElementById("user-dropdown");
  if (dd) dd.hidden = true;
});

document.getElementById("btn-logout")?.addEventListener("click", async (e) => {
  e.stopPropagation();
  try {
    await api("/auth/logout", { method: "POST" });
  } catch (_) {}
  setToken("");
  currentUser = null;
  document.getElementById("user-dropdown").hidden = true;
  showLogin(true);
});

document.getElementById("btn-change-pwd")?.addEventListener("click", (e) => {
  e.stopPropagation();
  document.getElementById("user-dropdown").hidden = true;
  const m = document.getElementById("pwd-modal");
  if (m) m.hidden = false;
  document.getElementById("pwd-error").hidden = true;
});

document.getElementById("btn-pwd-cancel")?.addEventListener("click", () => {
  document.getElementById("pwd-modal").hidden = true;
  document.getElementById("form-change-pwd")?.reset();
});

document.getElementById("form-change-pwd")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const errEl = document.getElementById("pwd-error");
  const n1 = String(fd.get("new_password") || "");
  const n2 = String(fd.get("new_password2") || "");
  if (n1 !== n2) {
    if (errEl) {
      errEl.hidden = false;
      errEl.textContent = "两次输入的新密码不一致";
    }
    return;
  }
  try {
    await api("/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        old_password: fd.get("old_password"),
        new_password: n1,
      }),
    });
    document.getElementById("pwd-modal").hidden = true;
    e.target.reset();
    toast("密码已修改");
  } catch (err) {
    if (errEl) {
      errEl.hidden = false;
      errEl.textContent = err.message || "修改失败";
    }
  }
});

/** 顶栏标题：与侧栏二级入口文案一致 */
const VIEW_TITLES = {
  jobs: "Job 列表",
  create: "数据标注",
  detail: "任务详情",
  templates: "提示词调试",
  "prompt-debug": "提示词调试",
  "data-clean": "清洗台",
  "data-search": "检索台",
  "data-generate": "生成台",
  "llm-mine": "挖掘台",
  settings: "系统设置",
};

/** 统一 Job 类型（与后端 JOB_TYPES 一致） */
const JOB_TYPE_META = {
  annotation: {
    label: "数据标注",
    module: "annotate",
    view: "detail",
    icon: "✎",
  },
  prompt_debug: {
    label: "提示词调试",
    module: "prompt",
    view: "prompt-debug",
    icon: "⚙",
  },
  data_clean: {
    label: "数据清洗",
    module: "clean",
    view: "data-clean",
    icon: "🧹",
  },
  data_search: {
    label: "数据库检索",
    module: "search",
    view: "data-search",
    icon: "🔍",
  },
  data_generate: {
    label: "数据生成",
    module: "generate",
    view: "data-generate",
    icon: "✦",
  },
  llm_mine: {
    label: "大模型挖掘",
    module: "llm-mine",
    view: "llm-mine",
    icon: "⬡",
  },
};

/** 视图所属任务模块（用于侧栏高亮分组） */
const VIEW_MODULE = {
  jobs: "jobs",
  create: "annotate",
  detail: "annotate",
  templates: "prompt",
  "prompt-debug": "prompt",
  "data-clean": "clean",
  "data-search": "search",
  "data-generate": "generate",
  "llm-mine": "llm-mine",
};

/** Job 列表类型筛选：各类型版式不同，不提供「全部」混排 */
let jobsTypeFilter = "annotation";

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelectorAll(".sidenav-item").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".top-link").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".sidenav-group").forEach((g) => g.classList.remove("is-active"));

  const view = document.getElementById("view-" + name);
  if (view) view.classList.add("active");

  // 侧栏：标注详情→数据标注；调试工作台→模板库；settings 在顶栏
  let navView = name;
  if (name === "detail") navView = "create";
  if (name === "prompt-debug") navView = "templates";
  const side = document.querySelector(`.sidenav-item[data-view="${navView}"]`);
  if (side) side.classList.add("active");

  const mod = VIEW_MODULE[name] || VIEW_MODULE[navView];
  if (mod) {
    document
      .querySelector(`.sidenav-group[data-module="${mod}"]`)
      ?.classList.add("is-active");
  }

  if (name === "settings") {
    const s = document.getElementById("btn-settings");
    if (s) s.classList.add("active");
  }

  const title = document.getElementById("topbar-title");
  if (title) title.textContent = VIEW_TITLES[name] || name;

  // 顶栏「返回列表」：标注详情 / 提示词调试工作台
  const btnBack = document.getElementById("btn-back");
  if (btnBack) {
    btnBack.hidden = name !== "detail" && name !== "prompt-debug";
  }
}

function goView(v) {
  if (v === "jobs" || v === "create" || v === "templates") {
    loadJobs().catch((e) => toast(e.message, true));
  }
  showView(v);
}

document.querySelectorAll(".sidenav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    const v = btn.dataset.view;
    if (v) goView(v);
  });
});

// 收起态：点分组第一个入口；占位页内跳转
document.querySelectorAll("[data-goto]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const v = btn.getAttribute("data-goto");
    if (v) goView(v);
  });
});

// 收起导航时：点击分组图标打开该组第一个入口
document.querySelectorAll(".sidenav-group").forEach((group) => {
  group.addEventListener("click", (e) => {
    const sn = document.getElementById("sidenav");
    if (!sn?.classList.contains("collapsed")) return;
    // 已点到子项则交给子项处理
    if (e.target.closest(".sidenav-item")) return;
    const first = group.querySelector(".sidenav-item[data-view]");
    if (first?.dataset.view) goView(first.dataset.view);
  });
});

document.getElementById("btn-settings")?.addEventListener("click", () => {
  goView("settings");
  syncThemeRadios();
});

// —— 显示主题：黑夜 / 白天 ——
const THEME_KEY = "ui_theme";

function getUiTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" ? "light" : "dark";
  } catch (_) {
    return "dark";
  }
}

function applyUiTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem(THEME_KEY, t);
  } catch (_) {}
  syncThemeRadios();
}

function syncThemeRadios() {
  const t = getUiTheme();
  const dark = document.getElementById("theme-dark");
  const light = document.getElementById("theme-light");
  if (dark) dark.checked = t === "dark";
  if (light) light.checked = t === "light";
}

(function initTheme() {
  applyUiTheme(getUiTheme());
  document.getElementById("theme-dark")?.addEventListener("change", (e) => {
    if (e.target.checked) {
      applyUiTheme("dark");
      toast("已切换为黑夜模式");
    }
  });
  document.getElementById("theme-light")?.addEventListener("change", (e) => {
    if (e.target.checked) {
      applyUiTheme("light");
      toast("已切换为白天模式");
    }
  });
})();

// 左侧导航收起/展开：箭头方向由 CSS .sidenav.collapsed 控制（← / →）
(function initSidenav() {
  const sn = document.getElementById("sidenav");
  const btn = document.getElementById("btn-sidenav-toggle");
  if (!sn || !btn) return;
  const key = "sidenav_collapsed";
  const syncToggleUi = () => {
    const collapsed = sn.classList.contains("collapsed");
    btn.title = collapsed ? "展开导航" : "收起导航";
    btn.setAttribute("aria-label", collapsed ? "展开导航" : "收起导航");
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  };
  if (localStorage.getItem(key) === "1") sn.classList.add("collapsed");
  syncToggleUi();
  btn.addEventListener("click", () => {
    sn.classList.toggle("collapsed");
    localStorage.setItem(key, sn.classList.contains("collapsed") ? "1" : "0");
    syncToggleUi();
  });
})();

/** 人工介入相关：后端细分状态，前端统一展示为「人工介入中」 */
const HUMAN_INTERVENTION_STATUSES = new Set([
  "AWAIT_DECISION_THRESHOLD",
  "AWAIT_CONFIDENCE_BINS",
  "AWAIT_QC",
  "AWAIT_DECISION",
  "PROMPT_IMPROVING",
]);

/** 状态码 → 简短中文（KPI / 列表展示） */
const STATUS_SHORT_ZH = {
  CREATED: "待开始",
  GOLD_OPTIMIZING: "Gold优化中",
  GOLD_READY: "Gold已达标",
  GOLD_FAILED: "Gold未达标",
  ROUND_LABELING: "全量标注中",
  // 以下合并展示
  AWAIT_DECISION_THRESHOLD: "人工介入中",
  AWAIT_CONFIDENCE_BINS: "人工介入中",
  AWAIT_QC: "人工介入中",
  AWAIT_DECISION: "人工介入中",
  PROMPT_IMPROVING: "人工介入中",
  COMPLETED: "已完成",
  FAILED: "失败",
  BUDGET_EXCEEDED: "预算超限",
  CANCELLED: "已取消",
  ABORTED: "已中止",
};

function statusLabelZh(s) {
  if (HUMAN_INTERVENTION_STATUSES.has(s)) return "人工介入中";
  return STATUS_SHORT_ZH[s] || String(s || "—");
}

function statusBadge(s) {
  let cls = "";
  if (["COMPLETED", "GOLD_READY"].includes(s)) cls = "ok";
  else if (["FAILED", "BUDGET_EXCEEDED", "GOLD_FAILED", "CANCELLED"].includes(s))
    cls = "err";
  else if (s === "ABORTED" || HUMAN_INTERVENTION_STATUSES.has(s)) cls = "warn";
  else if (s === "GOLD_OPTIMIZING" || s === "ROUND_LABELING") cls = "warn";
  const text = statusLabelZh(s);
  // title 保留细分步骤，便于排查
  const tip = STATUS_PHASE_LABEL[s]
    ? `${statusLabelZh(s)} · ${STATUS_PHASE_LABEL[s]}`
    : String(s || "");
  return `<span class="badge ${cls}" title="${escapeHtml(tip)}">${escapeHtml(
    text
  )}</span>`;
}

/** 当前 Job 列表多选集合 */
const selectedJobIds = new Set();

function syncJobsDeleteBtn() {
  const n = selectedJobIds.size;
  const label = n > 0 ? `删除选中（${n}）` : "删除选中";
  const btn = document.getElementById("btn-delete-jobs");
  if (!btn) return;
  btn.disabled = n === 0;
  btn.textContent = label;
  btn.classList.toggle("has-selection", n > 0);
}

function getSelectedJobIdsFromDom(rootEl) {
  const root =
    rootEl ||
    document.getElementById("jobs-table") ||
    document.getElementById("create-jobs-table");
  const ids = [];
  if (!root) return ids;
  root.querySelectorAll(".job-check:checked").forEach((el) => {
    const id = +el.value;
    if (id) ids.push(id);
  });
  return ids;
}

/** 最近一次拉取的 Job 列表（供标注页本地搜索） */
let cachedJobsList = [];
/** 数据标注页历史任务搜索关键词 */
let createJobsQuery = "";
let createJobsSearchTimer = null;

/**
 * 历史任务文本匹配：名称 / ID / 状态（中英）/ 错误信息
 */
function jobMatchesQuery(job, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  const status = String(job.status || "");
  const hay = [
    String(job.id ?? ""),
    String(job.name || ""),
    status,
    statusLabelZh(status) || "",
    STATUS_PHASE_LABEL[status] || "",
    String(job.error_message || ""),
    String(job.current_round_no ?? ""),
  ]
    .join(" ")
    .toLowerCase();
  // 多词：全部命中（空格分隔）
  return q.split(/\s+/).filter(Boolean).every((part) => hay.includes(part));
}

/**
 * 渲染 Job 表格到指定容器。
 * @param {HTMLElement|null} el
 * @param {object[]} jobs 全量 jobs
 * @param {string} typeKey 类型筛选
 * @param {{
 *   checkAllId?: string,
 *   emptyHint?: string,
 *   textQuery?: string,
 *   compact?: boolean  // 数据标注页：无勾选/无删除，仅打开
 * }} [opts]
 */
function renderJobsTable(el, jobs, typeKey, opts = {}) {
  if (!el) return;
  const checkAllId = opts.checkAllId || "job-check-all";
  const compact = !!opts.compact;
  const list = jobs || [];
  if (!list.length) {
    el.innerHTML = "<p class='hint'>暂无 Job，请先创建。</p>";
    return;
  }
  const textQuery = opts.textQuery || "";
  let filtered = list.filter(
    (j) => (j.job_type || "annotation") === typeKey
  );
  if (textQuery.trim()) {
    filtered = filtered.filter((j) => jobMatchesQuery(j, textQuery));
  }
  if (!filtered.length) {
    const typeLabel =
      (JOB_TYPE_META[typeKey] && JOB_TYPE_META[typeKey].label) || typeKey;
    if (textQuery.trim()) {
      el.innerHTML = `<p class='hint'>无匹配「${escapeHtml(
        textQuery.trim()
      )}」的历史任务，请换关键词试试。</p>`;
      return;
    }
    el.innerHTML =
      opts.emptyHint ||
      `<p class='hint'>暂无「${escapeHtml(
        typeLabel
      )}」任务。可从左侧对应模块新建，创建后会出现在本列表以便恢复。</p>`;
    return;
  }

  const headCheck = compact
    ? ""
    : `<th class="col-check">
        <input type="checkbox" id="${escapeHtml(
          checkAllId
        )}" class="job-check-all" title="全选 / 取消全选" aria-label="全选" />
      </th>`;
  const headActions = compact
    ? `<th class="col-actions">操作</th>`
    : `<th class="col-actions">操作</th>`;

  el.innerHTML = `<table class="jobs-table${compact ? " jobs-table-compact" : ""}">
    <thead><tr>
      ${headCheck}
      <th>ID</th><th>名称</th><th>类型</th><th>状态</th><th>轮次</th><th>数据/Gold</th><th>已用Token</th>
      ${headActions}
    </tr></thead>
    <tbody>
      ${filtered
        .map((j) => {
          const checked = selectedJobIds.has(j.id) ? "checked" : "";
          const jt = j.job_type || "annotation";
          const typeMeta = JOB_TYPE_META[jt] || JOB_TYPE_META.annotation;
          const checkCell = compact
            ? ""
            : `<td class="col-check" data-stop-nav="1">
          <input type="checkbox" class="job-check" value="${j.id}" ${checked} aria-label="选择 Job ${j.id}" />
        </td>`;
          const actionCell = compact
            ? `<td class="col-actions" data-stop-nav="1">
          <button type="button" class="btn-job-open secondary" data-id="${j.id}" title="打开 / 恢复任务">打开</button>
        </td>`
            : `<td class="col-actions" data-stop-nav="1">
          <button type="button" class="btn-job-open secondary" data-id="${j.id}" title="打开 / 恢复任务">打开</button>
          <button type="button" class="btn-job-del secondary" data-id="${j.id}" title="永久删除此 Job">删除</button>
        </td>`;
          return `<tr data-id="${j.id}" class="job-row" data-job-type="${escapeHtml(jt)}">
        ${checkCell}
        <td class="job-open">${j.id}</td>
        <td class="job-open">${escapeHtml(j.name)}</td>
        <td class="job-open"><span class="job-type-tag" title="${escapeHtml(jt)}">${escapeHtml(typeMeta.label)}</span></td>
        <td class="job-open">${statusBadge(j.status)}</td>
        <td class="job-open">${j.current_round_no}</td>
        <td class="job-open">${j.annotation_count} / ${j.gold_count}</td>
        <td class="job-open">${j.tokens_used || 0}</td>
        ${actionCell}
      </tr>`;
        })
        .join("")}
    </tbody></table>`;

  if (!compact) {
    const checkAll = el.querySelector(".job-check-all");
    if (checkAll) {
      const boxes = [...el.querySelectorAll(".job-check")];
      checkAll.checked = boxes.length > 0 && boxes.every((b) => b.checked);
      checkAll.indeterminate =
        boxes.some((b) => b.checked) && !boxes.every((b) => b.checked);
      checkAll.addEventListener("change", () => {
        boxes.forEach((b) => {
          b.checked = checkAll.checked;
          const id = +b.value;
          if (checkAll.checked) selectedJobIds.add(id);
          else selectedJobIds.delete(id);
        });
        checkAll.indeterminate = false;
        syncJobsDeleteBtn();
      });
    }

    el.querySelectorAll(".job-check").forEach((cb) => {
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", () => {
        const id = +cb.value;
        if (cb.checked) selectedJobIds.add(id);
        else selectedJobIds.delete(id);
        if (checkAll) {
          const boxes = [...el.querySelectorAll(".job-check")];
          checkAll.checked = boxes.every((b) => b.checked);
          checkAll.indeterminate =
            boxes.some((b) => b.checked) && !boxes.every((b) => b.checked);
        }
        syncJobsDeleteBtn();
      });
    });

    el.querySelectorAll(".btn-job-del").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = +btn.dataset.id;
        const name = list.find((j) => j.id === id)?.name || `Job #${id}`;
        try {
          await confirmAndDeleteJobs([id], [name]);
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  }

  el.querySelectorAll("tr.job-row").forEach((tr) => {
    tr.querySelectorAll(".job-open").forEach((cell) => {
      cell.addEventListener("click", () => openJob(+tr.dataset.id));
    });
  });
  el.querySelectorAll(".btn-job-open").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openJob(+btn.dataset.id);
    });
  });
}

function renderCreateAnnotationJobsTable() {
  const qEl = document.getElementById("create-jobs-q");
  if (qEl && document.activeElement !== qEl && qEl.value !== createJobsQuery) {
    qEl.value = createJobsQuery;
  }
  renderJobsTable(
    document.getElementById("create-jobs-table"),
    cachedJobsList,
    "annotation",
    {
      compact: true,
      textQuery: createJobsQuery,
      emptyHint:
        "<p class='hint'>暂无数据标注任务。请在上方「从模板导入新任务」创建。</p>",
    }
  );
}

/** 模板库页 · 提示词调试任务搜索关键词 */
let promptDebugJobsQuery = "";
let promptDebugJobsSearchTimer = null;

function renderPromptDebugJobsTable() {
  const qEl = document.getElementById("prompt-debug-jobs-q");
  if (
    qEl &&
    document.activeElement !== qEl &&
    qEl.value !== promptDebugJobsQuery
  ) {
    qEl.value = promptDebugJobsQuery;
  }
  renderJobsTable(
    document.getElementById("prompt-debug-jobs-table"),
    cachedJobsList,
    "prompt_debug",
    {
      compact: true,
      textQuery: promptDebugJobsQuery,
      emptyHint:
        "<p class='hint'>暂无任务。点击「新建」创建。</p>",
    }
  );
}

async function loadJobs() {
  const jobs = await api("/jobs");
  cachedJobsList = jobs || [];
  const alive = new Set(cachedJobsList.map((j) => j.id));
  for (const id of [...selectedJobIds]) {
    if (!alive.has(id)) selectedJobIds.delete(id);
  }
  const typeKey = jobsTypeFilter || "annotation";
  renderJobsTable(document.getElementById("jobs-table"), cachedJobsList, typeKey, {
    checkAllId: "job-check-all",
  });
  renderCreateAnnotationJobsTable();
  renderPromptDebugJobsTable();
  syncJobsDeleteBtn();
}

// 数据标注页 · 历史任务搜索
document.getElementById("create-jobs-q")?.addEventListener("input", (e) => {
  createJobsQuery = e.target.value || "";
  if (createJobsSearchTimer) clearTimeout(createJobsSearchTimer);
  createJobsSearchTimer = setTimeout(() => {
    renderCreateAnnotationJobsTable();
  }, 200);
});
document.getElementById("create-jobs-q")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    createJobsQuery = e.target.value || "";
    if (createJobsSearchTimer) clearTimeout(createJobsSearchTimer);
    renderCreateAnnotationJobsTable();
  }
});

// 模板库页 · 提示词调试任务搜索
document.getElementById("prompt-debug-jobs-q")?.addEventListener("input", (e) => {
  promptDebugJobsQuery = e.target.value || "";
  if (promptDebugJobsSearchTimer) clearTimeout(promptDebugJobsSearchTimer);
  promptDebugJobsSearchTimer = setTimeout(() => {
    renderPromptDebugJobsTable();
  }, 200);
});
document.getElementById("prompt-debug-jobs-q")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    promptDebugJobsQuery = e.target.value || "";
    if (promptDebugJobsSearchTimer) clearTimeout(promptDebugJobsSearchTimer);
    renderPromptDebugJobsTable();
  }
});

document.getElementById("btn-new-prompt-debug")?.addEventListener("click", () => {
  const nameInput = document.getElementById("prompt-debug-new-name");
  const name = (nameInput?.value || "").trim();
  createTypedJob("prompt_debug", { name })
    .then(() => {
      if (nameInput) nameInput.value = "";
    })
    .catch((e) => toast(e.message, true));
});
document.getElementById("prompt-debug-new-name")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("btn-new-prompt-debug")?.click();
  }
});

// Job 列表 · 按任务类型切换（不同类型版式不同，不混排）
document.getElementById("jobs-type-filter")?.addEventListener("click", (e) => {
  const chip = e.target.closest(".jobs-type-chip");
  // 排除「删除选中」按钮
  if (!chip || chip.id === "btn-delete-jobs" || chip.classList.contains("jobs-delete-chip")) {
    return;
  }
  if (!chip.dataset.type) return;
  jobsTypeFilter = chip.dataset.type || "annotation";
  document
    .querySelectorAll("#jobs-type-filter .jobs-type-chip[data-type]")
    .forEach((c) => c.classList.toggle("active", c === chip));
  loadJobs().catch((err) => toast(err.message, true));
});

/**
 * 二次确认后永久删除。
 * @param {number[]} ids
 * @param {string[]} [names]
 */
async function confirmAndDeleteJobs(ids, names = []) {
  const list = [...new Set(ids.map((x) => +x).filter(Boolean))];
  if (!list.length) {
    toast("请先勾选要删除的 Job", true);
    return;
  }
  const label =
    list.length === 1
      ? `「${names[0] || list[0]}」(ID ${list[0]})`
      : `${list.length} 个 Job（ID: ${list.join(", ")}）`;

  // 第一次确认
  const ok1 = window.confirm(
    `确定要删除 ${label} 吗？\n\n此操作将永久删除任务及全部关联数据（标注、Gold、QC、Prompt 历史等），不可恢复。`
  );
  if (!ok1) return;

  // 第二次确认
  const ok2 = window.confirm(
    `二次确认：真的要永久删除 ${label} 吗？\n\n删除后无法找回，请再次确认。`
  );
  if (!ok2) return;

  let result;
  if (list.length === 1) {
    result = await api(`/jobs/${list[0]}`, { method: "DELETE" });
  } else {
    result = await api(`/jobs/bulk-delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: list }),
    });
  }
  const n = result?.count ?? list.length;
  toast(`已永久删除 ${n} 个 Job`);
  list.forEach((id) => selectedJobIds.delete(id));
  // 若正在查看的 Job 被删，退回列表
  if (currentJobId && list.includes(currentJobId)) {
    currentJobId = null;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    goView("jobs");
  }
  await loadJobs();
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

document.getElementById("btn-delete-jobs")?.addEventListener("click", async () => {
  try {
    const ids = getSelectedJobIdsFromDom(document.getElementById("jobs-table"));
    await confirmAndDeleteJobs(ids);
  } catch (e) {
    toast(e.message, true);
  }
});

let selectedJobTemplateId = null;
let jobTmplSearchTimer = null;

function clearJobTemplatePick() {
  selectedJobTemplateId = null;
  const hid = document.getElementById("job-tmpl-id");
  if (hid) hid.value = "";
  const box = document.getElementById("job-tmpl-selected");
  if (box) box.hidden = true;
  const label = document.getElementById("job-tmpl-selected-label");
  if (label) label.textContent = "";
  const results = document.getElementById("job-tmpl-results");
  if (results) {
    results.hidden = true;
    results.innerHTML = "";
  }
  const q = document.getElementById("job-tmpl-q");
  if (q) q.value = "";
}

/**
 * 从模板创建数据标注 Job：名称=模板名，细则/Prompt=激活版正文。
 */
async function createAnnotationJobFromTemplate(templateId) {
  const tid = +templateId;
  if (!tid) throw new Error("请选择模板");
  const t = await api(`/templates/${tid}`);
  const rawName = (t.name || "").trim() || `标注任务 #${tid}`;
  const promptText = (t.prompt_text || "").trim();
  if (!promptText) {
    throw new Error("该模板激活版正文为空，无法作为风控细则/初始 Prompt");
  }
  const job = await api("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: rawName,
      job_type: "annotation",
      policy_rules: promptText,
      template_id: tid,
      target_accuracy: 1.0,
      max_gold_iterations: 3,
    }),
  });
  return job;
}

async function searchJobTemplates(keyword) {
  const results = document.getElementById("job-tmpl-results");
  if (!results) return;
  const q = (keyword || "").trim();
  if (!q) {
    results.hidden = true;
    results.innerHTML = "";
    return;
  }
  try {
    const list = await api(`/templates?q=${encodeURIComponent(q)}`);
    const qLow = q.toLowerCase();
    const ranked = [...list].sort((a, b) => {
      const an = (a.name || "").toLowerCase();
      const bn = (b.name || "").toLowerCase();
      const as = an.includes(qLow) ? 0 : 1;
      const bs = bn.includes(qLow) ? 0 : 1;
      if (as !== bs) return as - bs;
      return (b.score || 0) - (a.score || 0);
    });
    if (!ranked.length) {
      results.hidden = false;
      results.innerHTML = `<div class="job-tmpl-item"><div class="jti-meta">无匹配模板</div></div>`;
      return;
    }
    results.hidden = false;
    results.innerHTML = ranked
      .map(
        (t) => `<div class="job-tmpl-item" data-tid="${t.id}" data-ver="${
          t.current_version || 1
        }" title="点击创建数据标注任务">
        <div class="jti-name" data-raw-name="${escapeHtml(t.name)}">#${t.id} ${escapeHtml(
          t.name
        )}</div>
        <div class="jti-meta">分类 ${escapeHtml(t.category || "-")} · 激活 v${
          t.current_version || 1
        } · 使用 ${t.usage_count || 0} · 点击导入新任务</div>
      </div>`
      )
      .join("");
    results.querySelectorAll(".job-tmpl-item[data-tid]").forEach((el) => {
      el.addEventListener("click", async () => {
        const tid = +el.dataset.tid;
        if (el.dataset.busy === "1") return;
        el.dataset.busy = "1";
        try {
          const t = await api(`/templates/${tid}`);
          const rawName = t.name || "";
          const ver = t.current_version || el.dataset.ver || 1;
          selectedJobTemplateId = tid;
          const hid = document.getElementById("job-tmpl-id");
          if (hid) hid.value = String(tid);
          const box = document.getElementById("job-tmpl-selected");
          const label = document.getElementById("job-tmpl-selected-label");
          if (box) box.hidden = false;
          if (label) {
            label.textContent = `正在从模板 #${tid} ${rawName}（v${ver}）导入新任务…`;
          }
          const job = await createAnnotationJobFromTemplate(tid);
          toast(`已创建数据标注 Job #${job.id}（${job.name || rawName}）`);
          clearJobTemplatePick();
          results.hidden = true;
          results.innerHTML = "";
          await loadJobs();
          await openJob(job.id);
        } catch (err) {
          toast(err.message || "创建失败", true);
          const box = document.getElementById("job-tmpl-selected");
          if (box) box.hidden = true;
        } finally {
          el.dataset.busy = "0";
        }
      });
    });
  } catch (e) {
    toast(e.message || "搜索失败", true);
  }
}

// 仅输入即搜索；Enter 立即搜
document.getElementById("job-tmpl-q")?.addEventListener("input", (e) => {
  const val = e.target.value || "";
  if (jobTmplSearchTimer) clearTimeout(jobTmplSearchTimer);
  jobTmplSearchTimer = setTimeout(() => searchJobTemplates(val), 280);
});

document.getElementById("job-tmpl-q")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    if (jobTmplSearchTimer) clearTimeout(jobTmplSearchTimer);
    searchJobTemplates(e.target.value || "");
  }
});

document.getElementById("btn-job-tmpl-clear")?.addEventListener("click", () => {
  clearJobTemplatePick();
});

/**
 * 打开 / 恢复 Job：按 job_type 进入对应工作台，便于后续继续。
 */
async function openJob(id) {
  currentJobId = id;
  let job = null;
  try {
    job = await api(`/jobs/${id}`);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const jt = job.job_type || "annotation";
  const meta = JOB_TYPE_META[jt] || JOB_TYPE_META.annotation;

  // 数据标注 → 原详情页；其它类型 → 对应模块页（任务上下文保留在 currentJobId 便于恢复）
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (jt === "annotation") {
    showView("detail");
    await refreshDetail();
    pollTimer = setInterval(() => {
      refreshDetail().catch(() => {});
    }, 2000);
  } else if (jt === "prompt_debug") {
    showView("prompt-debug");
    await loadPromptDebugWorkbench(job);
  } else {
    showView(meta.view || "jobs");
    toast(
      `已恢复「${meta.label}」任务 #${id}${
        job.name ? ` · ${job.name}` : ""
      }。可继续该类型工作台；进度已挂到 Job 列表。`
    );
  }
}

/** ---------- 提示词调试工作台 ---------- */
let pdVersionsCache = [];
let pdOpenDiffVer = null;
let pdOpenReasonVer = null;

function formatPdTime(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").slice(0, 19);
}

function renderDiffLinesHtml(diffText) {
  return String(diffText || "(与上一版无差异或为第一版)")
    .split("\n")
    .map((line) => {
      let kind = "ctx";
      if (line.startsWith("+++") || line.startsWith("---")) kind = "file";
      else if (line.startsWith("@@")) kind = "hunk";
      else if (line.startsWith("+")) kind = "add";
      else if (line.startsWith("-")) kind = "del";
      return `<div class="tmpl-diff-line ${kind}">${escapeHtml(line || " ")}</div>`;
    })
    .join("");
}

/**
 * 按 data-order 恢复历史卡片原始顺序，并显示全部项。
 */
function restorePdHistoryOrder() {
  const list = document.getElementById("pd-history-list");
  if (!list) return;
  const items = [...list.querySelectorAll(".pd-hist-item")];
  items
    .sort((a, b) => Number(a.dataset.order || 0) - Number(b.dataset.order || 0))
    .forEach((it) => {
      list.appendChild(it);
      it.hidden = false;
      it.classList.remove(
        "reason-expanded",
        "is-pinned-first",
        "is-diff-focus"
      );
    });
  // 恢复列表后重新判断哪些原因被截断
  requestAnimationFrame(() => syncPdReasonExpandButtons(list));
}

/**
 * 只留置顶当前历史卡片，其它隐藏（Diff / 修改原因展开共用）
 */
function pinPdHistoryItem(ver) {
  const list = document.getElementById("pd-history-list");
  if (!list) return null;
  const target = list.querySelector(`.pd-hist-item[data-v="${CSS.escape(String(ver))}"]`)
    || list.querySelector(`.pd-hist-item[data-v="${ver}"]`);
  if (!target) return null;
  list.querySelectorAll(".pd-hist-item").forEach((it) => {
    const match = String(it.dataset.v) === String(ver);
    it.hidden = !match;
    it.classList.toggle("is-pinned-first", match);
    it.classList.toggle("is-diff-focus", match);
    it.classList.toggle("reason-expanded", match);
  });
  list.insertBefore(target, list.firstChild);
  return target;
}

function collapsePdDiff() {
  pdOpenDiffVer = null;
  const panel = document.getElementById("pd-diff-panel");
  const out = document.getElementById("pd-diff-out");
  const list = document.getElementById("pd-history-list");
  const sidebar = document.querySelector(".pd-sidebar");
  const reasonLine = document.getElementById("pd-diff-reason");
  const rbBtn = document.getElementById("btn-pd-rollback-diff");
  if (panel) panel.hidden = true;
  if (out) {
    out.innerHTML = "";
    delete out.dataset.openVersion;
  }
  if (reasonLine) {
    reasonLine.hidden = true;
    reasonLine.textContent = "";
    reasonLine.classList.remove("is-expanded");
    delete reasonLine.dataset.full;
    reasonLine.onclick = null;
  }
  if (rbBtn) {
    rbBtn.hidden = true;
    delete rbBtn.dataset.v;
  }
  list?.classList.remove("is-diff-open");
  sidebar?.classList.remove("is-diff-detail-open");
  // 若未在展示修改原因详情，则恢复列表；否则由原因展开逻辑接管
  if (!pdOpenReasonVer) {
    restorePdHistoryOrder();
  }
}

function collapsePdReasonExpand() {
  pdOpenReasonVer = null;
  const box = document.getElementById("pd-reason-expand");
  const list = document.getElementById("pd-history-list");
  const sidebar = document.querySelector(".pd-sidebar");
  if (box) {
    box.hidden = true;
    box.innerHTML = "";
    delete box.dataset.openVersion;
  }
  list?.classList.remove("is-reason-open");
  sidebar?.classList.remove("is-reason-detail-open");
  // 若未在展示 Diff，则恢复列表
  if (!pdOpenDiffVer) {
    restorePdHistoryOrder();
  }
  list?.querySelectorAll(".btn-pd-expand-reason").forEach((btn) => {
    btn.textContent = "展开";
    btn.classList.remove("is-open");
  });
}

/**
 * 展开完整修改原因（与 Diff 展开同一交互：置顶当前卡、藏其它、下方详情）
 */
function openPdReasonExpand(ver, fullText) {
  const list = document.getElementById("pd-history-list");
  const box = document.getElementById("pd-reason-expand");
  const sidebar = document.querySelector(".pd-sidebar");
  if (!list || !box) return;

  // 关掉 Diff 且不要抢恢复（下面会 pin）
  const keepVer = ver;
  pdOpenDiffVer = null;
  const panel = document.getElementById("pd-diff-panel");
  const out = document.getElementById("pd-diff-out");
  if (panel) panel.hidden = true;
  if (out) {
    out.innerHTML = "";
    delete out.dataset.openVersion;
  }
  list.classList.remove("is-diff-open");
  sidebar?.classList.remove("is-diff-detail-open");

  if (!pinPdHistoryItem(keepVer)) return;

  box.innerHTML = `
    <div class="pd-reason-expand-head">
      <span title="v${escapeHtml(String(ver))} · 完整修改原因">v${escapeHtml(
        String(ver)
      )} · 完整修改原因</span>
      <button type="button" class="secondary btn-pd-collapse-reason">收起</button>
    </div>
    <div class="pd-reason-expand-body">${escapeHtml(fullText || "（无变更原因）")}</div>
  `;
  box.hidden = false;
  box.dataset.openVersion = String(ver);
  pdOpenReasonVer = String(ver);
  list.classList.add("is-reason-open");
  sidebar?.classList.add("is-reason-detail-open");

  list.querySelectorAll(".btn-pd-expand-reason").forEach((b) => {
    const open = String(b.dataset.v) === String(ver);
    b.textContent = open ? "收起" : "展开";
    b.classList.toggle("is-open", open);
  });

  box.querySelector(".btn-pd-collapse-reason")?.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    collapsePdReasonExpand();
  });
}

async function loadPromptDebugWorkbench(job) {
  const meta = document.getElementById("pd-job-meta");
  const verEl = document.getElementById("pd-active-ver");
  const editor = document.getElementById("pd-prompt-editor");
  const reasonEl = document.getElementById("pd-change-reason");
  if (!currentJobId) return;

  const j = job || (await api(`/jobs/${currentJobId}`));
  if (meta) {
    meta.textContent = `#${j.id}${j.name ? ` · ${j.name}` : ""} · ${j.status || "—"}`;
  }

  const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
  pdVersionsCache = versions || [];
  const active =
    pdVersionsCache.find((v) => v.is_active) ||
    pdVersionsCache[pdVersionsCache.length - 1];

  if (verEl) {
    verEl.textContent = active
      ? `当前激活 v${active.version}`
      : "暂无版本";
  }
  if (editor) {
    // 用户正在编辑时不覆盖
    if (editor.dataset.dirty !== "1") {
      editor.value = active?.prompt_text || "";
      editor.dataset.dirty = "0";
      editor.dataset.loadedVersion = active
        ? `${active.version}:${(active.prompt_text || "").length}`
        : "";
    }
  }
  if (reasonEl && reasonEl.dataset.dirty !== "1") {
    reasonEl.value = "";
    reasonEl.dataset.dirty = "0";
  }

  collapsePdDiff();
  collapsePdReasonExpand();
  renderPromptDebugHistory(pdVersionsCache);
}

function renderPromptDebugHistory(versions) {
  const el = document.getElementById("pd-history-list");
  if (!el) return;
  const list = [...(versions || [])].reverse();
  if (!list.length) {
    el.innerHTML = "<p class='hint'>暂无版本历史。编辑左侧提示词并保存即可产生 Diff 记录。</p>";
    return;
  }

  const reasonByVer = {};
  el.innerHTML = list
    .map((v, idx) => {
      const reason =
        v.change_reason ||
        v.improvement_suggestion?.suggestion_summary ||
        v.improvement_suggestion?.change_reason ||
        "（无变更原因）";
      reasonByVer[String(v.version)] = reason;
      const time = formatPdTime(v.created_at);
      // 展开按钮先渲染，布局后按是否真正溢出再显示/隐藏；非当前版显示回退
      return `<div class="pd-hist-item${v.is_active ? " active" : ""}" data-v="${v.version}" data-order="${idx}" title="点击展开 Diff">
        <div class="pd-hist-meta">
          <span class="pd-hist-ver">v${v.version}${v.is_active ? " · 当前" : ""}</span>
          <span class="pd-hist-time">${escapeHtml(time)}</span>
        </div>
        <div class="pd-hist-reason-label">修改原因</div>
        <div class="pd-hist-reason-row">
          <div class="pd-hist-reason" title="${escapeHtml(reason)}">${escapeHtml(reason)}</div>
          <button type="button" class="secondary btn-pd-expand-reason" data-v="${v.version}" title="展开完整修改原因" hidden>展开</button>
        </div>
        <div class="pd-hist-actions">
          ${
            v.is_active
              ? `<span class="pd-hist-current-tag">当前版本</span>`
              : `<button type="button" class="secondary btn-pd-rollback" data-v="${v.version}" title="回退为当前激活版本">回退</button>`
          }
        </div>
      </div>`;
    })
    .join("");

  // 点击卡片主体 → 展开 Diff（点展开/回退不触发）
  el.querySelectorAll(".pd-hist-item").forEach((item) => {
    item.addEventListener("click", async (e) => {
      if (
        e.target.closest(
          ".btn-pd-expand-reason, .btn-pd-rollback, .pd-hist-reason-row button, .pd-hist-actions button"
        )
      ) {
        return;
      }
      const ver = String(item.dataset.v || "");
      await openPromptDebugDiff(ver);
    });
  });

  // 展开：隐藏其它卡片 + Diff，当前卡片置顶，下方显示完整原因；收起恢复顺序
  el.querySelectorAll(".btn-pd-expand-reason").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (btn.hidden) return;
      const ver = String(btn.dataset.v || "");
      if (pdOpenReasonVer === ver) {
        collapsePdReasonExpand();
        return;
      }
      openPdReasonExpand(ver, reasonByVer[ver] || "（无变更原因）");
    });
  });

  el.querySelectorAll(".btn-pd-rollback").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      rollbackPromptDebugVersion(btn.dataset.v).catch((err) =>
        toast(err.message || String(err), true)
      );
    });
  });

  // 仅当修改原因在单行内显示不全（溢出）时才显示「展开」
  requestAnimationFrame(() => syncPdReasonExpandButtons(el));
}

/** 回退指定版本为当前激活 Prompt */
async function rollbackPromptDebugVersion(ver) {
  if (!currentJobId || ver == null || ver === "") return;
  const v = String(ver);
  const ok = window.confirm(
    `确认将 Prompt 回退到 v${v}？\n\n会以该版本内容生成新的激活版本（可继续 Diff）。`
  );
  if (!ok) return;
  await api(`/jobs/${currentJobId}/prompt-versions/${v}/rollback`, {
    method: "POST",
  });
  toast(`已回退：以 v${v} 为当前 Prompt`);
  const editor = document.getElementById("pd-prompt-editor");
  if (editor) editor.dataset.dirty = "0";
  const reasonEl = document.getElementById("pd-change-reason");
  if (reasonEl) reasonEl.dataset.dirty = "0";
  await loadPromptDebugWorkbench();
}

/**
 * 根据 .pd-hist-reason 是否被截断（scrollWidth > clientWidth）显示/隐藏「展开」。
 * 完整可见则不显示按钮。
 */
function syncPdReasonExpandButtons(root) {
  const list = root || document.getElementById("pd-history-list");
  if (!list) return;
  list.querySelectorAll(".pd-hist-item").forEach((item) => {
    if (item.hidden) return;
    const reasonEl = item.querySelector(".pd-hist-reason");
    const btn = item.querySelector(".btn-pd-expand-reason");
    if (!reasonEl || !btn) return;
    // 测量时确保按钮不占宽（hidden 已不参与布局）
    const overflow =
      reasonEl.scrollWidth > reasonEl.clientWidth + 1 ||
      reasonEl.scrollHeight > reasonEl.clientHeight + 1;
    btn.hidden = !overflow;
  });
}

// 侧栏宽度变化时重新判断是否省略
if (typeof window !== "undefined" && !window.__pdReasonExpandResizeBound) {
  window.__pdReasonExpandResizeBound = true;
  let _pdReasonResizeTimer = null;
  window.addEventListener("resize", () => {
    if (_pdReasonResizeTimer) clearTimeout(_pdReasonResizeTimer);
    _pdReasonResizeTimer = setTimeout(() => syncPdReasonExpandButtons(), 120);
  });
}

async function openPromptDebugDiff(ver) {
  if (!currentJobId || !ver) return;
  const panel = document.getElementById("pd-diff-panel");
  const out = document.getElementById("pd-diff-out");
  const cap = document.getElementById("pd-diff-caption");
  const list = document.getElementById("pd-history-list");
  const sidebar = document.querySelector(".pd-sidebar");
  if (!panel || !out || !list) return;

  // 再次点击同一版本 → 收起，恢复列表
  if (String(pdOpenDiffVer) === String(ver) && !panel.hidden) {
    collapsePdDiff();
    return;
  }

  try {
    // 关掉修改原因详情（不恢复列表，下面会 pin）
    pdOpenReasonVer = null;
    const reasonBox = document.getElementById("pd-reason-expand");
    if (reasonBox) {
      reasonBox.hidden = true;
      reasonBox.innerHTML = "";
      delete reasonBox.dataset.openVersion;
    }
    list.classList.remove("is-reason-open");
    sidebar?.classList.remove("is-reason-detail-open");
    list.querySelectorAll(".btn-pd-expand-reason").forEach((btn) => {
      btn.textContent = "展开";
      btn.classList.remove("is-open");
    });

    const d = await api(`/jobs/${currentJobId}/prompt-versions/${ver}/diff`);

    // 与「修改原因」相同：当前卡置顶，隐藏其它历史
    if (!pinPdHistoryItem(ver)) {
      toast("未找到对应版本卡片", true);
      return;
    }

    if (cap) {
      cap.textContent = `v${d.parent_version ?? 0} → v${d.version}`;
      cap.title = cap.textContent;
    }
    const reasonLine = document.getElementById("pd-diff-reason");
    const reasonText = (d.change_reason || "").trim();
    if (reasonLine) {
      if (reasonText) {
        reasonLine.hidden = false;
        reasonLine.textContent = reasonText;
        reasonLine.dataset.full = reasonText;
        reasonLine.classList.remove("is-expanded");
        reasonLine.title = "点击展开 / 收起完整修改原因";
        reasonLine.onclick = (ev) => {
          ev.stopPropagation();
          const expanded = reasonLine.classList.toggle("is-expanded");
          reasonLine.title = expanded
            ? "点击收起"
            : "点击展开完整修改原因";
        };
      } else {
        reasonLine.hidden = true;
        reasonLine.textContent = "";
        reasonLine.onclick = null;
      }
    }
    out.innerHTML = renderDiffLinesHtml(d.diff);
    out.dataset.openVersion = String(ver);
    panel.hidden = false;
    pdOpenDiffVer = String(ver);
    list.classList.add("is-diff-open");
    sidebar?.classList.add("is-diff-detail-open");

    // Diff 顶栏回退：非当前激活版才显示
    const rbBtn = document.getElementById("btn-pd-rollback-diff");
    const active = (pdVersionsCache || []).find((x) => x.is_active);
    const isActive =
      active != null && String(active.version) === String(ver);
    if (rbBtn) {
      rbBtn.hidden = !!isActive;
      rbBtn.dataset.v = String(ver);
    }
  } catch (e) {
    toast(e.message || "加载 Diff 失败", true);
  }
}

document.getElementById("btn-pd-rollback-diff")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  const btn = e.currentTarget;
  const ver = btn?.dataset?.v;
  if (!ver || btn.hidden) return;
  rollbackPromptDebugVersion(ver).catch((err) =>
    toast(err.message || String(err), true)
  );
});

document.getElementById("btn-pd-hide-diff")?.addEventListener("click", () => {
  collapsePdDiff();
});

document.getElementById("btn-pd-back")?.addEventListener("click", () => {
  currentJobId = null;
  collapsePdDiff();
  collapsePdReasonExpand();
  goView("templates");
});

document.getElementById("pd-prompt-editor")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});
document.getElementById("pd-change-reason")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});

document.getElementById("btn-pd-save")?.addEventListener("click", async () => {
  if (!currentJobId) {
    toast("请先打开或新建一个提示词调试任务", true);
    return;
  }
  const editor = document.getElementById("pd-prompt-editor");
  const reasonEl = document.getElementById("pd-change-reason");
  const text = (editor?.value || "").trim();
  if (!text) {
    toast("提示词不能为空", true);
    return;
  }
  try {
    const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
    const active =
      versions.find((v) => v.is_active) || versions[versions.length - 1];
    if (active && (active.prompt_text || "").trim() === text) {
      toast("与当前版本无差异，未保存", true);
      return;
    }
    const reason = (reasonEl?.value || "").trim() || "提示词调试修改";
    const pv = await api(`/jobs/${currentJobId}/prompt-versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt_text: text,
        change_reason: reason,
      }),
    });
    if (editor) {
      editor.dataset.dirty = "0";
      editor.dataset.loadedVersion = `${pv.version}:${text.length}`;
    }
    if (reasonEl) {
      reasonEl.dataset.dirty = "0";
      reasonEl.value = "";
    }
    toast(`已保存 v${pv.version}`);
    // 强制重载编辑器为最新激活版
    const editor2 = document.getElementById("pd-prompt-editor");
    if (editor2) editor2.dataset.dirty = "0";
    await loadPromptDebugWorkbench();
  } catch (e) {
    toast(e.message, true);
  }
});

/**
 * 从各任务类型模块快速创建 Job（统一进 Job 列表）
 * @param {string} jobType
 * @param {{ name?: string }} [opts] 若传入 name 则不再弹窗（提示词调试页用输入框）
 */
async function createTypedJob(jobType, opts = {}) {
  const meta = JOB_TYPE_META[jobType] || JOB_TYPE_META.annotation;
  const defaultName = `${meta.label} ${new Date()
    .toISOString()
    .slice(0, 16)
    .replace("T", " ")}`;
  let trimmed = String(opts.name ?? "").trim();
  if (!trimmed) {
    // 未从页面输入框传入名称时，再弹窗（清洗台等占位入口）
    if (opts.name != null && jobType === "prompt_debug") {
      toast("请填写新建任务名称", true);
      document.getElementById("prompt-debug-new-name")?.focus();
      return;
    }
    const name = window.prompt(`新建${meta.label}任务名称：`, defaultName);
    if (name == null) return;
    trimmed = String(name).trim();
  }
  if (!trimmed) {
    toast("名称不能为空", true);
    return;
  }
  const body = {
    name: trimmed,
    job_type: jobType,
    policy_rules:
      jobType === "annotation"
        ? ""
        : jobType === "prompt_debug"
          ? trimmed // 仅作任务记录，不写入 Prompt 正文
          : `（${meta.label}任务）${trimmed}`,
  };
  // 提示词调试：初始 Prompt 置空，避免编辑框出现「（任务）名称 时间」占位
  if (jobType === "prompt_debug") {
    body.initial_prompt = "";
  }
  if (jobType === "annotation") {
    goView("create");
    toast("请搜索并点选模板，以创建数据标注任务");
    return;
  }
  const job = await api("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  toast(`已创建「${meta.label}」任务 #${job.id}，已加入 Job 列表`);
  await openJob(job.id);
}

document.querySelectorAll(".btn-create-typed-job").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = btn.getAttribute("data-job-type");
    if (!t) return;
    createTypedJob(t).catch((e) => toast(e.message, true));
  });
});

document.getElementById("btn-back").onclick = () => {
  if (pollTimer) clearInterval(pollTimer);
  const wasPromptDebug = document
    .getElementById("view-prompt-debug")
    ?.classList.contains("active");
  currentJobId = null;
  resetSidebarOverlays();
  collapsePdDiff?.();
  collapsePdReasonExpand?.();
  // 标注详情 → 数据标注；提示词调试 → 模板库任务列表
  goView(wasPromptDebug ? "templates" : "create");
};

/** 状态副标题：人工介入中展示当前子步骤 */
const STATUS_PHASE_LABEL = {
  CREATED: "上传数据与 Gold 后开始",
  GOLD_OPTIMIZING: "正在优化提示词",
  GOLD_READY: "可进入全量标注",
  GOLD_FAILED: "请改提示词或重新标注",
  ROUND_LABELING: "小模型标注数据集",
  AWAIT_DECISION_THRESHOLD: "设判定阈值",
  AWAIT_CONFIDENCE_BINS: "设置信度分层",
  AWAIT_QC: "核对 QC 样本",
  AWAIT_DECISION: "决定是否再开一轮",
  PROMPT_IMPROVING: "改写 Prompt",
  COMPLETED: "本轮流程已结束",
  FAILED: "出错，可重试",
  BUDGET_EXCEEDED: "Token 用尽",
  CANCELLED: "已终止，不可恢复",
  ABORTED: "已中止，可重新标注",
};

function kpiCard(label, value, sub, opts = {}) {
  const clickable = opts.clickable ? " kpi-card-clickable" : "";
  const idAttr = opts.id ? ` id="${escapeHtml(opts.id)}"` : "";
  const title = opts.title ? ` title="${escapeHtml(opts.title)}"` : "";
  // value 可为 HTML（如 status badge）；sub 默认转义，opts.subHtml 时原样插入（内嵌 input）
  const subHtml = sub
    ? `<div class="kpi-sub">${opts.subHtml ? sub : escapeHtml(sub)}</div>`
    : "";
  return `<div class="kpi-card${clickable}"${idAttr}${title} role="${
    opts.clickable ? "button" : "group"
  }" tabindex="${opts.clickable ? "0" : "-1"}">
    <div class="kpi-label">${escapeHtml(label)}</div>
    <div class="kpi-value">${value}</div>
    ${subHtml}
  </div>`;
}

async function renderStatusKpis(job) {
  const grid = document.getElementById("detail-kpis");
  const progWrap = document.getElementById("detail-kpi-progress");
  const progLabel = document.getElementById("detail-kpi-progress-label");
  const progFill = document.getElementById("detail-kpi-progress-fill");
  const msgEl = document.getElementById("detail-kpi-msg");
  const errEl = document.getElementById("detail-error");
  if (!grid) return;

  let live = null;
  try {
    live = await api(`/jobs/${currentJobId}/live-progress`);
  } catch (_) {}

  const status = job.status || live?.status || "—";
  // 人工介入类：主状态统一，副标题展示当前子步骤（设阈值/分层/QC/决策…）
  const phaseText = HUMAN_INTERVENTION_STATUSES.has(status)
    ? STATUS_PHASE_LABEL[status] || "等待人工操作"
    : STATUS_PHASE_LABEL[status] || live?.phase || status;
  const goldAcc =
    live?.gold?.last_metrics?.accuracy ?? job.last_gold_metrics?.accuracy;
  const goldTarget = live?.gold?.target_accuracy ?? job.target_accuracy;
  // 迭代：只信 job / live 列字段；0 是合法值（新 loop 起点），不要用 gold_log 反推
  // 失败停在 max/max 时保持显示，直到点「重新标注」才变 0
  const goldIterRaw = live?.gold?.iteration ?? job.gold_iteration;
  const goldIter =
    goldIterRaw != null && goldIterRaw !== ""
      ? Number(goldIterRaw)
      : 0;
  const goldMax = Number(job.max_gold_iterations ?? 3) || 3;
  const promptV = live?.gold?.active_prompt_version;
  const fl = live?.full_label || {};
  const labeled = Number(fl.labeled ?? 0) || 0;
  // 中止后不要用 annotation_count 当 total，否则 0 条进度也会被算成满格错觉
  const total = Number(
    fl.total != null && fl.total !== ""
      ? fl.total
      : fl.frozen
        ? 0
        : job.annotation_count ?? 0
  ) || 0;
  let pct = Number(fl.percent ?? 0) || 0;
  if (!pct && total > 0 && labeled > 0) {
    pct = Math.round((100 * labeled) / total);
  }
  // 中止且未开始全量：强制 0%
  if ((status === "ABORTED" || fl.frozen) && labeled <= 0) {
    pct = 0;
  }
  const roundNo =
    live?.current_round_no ?? fl.round_no ?? job.current_round_no ?? 0;

  // 轮询刷新时保留用户正在编辑的输入，避免焦点/草稿被冲掉
  const prevTarget = document.getElementById("kpi-input-target-acc");
  const prevMax = document.getElementById("kpi-input-gold-max");
  const focusId = document.activeElement?.id;
  const draftTarget =
    prevTarget &&
    (focusId === "kpi-input-target-acc" || prevTarget.dataset.dirty === "1")
      ? prevTarget.value
      : null;
  const draftMax =
    prevMax &&
    (focusId === "kpi-input-gold-max" || prevMax.dataset.dirty === "1")
      ? prevMax.value
      : null;
  const restoreFocus = focusId;

  const tgtNum =
    goldTarget != null && goldTarget !== ""
      ? Number(goldTarget)
      : 1;
  const tgtDisplay = Number.isFinite(tgtNum) ? String(tgtNum) : "1";
  const maxDisplay = String(goldMax);
  // 仅首次开跑前 / 可重新标注时允许改 Gold 参数；loop 中间只读
  const goldParamsEditable = canEditGoldParams(status);
  const disAttr = goldParamsEditable ? "" : " disabled";
  const lockHint = goldParamsEditable
    ? "首次开跑或重新标注前可改；回车/失焦保存"
    : "loop 进行中不可改，待重新标注时再设";

  // 三列两行（6 项）：Gold 准确率 / 迭代 内嵌数字框；准确率卡片点击仍展开 Gold 明细
  grid.innerHTML = [
    kpiCard("状态", statusBadge(status), phaseText),
    kpiCard("数据量", String(job.annotation_count ?? 0), `Gold ${job.gold_count ?? 0} 条`),
    kpiCard(
      "Gold 准确率",
      goldAcc != null ? `${(goldAcc * 100).toFixed(1)}%` : "—",
      `<span class="kpi-edit-row">目标 <input type="number" class="kpi-inline-input${
        goldParamsEditable ? "" : " is-locked"
      }" id="kpi-input-target-acc" min="0" max="1" step="0.01" value="${escapeHtml(
        draftTarget != null && goldParamsEditable ? draftTarget : tgtDisplay
      )}"${disAttr} title="${escapeHtml(lockHint)}" /></span>`,
      {
        id: "kpi-gold-acc",
        clickable: true,
        subHtml: true,
        title: goldParamsEditable
          ? "点击展开 Gold Test 明细；目标框可设目标准确率（仅开跑/重标前）"
          : "点击展开 Gold Test 明细；目标准确率 loop 中锁定",
      }
    ),
    kpiCard(
      "Gold 迭代",
      `<span class="kpi-iter-value">${goldIter}&nbsp;/&nbsp;<input type="number" class="kpi-inline-input kpi-inline-input-max${
        goldParamsEditable ? "" : " is-locked"
      }" id="kpi-input-gold-max" min="1" max="50" step="1" value="${escapeHtml(
        draftMax != null && goldParamsEditable ? draftMax : maxDisplay
      )}"${disAttr} title="${escapeHtml(lockHint)}" /></span>`,
      promptV != null
        ? `Prompt v${promptV}`
        : goldParamsEditable
          ? "最大迭代可编辑"
          : "最大迭代已锁定",
      {
        id: "kpi-gold-iter",
        clickable: goldParamsEditable,
        title: lockHint,
      }
    ),
    kpiCard(
      "全量进度",
      total ? `${labeled}/${total}` : "—",
      total ? `${Number(pct).toFixed(1)}%` : "未开始"
    ),
    kpiCard("当前轮次", String(roundNo || "—"), "多轮标注"),
  ].join("");

  bindGoldParamInputs(goldParamsEditable);

  const goldKpi = document.getElementById("kpi-gold-acc");
  if (goldKpi) {
    goldKpi.onclick = (e) => {
      // 点输入框只编辑，不触发展开明细
      if (e.target.closest("input, .kpi-inline-input, .kpi-edit-row")) return;
      toggleGoldEvalPanel();
    };
    goldKpi.onkeydown = (e) => {
      if (e.target.closest?.("input")) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleGoldEvalPanel();
      }
    };
  }
  const goldIterKpi = document.getElementById("kpi-gold-iter");
  if (goldIterKpi && goldParamsEditable) {
    goldIterKpi.onclick = (e) => {
      if (e.target.closest("input, .kpi-inline-input")) return;
      document.getElementById("kpi-input-gold-max")?.focus();
    };
  }

  if (
    goldParamsEditable &&
    (restoreFocus === "kpi-input-target-acc" || restoreFocus === "kpi-input-gold-max")
  ) {
    const el = document.getElementById(restoreFocus);
    if (el && !el.disabled) {
      el.focus();
      try {
        const len = String(el.value || "").length;
        el.setSelectionRange(len, len);
      } catch (_) {}
    }
  }

  // 动态进度条：Gold 优化 / 全量标注 时展示
  const showBar =
    status === "GOLD_OPTIMIZING" ||
    status === "ROUND_LABELING" ||
    status === "PROMPT_IMPROVING" ||
    (total > 0 && labeled > 0 && labeled < total);
  if (progWrap) {
    if (showBar || (total > 0 && labeled >= total && status === "AWAIT_DECISION_THRESHOLD")) {
      progWrap.hidden = false;
      let barPct = 0;
      let barText = "";
      if (status === "GOLD_OPTIMIZING") {
        barPct = goldMax ? Math.min(100, (100 * goldIter) / goldMax) : 0;
        barText = `Gold 优化中：第 ${goldIter}/${goldMax} 轮${
          goldAcc != null ? ` · 当前准确率 ${(goldAcc * 100).toFixed(1)}%` : ""
        }`;
      } else {
        barPct = Math.min(100, Number(pct) || 0);
        barText =
          fl.message ||
          `全量标注 ${labeled}/${total || "—"}（${barPct.toFixed(1)}%）`;
      }
      if (progFill) progFill.style.width = `${barPct}%`;
      if (progLabel) progLabel.textContent = barText;
    } else {
      progWrap.hidden = true;
    }
  }

  if (msgEl) {
    // 仅显示 Gold 优化过程中的动态说明，不展示 CREATED 等原始状态
    const latest = live?.gold?.latest?.message || "";
    msgEl.textContent = latest;
    msgEl.hidden = !latest;
  }
  if (errEl) {
    if (job.error_message) {
      errEl.hidden = false;
      errEl.textContent = job.error_message;
    } else {
      errEl.hidden = true;
      errEl.textContent = "";
    }
  }
}

function syncRoundRangeDefaults(job) {
  const roundNo = Math.max(1, parseInt(job?.current_round_no || "1", 10) || 1);
  const fromEl = document.getElementById("fin-from");
  const toEl = document.getElementById("fin-to");
  // 未手动改过时，默认跟随当前轮次
  if (fromEl && fromEl.dataset.dirty !== "1") {
    fromEl.value = String(roundNo);
  }
  if (toEl && toEl.dataset.dirty !== "1") {
    toEl.value = String(roundNo);
  }
}

function getFinalizeRoundRange() {
  const fromEl = document.getElementById("fin-from");
  const toEl = document.getElementById("fin-to");
  let fromR = parseInt(fromEl?.value || "1", 10);
  let toR = parseInt(toEl?.value || "1", 10);
  if (Number.isNaN(fromR) || fromR < 1) fromR = 1;
  if (Number.isNaN(toR) || toR < 1) toR = fromR;
  if (toR < fromR) {
    const t = fromR;
    fromR = toR;
    toR = t;
  }
  return { from_round: fromR, to_round: toR };
}

function syncActivePromptEditor(versions) {
  const active =
    (versions || []).find((v) => v.is_active) ||
    (versions || [])[(versions || []).length - 1];
  const editor = document.getElementById("active-prompt-editor");
  const reasonEl = document.getElementById("change-reason-input");
  const verEl = document.getElementById("active-prompt-ver");
  if (!active) {
    if (verEl) verEl.textContent = "暂无版本";
    return;
  }
  const curKey = `${active.version}:${(active.prompt_text || "").length}`;
  const reason =
    active.change_reason ||
    active.improvement_suggestion?.suggestion_summary ||
    active.improvement_suggestion?.change_reason ||
    "";
  const src = active.improvement_suggestion?.source || "";
  if (verEl) {
    const srcLabel =
      src === "qc_llm" || src === "qc_llm_gold" || src === "qc_llm_initial"
        ? "质检大模型"
        : src === "human_edit"
          ? "人工"
          : active.version <= 1
            ? "初始"
            : "";
    verEl.textContent = `当前 v${active.version}${
      srcLabel ? ` · ${srcLabel}` : ""
    }${active.is_active ? " · 激活" : ""}`;
  }
  if (editor) {
    const prev = editor.dataset.loadedVersion;
    // 用户正在手改时不覆盖；一旦有新版本（Gold/QC 优化）且未 dirty 则实时刷新
    if (editor.dataset.dirty !== "1" && prev !== curKey) {
      editor.value = active.prompt_text || "";
      editor.dataset.loadedVersion = curKey;
      editor.dataset.dirty = "0";
    } else if (!prev) {
      editor.value = active.prompt_text || "";
      editor.dataset.loadedVersion = curKey;
      editor.dataset.dirty = "0";
    }
  }
  if (reasonEl && reasonEl.dataset.dirty !== "1") {
    reasonEl.value = reason;
    reasonEl.dataset.loadedVersion = curKey;
  }
}

async function refreshDetail() {
  if (!currentJobId) return;
  const j = await api(`/jobs/${currentJobId}`);
  await renderStatusKpis(j);
  syncRoundRangeDefaults(j);

  const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
  // 历史面板打开且未展开 Diff 时才刷新列表，避免轮询冲掉正在看的 Diff
  if (
    typeof historySidebarOpen !== "undefined" &&
    historySidebarOpen &&
    !document.getElementById("diff-out")?.dataset.openVersion
  ) {
    renderPromptHistoryList(versions);
  }
  // 底部「提示词修改」：始终展示当前激活版；质检大模型落新版本后随轮询实时刷新
  syncActivePromptEditor(versions);

  // QC 列表：打开侧栏时拉取；中止后也会回退到上一轮有样本的 QC（不因轮次+1 变空）
  // 已加载且非强制刷新时不重绘，保留上次展示
  if (typeof qcSidebarOpen !== "undefined" && qcSidebarOpen) {
    try {
      const panel = document.getElementById("qc-panel");
      const need =
        !panel ||
        panel.dataset.loaded !== "1" ||
        panel.dataset.forceRefresh === "1";
      if (need) {
        const rn = Math.max(1, j.current_round_no || 1);
        // fallback=true：当前轮无样本则用最近一轮 QC
        const samples = await api(
          `/jobs/${currentJobId}/rounds/${rn}/qc-samples?fallback=true`
        );
        // 中止时即使空列表也不清空已渲染内容
        if (samples?.length || j.status !== "ABORTED" || panel?.dataset.loaded !== "1") {
          renderQc(samples || [], j);
        }
        if (panel) delete panel.dataset.forceRefresh;
      }
    } catch (_) {
      // 拉取失败：保留已有 QC 面板内容
    }
  }
  // 中止态：禁用阈值 / 分层按钮
  syncAbortActionButtons(j);
}

function syncAbortActionButtons(job) {
  const aborted = job?.status === "ABORTED";
  const btnThr = document.getElementById("btn-apply-threshold");
  const btnBins = document.getElementById("btn-apply-bins");
  const btnRec = document.getElementById("btn-recommend-bins");
  for (const btn of [btnThr, btnBins, btnRec]) {
    if (!btn) continue;
    btn.disabled = !!aborted;
    btn.title = aborted
      ? "任务已中止，无法操作；请先点「重新标注」"
      : "";
  }
}

function collapsePromptDiff(listEl) {
  const out = document.getElementById("diff-out");
  if (out) {
    out.hidden = true;
    out.innerHTML = "";
    delete out.dataset.openVersion;
  }
  const el = listEl || document.getElementById("prompt-timeline");
  el?.querySelectorAll(".btn-ph-diff").forEach((btn) => {
    btn.textContent = "查看 Diff";
    btn.classList.remove("is-diff-open");
  });
  // 恢复显示全部版本，版本列表恢复高度（若未在展开变更原因）
  const reasonOpen = document.getElementById("ph-reason-expand");
  const reasonIsOpen = reasonOpen && !reasonOpen.hidden;
  if (!reasonIsOpen) {
    el?.querySelectorAll(".ph-item").forEach((item) => {
      item.hidden = false;
      item.classList.remove("ph-item-focused");
    });
  }
  const panel = document.getElementById("prompt-history-panel");
  const overlay = document.getElementById("detail-history-overlay");
  panel?.classList.remove("is-diff-open");
  overlay?.classList.remove("is-diff-open");
}

function focusPromptVersionForDiff(listEl, ver) {
  const el = listEl || document.getElementById("prompt-timeline");
  if (!el) return;
  el.querySelectorAll(".ph-item").forEach((item) => {
    const match = String(item.dataset.v) === String(ver);
    item.hidden = !match;
    item.classList.toggle("ph-item-focused", match);
  });
  el.querySelectorAll(".btn-ph-diff").forEach((btn) => {
    const open = String(btn.dataset.v) === String(ver);
    btn.textContent = open ? "收起 Diff" : "查看 Diff";
    btn.classList.toggle("is-diff-open", open);
  });
  document.getElementById("prompt-history-panel")?.classList.add("is-diff-open");
  document.getElementById("detail-history-overlay")?.classList.add("is-diff-open");
}

function collapsePromptReason(listEl) {
  const overlay = document.getElementById("detail-history-overlay");
  const panel = document.getElementById("prompt-history-panel");
  const expand = document.getElementById("ph-reason-expand");
  overlay?.classList.remove("is-reason-open");
  panel?.classList.remove("is-reason-open");
  if (expand) {
    expand.hidden = true;
    expand.innerHTML = "";
    delete expand.dataset.openVersion;
  }
  (listEl || document.getElementById("prompt-timeline"))
    ?.querySelectorAll(".ph-item")
    .forEach((item) => {
      item.hidden = false;
      item.classList.remove("ph-item-reason-open");
    });
  (listEl || document.getElementById("prompt-timeline"))
    ?.querySelectorAll(".btn-ph-expand-reason")
    .forEach((btn) => {
      btn.textContent = "展开";
      btn.classList.remove("is-open");
    });
}

function renderPromptHistoryList(versions) {
  const el = document.getElementById("prompt-timeline");
  if (!el) return;
  collapsePromptDiff(el);
  collapsePromptReason(el);
  if (!versions || !versions.length) {
    el.innerHTML = "<p class='hint'>暂无 Prompt 版本</p>";
    return;
  }
  // 新版本在上；展示变更原因摘要，全文在主栏底部编辑器
  const list = [...versions].reverse();
  const reasonByVer = {};
  el.innerHTML = list
    .map((v) => {
      const acc =
        v.metrics?.accuracy != null
          ? `${(Number(v.metrics.accuracy) * 100).toFixed(1)}%`
          : "—";
      // 变更原因：优先 change_reason，其次 improvement_suggestion 摘要
      const suggest =
        v.improvement_suggestion?.suggestion_summary ||
        v.improvement_suggestion?.change_reason ||
        "";
      const feedbackNote = v.change_reason || suggest || "（无变更原因）";
      reasonByVer[String(v.version)] = feedbackNote;
      // 略长即显示「展开」（约两行截断阈值）
      const needExpand = String(feedbackNote).length > 36;
      return `<div class="ph-item${v.is_active ? " active" : ""}" data-v="${v.version}">
        <div class="ph-meta">
          <span class="ph-ver">v${v.version}${v.is_active ? " · 当前" : ""}</span>
          <span class="ph-acc">Gold Acc ${acc}</span>
        </div>
        <div class="ph-feedback-label">变更原因</div>
        <div class="ph-reason-row">
          <div class="ph-reason">${escapeHtml(feedbackNote)}</div>
          ${
            needExpand
              ? `<button type="button" class="secondary btn-ph-expand-reason" data-v="${v.version}">展开</button>`
              : ""
          }
        </div>
        <div class="ph-actions">
          <button type="button" class="secondary btn-ph-diff" data-v="${v.version}">查看 Diff</button>
          ${
            v.is_active
              ? ""
              : `<button type="button" class="secondary btn-ph-rb" data-v="${v.version}">回滚为当前版本</button>`
          }
        </div>
      </div>`;
    })
    .join("");

  el.querySelectorAll(".btn-ph-expand-reason").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const ver = String(b.dataset.v || "");
      const expand = document.getElementById("ph-reason-expand");
      const overlay = document.getElementById("detail-history-overlay");
      const panel = document.getElementById("prompt-history-panel");
      if (!expand) return;

      // 再次点击 → 收起
      if (expand.dataset.openVersion === ver && !expand.hidden) {
        collapsePromptReason(el);
        return;
      }

      // 展开时收起 Diff，避免两块争空间
      collapsePromptDiff(el);

      const full = reasonByVer[ver] || "（无变更原因）";
      expand.innerHTML = `
        <div class="ph-reason-expand-head">
          <span>v${escapeHtml(ver)} · 变更原因</span>
          <button type="button" class="secondary btn-ph-collapse-reason">收起</button>
        </div>
        <div class="ph-reason-expand-body">${escapeHtml(full)}</div>
      `;
      expand.hidden = false;
      expand.dataset.openVersion = ver;
      overlay?.classList.add("is-reason-open");
      panel?.classList.add("is-reason-open");

      // 只保留当前版本卡片，其余挤压/隐藏
      el.querySelectorAll(".ph-item").forEach((item) => {
        const match = String(item.dataset.v) === ver;
        item.hidden = !match;
        item.classList.toggle("ph-item-reason-open", match);
      });
      el.querySelectorAll(".btn-ph-expand-reason").forEach((btn) => {
        const open = String(btn.dataset.v) === ver;
        btn.textContent = open ? "收起" : "展开";
        btn.classList.toggle("is-open", open);
      });

      expand.querySelector(".btn-ph-collapse-reason")?.addEventListener("click", (e) => {
        e.stopPropagation();
        collapsePromptReason(el);
      });
    };
  });

  el.querySelectorAll(".btn-ph-diff").forEach((b) => {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      const ver = String(b.dataset.v || "");
      const out = document.getElementById("diff-out");
      if (!out) return;

      // 再次点击同一版本 → 收起 Diff
      if (out.dataset.openVersion === ver && !out.hidden) {
        collapsePromptDiff(el);
        return;
      }

      try {
        // 展开 Diff 前收起变更原因
        collapsePromptReason(el);
        const d = await api(
          `/jobs/${currentJobId}/prompt-versions/${ver}/diff`
        );
        const rows = (d.diff || "(与上一版无差异或为第一版)")
          .split("\n")
          .map((line) => {
            let kind = "ctx";
            if (line.startsWith("+++") || line.startsWith("---")) kind = "file";
            else if (line.startsWith("@@")) kind = "hunk";
            else if (line.startsWith("+")) kind = "add";
            else if (line.startsWith("-")) kind = "del";
            return `<div class="tmpl-diff-line ${kind}">${escapeHtml(line || " ")}</div>`;
          })
          .join("");
        out.innerHTML =
          `<div class="prompt-diff-caption">v${d.parent_version ?? 0} → v${d.version}${
            d.change_reason ? " · " + escapeHtml(d.change_reason) : ""
          }</div>` + rows;
        out.hidden = false;
        out.dataset.openVersion = ver;

        // 只保留当前版本卡片，列表高度被 Diff 挤压
        focusPromptVersionForDiff(el, ver);
        // 不自动 scrollIntoView，避免带动侧栏滚动
      } catch (e) {
        toast(e.message, true);
      }
    };
  });

  el.querySelectorAll(".btn-ph-rb").forEach((b) => {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await api(
          `/jobs/${currentJobId}/prompt-versions/${b.dataset.v}/rollback`,
          { method: "POST" }
        );
        toast(`已回滚：以 v${b.dataset.v} 为当前 Prompt`);
        refreshDetail();
        // 回滚后若历史仍打开，刷新列表
        if (historySidebarOpen) {
          const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
          renderPromptHistoryList(versions);
        }
      } catch (e) {
        toast(e.message, true);
      }
    };
  });
}

// 历史版本：临时占用右侧设置栏
document.getElementById("btn-prompt-history")?.addEventListener("click", async () => {
  try {
    if (historySidebarOpen) {
      setHistorySidebarOpen(false);
      return;
    }
    if (currentJobId) {
      const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
      renderPromptHistoryList(versions);
    }
    setHistorySidebarOpen(true);
  } catch (e) {
    toast(e.message, true);
  }
});

// QC 交互状态：展开判定依据的 seq、人工改过的 label（跨重绘保留）
const qcUiState = {
  reasonSeqs: new Set(),
  labels: new Map(),
};

function sortQcSamples(samples) {
  // High → Low 置信度；同分按 id/seq 升序
  return [...(samples || [])].sort((a, b) => {
    const ca =
      a.confidence != null && !Number.isNaN(Number(a.confidence))
        ? Number(a.confidence)
        : -1;
    const cb =
      b.confidence != null && !Number.isNaN(Number(b.confidence))
        ? Number(b.confidence)
        : -1;
    if (ca !== cb) return cb - ca;
    return (Number(a.seq) || 0) - (Number(b.seq) || 0);
  });
}

function setQcBodyMode(body, payload, mode) {
  if (!body) return;
  body.dataset.mode = mode;
  const labelEl = body.querySelector(".qc-body-label");
  const contentEl = body.querySelector(".qc-body-content");
  if (mode === "reason") {
    if (labelEl) labelEl.textContent = payload.field || "round1_reasoning";
    if (contentEl) contentEl.textContent = payload.reason || "";
    body.classList.add("is-reason");
  } else {
    if (labelEl) labelEl.textContent = "正文";
    if (contentEl) contentEl.textContent = payload.text || "";
    body.classList.remove("is-reason");
  }
}

function renderQc(samples, job) {
  const panel = document.getElementById("qc-panel");
  if (!panel) return;
  // 记录滚动位置，避免重绘后跳动
  const prevScroll = panel.scrollTop;

  if (!samples || !samples.length) {
    panel.innerHTML = "<p class='hint'>暂无 QC 样本。完成分层抽取后点「显示 QC」。</p>";
    panel.dataset.loaded = "1";
    return;
  }
  const sorted = sortQcSamples(samples);
  const pos = String(job.label_schema?.positive_label ?? "1");
  const neg = String(job.label_schema?.negative_label ?? "0");
  const payloadBySeq = {};
  panel.innerHTML = sorted
    .map((s) => {
      const conf =
        s.confidence != null && !Number.isNaN(Number(s.confidence))
          ? Number(s.confidence).toFixed(3)
          : "—";
      const seqKey = String(s.seq);
      const predLabel = String(s.pred_label ?? neg);
      const curLabel = String(
        qcUiState.labels.has(seqKey)
          ? qcUiState.labels.get(seqKey)
          : s.human_label ?? s.pred_label ?? neg
      );
      // 重复提交判定基线：已审用 human_label，否则用预测；无相对基线的改动则忽略
      const baselineLabel =
        s.reviewed && s.human_label != null && String(s.human_label) !== ""
          ? String(s.human_label)
          : predLabel;
      const field =
        s.reasoning_field ||
        (s.reasoning_round != null
          ? `round${s.reasoning_round}_reasoning`
          : "round1_reasoning");
      const reason =
        (s.reasoning || "").trim() || `（无内容：${field} 为空）`;
      const text = s.text || "";
      payloadBySeq[seqKey] = { text, reason, field, pred: s.pred_label };
      return `<div class="qc-item" data-seq="${s.seq}">
        <div class="meta">
          <span>id/seq=${s.seq} · ${escapeHtml(s.bin_name || "")}</span>
          ${s.reviewed ? '<span class="qc-badge">已审</span>' : ""}
        </div>
        <div class="qc-scores">
          <span class="qc-conf">置信度 <strong>${conf}</strong></span>
          <span class="qc-pred">预测 <strong>${escapeHtml(
            s.pred_label || "—"
          )}</strong></span>
        </div>
        <div class="qc-body" data-mode="text" title="点击切换：正文 ↔ 判定依据">
          <div class="qc-body-label">正文</div>
          <div class="qc-body-content">${escapeHtml(text)}</div>
        </div>
        <div class="qc-annotate-row">
          <span class="qc-annotate-caption">标注 label</span>
          <button type="button" class="qc-label-btn" data-label="${escapeHtml(
            curLabel
          )}" data-orig-label="${escapeHtml(
            baselineLabel
          )}" data-baseline-label="${escapeHtml(
            baselineLabel
          )}" data-pred="${escapeHtml(
            predLabel
          )}" data-pos="${escapeHtml(pos)}" data-neg="${escapeHtml(neg)}"
            title="点击切换 0/1；仅相对当前状态有修改才会提交；重复无改动提交将被忽略">
            ${escapeHtml(curLabel)}
          </button>
        </div>
      </div>`;
    })
    .join("");

  panel.dataset.loaded = "1";

  panel.querySelectorAll(".qc-item").forEach((item) => {
    const body = item.querySelector(".qc-body");
    const labelBtn = item.querySelector(".qc-label-btn");
    const seqKey = String(item.dataset.seq);
    const payload = payloadBySeq[seqKey] || {};

    // 恢复此前展开的判定依据（不会自动收起）
    if (qcUiState.reasonSeqs.has(seqKey)) {
      setQcBodyMode(body, payload, "reason");
    }

    body?.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const open = body.dataset.mode !== "reason";
      if (open) {
        qcUiState.reasonSeqs.add(seqKey);
        setQcBodyMode(body, payload, "reason");
      } else {
        // 仅再次点击同一区域才收起
        qcUiState.reasonSeqs.delete(seqKey);
        setQcBodyMode(body, payload, "text");
      }
    });

    // 仅点击按钮才切换 label（0↔1）
    labelBtn?.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const p = labelBtn.dataset.pos || "1";
      const n = labelBtn.dataset.neg || "0";
      const cur = String(labelBtn.dataset.label ?? n);
      const next = cur === p ? n : p;
      labelBtn.dataset.label = next;
      labelBtn.textContent = next;
      labelBtn.classList.toggle("is-pos", next === p);
      labelBtn.classList.toggle("is-neg", next === n);
      qcUiState.labels.set(seqKey, next);
    });
    if (labelBtn) {
      const p = labelBtn.dataset.pos || "1";
      const cur = String(labelBtn.dataset.label ?? "");
      labelBtn.classList.toggle("is-pos", cur === p);
      labelBtn.classList.toggle("is-neg", cur !== p);
    }
  });

  // 恢复滚动，禁止浏览器锚点把视图拽下去
  panel.scrollTop = prevScroll;
  requestAnimationFrame(() => {
    panel.scrollTop = prevScroll;
  });
}

/** 强制重新拉取并渲染 QC（抽 QC 后 / 点显示 QC 时） */
function forceReloadQc() {
  const panel = document.getElementById("qc-panel");
  if (panel) {
    panel.dataset.forceRefresh = "1";
    delete panel.dataset.loaded;
  }
}

document.getElementById("btn-abort-detail").onclick = async () => {
  if (!currentJobId) return;
  try {
    const j = await api(`/jobs/${currentJobId}`);
    if (j.status === "COMPLETED") {
      toast("任务已完成，无法中止", true);
      return;
    }
    if (j.status === "CANCELLED") {
      toast("任务已取消（终止），无法中止", true);
      return;
    }
    if (j.status === "ABORTED") {
      toast("任务已处于中止状态，可点「重新标注」继续");
      return;
    }
    const ok = window.confirm(
      "确认中止当前进度？\n\n中止后状态为「已中止」，可点「重新标注」继续，不是永久终止。"
    );
    if (!ok) return;
    const res = await api(`/jobs/${currentJobId}/abort`, { method: "POST" });
    toast(res?.message || "已中止当前进度");
    await refreshDetail();
  } catch (e) {
    toast(e.message, true);
  }
};

async function uploadFile(inputId, endpoint) {
  const input = document.getElementById(inputId);
  if (!input?.files?.length) throw new Error("请选择文件");
  const fd = new FormData();
  fd.append("file", input.files[0]);
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(API + endpoint, { method: "POST", body: fd, headers });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j.detail || res.statusText);
  }
  return res.json();
}

/** 若 file input 有文件则上传；无文件返回 null */
async function uploadFileIfSelected(inputId, endpoint) {
  const input = document.getElementById(inputId);
  if (!input?.files?.length) return null;
  return uploadFile(inputId, endpoint);
}

document.getElementById("btn-start-annotation").onclick = async () => {
  if (!currentJobId) return;
  const btn = document.getElementById("btn-start-annotation");
  if (btn?.dataset.busy === "1") {
    toast("正在启动，请稍候…", true);
    return;
  }
  try {
    if (btn) btn.dataset.busy = "1";
    // 1) 开跑前写入 KPI 中的 Gold 参数（仅可编辑状态允许）
    await flushGoldParamsBeforeRun();

    // 2) 有选文件则自动上传 Dataset / Gold
    const j0 = await api(`/jobs/${currentJobId}`);
    const dsInput = document.getElementById("file-dataset");
    const goldInput = document.getElementById("file-gold");
    const hasDsFile = !!(dsInput?.files?.length);
    const hasGoldFile = !!(goldInput?.files?.length);

    if (hasDsFile) {
      const r = await uploadFileIfSelected(
        "file-dataset",
        `/jobs/${currentJobId}/dataset`
      );
      if (r) toast(`Dataset 已上传 ${r.count} 条`);
    }
    if (hasGoldFile) {
      const r = await uploadFileIfSelected(
        "file-gold",
        `/jobs/${currentJobId}/gold`
      );
      if (r) toast(`Gold 已上传 ${r.count} 条`);
    }

    // 无本地文件且库里也没有 → 明确提示选文件
    const j1 =
      hasDsFile || hasGoldFile
        ? await api(`/jobs/${currentJobId}`)
        : j0;
    if (!(j1.annotation_count > 0) && !hasDsFile) {
      throw new Error("请先选择全量未标注数据文件");
    }
    if (!(j1.gold_count > 0) && !hasGoldFile) {
      throw new Error("请先选择初始 Gold Test Set 文件");
    }

    // 3) 启动标注流水线
    const r = await api(`/jobs/${currentJobId}/start-annotation`, {
      method: "POST",
    });
    toast(r.message || "数据标注已启动");
    // 清空已用文件选择，避免重复误传
    if (dsInput) dsInput.value = "";
    if (goldInput) goldInput.value = "";
    refreshDetail();
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (btn) btn.dataset.busy = "0";
  }
};

function getLayerCuts() {
  let lo = parseFloat(document.getElementById("layer-cut-low")?.value || "0.5");
  let hi = parseFloat(document.getElementById("layer-cut-high")?.value || "0.85");
  if (Number.isNaN(lo)) lo = 0.5;
  if (Number.isNaN(hi)) hi = 0.85;
  lo = Math.max(0.01, Math.min(0.98, lo));
  hi = Math.max(0.02, Math.min(0.99, hi));
  if (lo >= hi) {
    // 保证 cut_low < cut_high
    if (document.activeElement?.id === "layer-cut-low") {
      hi = Math.min(0.99, lo + 0.01);
      const el = document.getElementById("layer-cut-high");
      if (el) el.value = hi.toFixed(2);
    } else {
      lo = Math.max(0.01, hi - 0.01);
      const el = document.getElementById("layer-cut-low");
      if (el) el.value = lo.toFixed(2);
    }
  }
  return { lo: +lo.toFixed(2), hi: +hi.toFixed(2) };
}

function binsFromCuts(lo, hi) {
  return [
    { name: "Low", min: 0, max: lo },
    { name: "Medium", min: lo, max: hi },
    { name: "High", min: hi, max: 1 },
  ];
}

function syncLayerBarUI() {
  const { lo, hi } = getLayerCuts();
  const lowPct = lo * 100;
  const medPct = (hi - lo) * 100;
  const highPct = (1 - hi) * 100;
  const segLow = document.getElementById("layer-seg-low");
  const segMed = document.getElementById("layer-seg-med");
  const segHigh = document.getElementById("layer-seg-high");
  if (segLow) {
    segLow.style.flex = `0 0 ${lowPct}%`;
    segLow.style.background = "#22c55e"; // Low 绿
  }
  if (segMed) {
    segMed.style.flex = `0 0 ${medPct}%`;
    segMed.style.background = "#f59e0b"; // Med 黄
  }
  if (segHigh) {
    segHigh.style.flex = `0 0 ${highPct}%`;
    segHigh.style.background = "#ef4444"; // High 红
  }
  const cutsLabel = document.getElementById("layer-cuts-label");
  if (cutsLabel) {
    cutsLabel.textContent = `Low≤${lo.toFixed(2)} · Med≤${hi.toFixed(2)} · High`;
  }
}

function syncDecisionThresholdBar() {
  const el = document.getElementById("decision-threshold-input");
  const lab = document.getElementById("decision-threshold-label");
  if (!el) return;
  let v = parseFloat(el.value || "0.5");
  if (Number.isNaN(v)) v = 0.5;
  v = Math.max(0, Math.min(1, v));
  const negPct = v * 100;
  const posPct = (1 - v) * 100;
  const segNeg = document.getElementById("thr-seg-neg");
  const segPos = document.getElementById("thr-seg-pos");
  if (segNeg) {
    segNeg.style.flex = `0 0 ${negPct}%`;
    segNeg.style.background = "#22c55e";
  }
  if (segPos) {
    segPos.style.flex = `0 0 ${posPct}%`;
    segPos.style.background = "#ef4444";
  }
  if (lab) {
    lab.textContent = `0≤${v.toFixed(2)} · 1`;
  }
}

document.getElementById("decision-threshold-input")?.addEventListener("input", () => {
  syncDecisionThresholdBar();
});
document.getElementById("layer-cut-low")?.addEventListener("input", () => {
  syncLayerBarUI();
});
document.getElementById("layer-cut-high")?.addEventListener("input", () => {
  syncLayerBarUI();
});
// 初始化阈值切点条与分层横条
syncDecisionThresholdBar();
syncLayerBarUI();

document.getElementById("btn-apply-threshold").onclick = async () => {
  try {
    const j = await api(`/jobs/${currentJobId}`);
    if (j.status === "ABORTED") {
      toast("任务已中止，无法应用阈值；请先点「重新标注」", true);
      return;
    }
    const th = parseFloat(
      document.getElementById("decision-threshold-input").value || "0.5"
    );
    const r = await api(`/jobs/${currentJobId}/decision-threshold`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threshold: th }),
    });
    const tr = document.getElementById("threshold-result");
    if (tr) {
      tr.textContent = `阈值 ${r.threshold} · 1: ${r.positive_count} · 0: ${r.negative_count}`;
    }
    toast(
      `已应用阈值 ${r.threshold}：label=1 共 ${r.positive_count} 条 / label=0 共 ${r.negative_count} 条`
    );
    refreshDetail();
  } catch (e) {
    toast(e.message, true);
  }
};

document.getElementById("btn-recommend-bins").onclick = async () => {
  try {
    const r = await api(`/jobs/${currentJobId}/recommend-bins`);
    const bins = r.bins || [];
    // 从推荐 bins 反推两个切点
    const low = bins.find((b) => b.name === "Low") || bins[0];
    const med = bins.find((b) => b.name === "Medium") || bins[1];
    const cutLo = low?.max != null ? Number(low.max) : 0.5;
    const cutHi = med?.max != null ? Number(med.max) : 0.85;
    const elLo = document.getElementById("layer-cut-low");
    const elHi = document.getElementById("layer-cut-high");
    if (elLo) elLo.value = String(Math.max(0.01, Math.min(0.98, cutLo)).toFixed(2));
    if (elHi) elHi.value = String(Math.max(0.02, Math.min(0.99, cutHi)).toFixed(2));
    syncLayerBarUI();
    toast("已按当前置信度分布推荐分层切点");
  } catch (e) {
    toast(e.message, true);
  }
};

document.getElementById("fin-from")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});
document.getElementById("fin-to")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});

document.getElementById("btn-apply-bins").onclick = async () => {
  try {
    const j = await api(`/jobs/${currentJobId}`);
    if (j.status === "ABORTED") {
      toast("任务已中止，无法应用分层并抽 QC；请先点「重新标注」", true);
      return;
    }
    if (!j.current_round_no || j.current_round_no < 1) {
      toast("当前还没有标注轮次，无法分层抽 QC", true);
      return;
    }
    const { lo, hi } = getLayerCuts();
    const bins = binsFromCuts(lo, hi);
    const body = {
      bins,
      qc_per_bin: parseInt(document.getElementById("qc-per-bin").value || "20", 10),
    };
    await api(`/jobs/${currentJobId}/rounds/${j.current_round_no}/confidence-bins`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    // 使用 from_round / to_round 自动应用多轮平均（不标 COMPLETED）
    const range = getFinalizeRoundRange();
    const fin = await api(`/jobs/${currentJobId}/finalize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(range),
    });
    toast(
      `已分层抽 QC（Low≤${lo} · Med≤${hi}）并多轮平均 r${range.from_round}–r${range.to_round}` +
        (fin?.updated != null ? `（${fin.updated} 条）` : "") +
        " · 状态保持待QC，可继续审查或重新标注"
    );
    // 新抽样本：仅此时清空 UI 态并刷新（中止不会走这里）
    qcUiState.reasonSeqs.clear();
    qcUiState.labels.clear();
    forceReloadQc();
    setQcSidebarOpen(true);
    await refreshDetail();
  } catch (e) {
    toast(e.message, true);
  }
};

let qcSidebarOpen = false;
let historySidebarOpen = false;

function resetSidebarOverlays() {
  qcSidebarOpen = false;
  historySidebarOpen = false;
  const settings = document.getElementById("detail-settings-panels");
  const qcOv = document.getElementById("detail-qc-overlay");
  const histOv = document.getElementById("detail-history-overlay");
  const btnQc = document.getElementById("btn-show-qc");
  const btnHist = document.getElementById("btn-prompt-history");
  if (settings) settings.hidden = false;
  if (qcOv) qcOv.hidden = true;
  if (histOv) histOv.hidden = true;
  if (btnQc) btnQc.textContent = "显示 QC";
  if (btnHist) btnHist.textContent = "历史版本";
  collapsePromptDiff();
  collapsePromptReason();
}

function setQcSidebarOpen(open) {
  if (open) {
    historySidebarOpen = false;
    const histOv = document.getElementById("detail-history-overlay");
    if (histOv) histOv.hidden = true;
    const btnHist = document.getElementById("btn-prompt-history");
    if (btnHist) btnHist.textContent = "历史版本";
  }
  qcSidebarOpen = !!open;
  const settings = document.getElementById("detail-settings-panels");
  const overlay = document.getElementById("detail-qc-overlay");
  const btn = document.getElementById("btn-show-qc");
  // 两者都关时才显示设置
  if (settings) settings.hidden = open || historySidebarOpen;
  if (overlay) overlay.hidden = !open;
  if (btn) btn.textContent = open ? "收起 QC" : "显示 QC";
}

function setHistorySidebarOpen(open) {
  if (open) {
    qcSidebarOpen = false;
    const qcOv = document.getElementById("detail-qc-overlay");
    if (qcOv) qcOv.hidden = true;
    const btnQc = document.getElementById("btn-show-qc");
    if (btnQc) btnQc.textContent = "显示 QC";
  }
  historySidebarOpen = !!open;
  const settings = document.getElementById("detail-settings-panels");
  const overlay = document.getElementById("detail-history-overlay");
  const btn = document.getElementById("btn-prompt-history");
  if (settings) settings.hidden = open || qcSidebarOpen;
  if (overlay) overlay.hidden = !open;
  if (btn) btn.textContent = open ? "收起历史" : "历史版本";
  if (!open) {
    collapsePromptDiff();
    collapsePromptReason();
  }
}

document.getElementById("btn-show-qc")?.addEventListener("click", async () => {
  try {
    if (qcSidebarOpen) {
      setQcSidebarOpen(false);
      return;
    }
    // 仅当面板尚未加载过时才强制拉取；已有内容则直接展示（中止后保留上次 QC）
    const panel = document.getElementById("qc-panel");
    if (!panel || panel.dataset.loaded !== "1") {
      forceReloadQc();
    }
    setQcSidebarOpen(true);
    if (currentJobId) await refreshDetail();
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-hide-qc")?.addEventListener("click", () => {
  setQcSidebarOpen(false);
});

document.getElementById("btn-hide-history")?.addEventListener("click", () => {
  setHistorySidebarOpen(false);
});

// 标记用户已编辑当前 Prompt
document.getElementById("active-prompt-editor")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});
document.getElementById("change-reason-input")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});

// 保存提示词：有 diff 才写入历史版本
document.getElementById("btn-save-prompt")?.addEventListener("click", async () => {
  try {
    if (!currentJobId) return;
    const editor = document.getElementById("active-prompt-editor");
    const reasonEl = document.getElementById("change-reason-input");
    const text = (editor?.value || "").trim();
    if (!text) {
      toast("提示词不能为空", true);
      return;
    }
    const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
    const active =
      versions.find((v) => v.is_active) || versions[versions.length - 1];
    if (active && (active.prompt_text || "").trim() === text) {
      toast("与当前版本无差异，未保存", true);
      return;
    }
    const reason =
      (reasonEl?.value || "").trim() || "人工修改提示词";
    const pv = await api(`/jobs/${currentJobId}/prompt-versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt_text: text,
        change_reason: reason,
      }),
    });
    if (editor) {
      editor.dataset.dirty = "0";
      editor.dataset.loadedVersion = `${pv.version}:${text.length}`;
    }
    if (reasonEl) {
      reasonEl.dataset.dirty = "0";
      reasonEl.value = pv.change_reason || reason;
    }
    toast(`已保存提示词 v${pv.version} 到历史版本`);
    // 强制历史列表与编辑器同步
    if (typeof historySidebarOpen !== "undefined" && historySidebarOpen) {
      const vs = await api(`/jobs/${currentJobId}/prompt-versions`);
      renderPromptHistoryList(vs);
    }
    refreshDetail();
  } catch (e) {
    toast(e.message, true);
  }
});

// Gold 准确率 KPI 展开明细（展开时隐藏上传/下载区，占满下方空间）
let goldEvalOpen = false;

function setSidePanelsForGoldEval(open) {
  const upload = document.querySelector(".detail-side-upload");
  const download = document.querySelector(".detail-side-download");
  const status = document.querySelector(".detail-side-status");
  const panel = document.getElementById("gold-eval-panel");
  if (upload) upload.hidden = !!open;
  if (download) download.hidden = !!open;
  if (status) status.classList.toggle("gold-eval-expanded", !!open);
  if (panel) panel.classList.toggle("is-expanded", !!open);
}

/** 解析目标准确率：0~1，或大于 1 时按百分比（如 95 → 0.95） */
function parseAccuracyInput(raw) {
  const s = String(raw ?? "").trim().replace(/%/g, "");
  if (!s) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  if (n > 1) {
    if (n > 100) return null;
    return n / 100;
  }
  if (n < 0) return null;
  return n;
}

/**
 * 仅首次开始标注前、或可点「重新标注」的状态可改 Gold 参数；
 * Gold 优化 / 全量标注 / Prompt 改进进行中锁定。
 */
function canEditGoldParams(status) {
  return [
    "CREATED",
    "GOLD_FAILED",
    "GOLD_READY",
    "AWAIT_DECISION_THRESHOLD",
    "AWAIT_CONFIDENCE_BINS",
    "AWAIT_QC",
    "AWAIT_DECISION",
    "ABORTED",
    "COMPLETED",
    "FAILED",
  ].includes(status);
}

/** 绑定 KPI 内嵌 Gold 参数输入框（仅可编辑时保存） */
function bindGoldParamInputs(editable) {
  const accEl = document.getElementById("kpi-input-target-acc");
  const maxEl = document.getElementById("kpi-input-gold-max");

  const markDirty = (el) => {
    if (el) el.dataset.dirty = "1";
  };

  if (accEl && accEl.dataset.bound !== "1") {
    accEl.dataset.bound = "1";
    accEl.addEventListener("click", (e) => e.stopPropagation());
    accEl.addEventListener("mousedown", (e) => e.stopPropagation());
    accEl.addEventListener("input", () => {
      if (!accEl.disabled) markDirty(accEl);
    });
    accEl.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        accEl.blur();
      }
    });
    accEl.addEventListener("blur", () => {
      if (!accEl.disabled) saveGoldTargetFromInput(accEl);
    });
  }

  if (maxEl && maxEl.dataset.bound !== "1") {
    maxEl.dataset.bound = "1";
    maxEl.addEventListener("click", (e) => e.stopPropagation());
    maxEl.addEventListener("mousedown", (e) => e.stopPropagation());
    maxEl.addEventListener("input", () => {
      if (!maxEl.disabled) markDirty(maxEl);
    });
    maxEl.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        maxEl.blur();
      }
    });
    maxEl.addEventListener("blur", () => {
      if (!maxEl.disabled) saveGoldMaxIterFromInput(maxEl);
    });
  }

  // 同步 disabled（rebuild 后）
  if (accEl) accEl.disabled = !editable;
  if (maxEl) maxEl.disabled = !editable;
}

async function saveGoldTargetFromInput(el) {
  if (!currentJobId || !el || el.disabled || el.dataset.saving === "1") return;
  if (el.dataset.dirty !== "1") return;
  const v = parseAccuracyInput(el.value);
  if (v == null) {
    toast("目标准确率须为 0~1（或 0~100%）", true);
    el.focus();
    return;
  }
  el.dataset.saving = "1";
  try {
    await api(`/jobs/${currentJobId}/gold-params`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_accuracy: v }),
    });
    el.value = String(v);
    el.dataset.dirty = "0";
    toast(`目标准确率 ${(v * 100).toFixed(0)}%`);
  } catch (e) {
    toast(e.message || String(e), true);
  } finally {
    el.dataset.saving = "0";
  }
}

async function saveGoldMaxIterFromInput(el) {
  if (!currentJobId || !el || el.disabled || el.dataset.saving === "1") return;
  if (el.dataset.dirty !== "1") return;
  const n = parseInt(String(el.value).trim(), 10);
  if (!Number.isFinite(n) || n < 1 || n > 50) {
    toast("最大迭代次数须为 1~50 的整数", true);
    el.focus();
    return;
  }
  el.dataset.saving = "1";
  try {
    await api(`/jobs/${currentJobId}/gold-params`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_gold_iterations: n }),
    });
    el.value = String(n);
    el.dataset.dirty = "0";
    toast(`最大迭代次数 ${n}`);
  } catch (e) {
    toast(e.message || String(e), true);
  } finally {
    el.dataset.saving = "0";
  }
}

/**
 * 开跑 / 重新标注前：把 KPI 输入框中的 Gold 参数一并提交。
 * 可编辑时强制读取当前值（即使未 dirty）。
 */
async function flushGoldParamsBeforeRun() {
  if (!currentJobId) return;
  const accEl = document.getElementById("kpi-input-target-acc");
  const maxEl = document.getElementById("kpi-input-gold-max");
  const body = {};
  if (accEl && !accEl.disabled) {
    const v = parseAccuracyInput(accEl.value);
    if (v == null) throw new Error("目标准确率须为 0~1（或 0~100%）");
    body.target_accuracy = v;
  }
  if (maxEl && !maxEl.disabled) {
    const n = parseInt(String(maxEl.value).trim(), 10);
    if (!Number.isFinite(n) || n < 1 || n > 50) {
      throw new Error("最大迭代次数须为 1~50 的整数");
    }
    body.max_gold_iterations = n;
  }
  if (body.target_accuracy == null && body.max_gold_iterations == null) return;
  await api(`/jobs/${currentJobId}/gold-params`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (accEl) accEl.dataset.dirty = "0";
  if (maxEl) maxEl.dataset.dirty = "0";
}

async function toggleGoldEvalPanel(force) {
  const panel = document.getElementById("gold-eval-panel");
  if (!panel || !currentJobId) return;
  const open = force != null ? force : panel.hidden;
  if (!open) {
    panel.hidden = true;
    goldEvalOpen = false;
    setSidePanelsForGoldEval(false);
    return;
  }
  try {
    const data = await api(`/jobs/${currentJobId}/gold-eval`);
    const meta = document.getElementById("gold-eval-meta");
    const list = document.getElementById("gold-eval-list");
    const acc =
      data.accuracy != null ? `${(Number(data.accuracy) * 100).toFixed(1)}%` : "—";
    const tgt =
      data.target_accuracy != null
        ? `${(Number(data.target_accuracy) * 100).toFixed(0)}%`
        : "—";
    if (meta) {
      meta.textContent =
        `Accuracy ${acc} · 目标 ${tgt} · n=${data.n ?? 0}` +
        (data.prompt_version != null ? ` · Prompt v${data.prompt_version}` : "") +
        (data.gold_eval_threshold != null
          ? ` · 评测阈值 ${data.gold_eval_threshold}`
          : "");
    }
    const details = data.details || [];
    if (!list) return;
    if (!details.length) {
      list.innerHTML =
        "<p class='hint'>暂无明细。完成 Gold 评测后（数据标注/优化）再展开。</p>";
    } else {
      list.innerHTML = details
        .map((d, i) => {
          const conf =
            d.confidence != null ? Number(d.confidence).toFixed(3) : "—";
          const ok = d.correct ? "ok" : "bad";
          return `<div class="gold-eval-item ${ok}">
            <div class="ge-meta">#${d.id ?? i + 1}
              · 金标 <strong>${escapeHtml(String(d.gold_label ?? "—"))}</strong>
              · 预测 <strong>${escapeHtml(String(d.pred_label ?? "—"))}</strong>
              · 置信度 <strong>${conf}</strong>
              ${d.correct ? '<span class="ge-tag ok">对</span>' : '<span class="ge-tag bad">错</span>'}
            </div>
            <div class="ge-text">${escapeHtml(d.text || "")}</div>
          </div>`;
        })
        .join("");
    }
    panel.hidden = false;
    goldEvalOpen = true;
    setSidePanelsForGoldEval(true);
  } catch (e) {
    toast(e.message, true);
    setSidePanelsForGoldEval(false);
  }
}
document.getElementById("btn-hide-gold-eval")?.addEventListener("click", () => {
  toggleGoldEvalPanel(false);
});

document.getElementById("btn-submit-qc").onclick = async () => {
  try {
    const j = await api(`/jobs/${currentJobId}`);
    const items = [...document.querySelectorAll(".qc-item")];
    // 按 id/seq 提交当前 UI label；后端按 id 比对当前状态，不一致才更新
    const reviews = items.map((el) => {
      const seq = +el.dataset.seq;
      const btn = el.querySelector(".qc-label-btn");
      const human_label = String(
        btn?.dataset.label ?? btn?.textContent?.trim() ?? "0"
      );
      const pred = String(
        (
          el.querySelector(".qc-pred strong")?.textContent ||
          btn?.dataset.pred ||
          ""
        ).trim()
      );
      const baseline = String(
        (
          btn?.dataset.baselineLabel ||
          btn?.dataset.origLabel ||
          pred ||
          ""
        ).trim()
      );
      const changed = human_label !== baseline;
      return { seq, human_label, corrected: changed, changed };
    });
    // 前端先过滤无改动项；后端仍会再按 id 比对一次
    const changedReviews = reviews
      .filter((r) => r.changed)
      .map(({ seq, human_label, corrected }) => ({ seq, human_label, corrected }));

    if (!changedReviews.length) {
      toast("忽略重复提交 QC：没有做任何标签修改", true);
      return;
    }

    const res = await api(`/jobs/${currentJobId}/rounds/${j.current_round_no}/qc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reviews: changedReviews,
        feedback_text: `QC 人工修正 ${changedReviews.length} 条 label`,
      }),
    });
    if (res?.ignored) {
      toast(res.message || "忽略重复提交 QC：没有做任何标签修改", true);
      return;
    }
    toast(res?.message || `QC 已提交：更新 ${changedReviews.length} 条 label`);
    // 提交成功后把基线更新为当前 label
    items.forEach((el) => {
      const btn = el.querySelector(".qc-label-btn");
      if (!btn) return;
      const cur = btn.dataset.label ?? btn.textContent?.trim() ?? "";
      btn.dataset.origLabel = cur;
      btn.dataset.baselineLabel = cur;
    });
    forceReloadQc();
    refreshDetail();
  } catch (e) {
    toast(e.message, true);
  }
};

const LAYER_ORDER = ["Low", "Medium", "High"];

/** 将 from / to 置信度层展开为区间列表，如 Low→High => [Low, Medium, High] */
function rangesFromLayerFromTo(fromRaw, toRaw) {
  const from = String(fromRaw || "").trim();
  const to = String(toRaw || "").trim();
  if (!from && !to) return [];
  if (from && !to) return [from];
  if (!from && to) return [to];
  const norm = (s) => {
    const hit = LAYER_ORDER.find((x) => x.toLowerCase() === s.toLowerCase());
    return hit || s;
  };
  const aName = norm(from);
  const bName = norm(to);
  let ai = LAYER_ORDER.indexOf(aName);
  let bi = LAYER_ORDER.indexOf(bName);
  // 自定义层名：原样返回两端（去重）
  if (ai < 0 || bi < 0) {
    return aName === bName ? [aName] : [aName, bName];
  }
  if (ai > bi) {
    const t = ai;
    ai = bi;
    bi = t;
  }
  return LAYER_ORDER.slice(ai, bi + 1);
}

let _reannotateBusy = false;
document.getElementById("btn-decision").onclick = async () => {
  if (_reannotateBusy) {
    toast("正在开启 / 优化中，请勿重复点击", true);
    return;
  }
  try {
    const j = await api(`/jobs/${currentJobId}`);
    if (j.status === "GOLD_OPTIMIZING") {
      toast("Gold 优化进行中，请等待完成", true);
      return;
    }
    // Gold 失败，或错误信息表明 Gold 未达标（状态可能被阈值 UI 误覆盖）
    const goldFailHint = /Gold|gold|未达目标|重新标注/.test(
      String(j.error_message || "")
    );
    const isGoldFailed =
      j.status === "GOLD_FAILED" ||
      j.status === "ABORTED" ||
      (goldFailHint &&
        [
          "AWAIT_CONFIDENCE_BINS",
          "AWAIT_DECISION_THRESHOLD",
          "AWAIT_QC",
          "AWAIT_DECISION",
          "PROMPT_IMPROVING",
          "ABORTED",
        ].includes(j.status));
    // 这些状态均可直接开下一轮 Gold loop（不强制填范围）
    const canReopenWithoutRanges = [
      "GOLD_FAILED",
      "GOLD_READY",
      "ABORTED",
      "COMPLETED",
      "FAILED",
      "AWAIT_DECISION_THRESHOLD",
      "AWAIT_CONFIDENCE_BINS",
      "AWAIT_QC",
      "AWAIT_DECISION",
      "PROMPT_IMPROVING",
    ].includes(j.status);
    const roundNo = j.current_round_no || 0;
    const ranges = rangesFromLayerFromTo(
      document.getElementById("dec-range-from")?.value,
      document.getElementById("dec-range-to")?.value
    );
    // Gold 失败 / 可重开状态：可不填范围；仅明确子集重标时才需要 from/to
    if (!canReopenWithoutRanges && !ranges.length) {
      toast("请填写重新标注范围 from / to，如 Low 与 Medium", true);
      return;
    }
    const editor = document.getElementById("active-prompt-editor");
    const reasonEl = document.getElementById("change-reason-input");
    const promptText = (editor?.value || "").trim();
    const promptEdited = editor?.dataset.dirty === "1";
    const humanNote = (reasonEl?.value || "").trim();

    // 重新标注开始前：提交 KPI 中的 Gold 目标准确率 / 最大迭代（仅边界可改）
    await flushGoldParamsBeforeRun();

    // 新 loop：可选改 Prompt；后端迭代从 0、清空全量进度；仅达标才标注
    const body = {
      continue_next: true,
      feedback:
        humanNote ||
        (promptEdited
          ? "人工修改提示词后开启下一轮 Gold loop"
          : isGoldFailed
            ? "人工介入：重新标注开启下一轮 Gold loop"
            : "人工介入：请根据 Gold 结果继续优化 Prompt"),
      next_confidence_ranges: ranges,
    };
    if (promptEdited) {
      if (!promptText) {
        toast("当前提示词不能为空", true);
        return;
      }
      body.prompt_text = promptText;
      body.change_reason = humanNote || "人工修改提示词";
    } else {
      // 未改正文也可直接开下一轮 loop（尤其 GOLD_FAILED）
      body.change_reason = humanNote || "";
    }

    _reannotateBusy = true;
    await api(`/jobs/${currentJobId}/rounds/${roundNo}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    toast(
      isGoldFailed
        ? "已开启下一轮 loop：Gold 迭代→0，全量进度→0，当前轮次+1；仅达标后才会全量标注"
        : "已开启下一轮 loop：Gold 迭代→0，全量进度→0，当前轮次+1；达标后对指定范围重标"
    );
    if (editor) {
      editor.dataset.dirty = "0";
      delete editor.dataset.loadedVersion;
    }
    if (reasonEl) {
      reasonEl.dataset.dirty = "0";
      delete reasonEl.dataset.loadedVersion;
    }
    resetSidebarOverlays();
    refreshDetail();
  } catch (e) {
    toast(e.message, true);
  } finally {
    // 短暂锁，避免连点排队多个后台 Gold loop
    setTimeout(() => {
      _reannotateBusy = false;
    }, 3000);
  }
};

async function downloadExport(fmt, { goldOnly = false } = {}) {
  const path = goldOnly
    ? `/jobs/${currentJobId}/export-gold?format=${fmt}`
    : `/jobs/${currentJobId}/export?format=${fmt}`;
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail || msg;
    } catch (_) {
      try {
        msg = await res.text();
      } catch (__) {}
    }
    // 后端：未进行全量标注 / 没有 Gold Test 内容
    if (
      String(msg).includes("未进行全量标注") ||
      String(msg).includes("没有 Gold") ||
      res.status === 400
    ) {
      throw new Error(
        msg || (goldOnly ? "没有 Gold Test 内容" : "未进行全量标注")
      );
    }
    throw new Error(msg || "下载失败");
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename="?([^"]+)"?/);
  let name = m ? m[1] : null;
  if (!name) {
    if (goldOnly)
      name = `job_${currentJobId}_gold.${fmt === "csv" ? "csv" : "xlsx"}`;
    else name = `job_${currentJobId}.${fmt === "csv" ? "zip" : "xlsx"}`;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
  toast(goldOnly ? "Gold Test 已开始下载" : "导出文件已开始下载");
}

document.getElementById("btn-export-xlsx").onclick = () =>
  downloadExport("xlsx").catch((e) => toast(e.message, true));
document.getElementById("btn-export-csv").onclick = () =>
  downloadExport("csv").catch((e) => toast(e.message, true));
document.getElementById("btn-export-gold-xlsx")?.addEventListener("click", () =>
  downloadExport("xlsx", { goldOnly: true }).catch((e) => toast(e.message, true))
);
document.getElementById("btn-export-gold-csv")?.addEventListener("click", () =>
  downloadExport("csv", { goldOnly: true }).catch((e) => toast(e.message, true))
);


/** 标注详情：将当前激活 Prompt 存为模板库条目（供「从模板导入」） */
document.getElementById("btn-save-template")?.addEventListener("click", async () => {
  try {
    if (!currentJobId) {
      toast("请先打开任务", true);
      return;
    }
    const versions = await api(`/jobs/${currentJobId}/prompt-versions`);
    const active = versions.find((v) => v.is_active) || versions[versions.length - 1];
    if (!active) throw new Error("无 prompt 版本");
    const name = prompt(
      "模板名称",
      `job${currentJobId}-v${active.version}`
    );
    if (!name) return;
    const t = await api(
      `/templates/from-prompt/${active.id}?name=${encodeURIComponent(name)}`,
      { method: "POST" }
    );
    toast(`已创建模板 #${t.id}（v${t.current_version}）`);
  } catch (e) {
    toast(e.message, true);
  }
});

// boot
(async () => {
  const ok = await bootstrapAuth();
  if (ok) {
    loadJobs().catch((e) => toast(e.message, true));
  }
})();
