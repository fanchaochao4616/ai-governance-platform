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
  datasets: "数据集库",
  "dataset-search": "数据检索",
  create: "数据标注",
  detail: "数据标注",
  templates: "提示词调试",
  "prompt-debug": "提示词调试",
  "prompt-templates": "提示词模板",
  "data-clean": "数据清洗",
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
    module: "datasets",
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
  datasets: "datasets",
  "dataset-search": "datasets",
  create: "annotate",
  detail: "annotate",
  templates: "prompt",
  "prompt-debug": "prompt",
  "prompt-templates": "prompt",
  "data-clean": "datasets",
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
    const activeGroup = document.querySelector(
      `.sidenav-group[data-module="${mod}"]`
    );
    activeGroup?.classList.add("is-active");
    // 进入某模块时自动展开其二级标签
    if (activeGroup) setSidenavGroupOpen(activeGroup, true);
  }

  if (name === "settings") {
    const s = document.getElementById("btn-settings");
    if (s) s.classList.add("active");
  }
  // 顶栏 Job 列表按钮高亮
  const btnJobs = document.getElementById("btn-jobs");
  if (btnJobs) {
    btnJobs.classList.toggle("active", name === "jobs");
  }

  const title = document.getElementById("topbar-title");
  if (title) title.textContent = VIEW_TITLES[name] || name;

  // 顶栏「返回列表」：仅提示词调试工作台（标注详情用顶栏 Job 列表，避免重复）
  const btnBack = document.getElementById("btn-back");
  if (btnBack) {
    btnBack.hidden = name !== "prompt-debug";
  }
}

function goView(v) {
  // 数据标注：不再展示模板/历史任务页，直接进入标注详情
  if (v === "create") {
    enterAnnotationWorkbench().catch((e) => toast(e.message, true));
    return;
  }
  if (v === "jobs" || v === "templates") {
    loadJobs().catch((e) => toast(e.message, true));
  }
  if (v === "datasets") {
    loadManagedDatasets().catch((e) => toast(e.message, true));
  }
  if (v === "dataset-search") {
    initDatasetSearchPage().catch((e) => toast(e.message, true));
  }
  if (v === "prompt-templates") {
    loadPromptTemplateLibrary().catch((e) => toast(e.message, true));
  }
  if (v === "data-clean") {
    initDataCleanPage().catch((e) => toast(e.message, true));
  }
  showView(v);
}

/**
 * 进入数据标注工作台：新建空白标注 Job 并打开详情页。
 * （历史任务请从顶栏「Job 列表」恢复）
 */
async function enterAnnotationWorkbench() {
  const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
  const job = await api("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `数据标注 ${stamp}`,
      job_type: "annotation",
      policy_rules: "（请填写风控细则 / 初始 Prompt）",
      target_accuracy: 1.0,
      max_gold_iterations: 3,
    }),
  });
  toast(`已创建标注任务 #${job.id}`);
  try {
    await loadJobs();
  } catch (_) {
    /* ignore */
  }
  await openJob(job.id);
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

/** 一级标签展开/收起二级 */
function setSidenavGroupOpen(group, open) {
  if (!group) return;
  group.classList.toggle("is-open", !!open);
  const head = group.querySelector(".sidenav-group-head");
  if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
}

function toggleSidenavGroup(group) {
  if (!group) return;
  setSidenavGroupOpen(group, !group.classList.contains("is-open"));
}

// 展开态：点一级标签切换二级收起/展开
// 收起态（窄导航）：点分组打开该组第一个入口
document.querySelectorAll(".sidenav-group").forEach((group) => {
  const head = group.querySelector(".sidenav-group-head");
  if (head) {
    head.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sn = document.getElementById("sidenav");
      if (sn?.classList.contains("collapsed")) {
        const first = group.querySelector(".sidenav-item[data-view]");
        if (first?.dataset.view) goView(first.dataset.view);
        return;
      }
      toggleSidenavGroup(group);
    });
    head.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      head.click();
    });
  }
  group.addEventListener("click", (e) => {
    const sn = document.getElementById("sidenav");
    if (!sn?.classList.contains("collapsed")) return;
    // 已点到子项则交给子项处理
    if (e.target.closest(".sidenav-item")) return;
    if (e.target.closest(".sidenav-group-head")) return;
    const first = group.querySelector(".sidenav-item[data-view]");
    if (first?.dataset.view) goView(first.dataset.view);
  });
});

// 初始：默认收起全部带二级的分组；当前激活模块展开
(function initSidenavGroups() {
  document.querySelectorAll(".sidenav-group").forEach((g) => {
    if (!g.querySelector(".sidenav-group-head")) return;
    setSidenavGroupOpen(g, g.classList.contains("is-active"));
  });
})();

document.getElementById("btn-jobs")?.addEventListener("click", () => {
  goView("jobs");
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
  // 数据标注页已取消历史任务列表；保留空实现以免 loadJobs 报错
  const el = document.getElementById("create-jobs-table");
  if (!el) return;
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
        "<p class='hint'>暂无任务。点击「新建」进入工作台，保存版本后才会出现在此列表。</p>",
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

// 提示词调试 · 新建：进入草稿（不弹名称、不调创建接口）
// 使用 document 委托，避免脚本执行时机 / 缓存旧绑定导致仍走 createTypedJob
document.addEventListener("click", (e) => {
  const btn = e.target?.closest?.("#btn-new-prompt-debug");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  try {
    openPromptDebugDraft();
  } catch (err) {
    console.error(err);
    toast(err?.message || String(err), true);
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
/** 未落库的新建草稿（点「新建」进入，保存版本时才创建 Job + 写名称） */
let pdDraftMode = false;
/** 已落库任务名称（SQL jobs.name） */
let pdLoadedJobName = "";
/** 草稿态展示的默认名称（稳定，不随每次渲染变） */
let pdDraftDefaultName = "";
/** 名称弹窗回调 resolve(null | string) */
let pdNameModalResolve = null;

function formatPdTime(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").slice(0, 19);
}

function defaultPromptDebugName() {
  return `提示词调试 ${new Date()
    .toISOString()
    .slice(0, 16)
    .replace("T", " ")}`;
}

/**
 * 刷新工作台顶部名称展示（纯文本，非输入框）。
 * 草稿：显示默认名；已保存：显示 SQL 中的 jobs.name
 */
function updatePdNameDisplay() {
  const nameText = document.getElementById("pd-job-name-text");
  const renameBtn = document.getElementById("btn-pd-rename");
  const meta = document.getElementById("pd-job-meta");
  if (!nameText) return;

  if (!currentJobId || pdDraftMode) {
    const shown = pdDraftDefaultName || defaultPromptDebugName();
    pdDraftDefaultName = shown;
    nameText.textContent = shown;
    nameText.classList.add("is-draft");
    nameText.classList.remove("is-clickable");
    nameText.title = "保存版本时将弹出名称设置（可改，可留空用默认）";
    if (renameBtn) renameBtn.hidden = true;
    if (meta) meta.textContent = "未保存草稿 · 保存版本后才会写入数据库";
    return;
  }

  const shown = (pdLoadedJobName || "").trim() || defaultPromptDebugName();
  nameText.textContent = shown;
  nameText.classList.remove("is-draft");
  nameText.classList.add("is-clickable");
  nameText.title = "点击修改任务名称";
  if (renameBtn) renameBtn.hidden = false;
  if (meta) {
    meta.textContent = currentJobId
      ? `#${currentJobId} · 名称已保存`
      : "—";
  }
}

/**
 * 弹出任务名称设置框。
 * @param {{ title?: string, defaultName: string, confirmLabel?: string, hint?: string }} opts
 * @returns {Promise<string|null>} 确认后返回最终名称；取消返回 null
 */
function askPromptDebugJobName(opts) {
  const modal = document.getElementById("pd-name-modal");
  const form = document.getElementById("form-pd-name");
  const input = document.getElementById("pd-name-modal-input");
  const titleEl = document.getElementById("pd-name-modal-title");
  const hintEl = document.getElementById("pd-name-modal-hint");
  const defEl = document.getElementById("pd-name-modal-default");
  const okBtn = document.getElementById("pd-name-modal-ok");
  if (!modal || !form || !input) {
    // 兜底：无弹层时直接用默认名
    return Promise.resolve((opts.defaultName || defaultPromptDebugName()).trim());
  }

  // 若已有未完成弹窗，先取消
  if (pdNameModalResolve) {
    const prev = pdNameModalResolve;
    pdNameModalResolve = null;
    prev(null);
  }

  const defaultName = (opts.defaultName || defaultPromptDebugName()).trim();
  if (titleEl) titleEl.textContent = opts.title || "设置任务名称";
  if (hintEl) {
    hintEl.textContent =
      opts.hint || "留空则使用默认名称；名称写入数据库（可改、可删）";
  }
  if (defEl) defEl.textContent = defaultName;
  if (okBtn) okBtn.textContent = opts.confirmLabel || "确认保存";
  input.value = "";
  input.placeholder = defaultName;
  input.dataset.defaultName = defaultName;
  modal.hidden = false;
  setTimeout(() => {
    input.focus();
    input.select?.();
  }, 30);

  return new Promise((resolve) => {
    pdNameModalResolve = resolve;
  });
}

function closePdNameModal(result) {
  const modal = document.getElementById("pd-name-modal");
  if (modal) modal.hidden = true;
  const resolve = pdNameModalResolve;
  pdNameModalResolve = null;
  if (resolve) resolve(result);
}

document.getElementById("form-pd-name")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("pd-name-modal-input");
  const typed = (input?.value || "").trim();
  const fallback =
    (input?.dataset.defaultName || "").trim() || defaultPromptDebugName();
  closePdNameModal(typed || fallback);
});

document.getElementById("pd-name-modal-cancel")?.addEventListener("click", () => {
  closePdNameModal(null);
});

document.getElementById("pd-name-modal")?.addEventListener("click", (e) => {
  if (e.target?.id === "pd-name-modal") closePdNameModal(null);
});

/**
 * 新建：不调 API，直接进入空工作台草稿（名称仅展示默认文本）。
 */
function openPromptDebugDraft() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  currentJobId = null;
  pdDraftMode = true;
  pdLoadedJobName = "";
  pdDraftDefaultName = defaultPromptDebugName();
  pdVersionsCache = [];
  collapsePdDiff();
  collapsePdReasonExpand();

  const verEl = document.getElementById("pd-active-ver");
  const editor = document.getElementById("pd-prompt-editor");
  const reasonEl = document.getElementById("pd-change-reason");
  if (verEl) verEl.textContent = "尚未创建版本";
  if (editor) {
    editor.value = "";
    editor.dataset.dirty = "0";
    editor.dataset.loadedVersion = "";
  }
  if (reasonEl) {
    reasonEl.value = "";
    reasonEl.dataset.dirty = "0";
  }
  updatePdNameDisplay();
  renderPromptDebugHistory([]);
  showView("prompt-debug");
}

/**
 * 改名：更新 SQL jobs.name
 */
async function renamePromptDebugJob() {
  if (!currentJobId || pdDraftMode) {
    toast("请先保存版本创建任务后再改名", true);
    return;
  }
  const name = await askPromptDebugJobName({
    title: "修改任务名称",
    defaultName: pdLoadedJobName || defaultPromptDebugName(),
    confirmLabel: "保存名称",
    hint: "留空则使用默认名称；修改后写入数据库",
  });
  if (name == null) return;
  try {
    const job = await api(`/jobs/${currentJobId}/name`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    pdLoadedJobName = (job.name || name).trim();
    updatePdNameDisplay();
    toast("任务名称已更新");
    loadJobs().catch(() => {});
  } catch (e) {
    toast(e.message, true);
  }
}

document.getElementById("btn-pd-rename")?.addEventListener("click", () => {
  renamePromptDebugJob().catch((e) => toast(e.message, true));
});
document.getElementById("pd-job-name-text")?.addEventListener("click", () => {
  if (!currentJobId || pdDraftMode) return;
  renamePromptDebugJob().catch((e) => toast(e.message, true));
});

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
  const verEl = document.getElementById("pd-active-ver");
  const editor = document.getElementById("pd-prompt-editor");
  const reasonEl = document.getElementById("pd-change-reason");
  if (!currentJobId) {
    // 草稿态由 openPromptDebugDraft 负责
    return;
  }

  pdDraftMode = false;
  const j = job || (await api(`/jobs/${currentJobId}`));
  pdLoadedJobName = (j.name || "").trim();
  pdDraftDefaultName = "";
  updatePdNameDisplay();
  const meta = document.getElementById("pd-job-meta");
  if (meta) {
    meta.textContent = `#${j.id} · ${j.status || "—"}`;
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

/** ---------- 提示词调试 · 导入模板 ---------- */
let pdTmplSearchTimer = null;

function closePdTmplModal() {
  const modal = document.getElementById("pd-tmpl-modal");
  if (modal) modal.hidden = true;
}

async function renderPdTemplateList(keyword) {
  const listEl = document.getElementById("pd-tmpl-list");
  if (!listEl) return;
  listEl.innerHTML = `<div class="pd-tmpl-empty">加载中…</div>`;
  try {
    const q = (keyword || "").trim();
    const path = q
      ? `/templates?q=${encodeURIComponent(q)}`
      : `/templates`;
    const list = await api(path);
    if (!list || !list.length) {
      listEl.innerHTML = `<div class="pd-tmpl-empty">暂无模板${
        q ? "匹配" : ""
      }。请先在模板库沉淀提示词。</div>`;
      return;
    }
    listEl.innerHTML = list
      .map(
        (t) => `<div class="pd-tmpl-item" data-tid="${t.id}" role="option" title="点击导入到编辑器">
        <div class="pti-name">#${t.id} ${escapeHtml(t.name || "未命名")}</div>
        <div class="pti-meta">分类 ${escapeHtml(t.category || "-")} · 激活 v${
          t.current_version || 1
        } · 使用 ${t.usage_count || 0}</div>
      </div>`
      )
      .join("");
    listEl.querySelectorAll(".pd-tmpl-item[data-tid]").forEach((el) => {
      el.addEventListener("click", async () => {
        if (el.dataset.busy === "1") return;
        el.dataset.busy = "1";
        try {
          await importTemplateIntoPromptDebug(+el.dataset.tid);
        } catch (err) {
          toast(err.message || "导入失败", true);
        } finally {
          el.dataset.busy = "0";
        }
      });
    });
  } catch (e) {
    listEl.innerHTML = `<div class="pd-tmpl-empty">${escapeHtml(
      e.message || "加载失败"
    )}</div>`;
  }
}

/**
 * 将模板激活版正文写入提示词编辑器（不建 Job、不自动保存版本）。
 */
async function importTemplateIntoPromptDebug(templateId) {
  const tid = +templateId;
  if (!tid) throw new Error("请选择模板");
  const t = await api(`/templates/${tid}`);
  const text = (t.prompt_text || "").trim();
  if (!text) throw new Error("该模板激活版正文为空，无法导入");

  const editor = document.getElementById("pd-prompt-editor");
  const reasonEl = document.getElementById("pd-change-reason");
  if (editor) {
    editor.value = t.prompt_text || "";
    editor.dataset.dirty = "1";
  }
  if (reasonEl) {
    const name = (t.name || "").trim() || `#${tid}`;
    reasonEl.value = `从模板 #${tid} ${name}（v${t.current_version || 1}）导入`;
    reasonEl.dataset.dirty = "1";
  }
  closePdTmplModal();
  toast(`已导入模板 #${tid}「${t.name || ""}」到编辑器，请检查后点「保存版本」`);
}

async function openPdImportTemplateModal() {
  const modal = document.getElementById("pd-tmpl-modal");
  const search = document.getElementById("pd-tmpl-search");
  if (!modal) {
    toast("导入模板弹层未就绪", true);
    return;
  }
  if (search) search.value = "";
  modal.hidden = false;
  await renderPdTemplateList("");
  setTimeout(() => search?.focus(), 30);
}

document.getElementById("btn-pd-import-template")?.addEventListener("click", () => {
  openPdImportTemplateModal().catch((e) => toast(e.message, true));
});
document.getElementById("pd-tmpl-modal-cancel")?.addEventListener("click", () => {
  closePdTmplModal();
});
document.getElementById("pd-tmpl-modal")?.addEventListener("click", (e) => {
  if (e.target?.id === "pd-tmpl-modal") closePdTmplModal();
});
document.getElementById("pd-tmpl-search")?.addEventListener("input", (e) => {
  const q = e.target.value || "";
  if (pdTmplSearchTimer) clearTimeout(pdTmplSearchTimer);
  pdTmplSearchTimer = setTimeout(() => {
    renderPdTemplateList(q).catch((err) => toast(err.message, true));
  }, 200);
});
document.getElementById("pd-tmpl-search")?.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    e.preventDefault();
    closePdTmplModal();
  }
});

/** ---------- 提示词模板库（二级导航） ---------- */
let ptLibQuery = "";
let ptLibSearchTimer = null;
let ptCurrentId = null;
let ptLibCache = [];
/** 打开详情时的快照，用于判断元信息/正文是否有改动 */
let ptLoadedSnapshot = null;

async function loadPromptTemplateLibrary(selectId) {
  const listEl = document.getElementById("pt-lib-list");
  if (!listEl) return;
  listEl.innerHTML = `<p class="hint" style="padding:12px">加载中…</p>`;
  try {
    const q = (ptLibQuery || "").trim();
    const path = q ? `/templates?q=${encodeURIComponent(q)}` : `/templates`;
    ptLibCache = (await api(path)) || [];
    if (!ptLibCache.length) {
      listEl.innerHTML = `<p class="hint" style="padding:12px">暂无模板。点击「新建模板」创建。</p>`;
    } else {
      listEl.innerHTML = ptLibCache
        .map(
          (t) => `<div class="pt-lib-item${
            ptCurrentId === t.id ? " active" : ""
          }" data-tid="${t.id}">
          <div class="pli-name">#${t.id} ${escapeHtml(t.name || "未命名")}</div>
          <div class="pli-meta">分类 ${escapeHtml(
            t.category || "-"
          )} · 激活 v${t.current_version || 1} · 使用 ${t.usage_count || 0}</div>
        </div>`
        )
        .join("");
      listEl.querySelectorAll(".pt-lib-item[data-tid]").forEach((el) => {
        el.addEventListener("click", () => {
          openPromptTemplateDetail(+el.dataset.tid).catch((e) =>
            toast(e.message, true)
          );
        });
      });
    }
    const want = selectId != null ? +selectId : ptCurrentId;
    if (want && ptLibCache.some((t) => t.id === want)) {
      await openPromptTemplateDetail(want);
    } else if (!ptCurrentId) {
      showPromptTemplateDetailEmpty();
    }
  } catch (e) {
    listEl.innerHTML = `<p class="hint" style="padding:12px">${escapeHtml(
      e.message || "加载失败"
    )}</p>`;
  }
}

function showPromptTemplateDetailEmpty() {
  ptCurrentId = null;
  ptLoadedSnapshot = null;
  const detail = document.getElementById("pt-detail");
  if (detail) detail.hidden = true;
  document
    .querySelectorAll("#pt-lib-list .pt-lib-item")
    .forEach((el) => el.classList.remove("active"));
}

function readPromptTemplateForm() {
  return {
    name: (document.getElementById("pt-name")?.value || "").trim(),
    category:
      (document.getElementById("pt-category")?.value || "general").trim() ||
      "general",
    description: (document.getElementById("pt-description")?.value || "").trim(),
    prompt_text: (document.getElementById("pt-prompt")?.value || "").trim(),
  };
}

async function openPromptTemplateDetail(id) {
  const tid = +id;
  if (!tid) return;
  const t = await api(`/templates/${tid}`);
  const versions = await api(`/templates/${tid}/versions`);
  ptCurrentId = tid;
  ptLoadedSnapshot = {
    name: (t.name || "").trim(),
    category: (t.category || "general").trim() || "general",
    description: (t.description || "").trim(),
    prompt_text: (t.prompt_text || "").trim(),
  };

  const detail = document.getElementById("pt-detail");
  if (detail) detail.hidden = false;

  const nameEl = document.getElementById("pt-name");
  const catEl = document.getElementById("pt-category");
  const descEl = document.getElementById("pt-description");
  const promptEl = document.getElementById("pt-prompt");
  const reasonEl = document.getElementById("pt-change-reason");
  const metaEl = document.getElementById("pt-meta");
  if (nameEl) nameEl.value = t.name || "";
  if (catEl) catEl.value = t.category || "general";
  if (descEl) descEl.value = t.description || "";
  if (promptEl) {
    promptEl.value = t.prompt_text || "";
    promptEl.dataset.loaded = `${t.current_version}:${(t.prompt_text || "").length}`;
  }
  if (reasonEl) reasonEl.value = "";
  if (metaEl) {
    metaEl.textContent = `#${t.id} · 激活 v${t.current_version || 1} · 共 ${
      (versions || []).length
    } 个版本 · 使用 ${t.usage_count || 0}`;
  }

  document.querySelectorAll("#pt-lib-list .pt-lib-item").forEach((el) => {
    el.classList.toggle("active", +el.dataset.tid === tid);
  });

  const verEl = document.getElementById("pt-versions");
  if (verEl) {
    const list = [...(versions || [])].reverse();
    if (!list.length) {
      verEl.innerHTML = `<div class="hint" style="padding:8px">暂无版本</div>`;
    } else {
      verEl.innerHTML = list
        .map((v) => {
          const time = formatPdTime(v.created_at);
          return `<div class="pt-ver-item${v.is_active ? " active" : ""}">
            <div>
              <strong>v${v.version}${v.is_active ? " · 当前" : ""}</strong>
              <div class="pvi-meta">${escapeHtml(time)} · ${escapeHtml(
                v.change_reason || "—"
              )}</div>
            </div>
            ${
              v.is_active
                ? ""
                : `<button type="button" class="secondary btn-pt-activate" data-v="${v.version}">激活</button>`
            }
          </div>`;
        })
        .join("");
      verEl.querySelectorAll(".btn-pt-activate").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            await api(`/templates/${tid}/versions/${btn.dataset.v}/activate`, {
              method: "POST",
            });
            toast(`已激活 v${btn.dataset.v}`);
            await openPromptTemplateDetail(tid);
            await loadPromptTemplateLibrary(tid);
          } catch (e) {
            toast(e.message, true);
          }
        });
      });
    }
  }
}

document.getElementById("pt-lib-q")?.addEventListener("input", (e) => {
  ptLibQuery = e.target.value || "";
  if (ptLibSearchTimer) clearTimeout(ptLibSearchTimer);
  ptLibSearchTimer = setTimeout(() => {
    loadPromptTemplateLibrary().catch((err) => toast(err.message, true));
  }, 200);
});

document.getElementById("btn-pt-new")?.addEventListener("click", async () => {
  const name = window.prompt("新建模板名称：", `模板 ${new Date().toISOString().slice(0, 10)}`);
  if (name == null) return;
  const trimmed = String(name).trim();
  if (!trimmed) {
    toast("名称不能为空", true);
    return;
  }
  try {
    const t = await api("/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: trimmed,
        category: "general",
        prompt_text: "（请编辑模板正文）",
        change_reason: "initial create",
      }),
    });
    toast(`已创建模板 #${t.id}`);
    ptCurrentId = t.id;
    await loadPromptTemplateLibrary(t.id);
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-pt-save-version")?.addEventListener("click", async () => {
  if (!ptCurrentId) {
    toast("请先选择或新建模板", true);
    return;
  }
  const form = readPromptTemplateForm();
  if (!form.name) {
    toast("模板名称不能为空", true);
    return;
  }
  if (!form.prompt_text) {
    toast("提示词正文不能为空", true);
    return;
  }
  const snap = ptLoadedSnapshot || {
    name: "",
    category: "general",
    description: "",
    prompt_text: "",
  };
  const metaChanged =
    form.name !== snap.name ||
    form.category !== snap.category ||
    form.description !== snap.description;
  const bodyChanged = form.prompt_text !== snap.prompt_text;

  if (!metaChanged && !bodyChanged) {
    toast("没有任何更改，未保存", true);
    return;
  }

  const reason =
    (document.getElementById("pt-change-reason")?.value || "").trim() ||
    "模板库保存";
  try {
    const parts = [];
    if (metaChanged) {
      await api(`/templates/${ptCurrentId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          category: form.category,
          description: form.description || null,
        }),
      });
      parts.push("元信息");
    }
    let ver = null;
    if (bodyChanged) {
      ver = await api(`/templates/${ptCurrentId}/versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt_text: form.prompt_text,
          change_reason: reason,
        }),
      });
      parts.push(`正文 v${ver.version}`);
    }
    const reasonEl = document.getElementById("pt-change-reason");
    if (reasonEl) reasonEl.value = "";
    toast(`已保存：${parts.join(" · ")}`);
    await openPromptTemplateDetail(ptCurrentId);
    await loadPromptTemplateLibrary(ptCurrentId);
  } catch (e) {
    toast(e.message, true);
  }
});

/** ---------- 数据管理（一级：库 / 检索 / 清洗） ---------- */
let dsQuery = "";
let dsQueryTimer = null;
let dsModalityFilter = "";
let dsCurrentId = null;
let dsCache = [];

const DS_MODALITY_LABEL = {
  text: "文本",
  image: "图像",
  audio: "音频",
  video: "视频",
};

function renderDsPreview(preview) {
  const head = document.getElementById("ds-preview-head");
  const body = document.getElementById("ds-preview-body");
  if (!head || !body) return;
  const rows = preview || [];
  if (!rows.length) {
    head.innerHTML = "";
    body.innerHTML = `<tr><td class="hint">暂无预览（未上传或为空）</td></tr>`;
    return;
  }
  const cols = Object.keys(rows[0]);
  head.innerHTML = `<tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr>`;
  body.innerHTML = rows
    .map(
      (r) =>
        `<tr>${cols
          .map((c) => {
            const v = r[c];
            const s = v == null ? "" : String(v);
            return `<td title="${escapeHtml(s)}">${escapeHtml(
              s.length > 80 ? s.slice(0, 80) + "…" : s
            )}</td>`;
          })
          .join("")}</tr>`
    )
    .join("");
}

function resetDsCreateColmap() {
  const wrap = document.getElementById("ds-create-colmap");
  const idSel = document.getElementById("ds-create-id-column");
  const textSel = document.getElementById("ds-create-text-column");
  const meta = document.getElementById("ds-create-inspect-meta");
  const prevWrap = document.getElementById("ds-create-preview-wrap");
  if (wrap) wrap.hidden = true;
  if (idSel) idSel.innerHTML = `<option value="">（不使用 ID 列）</option>`;
  if (textSel) {
    textSel.innerHTML = "";
    textSel.required = false;
  }
  if (meta) meta.textContent = "";
  if (prevWrap) prevWrap.hidden = true;
  const head = document.getElementById("ds-create-preview-head");
  const body = document.getElementById("ds-create-preview-body");
  if (head) head.innerHTML = "";
  if (body) body.innerHTML = "";
}

function fillDsCreateColmap(info) {
  const wrap = document.getElementById("ds-create-colmap");
  const idSel = document.getElementById("ds-create-id-column");
  const textSel = document.getElementById("ds-create-text-column");
  const meta = document.getElementById("ds-create-inspect-meta");
  const prevWrap = document.getElementById("ds-create-preview-wrap");
  const cols = Array.isArray(info?.columns) ? info.columns.map(String) : [];
  if (!wrap || !idSel || !textSel) return;
  if (!cols.length) {
    resetDsCreateColmap();
    toast(info?.error_message || "未能识别列", true);
    return;
  }
  const idVal = info.id_column && cols.includes(String(info.id_column)) ? String(info.id_column) : "";
  const textVal =
    info.text_column && cols.includes(String(info.text_column))
      ? String(info.text_column)
      : cols[0];
  idSel.innerHTML =
    `<option value="">（不使用 ID 列）</option>` +
    cols
      .map(
        (c) =>
          `<option value="${escapeHtml(c)}"${c === idVal ? " selected" : ""}>${escapeHtml(
            c
          )}</option>`
      )
      .join("");
  textSel.innerHTML = cols
    .map(
      (c) =>
        `<option value="${escapeHtml(c)}"${c === textVal ? " selected" : ""}>${escapeHtml(
          c
        )}</option>`
    )
    .join("");
  textSel.required = true;
  if (meta) {
    meta.textContent = `${info.row_count || 0} 行 · ${info.column_count || cols.length} 列 · 请确认 id / text 列后创建`;
  }
  // 预览原始表
  const head = document.getElementById("ds-create-preview-head");
  const body = document.getElementById("ds-create-preview-body");
  const rows = info.preview || [];
  if (prevWrap && head && body) {
    if (rows.length) {
      prevWrap.hidden = false;
      head.innerHTML = `<tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr>`;
      body.innerHTML = rows
        .map(
          (r) =>
            `<tr>${cols
              .map((c) => {
                const v = r[c];
                const s = v == null ? "" : String(v);
                return `<td title="${escapeHtml(s)}">${escapeHtml(
                  s.length > 60 ? s.slice(0, 60) + "…" : s
                )}</td>`;
              })
              .join("")}</tr>`
        )
        .join("");
    } else {
      prevWrap.hidden = true;
    }
  }
  wrap.hidden = false;
}

function showDsDetailEmpty() {
  dsCurrentId = null;
  const detail = document.getElementById("ds-detail");
  if (detail) detail.hidden = true;
  document
    .querySelectorAll("#ds-list .ds-item")
    .forEach((el) => el.classList.remove("active"));
}

async function loadManagedDatasets(selectId) {
  const listEl = document.getElementById("ds-list");
  if (!listEl) return;
  listEl.innerHTML = `<p class="hint" style="padding:12px">加载中…</p>`;
  try {
    const params = new URLSearchParams();
    if (dsQuery.trim()) params.set("q", dsQuery.trim());
    if (dsModalityFilter) params.set("modality", dsModalityFilter);
    const qs = params.toString();
    dsCache = (await api(`/datasets${qs ? `?${qs}` : ""}`)) || [];
    if (!dsCache.length) {
      listEl.innerHTML = `<p class="hint" style="padding:12px">暂无数据集。点击「新建」创建。</p>`;
    } else {
      listEl.innerHTML = dsCache
        .map((d) => {
          const mod = DS_MODALITY_LABEL[d.modality] || d.modality || "—";
          return `<div class="ds-item${dsCurrentId === d.id ? " active" : ""}" data-id="${d.id}">
            <div class="dsi-name">#${d.id} ${escapeHtml(d.name || "未命名")}</div>
            <div class="dsi-meta">${escapeHtml(mod)} · ${d.file_format || "—"} · ${
              d.row_count || 0
            } 行 · ${escapeHtml(d.status || "—")}</div>
          </div>`;
        })
        .join("");
      listEl.querySelectorAll(".ds-item[data-id]").forEach((el) => {
        el.addEventListener("click", () => {
          openManagedDataset(+el.dataset.id).catch((e) => toast(e.message, true));
        });
      });
    }
    const want = selectId != null ? +selectId : dsCurrentId;
    if (want && dsCache.some((d) => d.id === want)) {
      await openManagedDataset(want);
    } else if (!dsCurrentId) {
      showDsDetailEmpty();
    }
  } catch (e) {
    listEl.innerHTML = `<p class="hint" style="padding:12px">${escapeHtml(
      e.message || "加载失败"
    )}</p>`;
  }
}

async function openManagedDataset(id) {
  const tid = +id;
  if (!tid) return;
  const d = await api(`/datasets/${tid}?preview=true`);
  dsCurrentId = tid;
  const detail = document.getElementById("ds-detail");
  if (detail) detail.hidden = false;

  const nameEl = document.getElementById("ds-name");
  const descEl = document.getElementById("ds-description");
  const metaEl = document.getElementById("ds-meta");
  const fileLabel = document.getElementById("ds-file-label");
  const trainPaths = document.getElementById("ds-train-paths");
  const btnDl = document.getElementById("btn-ds-download");

  if (nameEl) nameEl.value = d.name || "";
  if (descEl) descEl.value = d.description || "";
  if (metaEl) {
    const vec = d.vector_ready ? `向量就绪 d=${d.vector_dim || "—"}` : "向量未建";
    metaEl.textContent = `#${d.id} · 存储 ${d.storage || "files"} · ${
      d.row_count || 0
    } 行 · ${vec} · ${d.status || "—"}`;
  }
  if (fileLabel) {
    fileLabel.textContent = d.has_file
      ? `${d.original_filename || "训练包"} · ${d.row_count || 0} 条 · JSONL+CSV`
      : "无训练数据";
  }
  if (trainPaths) {
    trainPaths.textContent = d.has_file
      ? `训练入口：data.jsonl（推荐） · data.csv · media/ · vectors/`
      : "—";
    if (d.train_paths?.jsonl) trainPaths.title = d.train_paths.jsonl;
  }
  if (btnDl) btnDl.disabled = !d.has_file;
  if (d.status === "error" && d.error_message) {
    toast(d.error_message, true);
  }
  renderDsPreview(d.preview || []);
  document.querySelectorAll("#ds-list .ds-item").forEach((el) => {
    el.classList.toggle("active", +el.dataset.id === tid);
  });
}

/** ---------- 数据检索（数据管理 · 二级） ---------- */
let dssDatasetCache = [];
let dssDsQuery = "";
let dssDsQueryTimer = null;
let dssCurrentId = null;
/** @type {{ mode: string, hits: any[], total: number, query?: string }} */
let dssResultState = { mode: "browse", hits: [], total: 0 };
let dssPage = 1;
let dssPageSize = 50;

function dssSetHidden(el, hide) {
  if (!el) return;
  if (hide) {
    el.hidden = true;
    el.setAttribute("hidden", "");
    el.style.display = "none";
  } else {
    el.hidden = false;
    el.removeAttribute("hidden");
    el.style.display = "";
  }
}

function dssUpdateModeUI() {
  const mode = document.getElementById("dss-mode")?.value || "keywords";
  const matchOpt = document.getElementById("dss-options-match");
  const vecOpt = document.getElementById("dss-options-vector");
  const label = document.getElementById("dss-query-label");
  const ta = document.getElementById("dss-query");
  const isVec = mode === "vector" || mode === "vector_fast";
  // 命中任一/全部 + 区分大小写：仅关键词
  dssSetHidden(matchOpt, mode !== "keywords");
  dssSetHidden(vecOpt, !isVec);
  // 查询框标签统一为「检索条件」，仅切换 placeholder 提示
  if (label) label.textContent = "检索条件";
  if (mode === "keywords") {
    if (ta) ta.placeholder = "多个关键词用空格或逗号分隔，例如：作业 孩子";
  } else if (mode === "regex") {
    if (ta) ta.placeholder = "例如：作业.{0,12}课外|写完了（OR 用 |，AND 用 (?=.*a)(?=.*b)）";
  } else if (mode === "vector_fast") {
    if (ta) ta.placeholder = "偏字面相关的快速向量检索…";
  } else {
    if (ta) ta.placeholder = "语义相近检索，例如：小孩做完功课可以看课外书吗";
  }
  dssRefreshVectorMeta();
}

function dssRefreshVectorMeta() {
  const meta = document.getElementById("dss-vector-meta");
  if (!meta) return;
  const d = dssDatasetCache.find((x) => x.id === dssCurrentId);
  if (!d) {
    meta.textContent = "请先选择数据集";
    return;
  }
  meta.textContent = d.vector_ready
    ? `索引就绪 · ${d.vector_model || "—"} · dim=${d.vector_dim || "—"} · n=${
        d.vector_count || 0
      }`
    : "尚未构建向量索引（可点「重建向量索引」）";
}

function dssTotalPages() {
  const total = dssResultState.total || 0;
  const size = dssPageSize || 50;
  if (total <= 0) return 1;
  return Math.max(1, Math.ceil(total / size));
}

function renderDssPager() {
  const pager = document.getElementById("dss-pager");
  const info = document.getElementById("dss-page-info");
  const prev = document.getElementById("dss-page-prev");
  const next = document.getElementById("dss-page-next");
  const sizeSel = document.getElementById("dss-page-size");
  if (!pager) return;
  const total = dssResultState.total || 0;
  const pages = dssTotalPages();
  if (total <= 0) {
    pager.hidden = true;
    return;
  }
  pager.hidden = false;
  if (dssPage > pages) dssPage = pages;
  if (dssPage < 1) dssPage = 1;
  if (info) info.textContent = `第 ${dssPage} / ${pages} 页 · 共 ${total} 条`;
  if (prev) prev.disabled = dssPage <= 1;
  if (next) next.disabled = dssPage >= pages;
  if (sizeSel && String(sizeSel.value) !== String(dssPageSize)) {
    sizeSel.value = String(dssPageSize);
  }
}

async function dssGoPage(page) {
  const pages = dssTotalPages();
  dssPage = Math.min(Math.max(1, page), pages);
  if (dssResultState.mode === "browse") {
    await loadDssBrowsePage();
  } else {
    renderDssResultsPage();
  }
}

function renderDssResultsPage() {
  const head = document.getElementById("dss-result-head");
  const body = document.getElementById("dss-result-body");
  const countEl = document.getElementById("dss-result-count");
  const titleEl = document.getElementById("dss-result-title");
  const badgeEl = document.getElementById("dss-result-badge");
  if (!head || !body) return;

  const mode = dssResultState.mode || "browse";
  // browse 使用服务端分页，由 loadDssBrowsePage 渲染
  if (mode === "browse" && dssResultState._serverPage) {
    return;
  }
  const allHits = dssResultState.hits || [];
  const total = dssResultState.total || allHits.length;
  const size = dssPageSize || 50;
  const pages = Math.max(1, Math.ceil(total / size) || 1);
  if (dssPage > pages) dssPage = pages;
  if (dssPage < 1) dssPage = 1;
  const start = (dssPage - 1) * size;
  const hits = allHits.slice(start, start + size);

  if (titleEl) {
    titleEl.textContent =
      mode === "browse"
        ? "数据集内容"
        : mode === "vector"
          ? "向量检索结果"
          : "检索结果";
  }
  if (badgeEl) {
    if (mode === "browse") {
      badgeEl.textContent = "默认（无规则）";
      badgeEl.className = "dss-badge dss-badge-default";
    } else if (mode === "keywords") {
      badgeEl.textContent = "关键词";
      badgeEl.className = "dss-badge";
    } else if (mode === "regex") {
      badgeEl.textContent = "正则";
      badgeEl.className = "dss-badge";
    } else if (mode === "vector_fast") {
      badgeEl.textContent = "快速向量(TF-IDF)";
      badgeEl.className = "dss-badge";
    } else {
      badgeEl.textContent = "语义向量(BGE)";
      badgeEl.className = "dss-badge";
    }
  }
  if (countEl) {
    if (total === 0) {
      countEl.textContent = "（0 条）";
    } else {
      const from = start + 1;
      const to = Math.min(start + hits.length, total);
      countEl.textContent = `（${from}–${to} / 共 ${total} 条）`;
    }
  }

  if (!hits.length) {
    head.innerHTML = "";
    body.innerHTML = `<tr><td class="hint">${
      mode === "browse" ? "数据集为空" : "无匹配结果"
    }</td></tr>`;
    renderDssPager();
    return;
  }

  if (mode === "keywords") {
    head.innerHTML = `<tr><th>#</th><th>score</th><th>命中词</th><th>id</th><th>text</th></tr>`;
    body.innerHTML = hits
      .map((r, i) => {
        const text = r.text == null ? "" : String(r.text);
        const mks = (r.matched_keywords || []).join("、");
        const score = typeof r.score === "number" ? r.score.toFixed(3) : "—";
        const rowNo = start + i + 1;
        return `<tr>
          <td>${rowNo}</td>
          <td>${score}</td>
          <td>${escapeHtml(mks)}</td>
          <td>${escapeHtml(r.id == null ? "" : String(r.id))}</td>
          <td title="${escapeHtml(text)}">${escapeHtml(
            text.length > 160 ? text.slice(0, 160) + "…" : text
          )}</td>
        </tr>`;
      })
      .join("");
  } else if (mode === "regex") {
    head.innerHTML = `<tr><th>#</th><th>匹配片段</th><th>id</th><th>text</th></tr>`;
    body.innerHTML = hits
      .map((r, i) => {
        const text = r.text == null ? "" : String(r.text);
        const rowNo = start + i + 1;
        return `<tr>
          <td>${rowNo}</td>
          <td>${escapeHtml(r.match_text || "")}</td>
          <td>${escapeHtml(r.id == null ? "" : String(r.id))}</td>
          <td title="${escapeHtml(text)}">${escapeHtml(
            text.length > 160 ? text.slice(0, 160) + "…" : text
          )}</td>
        </tr>`;
      })
      .join("");
  } else if (mode === "vector" || mode === "vector_fast") {
    head.innerHTML = `<tr><th>#</th><th>score</th><th>cosine</th><th>类型</th><th>id</th><th>text</th></tr>`;
    body.innerHTML = hits
      .map((r, i) => {
        const text = r.text == null ? "" : String(r.text);
        const score = typeof r.score === "number" ? r.score.toFixed(4) : "—";
        const cos =
          typeof r.cosine === "number" ? r.cosine.toFixed(4) : score;
        const kindLabel =
          r.index_label ||
          (mode === "vector_fast" ? "快速向量(TF-IDF)" : "语义向量(BGE)");
        const rowNo = start + i + 1;
        return `<tr>
          <td>${rowNo}</td>
          <td>${score}</td>
          <td>${cos}</td>
          <td>${escapeHtml(kindLabel)}</td>
          <td>${escapeHtml(r.id == null ? "" : String(r.id))}</td>
          <td title="${escapeHtml(text)}">${escapeHtml(
            text.length > 160 ? text.slice(0, 160) + "…" : text
          )}</td>
        </tr>`;
      })
      .join("");
  } else {
    // browse：默认（无规则）
    head.innerHTML = `<tr><th>#</th><th>id</th><th>text</th></tr>`;
    body.innerHTML = hits
      .map((r, i) => {
        const text = r.text == null ? "" : String(r.text);
        const rowNo = r.rank ?? r.seq ?? start + i + 1;
        return `<tr>
          <td>${rowNo}</td>
          <td>${escapeHtml(r.id == null ? "" : String(r.id))}</td>
          <td title="${escapeHtml(text)}">${escapeHtml(
            text.length > 200 ? text.slice(0, 200) + "…" : text
          )}</td>
        </tr>`;
      })
      .join("");
  }
  renderDssPager();
}

/** 兼容旧调用名 */
function renderDssResults(res) {
  const hits = res?.hits || [];
  dssResultState = {
    mode: res?.mode || "browse",
    hits,
    total: typeof res?.total === "number" ? res.total : hits.length,
    query: res?.query,
  };
  dssPage = 1;
  renderDssResultsPage();
}

async function loadDssBrowsePage() {
  if (!dssCurrentId) return;
  const status = document.getElementById("dss-status");
  if (status) status.textContent = "加载内容…";
  // 浏览模式：服务端按页拉取（避免一次加载过大）
  const offset = (dssPage - 1) * dssPageSize;
  try {
    let res;
    try {
      res = await api(
        `/datasets/${dssCurrentId}/records?limit=${dssPageSize}&offset=${offset}`
      );
    } catch (e1) {
      // 兼容旧后端无 /records：用详情 preview 兜底
      const d = await api(`/datasets/${dssCurrentId}?preview=true`);
      const preview = d.preview || [];
      const total = d.row_count || preview.length;
      const hits = preview.map((row, i) => ({
        rank: i + 1,
        seq: i + 1,
        id: row.id ?? row.external_id ?? null,
        text: row.text ?? row.content ?? Object.values(row)[0],
      }));
      res = {
        mode: "browse",
        hits,
        total,
        count: hits.length,
        offset: 0,
        limit: hits.length,
      };
      if (status) status.textContent = "预览兜底（请重启后端以启用完整分页）";
    }
    // 合并为完整分页状态：保留 total，当前页 hits
    // 为了统一 pager，browse 时 hits 只存当前页，用 total 算页数
    dssResultState = {
      mode: "browse",
      hits: res.hits || [],
      total: res.total || 0,
      // 标记 browse 为服务端分页：hits 仅当前页
      _serverPage: true,
      _offset: offset,
    };
    // 渲染：browse 服务端分页时不再 slice
    const head = document.getElementById("dss-result-head");
    const body = document.getElementById("dss-result-body");
    const countEl = document.getElementById("dss-result-count");
    const titleEl = document.getElementById("dss-result-title");
    const badgeEl = document.getElementById("dss-result-badge");
    if (titleEl) titleEl.textContent = "数据集内容";
    if (badgeEl) {
      badgeEl.textContent = "默认（无规则）";
      badgeEl.className = "dss-badge dss-badge-default";
    }
    const hits = res.hits || [];
    const total = res.total || 0;
    if (countEl) {
      if (!total) countEl.textContent = "（0 条）";
      else {
        const from = total ? offset + 1 : 0;
        const to = offset + hits.length;
        countEl.textContent = `（${from}–${to} / 共 ${total} 条）`;
      }
    }
    if (!head || !body) return;
    if (!hits.length) {
      head.innerHTML = "";
      body.innerHTML = `<tr><td class="hint">数据集为空</td></tr>`;
    } else {
      head.innerHTML = `<tr><th>#</th><th>id</th><th>text</th></tr>`;
      body.innerHTML = hits
        .map((r) => {
          const text = r.text == null ? "" : String(r.text);
          return `<tr>
            <td>${r.rank ?? r.seq ?? ""}</td>
            <td>${escapeHtml(r.id == null ? "" : String(r.id))}</td>
            <td title="${escapeHtml(text)}">${escapeHtml(
              text.length > 200 ? text.slice(0, 200) + "…" : text
            )}</td>
          </tr>`;
        })
        .join("");
    }
    renderDssPager();
    if (status) status.textContent = "默认（无规则）· 可继续检索";
  } catch (e) {
    if (status) status.textContent = "加载失败";
    toast(e.message || "加载数据集内容失败", true);
  }
}

function dssSelectedDataset() {
  if (!dssCurrentId) return null;
  return dssDatasetCache.find((x) => x.id === dssCurrentId) || null;
}

/** 主界面只显示已选数据集名称 */
function updateDssSelectedLabel() {
  const nameEl = document.getElementById("dss-ds-selected-name");
  const meta = document.getElementById("dss-selected-meta");
  const pickBtn = document.getElementById("btn-dss-pick-ds");
  const d = dssSelectedDataset();
  if (nameEl) {
    if (d) {
      nameEl.textContent = d.name || `数据集 #${d.id}`;
      nameEl.classList.remove("is-empty");
      nameEl.title = `#${d.id} · ${d.row_count || 0} 条`;
    } else if (dssCurrentId) {
      nameEl.textContent = `数据集 #${dssCurrentId}`;
      nameEl.classList.remove("is-empty");
      nameEl.title = "";
    } else {
      nameEl.textContent = "未选择数据集";
      nameEl.classList.add("is-empty");
      nameEl.title = "";
    }
  }
  if (meta) {
    meta.textContent = d
      ? d.name || `数据集 #${d.id}`
      : dssCurrentId
        ? `数据集 #${dssCurrentId}`
        : "请选择数据集";
  }
  if (pickBtn) {
    pickBtn.textContent = dssCurrentId ? "更换" : "选择数据集";
  }
}

function openDssDatasetModal() {
  const modal = document.getElementById("dss-ds-modal");
  if (!modal) return;
  modal.hidden = false;
  renderDssDatasetList();
  const q = document.getElementById("dss-ds-q");
  if (q) {
    // 打开时保留上次筛选，并聚焦搜索框
    setTimeout(() => q.focus(), 30);
  }
}

function closeDssDatasetModal() {
  const modal = document.getElementById("dss-ds-modal");
  if (modal) modal.hidden = true;
}

function renderDssDatasetList() {
  const listEl = document.getElementById("dss-ds-list");
  if (!listEl) return;
  const q = (dssDsQuery || "").trim().toLowerCase();
  let items = dssDatasetCache || [];
  if (q) {
    items = items.filter((d) => {
      const blob = `${d.id} ${d.name || ""} ${d.description || ""} ${
        d.original_filename || ""
      }`.toLowerCase();
      return blob.includes(q);
    });
  }
  if (!items.length) {
    listEl.innerHTML = `<p class="hint" style="padding:12px">${
      dssDatasetCache.length ? "无匹配数据集" : "暂无数据集，请先在数据集库创建"
    }</p>`;
    return;
  }
  listEl.innerHTML = items
    .map((d) => {
      const active = dssCurrentId === d.id ? " active" : "";
      return `<div class="ds-item${active}" data-id="${d.id}" role="option" aria-selected="${
        dssCurrentId === d.id ? "true" : "false"
      }">
        <div class="dsi-name">${escapeHtml(d.name || "未命名")}</div>
        <div class="dsi-meta">#${d.id} · ${d.row_count || 0} 条 · ${escapeHtml(
          d.status || "—"
        )}${d.vector_ready ? " · 向量就绪" : ""}</div>
      </div>`;
    })
    .join("");
  listEl.querySelectorAll(".ds-item[data-id]").forEach((el) => {
    el.addEventListener("click", () => {
      selectDssDataset(+el.dataset.id)
        .then(() => closeDssDatasetModal())
        .catch((e) => toast(e.message, true));
    });
  });
}

async function selectDssDataset(id) {
  const tid = +id;
  if (!tid) return;
  dssCurrentId = tid;
  dssPage = 1;
  dssResultState = { mode: "browse", hits: [], total: 0, _serverPage: true };
  updateDssSelectedLabel();
  renderDssDatasetList();
  dssRefreshVectorMeta();
  await loadDssBrowsePage();
}

async function initDatasetSearchPage() {
  const formCard = document.querySelector("#view-dataset-search .dss-form-card");
  if (!formCard) return;
  try {
    dssDatasetCache = (await api("/datasets")) || [];
  } catch (e) {
    dssDatasetCache = [];
    toast(e.message || "加载数据集失败", true);
  }
  updateDssSelectedLabel();
  dssUpdateModeUI();
  const status = document.getElementById("dss-status");
  if (status) {
    status.textContent = dssDatasetCache.length
      ? dssCurrentId
        ? "就绪"
        : "请选择数据集"
      : "请先在数据集库中创建数据集";
  }
  // 若之前选中的仍在列表中，刷新内容；否则默认选中第一项
  if (dssCurrentId && dssDatasetCache.some((d) => d.id === dssCurrentId)) {
    await selectDssDataset(dssCurrentId);
  } else if (dssDatasetCache.length) {
    await selectDssDataset(dssDatasetCache[0].id);
  } else {
    dssCurrentId = null;
    updateDssSelectedLabel();
    renderDssResults({ mode: "browse", hits: [], count: 0, total: 0 });
  }
}

document.getElementById("dss-mode")?.addEventListener("change", () => dssUpdateModeUI());

document.getElementById("btn-dss-pick-ds")?.addEventListener("click", () => {
  openDssDatasetModal();
});
document.getElementById("dss-ds-selected")?.addEventListener("click", () => {
  openDssDatasetModal();
});
document.getElementById("dss-ds-modal-cancel")?.addEventListener("click", () => {
  closeDssDatasetModal();
});
document.getElementById("dss-ds-modal")?.addEventListener("click", (e) => {
  // 点击遮罩关闭
  if (e.target === e.currentTarget) closeDssDatasetModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const modal = document.getElementById("dss-ds-modal");
  if (modal && !modal.hidden) closeDssDatasetModal();
});

document.getElementById("dss-ds-q")?.addEventListener("input", (e) => {
  dssDsQuery = e.target.value || "";
  if (dssDsQueryTimer) clearTimeout(dssDsQueryTimer);
  dssDsQueryTimer = setTimeout(() => renderDssDatasetList(), 150);
});

document.getElementById("btn-dss-run")?.addEventListener("click", async () => {
  const dsId = dssCurrentId;
  const mode = document.getElementById("dss-mode")?.value || "keywords";
  const query = (document.getElementById("dss-query")?.value || "").trim();
  const status = document.getElementById("dss-status");
  if (!dsId) {
    toast("请先选择数据集", true);
    return;
  }
  // 条件为空：恢复默认（无规则）浏览
  if (!query) {
    dssPage = 1;
    dssResultState = { mode: "browse", hits: [], total: 0, _serverPage: true };
    await loadDssBrowsePage();
    toast("已恢复默认浏览（无规则）");
    return;
  }
  const match =
    document.querySelector('input[name="dss-match"]:checked')?.value || "any";
  const caseSensitive =
    mode === "keywords" ? !!document.getElementById("dss-case")?.checked : false;
  // 与后端校验对齐：top_k ≤ 200，limit ≤ 500
  let topK = +(document.getElementById("dss-top-k")?.value || 20) || 20;
  topK = Math.min(200, Math.max(1, topK));
  // 向量模式可选阈值：score ≥ min_score，再取 Top-K；留空 = 不过滤
  const minScoreRaw = (document.getElementById("dss-min-score")?.value || "").trim();
  let minScore = null;
  if (minScoreRaw !== "" && (mode === "vector" || mode === "vector_fast")) {
    const parsed = Number(minScoreRaw);
    if (!Number.isFinite(parsed)) {
      toast("阈值必须是数字（可留空表示不限）", true);
      return;
    }
    minScore = parsed;
  }
  // 关键词/正则：一次最多拉 500 条命中，再前端分页
  const limit = 500;
  if (status) status.textContent = "检索中…";
  try {
    const body = {
      mode,
      query,
      match,
      case_sensitive: caseSensitive,
      top_k: topK,
      limit,
    };
    if (minScore != null) body.min_score = minScore;
    const res = await api(`/datasets/${dsId}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    dssResultState = {
      mode: res.mode || mode,
      hits: res.hits || [],
      total:
        typeof res.total === "number"
          ? res.total
          : (res.hits || []).length,
      query: res.query || query,
      min_score: res.min_score ?? minScore,
      _serverPage: false,
    };
    dssPage = 1;
    renderDssResultsPage();
    const thrHint =
      res.min_score != null && res.min_score !== undefined
        ? ` · 阈值≥${res.min_score}`
        : minScore != null
          ? ` · 阈值≥${minScore}`
          : "";
    if (status) status.textContent = `完成 · ${dssResultState.total || 0} 条${thrHint}`;
    toast(`检索完成：${dssResultState.total || 0} 条${thrHint}`);
  } catch (e) {
    if (status) status.textContent = "失败";
    toast(e.message || "检索失败", true);
  }
});

document.getElementById("dss-page-prev")?.addEventListener("click", () => {
  dssGoPage(dssPage - 1).catch((e) => toast(e.message, true));
});
document.getElementById("dss-page-next")?.addEventListener("click", () => {
  dssGoPage(dssPage + 1).catch((e) => toast(e.message, true));
});
document.getElementById("dss-page-size")?.addEventListener("change", (e) => {
  dssPageSize = +(e.target.value || 50) || 50;
  dssPage = 1;
  if (dssResultState.mode === "browse") {
    loadDssBrowsePage().catch((err) => toast(err.message, true));
  } else {
    renderDssResultsPage();
  }
});

document.getElementById("dss-query")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    document.getElementById("btn-dss-run")?.click();
  }
});

document.getElementById("ds-q")?.addEventListener("input", (e) => {
  dsQuery = e.target.value || "";
  if (dsQueryTimer) clearTimeout(dsQueryTimer);
  dsQueryTimer = setTimeout(() => {
    loadManagedDatasets().catch((err) => toast(err.message, true));
  }, 200);
});

document.getElementById("ds-modality-filter")?.addEventListener("click", (e) => {
  const chip = e.target.closest(".ds-mod-chip");
  if (!chip || chip.disabled) return;
  dsModalityFilter = chip.dataset.mod || "";
  document
    .querySelectorAll("#ds-modality-filter .ds-mod-chip")
    .forEach((c) => c.classList.toggle("active", c === chip));
  loadManagedDatasets().catch((err) => toast(err.message, true));
});

document.getElementById("btn-ds-new")?.addEventListener("click", () => {
  const modal = document.getElementById("ds-create-modal");
  const form = document.getElementById("form-ds-create");
  if (form) form.reset();
  const mod = document.getElementById("ds-create-modality");
  if (mod) mod.value = "text";
  resetDsCreateColmap();
  if (modal) modal.hidden = false;
  setTimeout(() => document.getElementById("ds-create-name")?.focus(), 30);
});

document.getElementById("ds-create-cancel")?.addEventListener("click", () => {
  const modal = document.getElementById("ds-create-modal");
  if (modal) modal.hidden = true;
  resetDsCreateColmap();
});
document.getElementById("ds-create-modal")?.addEventListener("click", (e) => {
  if (e.target?.id === "ds-create-modal") {
    e.target.hidden = true;
    resetDsCreateColmap();
  }
});

document.getElementById("ds-create-file")?.addEventListener("change", async (e) => {
  const input = e.target;
  const file = input?.files?.[0];
  if (!file) {
    resetDsCreateColmap();
    return;
  }
  const meta = document.getElementById("ds-create-inspect-meta");
  if (meta) meta.textContent = "解析文件中…";
  const colmap = document.getElementById("ds-create-colmap");
  if (colmap) colmap.hidden = false;
  try {
    const fd = new FormData();
    fd.append("file", file);
    const headers = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(API + "/datasets/inspect-upload", {
      method: "POST",
      body: fd,
      headers,
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || res.statusText);
    }
    const info = await res.json();
    fillDsCreateColmap(info);
  } catch (err) {
    resetDsCreateColmap();
    if (input) input.value = "";
    toast(err.message || "解析文件失败", true);
  }
});

document.getElementById("form-ds-create")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = (document.getElementById("ds-create-name")?.value || "").trim();
  const modality = document.getElementById("ds-create-modality")?.value || "text";
  const description = (document.getElementById("ds-create-desc")?.value || "").trim();
  const fileInput = document.getElementById("ds-create-file");
  const hasFile = !!(fileInput?.files?.length);
  const idCol = (document.getElementById("ds-create-id-column")?.value || "").trim();
  const textCol = (document.getElementById("ds-create-text-column")?.value || "").trim();
  if (!name) {
    toast("名称不能为空", true);
    return;
  }
  if (modality !== "text") {
    toast("当前仅支持文本模态", true);
    return;
  }
  if (!hasFile) {
    toast("请上传数据文件", true);
    return;
  }
  if (!textCol) {
    toast("请先选择文件并勾选 Text 列", true);
    return;
  }
  if (idCol && idCol === textCol) {
    toast("ID 列与 Text 列不能相同", true);
    return;
  }
  try {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("modality", modality);
    fd.append("description", description);
    fd.append("file", fileInput.files[0]);
    fd.append("id_column", idCol);
    fd.append("text_column", textCol);
    const headers = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(API + "/datasets", {
      method: "POST",
      body: fd,
      headers,
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || res.statusText);
    }
    const d = await res.json();
    const modal = document.getElementById("ds-create-modal");
    if (modal) modal.hidden = true;
    resetDsCreateColmap();
    toast(`已创建数据集 #${d.id}（${d.row_count || 0} 行，id/text 已按所选列生成）`);
    dsCurrentId = d.id;
    await loadManagedDatasets(d.id);
  } catch (err) {
    toast(err.message || "创建失败", true);
  }
});

document.getElementById("btn-ds-save")?.addEventListener("click", async () => {
  if (!dsCurrentId) {
    toast("请先选择数据集", true);
    return;
  }
  const name = (document.getElementById("ds-name")?.value || "").trim();
  if (!name) {
    toast("名称不能为空", true);
    return;
  }
  try {
    await api(`/datasets/${dsCurrentId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        description: (document.getElementById("ds-description")?.value || "").trim() || null,
      }),
    });
    toast("已保存");
    await loadManagedDatasets(dsCurrentId);
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-ds-delete")?.addEventListener("click", async () => {
  if (!dsCurrentId) return;
  if (!window.confirm(`确定删除数据集 #${dsCurrentId}？此操作不可恢复。`)) return;
  try {
    await api(`/datasets/${dsCurrentId}`, { method: "DELETE" });
    toast("已删除");
    dsCurrentId = null;
    await loadManagedDatasets();
    showDsDetailEmpty();
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-ds-download")?.addEventListener("click", async () => {
  if (!dsCurrentId) return;
  try {
    const headers = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(API + `/datasets/${dsCurrentId}/download`, { headers });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || res.statusText);
    }
    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    let filename = `dataset_${dsCurrentId}`;
    const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(cd);
    if (m) filename = decodeURIComponent(m[1].replace(/"/g, ""));
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    toast(e.message, true);
  }
});

/** ---------- 数据清洗（数据管理 · 二级） ---------- */
/** 删除只记 id，原始 data.jsonl 不改写；支持按批次恢复 */
let dcDatasetCache = [];
let dcCurrentId = null;
let dcDsQuery = "";
let dcDsQueryTimer = null;
/** @type {any|null} */
let dcLastPreview = null;
/** @type {any|null} */
let dcOverview = null;
/** @type {{ mode: string, hits: any[], total: number, selectedMark?: boolean, selectable?: boolean, _serverPage?: boolean }} */
let dcResultState = {
  mode: "browse",
  hits: [],
  total: 0,
  selectedMark: false,
  selectable: false,
  _serverPage: true,
};
let dcPage = 1;
let dcPageSize = 50;
/** 匹配预览下的勾选 id 集合（默认不勾选；应用阈值/反选/点行可改） */
let dcMatchSelectedIds = new Set();
/** 当前选中是否处于「反选」态（仅 UI 标记；反选=对当前结果勾选取反，不重跑匹配） */
let dcInvertApplied = false;
/** 已生效的结果阈值（仅点「应用阈值」后写入；输入框草稿不直接生效） */
let dcAppliedScoreThreshold = null;

function dcSetHidden(el, hide) {
  if (!el) return;
  if (hide) {
    el.hidden = true;
    el.setAttribute("hidden", "");
    el.style.display = "none";
  } else {
    el.hidden = false;
    el.removeAttribute("hidden");
    el.style.display = "";
  }
}

function dcMethodNeedsScoreFilter() {
  const method = document.getElementById("dc-method")?.value || "keywords";
  return method === "vector" || method === "vector_fast" || method === "llm";
}

/** 已生效的结果阈值（点「应用阈值」后才有；用于角标/反选提示） */
function getDcScoreThreshold() {
  if (!dcMethodNeedsScoreFilter()) return null;
  return dcAppliedScoreThreshold;
}

function syncDcApplyScoreButton() {
  const btn = document.getElementById("btn-dc-apply-score");
  if (!btn) return;
  const can =
    dcMethodNeedsScoreFilter() &&
    !!dcLastPreview &&
    dcResultState.mode === "match" &&
    !!dcResultState.selectable &&
    (dcResultState.hits || []).length > 0;
  btn.disabled = !can;
}

/**
 * 点击「应用阈值」：把输入框数值固化为生效阈值，并本地筛选「选中」。
 * - 必须输入有效数字；空输入不会生效（提示先填阈值）
 * - 有数值：在仍展示全部结果的前提下，选中 score ≥ 阈值
 * - 不重跑匹配；不隐藏低分样本
 */
function applyDcScoreThreshold(opts = {}) {
  const silent = !!opts.silent;
  if (dcResultState.mode !== "match" || !dcResultState.selectable) {
    if (!silent) toast("请先预览匹配，再应用阈值", true);
    return;
  }
  if (!dcMethodNeedsScoreFilter()) {
    renderDcTablePage();
    return;
  }
  const hits = dcResultState.hits || [];
  if (!hits.length) {
    if (!silent) toast("当前没有可筛选的结果", true);
    return;
  }
  const raw = (document.getElementById("dc-min-score")?.value || "").trim();
  if (raw === "") {
    if (!silent) toast("请先在「结果阈值」输入数字，再点应用阈值", true);
    const el = document.getElementById("dc-min-score");
    if (el) el.focus();
    return;
  }
  const thr = Number(raw);
  if (!Number.isFinite(thr)) {
    if (!silent) toast("阈值必须是有效数字，例如 0.6", true);
    return;
  }
  dcAppliedScoreThreshold = thr;
  dcInvertApplied = false;
  // 只改勾选，不删行：全部 hits 仍展示
  dcMatchSelectedIds = new Set(
    hits
      .filter((r) => {
        if (r.score == null || r.score === "") return false;
        const sc = Number(r.score);
        return Number.isFinite(sc) && sc >= thr;
      })
      .map((r) => (r.id != null ? String(r.id) : ""))
      .filter(Boolean)
  );
  renderDcTablePage();
  syncDcInvertButton();
  syncDcApplyScoreButton();
  if (!silent) {
    const total = hits.length;
    toast(`已应用阈值 ≥ ${thr}：选中 ${dcMatchSelectedIds.size} / 共 ${total} 条（列表仍显示全部）`);
    flashButton(document.getElementById("btn-dc-apply-score"), "ok", 500);
  }
}

function dcUpdateMethodUI() {
  const method = document.getElementById("dc-method")?.value || "keywords";
  const matchOpt = document.getElementById("dc-options-match");
  const scoreOpt = document.getElementById("dc-options-score");
  const minScoreEl = document.getElementById("dc-min-score");
  const minScoreField = document.getElementById("dc-min-score-field");
  const scoreHint = document.getElementById("dc-score-hint");
  const ta = document.getElementById("dc-query");
  const isVec = method === "vector" || method === "vector_fast";
  const isLlm = method === "llm";
  dcSetHidden(matchOpt, method !== "keywords");
  // 结果阈值：向量(TF-IDF/BGE) + 大模型（匹配后本地筛选选中）
  const showScore = isVec || isLlm;
  if (scoreOpt) {
    if (showScore) {
      scoreOpt.hidden = false;
      scoreOpt.removeAttribute("hidden");
      scoreOpt.style.display = "flex";
    } else {
      scoreOpt.hidden = true;
      scoreOpt.setAttribute("hidden", "");
      scoreOpt.style.display = "none";
    }
  }
  if (minScoreEl) {
    if (isLlm) {
      minScoreEl.min = "0";
      minScoreEl.max = "1";
      minScoreEl.step = "0.05";
      minScoreEl.placeholder = "例如 0.6";
      if (minScoreField) {
        minScoreField.title =
          "预览后默认全不选；输入后点「应用阈值」选中 score≥；反选在右侧";
      }
    } else if (isVec) {
      minScoreEl.min = "-1";
      minScoreEl.max = "5";
      minScoreEl.step = "0.05";
      minScoreEl.placeholder = "例如 0.2";
      if (minScoreField) {
        minScoreField.title =
          "预览后默认全不选；输入后点「应用阈值」选中 score≥；反选在右侧";
      }
    }
  }
  if (scoreHint) {
    scoreHint.textContent = showScore
      ? "带分结果：应用阈值筛选 · 反选对勾选取反"
      : "预览后可反选当前勾选（本地，不重跑匹配）";
  }
  syncDcApplyScoreButton();
  if (!ta) {
    syncDcInvertButton();
    return;
  }
  if (method === "keywords") {
    ta.placeholder = "关键词，空格/逗号分隔，例如：广告 推广";
  } else if (method === "regex") {
    ta.placeholder = "正则，例如：加微|VX\\s*\\d{5,}";
  } else if (method === "vector_fast") {
    ta.placeholder = "快速向量匹配的查询文本…";
  } else if (isLlm) {
    ta.placeholder =
      "支持自然语言。例：广告引流 / 删除含微信号或手机号的样本 / 去掉灌水乱码";
  } else {
    ta.placeholder = "语义相近的查询文本…";
  }
  syncDcInvertButton();
  syncDcApplyScoreButton();
}

function syncDcInvertButton() {
  const btn = document.getElementById("btn-dc-invert");
  if (!btn) return;
  // 有可勾选结果即可反选：匹配预览 或 无条件浏览
  const can =
    !!dcCurrentId &&
    !!dcResultState.selectable &&
    (dcResultState.mode === "match" || dcResultState.mode === "browse") &&
    (dcResultState.hits || []).length > 0;
  btn.disabled = !can;
  btn.classList.toggle("is-active", !!dcInvertApplied);
  btn.title = !can
    ? "右侧列表可点击勾选；有数据后可对当前页勾选取反"
    : dcInvertApplied
      ? "当前已反选；再点一次再取反（恢复上次勾选集合）"
      : "对当前页结果勾选取反：已选→不选，未选→选中（本地瞬间完成）";
  btn.textContent = dcInvertApplied ? "反选（已开）" : "反选";
}

function dcTotalPages() {
  const total = dcResultState.total || 0;
  const size = dcPageSize || 50;
  if (total <= 0) return 1;
  return Math.max(1, Math.ceil(total / size));
}

function renderDcPager() {
  const pager = document.getElementById("dc-pager");
  const info = document.getElementById("dc-page-info");
  const prev = document.getElementById("dc-page-prev");
  const next = document.getElementById("dc-page-next");
  const sizeSel = document.getElementById("dc-page-size");
  if (!pager) return;
  const total = dcResultState.total || 0;
  const pages = dcTotalPages();
  if (total <= 0) {
    pager.hidden = true;
    return;
  }
  pager.hidden = false;
  if (dcPage > pages) dcPage = pages;
  if (dcPage < 1) dcPage = 1;
  if (info) info.textContent = `第 ${dcPage} / ${pages} 页 · 共 ${total} 条`;
  if (prev) prev.disabled = dcPage <= 1;
  if (next) next.disabled = dcPage >= pages;
  if (sizeSel && String(sizeSel.value) !== String(dcPageSize)) {
    sizeSel.value = String(dcPageSize);
  }
}

function dcMatchSelectedCount() {
  return dcMatchSelectedIds ? dcMatchSelectedIds.size : 0;
}

function updateDcMatchHeaderCount() {
  const selectable =
    !!dcResultState.selectable &&
    (dcResultState.mode === "match" || dcResultState.mode === "browse");
  if (!selectable) {
    updateDcHeaderCount();
    return;
  }
  const total = dcResultState.total || (dcResultState.hits || []).length;
  const selected = dcMatchSelectedCount();
  const thr = getDcScoreThreshold();
  let extra = `选中 ${selected} / ${
    dcResultState.mode === "browse" ? "生效" : "结果"
  } ${total}`;
  if (
    thr != null &&
    dcMethodNeedsScoreFilter() &&
    !dcInvertApplied &&
    dcResultState.mode === "match"
  ) {
    extra += ` · 阈值≥${thr}`;
  }
  updateDcHeaderCount(extra);
}

function renderDcTablePage() {
  const head = document.getElementById("dc-preview-head");
  const body = document.getElementById("dc-preview-body");
  if (!head || !body) return;
  const all = dcResultState.hits || [];
  const server = !!dcResultState._serverPage;
  let pageRows = all;
  if (!server) {
    const start = (dcPage - 1) * dcPageSize;
    pageRows = all.slice(start, start + dcPageSize);
  }
  // 可勾选时显示选中数（浏览/匹配）；否则显示生效/原始
  if (
    dcResultState.selectable &&
    (dcResultState.mode === "match" || dcResultState.mode === "browse")
  ) {
    updateDcMatchHeaderCount();
  } else {
    updateDcHeaderCount();
  }
  if (!pageRows.length) {
    head.innerHTML = "";
    body.innerHTML = `<tr><td class="hint">暂无数据</td></tr>`;
    renderDcPager();
    return;
  }
  const cols = ["id", "text"];
  if (pageRows.some((r) => r.score != null)) cols.splice(1, 0, "score");
  if (pageRows.some((r) => r.matched)) cols.push("matched");
  if (pageRows.some((r) => r.vs_current != null || r.status != null)) {
    cols.push("vs_current");
  }
  if (
    pageRows.some((r) => r.status != null) &&
    !cols.includes("status") &&
    !pageRows.some((r) => r.vs_current != null)
  ) {
    cols.push("status");
  }
  const selectable = !!dcResultState.selectable;
  const colLabels = {
    id: "id",
    text: "text",
    score: "score",
    matched: "matched",
    vs_current: "相对当前",
    status: "状态",
  };
  head.innerHTML = `<tr>${cols
    .map((c) => `<th>${escapeHtml(colLabels[c] || c)}</th>`)
    .join("")}</tr>`;
  body.innerHTML = pageRows
    .map((r) => {
      const rid = r.id != null ? String(r.id) : "";
      let rowClass = "";
      if (selectable && rid) {
        const on = dcMatchSelectedIds.has(rid);
        rowClass = on ? "dc-row-pickable dc-row-on" : "dc-row-pickable dc-row-off";
      } else if (dcResultState.selectedMark) {
        rowClass = "dc-row-selected";
      }
      return `<tr class="${rowClass}" data-id="${escapeHtml(rid)}" title="${
        selectable ? "点击切换勾选（深色=删除，浅色=保留）" : ""
      }">${cols
        .map((c) => {
          let v = r[c];
          if (c === "matched" && Array.isArray(v)) v = v.join(", ");
          if (c === "score" && typeof v === "number") v = v.toFixed(4);
          const s = v == null ? "" : String(v);
          return `<td title="${escapeHtml(s)}">${escapeHtml(
            s.length > 120 ? s.slice(0, 120) + "…" : s
          )}</td>`;
        })
        .join("")}</tr>`;
    })
    .join("");
  if (selectable) {
    body.querySelectorAll("tr.dc-row-pickable[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => {
        const id = tr.getAttribute("data-id") || "";
        if (!id) return;
        if (dcMatchSelectedIds.has(id)) dcMatchSelectedIds.delete(id);
        else dcMatchSelectedIds.add(id);
        // 只更新当前行样式与计数，避免整表闪烁
        const on = dcMatchSelectedIds.has(id);
        tr.classList.toggle("dc-row-on", on);
        tr.classList.toggle("dc-row-off", !on);
        updateDcMatchHeaderCount();
      });
    });
  }
  renderDcPager();
}

async function loadDcBrowsePage(opts = {}) {
  if (!dcCurrentId) return;
  const resetSelection = !!opts.resetSelection;
  const offset = (dcPage - 1) * dcPageSize;
  const res = await api(
    `/datasets/${dcCurrentId}/clean/records?limit=${dcPageSize}&offset=${offset}`
  );
  dcResultState = {
    mode: "browse",
    hits: res.items || [],
    total: typeof res.total === "number" ? res.total : (res.items || []).length,
    selectedMark: false,
    // 无条件也可点击勾选删除
    selectable: true,
    _serverPage: true,
  };
  if (resetSelection) {
    dcMatchSelectedIds = new Set();
    dcInvertApplied = false;
  }
  renderDcTablePage();
  syncDcInvertButton();
}

async function dcGoPage(page) {
  const pages = dcTotalPages();
  dcPage = Math.min(Math.max(1, page), pages);
  if (dcResultState.mode === "browse" && dcResultState._serverPage) {
    await loadDcBrowsePage();
  } else {
    renderDcTablePage();
  }
}

function updateDcSelectedLabel() {
  const nameEl = document.getElementById("dc-ds-selected-name");
  const meta = document.getElementById("dc-selected-meta");
  const pickBtn = document.getElementById("btn-dc-pick-ds");
  const d = dcDatasetCache.find((x) => x.id === dcCurrentId);
  if (nameEl) {
    if (d) {
      nameEl.textContent = d.name || `数据集 #${d.id}`;
      nameEl.classList.remove("is-empty");
      nameEl.title = `#${d.id} · ${d.row_count || 0} 条`;
    } else if (dcCurrentId) {
      nameEl.textContent = `数据集 #${dcCurrentId}`;
      nameEl.classList.remove("is-empty");
    } else {
      nameEl.textContent = "未选择数据集";
      nameEl.classList.add("is-empty");
    }
  }
  if (meta) {
    meta.textContent = d
      ? d.name || `数据集 #${d.id}`
      : dcCurrentId
        ? `数据集 #${dcCurrentId}`
        : "请选择数据集";
  }
  if (pickBtn) pickBtn.textContent = dcCurrentId ? "更换" : "选择数据集";
}

function openDcDatasetModal() {
  const modal = document.getElementById("dc-ds-modal");
  if (!modal) return;
  modal.hidden = false;
  renderDcDatasetList();
  setTimeout(() => document.getElementById("dc-ds-q")?.focus(), 30);
}

function closeDcDatasetModal() {
  const modal = document.getElementById("dc-ds-modal");
  if (modal) modal.hidden = true;
}

function renderDcDatasetList() {
  const listEl = document.getElementById("dc-ds-list");
  if (!listEl) return;
  const q = (dcDsQuery || "").trim().toLowerCase();
  let items = dcDatasetCache || [];
  if (q) {
    items = items.filter((d) => {
      const blob = `${d.id} ${d.name || ""} ${d.description || ""} ${
        d.original_filename || ""
      }`.toLowerCase();
      return blob.includes(q);
    });
  }
  if (!items.length) {
    listEl.innerHTML = `<p class="hint" style="padding:12px">${
      dcDatasetCache.length ? "无匹配数据集" : "暂无数据集，请先在数据集库创建"
    }</p>`;
    return;
  }
  listEl.innerHTML = items
    .map((d) => {
      const active = dcCurrentId === d.id ? " active" : "";
      return `<div class="ds-item${active}" data-id="${d.id}">
        <div class="dsi-name">${escapeHtml(d.name || "未命名")}</div>
        <div class="dsi-meta">#${d.id} · ${d.row_count || 0} 条 · ${escapeHtml(
          d.status || "—"
        )}</div>
      </div>`;
    })
    .join("");
  listEl.querySelectorAll(".ds-item[data-id]").forEach((el) => {
    el.addEventListener("click", () => {
      selectDcDataset(+el.dataset.id)
        .then(() => closeDcDatasetModal())
        .catch((e) => toast(e.message, true));
    });
  });
}

function setDcResultBadge(kind, text) {
  const badge = document.getElementById("dc-result-badge");
  if (!badge) return;
  badge.textContent = text || "—";
  badge.className =
    kind === "default" ? "dss-badge dss-badge-default" : "dss-badge";
}

function setDcClientRows(rows, opts = {}) {
  const list = rows || [];
  const selectable = !!opts.selectable;
  dcResultState = {
    mode: opts.mode || "match",
    hits: list,
    total: typeof opts.total === "number" ? opts.total : list.length,
    selectedMark: !!opts.selectedMark,
    selectable,
    _serverPage: false,
  };
  if (selectable) {
    if (opts.selectAll) {
      // 关键词/正则等：可默认全选匹配结果
      dcMatchSelectedIds = new Set(
        list.map((r) => (r.id != null ? String(r.id) : "")).filter(Boolean)
      );
    } else {
      // 向量/大模型/带分：默认全不选，等点「应用阈值」或手动点行
      dcMatchSelectedIds = new Set();
    }
  } else {
    dcMatchSelectedIds = new Set();
  }
  dcPage = 1;
  renderDcTablePage();
  syncDcApplyScoreButton();
}

/** 「数据集内容」右侧：生效 x / 原始 y（原「共 n 条」位置） */
function updateDcHeaderCount(extra) {
  const countEl = document.getElementById("dc-result-count");
  if (!countEl) return;
  if (dcOverview) {
    let t = `生效 ${dcOverview.active_count ?? "—"} / 原始 ${
      dcOverview.original_count ?? "—"
    }`;
    if (extra) t += ` · ${extra}`;
    countEl.textContent = t;
  } else if (extra) {
    countEl.textContent = extra;
  } else {
    countEl.textContent = "";
  }
}

function methodLabel(m) {
  const map = {
    keywords: "关键词",
    regex: "正则",
    vector_fast: "快速向量",
    vector: "语义向量",
    llm: "大模型",
    manual: "手动勾选",
    rollback: "回退",
    restore: "回退",
    checkpoint: "进度",
    save: "进度",
    progress: "进度",
  };
  return map[m] || m || "—";
}

/** 当前右侧正在查看的 diff 批次 id */
let dcViewingOpId = null;

function setDcDiffRestoreButton(opId, canRollback) {
  const btn = document.getElementById("btn-dc-diff-restore");
  if (!btn) return;
  if (!opId || !canRollback) {
    btn.hidden = true;
    btn.dataset.opId = "";
    btn.disabled = true;
    return;
  }
  btn.hidden = false;
  btn.dataset.opId = opId;
  btn.textContent = "回退本批";
  btn.disabled = false;
  btn.title = "回退该删除批次：恢复本批删除的样本到生效集（写入回退 diff）";
}

function renderDcOpsList(ops) {
  const list = document.getElementById("dc-ops-list");
  if (!list) return;
  const items = ops || [];
  if (!items.length) {
    list.innerHTML = `<p class="hint" style="padding:10px">暂无清洗记录</p>`;
    return;
  }
  list.innerHTML = items
    .map((op, idx) => {
      const kind = String(op.kind || "delete").toLowerCase();
      const isRollback = kind === "rollback" || kind === "restore";
      const isCheckpoint =
        kind === "checkpoint" || kind === "save" || kind === "progress";
      // 回退 / 进度快照 无回退按钮
      const canRollback =
        op.can_rollback !== false &&
        !isRollback &&
        !isCheckpoint &&
        !(idx === 0 && isRollback);
      const inv = isRollback
        ? "回退"
        : isCheckpoint
          ? "快照"
          : op.invert
            ? "反选"
            : "正选";
      const q = (op.query || "").slice(0, 40);
      const dsum = (op.diff_summary || "").slice(0, 60);
      const active =
        dcViewingOpId && String(dcViewingOpId) === String(op.id)
          ? " is-active-diff"
          : "";
      const countLabel = isRollback
        ? `恢复 ${op.count ?? 0} 条`
        : isCheckpoint
          ? `累计已删 ${op.count ?? 0}`
          : `删 ${op.count ?? 0} 条`;
      const restoreBtn = canRollback
        ? `<button type="button" class="secondary dc-op-restore" data-op-id="${escapeHtml(
            op.id || ""
          )}">回退</button>`
        : "";
      return `<div class="dc-op-item${active}" data-op-id="${escapeHtml(op.id || "")}">
        <div class="dc-op-main" data-op-id="${escapeHtml(op.id || "")}" title="点击查看 diff">
          <div class="dc-op-title">${escapeHtml(methodLabel(op.method))} · ${inv} · ${countLabel}</div>
          <div class="dc-op-meta">${escapeHtml(dsum || q || "—")}${
            op.label ? " · " + escapeHtml(op.label) : ""
          } · ${escapeHtml((op.created_at || "").replace("T", " ").slice(0, 19))}</div>
        </div>
        <div class="dc-op-actions">
          <button type="button" class="secondary dc-op-diff" data-op-id="${escapeHtml(
            op.id || ""
          )}">diff</button>
          ${restoreBtn}
        </div>
      </div>`;
    })
    .join("");
  list.querySelectorAll(".dc-op-diff, .dc-op-main").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = el.dataset.opId;
      if (id) viewDcDiff(id).catch((err) => toast(err.message, true));
    });
  });
  list.querySelectorAll(".dc-op-restore").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      restoreDcOp(btn.dataset.opId).catch((err) => toast(err.message, true));
    });
  });
}

function clearDcFormForNextRound() {
  const q = document.getElementById("dc-query");
  if (q) q.value = "";
  const label = document.getElementById("dc-label");
  if (label) label.value = "";
  const any = document.querySelector('input[name="dc-match"][value="any"]');
  if (any) any.checked = true;
  const cs = document.getElementById("dc-case");
  if (cs) cs.checked = false;
  const minScore = document.getElementById("dc-min-score");
  if (minScore) minScore.value = "";
  dcLastPreview = null;
  dcInvertApplied = false;
  dcAppliedScoreThreshold = null;
  dcUpdateMethodUI();
  syncDcInvertButton();
  syncDcApplyScoreButton();
}

async function viewDcDiff(opId) {
  if (!dcCurrentId || !opId) return;
  const status = document.getElementById("dc-status");
  if (status) status.textContent = "加载 diff…";
  try {
    const res = await api(
      `/datasets/${dcCurrentId}/clean/ops/${encodeURIComponent(opId)}`
    );
    const op = res.op || {};
    const diff = res.diff || {};
    const vs = res.vs_current || {};
    const batchRows = diff.deleted_records || [];
    // 优先展示「相对当前版本」有差异的样本；若无集合差，展示本批涉及样本及相对当前状态
    const vsRows = vs.rows || [];
    const rows = vsRows.length
      ? vsRows
      : batchRows.map((r) => ({
          ...r,
          status: r.vs_current || (r.still_deleted ? "仍已删除" : "当前已生效"),
        }));
    dcViewingOpId = op.id || opId;
    const titleEl = document.getElementById("dc-result-title");
    const action = String(diff.action || op.method || op.kind || "").toLowerCase();
    const isRollback = action === "rollback" || action === "restore";
    const isCheckpoint =
      action === "checkpoint" ||
      action === "save" ||
      action === "progress" ||
      String(op.method || "").toLowerCase() === "checkpoint";
    const canRollback = !!(
      res.can_rollback ??
      op.can_rollback ??
      (!isRollback && !isCheckpoint)
    );
    if (titleEl) {
      titleEl.textContent = vsRows.length
        ? `Diff #${op.id || opId} · 相对当前`
        : `Diff #${op.id || opId} · 本批`;
    }
    setDcResultBadge(
      "",
      isRollback ? "回退 diff" : isCheckpoint ? "进度快照" : "删除 diff"
    );
    setDcDiffRestoreButton(dcViewingOpId, canRollback);
    setDcClientRows(rows, {
      mode: "diff",
      selectedMark: true,
      total: rows.length,
    });
    // 高亮当前历史项
    document.querySelectorAll(".dc-op-item").forEach((el) => {
      el.classList.toggle("is-active-diff", el.dataset.opId === String(opId));
    });
    const vsSum =
      vs.summary ||
      diff.summary ||
      (isRollback
        ? `回退 ${op.count ?? rows.length} 条`
        : `本批 ${batchRows.length} 条`);
    updateDcHeaderCount(
      `${vsSum}${canRollback ? " · 可回退" : ""}`
    );
    // diff 视图不可反选 / 应用阈值
    dcLastPreview = null;
    dcInvertApplied = false;
    dcAppliedScoreThreshold = null;
    syncDcInvertButton();
    syncDcApplyScoreButton();
    if (status) {
      status.hidden = true;
      status.textContent = "";
    }
  } catch (e) {
    if (status) {
      status.hidden = false;
      status.textContent = "失败";
    }
    throw e;
  }
}

function collectDcMatchBody(opts = {}) {
  const method = document.getElementById("dc-method")?.value || "keywords";
  const query = (document.getElementById("dc-query")?.value || "").trim();
  // 结果阈值仅前端筛选，不发给后端（后端返回全部带分结果）
  // invert 不是清洗条件：预览始终正选；反选按钮/删除时再带上当前反选状态
  const invert =
    opts.invert != null ? !!opts.invert : !!dcInvertApplied;
  return {
    method,
    query,
    match:
      document.querySelector('input[name="dc-match"]:checked')?.value || "any",
    case_sensitive:
      method === "keywords"
        ? !!document.getElementById("dc-case")?.checked
        : false,
    invert,
    label: (document.getElementById("dc-label")?.value || "").trim(),
  };
}

function buildDcSuggestedSaveName(ov) {
  if (ov && ov.suggested_save_name) return ov.suggested_save_name;
  const base = (ov && ov.name) || "数据集";
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(
    d.getHours()
  )}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${base}_清洗_${stamp}`;
}

function refreshDcSaveNameDefault(ov) {
  const expBtn = document.getElementById("btn-dc-export");
  // 导出到库：需要仍有生效样本
  if (expBtn) expBtn.disabled = !dcCurrentId || !(ov?.active_count > 0);
}

/** 导出到数据集库弹层 */
function openDcExportDatasetModal() {
  if (!dcCurrentId) {
    toast("请先选择数据集", true);
    return;
  }
  if (!(dcOverview?.active_count > 0)) {
    toast("当前生效样本为空，无法导出到数据集库", true);
    return;
  }
  const modal = document.getElementById("dc-save-modal");
  const input = document.getElementById("dc-save-name");
  const hint = document.getElementById("dc-save-name-hint");
  const modalHint = document.getElementById("dc-save-modal-hint");
  const err = document.getElementById("dc-save-error");
  const suggested = buildDcSuggestedSaveName(dcOverview);
  if (input) {
    input.value = suggested;
    input.dataset.defaultName = suggested;
  }
  if (hint) {
    hint.textContent = `默认：${suggested}（可改）`;
  }
  if (modalHint) {
    modalHint.textContent = `将当前生效 ${
      dcOverview?.active_count ?? "—"
    } 条导出为新数据集（仅存 id 引用，不复制原始全文）；之后可在数据集库下载。不修改本数据集清洗进度。`;
  }
  if (err) {
    err.hidden = true;
    err.textContent = "";
  }
  if (modal) modal.hidden = false;
  setTimeout(() => {
    input?.focus();
    input?.select();
  }, 30);
}

function closeDcSaveModal() {
  const modal = document.getElementById("dc-save-modal");
  if (modal) modal.hidden = true;
}

async function loadDcOverview() {
  if (!dcCurrentId) return null;
  const ov = await api(`/datasets/${dcCurrentId}/clean`);
  dcOverview = ov;
  dcViewingOpId = null;
  dcLastPreview = null;
  dcInvertApplied = false;
  dcAppliedScoreThreshold = null;
  const titleEl = document.getElementById("dc-result-title");
  if (titleEl) titleEl.textContent = "数据集内容";
  setDcResultBadge(
    "default",
    ov.deleted_count > 0 ? "已清洗" : "默认（无规则）"
  );
  setDcDiffRestoreButton(null, false);
  renderDcOpsList(ov.ops || []);
  refreshDcSaveNameDefault(ov);
  // 无条件浏览：服务端分页加载生效样本（可点击勾选）
  dcPage = 1;
  dcMatchSelectedIds = new Set();
  dcInvertApplied = false;
  await loadDcBrowsePage({ resetSelection: true });
  refreshDcSaveNameDefault(ov);
  updateDcHeaderCount();
  syncDcInvertButton();
  syncDcApplyScoreButton();
  const status = document.getElementById("dc-status");
  if (status) {
    status.textContent = "";
    status.hidden = true;
  }
  return ov;
}

/** 确认导出：当前生效样本 → 数据集库新数据集 */
async function exportDcToLibraryFromModal() {
  if (!dcCurrentId) {
    toast("请先选择数据集", true);
    return;
  }
  const nameInput = document.getElementById("dc-save-name");
  const err = document.getElementById("dc-save-error");
  const confirmBtn = document.getElementById("dc-save-modal-confirm");
  let name = (nameInput?.value || "").trim();
  if (!name) {
    name = buildDcSuggestedSaveName(dcOverview);
    if (nameInput) nameInput.value = name;
  }
  if (!name) {
    if (err) {
      err.hidden = false;
      err.textContent = "请填写数据集名称";
    }
    return;
  }
  if (err) {
    err.hidden = true;
    err.textContent = "";
  }
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "导出中…";
  }
  try {
    const res = await api(`/datasets/${dcCurrentId}/clean/export-dataset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, build_vectors: true }),
    });
    closeDcSaveModal();
    toast(
      `已导出到数据集库 #${res.dataset_id}「${res.name}」（${res.row_count} 条，仅 id 引用不复制全文），可在数据集库下载`
    );
    try {
      dcDatasetCache = (await api("/datasets")) || [];
    } catch (_) {
      /* ignore */
    }
    refreshDcSaveNameDefault(dcOverview);
  } catch (e) {
    if (err) {
      err.hidden = false;
      err.textContent = e.message || "导出失败";
    }
    toast(e.message || "导出失败", true);
  } finally {
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "确认导出";
    }
  }
}

async function selectDcDataset(id) {
  const tid = +id;
  if (!tid) return;
  dcCurrentId = tid;
  dcLastPreview = null;
  dcInvertApplied = false;
  dcAppliedScoreThreshold = null;
  updateDcSelectedLabel();
  const status = document.getElementById("dc-status");
  if (status) status.textContent = "载入中…";
  try {
    await loadDcOverview();
    syncDcInvertButton();
    syncDcApplyScoreButton();
    toast(`已载入数据集 #${tid}`);
  } catch (e) {
    if (status) status.textContent = "载入失败";
    syncDcInvertButton();
    syncDcApplyScoreButton();
    throw e;
  }
}

async function initDataCleanPage() {
  const form = document.querySelector("#view-data-clean .dss-form-card");
  if (!form) return;
  try {
    dcDatasetCache = (await api("/datasets")) || [];
  } catch (e) {
    dcDatasetCache = [];
    toast(e.message || "加载数据集失败", true);
  }
  updateDcSelectedLabel();
  dcUpdateMethodUI();
  if (dcCurrentId && dcDatasetCache.some((d) => d.id === dcCurrentId)) {
    await selectDcDataset(dcCurrentId);
  } else if (dcDatasetCache.length) {
    await selectDcDataset(dcDatasetCache[0].id);
  } else {
    dcCurrentId = null;
    updateDcSelectedLabel();
    const status = document.getElementById("dc-status");
    if (status) status.textContent = "请先在数据集库创建数据集";
  }
}

function flashButton(btn, kind = "ok", ms = 700) {
  if (!btn) return;
  const cls = kind === "err" ? "btn-flash-err" : "btn-flash-ok";
  btn.classList.remove("btn-flash-ok", "btn-flash-err");
  // 强制重触发动画/样式
  void btn.offsetWidth;
  btn.classList.add(cls);
  if (btn._flashTimer) clearTimeout(btn._flashTimer);
  btn._flashTimer = setTimeout(() => {
    btn.classList.remove("btn-flash-ok", "btn-flash-err");
    btn._flashTimer = null;
  }, ms);
}

/**
 * 按清洗条件预览匹配。
 * 向量/大模型：返回带分全量结果，默认全不选；结果阈值/反选仅本地操作。
 */
async function previewDcMatch() {
  if (!dcCurrentId) {
    toast("请先选择数据集", true);
    return;
  }
  const btn = document.getElementById("btn-dc-preview");
  // 预览不带 invert；反选只做本地勾选取反
  const body = collectDcMatchBody({ invert: false });
  // 允许空条件：匹配全部生效样本
  const status = document.getElementById("dc-status");
  if (status) status.textContent = "匹配中…";
  if (btn) {
    btn.disabled = true;
    btn.dataset._label = btn.textContent || "预览匹配";
    btn.textContent = "匹配中…";
  }
  try {
    const res = await api(`/datasets/${dcCurrentId}/clean/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    dcLastPreview = res;
    dcInvertApplied = false;
    // 新预览：阈值需重新点「应用阈值」才生效
    dcAppliedScoreThreshold = null;
    dcViewingOpId = null;
    setDcDiffRestoreButton(null, false);
    const titleEl = document.getElementById("dc-result-title");
    const empty = !body.query;
    if (titleEl) {
      titleEl.textContent = empty ? "匹配全部（空条件）" : "匹配结果";
    }
    setDcResultBadge(empty ? "default" : "", empty ? "全部" : "结果");
    const rows = res.selected || res.preview || [];
    const scored = dcMethodNeedsScoreFilter();
    setDcClientRows(rows, {
      mode: "match",
      selectable: true,
      selectedMark: false,
      total: res.selected_count ?? rows.length,
      // 关键词/正则：默认勾选全部匹配；TF-IDF/BGE/大模型：默认全不选
      selectAll: !scored,
    });
    if (status) {
      status.hidden = true;
      status.textContent = "";
    }
    {
      const selN = dcMatchSelectedIds.size;
      const totN = (dcResultState.hits || []).length || res.selected_count || 0;
      if (scored) {
        toast(
          `预览完成：共 ${totN} 条带分结果 · 默认未选中，请输入阈值后点「应用阈值」`
        );
      } else if (empty) {
        toast(`空条件预览：结果 ${totN} · 选中 ${selN}`);
      } else {
        toast(`预览：结果 ${totN} · 选中 ${selN}`);
      }
    }
    syncDcInvertButton();
    syncDcApplyScoreButton();
    if (btn) {
      btn.disabled = false;
      btn.textContent = btn.dataset._label || "预览匹配";
      flashButton(btn, "ok", 700);
    }
  } catch (e) {
    if (status) {
      status.hidden = false;
      status.textContent = "失败";
    }
    toast(e.message, true);
    syncDcInvertButton();
    if (btn) {
      btn.disabled = false;
      btn.textContent = btn.dataset._label || "预览匹配";
      flashButton(btn, "err", 700);
    }
  }
}

/**
 * 对「当前结果列表」的勾选取反（本地瞬间完成，不请求后端、不重跑条件）。
 * 例：阈值选中 score≥0.6 的 4 条 → 反选 = 选中其余（通常即 score&lt;0.6）。
 * 再点一次再取反，恢复上一批勾选。
 */
function invertDcSelection() {
  if (
    !dcResultState.selectable ||
    (dcResultState.mode !== "match" && dcResultState.mode !== "browse")
  ) {
    toast("当前列表不可勾选", true);
    return;
  }
  const hits = dcResultState.hits || [];
  if (!hits.length) {
    toast("当前没有可反选的结果", true);
    return;
  }
  const allIds = hits
    .map((r) => (r.id != null ? String(r.id) : ""))
    .filter(Boolean);
  const next = new Set();
  for (const id of allIds) {
    if (!dcMatchSelectedIds.has(id)) next.add(id);
  }
  dcMatchSelectedIds = next;
  dcInvertApplied = !dcInvertApplied;
  const titleEl = document.getElementById("dc-result-title");
  if (titleEl) {
    titleEl.textContent = dcInvertApplied ? "匹配结果（已反选）" : "匹配结果";
  }
  setDcResultBadge("", dcInvertApplied ? "反选" : "结果");
  renderDcTablePage();
  syncDcInvertButton();
  syncDcApplyScoreButton();
  const thr = getDcScoreThreshold();
  const thrHint =
    thr != null && dcMethodNeedsScoreFilter()
      ? dcInvertApplied
        ? `（相对已应用阈值≥${thr} 的补集）`
        : ""
      : "";
  toast(`反选完成：选中 ${dcMatchSelectedIds.size} / ${allIds.length}${thrHint}`);
  flashButton(document.getElementById("btn-dc-invert"), "ok", 500);
}

async function applyDcDelete() {
  if (!dcCurrentId) {
    toast("请先选择数据集", true);
    return;
  }
  const body = collectDcMatchBody();
  // 勾选优先：匹配预览 或 无条件浏览均可
  let selectedIds = [];
  if (
    dcResultState.selectable &&
    (dcResultState.mode === "match" || dcResultState.mode === "browse") &&
    dcMatchSelectedIds &&
    dcMatchSelectedIds.size > 0
  ) {
    selectedIds = Array.from(dcMatchSelectedIds);
  } else if (dcLastPreview?.selected_ids?.length) {
    selectedIds = dcLastPreview.selected_ids.map(String);
  }
  if (!selectedIds.length) {
    toast("没有勾选要删除的样本（深色=勾选，点击行可切换；无需先填条件）", true);
    return;
  }
  // 删除选中 = 从生效集移除 id + 写入 diff 进度；不二次确认
  body.selected_ids = selectedIds;
  // 无条件浏览勾选时，不强制走匹配条件
  if (dcResultState.mode === "browse" && !dcLastPreview) {
    body.method = "manual";
    body.query = "";
    body.invert = false;
  }
  const btn = document.getElementById("btn-dc-apply");
  const status = document.getElementById("dc-status");
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
  if (btn) {
    btn.disabled = true;
    btn.dataset._label = btn.textContent || "删除选中";
    btn.textContent = "删除中…";
  }
  try {
    const res = await api(`/datasets/${dcCurrentId}/clean/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const opId = res.op?.id || "";
    toast(
      `已删除 ${res.deleted_this_op} 条并保存进度（#${opId}），可在历史中查看 diff / 回退`
    );
    // 清空清洗条件，进入新一轮；右侧恢复「无条件」生效样本列表
    clearDcFormForNextRound();
    dcViewingOpId = null;
    await loadDcOverview();
    if (btn) flashButton(btn, "ok", 700);
  } catch (e) {
    if (btn) flashButton(btn, "err", 700);
    if (status) {
      status.hidden = false;
      status.textContent = "失败";
    }
    toast(e.message, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = btn.dataset._label || "删除选中";
    }
  }
}

async function restoreDcOp(opId) {
  if (!dcCurrentId || !opId) return;
  // 不二次确认；回退后新增一条 diff 历史并回到无条件列表
  const status = document.getElementById("dc-status");
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
  try {
    const res = await api(
      `/datasets/${dcCurrentId}/clean/ops/${encodeURIComponent(opId)}/restore`,
      { method: "POST" }
    );
    toast(`已回退 ${res.restored_count || 0} 条（新增历史 #${res.op_id || ""}）`);
    dcLastPreview = null;
    dcViewingOpId = null;
    clearDcFormForNextRound();
    // 无条件数据展示
    await loadDcOverview();
  } catch (e) {
    if (status) {
      status.hidden = false;
      status.textContent = "失败";
    }
    toast(e.message, true);
  }
}

document.getElementById("dc-method")?.addEventListener("change", () =>
  dcUpdateMethodUI()
);
// 结果阈值：仅点「应用阈值」生效，输入过程不自动筛选
document.getElementById("btn-dc-apply-score")?.addEventListener("click", () => {
  try {
    applyDcScoreThreshold();
  } catch (e) {
    toast(e.message || String(e), true);
  }
});
document.getElementById("dc-min-score")?.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  try {
    applyDcScoreThreshold();
  } catch (err) {
    toast(err.message || String(err), true);
  }
});
document.getElementById("btn-dc-pick-ds")?.addEventListener("click", () =>
  openDcDatasetModal()
);
document.getElementById("dc-ds-selected")?.addEventListener("click", () =>
  openDcDatasetModal()
);
document.getElementById("dc-ds-modal-cancel")?.addEventListener("click", () =>
  closeDcDatasetModal()
);
document.getElementById("dc-ds-modal")?.addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeDcDatasetModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const modal = document.getElementById("dc-ds-modal");
  if (modal && !modal.hidden) closeDcDatasetModal();
});
document.getElementById("dc-ds-q")?.addEventListener("input", (e) => {
  dcDsQuery = e.target.value || "";
  if (dcDsQueryTimer) clearTimeout(dcDsQueryTimer);
  dcDsQueryTimer = setTimeout(() => renderDcDatasetList(), 150);
});
document.getElementById("btn-dc-preview")?.addEventListener("click", () => {
  previewDcMatch().catch((e) => toast(e.message, true));
});
document.getElementById("btn-dc-invert")?.addEventListener("click", () => {
  try {
    invertDcSelection();
  } catch (e) {
    toast(e.message || String(e), true);
  }
});
document.getElementById("btn-dc-apply")?.addEventListener("click", () => {
  applyDcDelete().catch((e) => toast(e.message, true));
});
document.getElementById("btn-dc-export")?.addEventListener("click", () => {
  openDcExportDatasetModal();
});
document.getElementById("dc-save-modal-cancel")?.addEventListener("click", () => {
  closeDcSaveModal();
});
document.getElementById("dc-save-modal")?.addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeDcSaveModal();
});
document.getElementById("form-dc-save")?.addEventListener("submit", (e) => {
  e.preventDefault();
  exportDcToLibraryFromModal().catch((err) => toast(err.message, true));
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const modal = document.getElementById("dc-save-modal");
  if (modal && !modal.hidden) closeDcSaveModal();
});
document.getElementById("btn-dc-diff-restore")?.addEventListener("click", () => {
  const id = document.getElementById("btn-dc-diff-restore")?.dataset?.opId;
  if (id) restoreDcOp(id).catch((e) => toast(e.message, true));
});
document.getElementById("dc-page-prev")?.addEventListener("click", () => {
  dcGoPage(dcPage - 1).catch((e) => toast(e.message, true));
});
document.getElementById("dc-page-next")?.addEventListener("click", () => {
  dcGoPage(dcPage + 1).catch((e) => toast(e.message, true));
});
document.getElementById("dc-page-size")?.addEventListener("change", (e) => {
  dcPageSize = +(e.target.value || 50) || 50;
  dcPage = 1;
  if (dcResultState.mode === "browse" && dcResultState._serverPage) {
    loadDcBrowsePage().catch((err) => toast(err.message, true));
  } else {
    renderDcTablePage();
  }
});

document.getElementById("pd-prompt-editor")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});
document.getElementById("pd-change-reason")?.addEventListener("input", (e) => {
  e.target.dataset.dirty = "1";
});

document.getElementById("btn-pd-save")?.addEventListener("click", async () => {
  const editor = document.getElementById("pd-prompt-editor");
  const reasonEl = document.getElementById("pd-change-reason");
  const text = (editor?.value || "").trim();
  const reason = (reasonEl?.value || "").trim() || "提示词调试修改";

  // 草稿：先弹名称设置框 → 再创建 Job（SQL）+ Prompt v1
  if (!currentJobId || pdDraftMode) {
    if (!text) {
      toast("提示词不能为空", true);
      return;
    }
    const defaultName = pdDraftDefaultName || defaultPromptDebugName();
    const name = await askPromptDebugJobName({
      title: "设置任务名称",
      defaultName,
      confirmLabel: "确认并保存版本",
      hint: "首次保存将创建任务并写入数据库；留空则使用默认名称",
    });
    if (name == null) return; // 用户取消
    try {
      const job = await api("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          job_type: "prompt_debug",
          policy_rules: name,
          initial_prompt: text,
          seed_change_reason: reason,
        }),
      });
      currentJobId = job.id;
      pdDraftMode = false;
      pdLoadedJobName = (job.name || name).trim();
      pdDraftDefaultName = "";
      if (editor) {
        editor.dataset.dirty = "0";
        editor.dataset.loadedVersion = `1:${text.length}`;
      }
      if (reasonEl) {
        reasonEl.dataset.dirty = "0";
        reasonEl.value = "";
      }
      toast(`已创建任务 #${job.id} 并保存 v1`);
      await loadPromptDebugWorkbench(job);
      loadJobs().catch(() => {});
    } catch (e) {
      toast(e.message, true);
    }
    return;
  }

  // 已有任务：只保存新 Prompt 版本（改名用顶部「改名」/点名称）
  try {
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
    await loadPromptDebugWorkbench();
    loadJobs().catch(() => {});
  } catch (e) {
    toast(e.message, true);
  }
});

/**
 * 从各任务类型模块快速创建 Job（统一进 Job 列表）
 * @param {string} jobType
 * @param {{ name?: string }} [opts] 若传入 name 则使用该名称；提示词调试不传则自动命名
 */
async function createTypedJob(jobType, opts = {}) {
  const meta = JOB_TYPE_META[jobType] || JOB_TYPE_META.annotation;
  const defaultName = `${meta.label} ${new Date()
    .toISOString()
    .slice(0, 16)
    .replace("T", " ")}`;
  let trimmed = String(opts.name ?? "").trim();
  if (!trimmed) {
    // 提示词调试：直接用时间戳默认名，不再弹窗/填表
    if (jobType === "prompt_debug") {
      trimmed = defaultName;
    } else {
      // 清洗台等占位入口仍弹窗命名
      const name = window.prompt(`新建${meta.label}任务名称：`, defaultName);
      if (name == null) return;
      trimmed = String(name).trim();
    }
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
    await enterAnnotationWorkbench();
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
  currentJobId = null;
  pdDraftMode = false;
  pdLoadedJobName = "";
  pdDraftDefaultName = "";
  closePdNameModal?.(null);
  resetSidebarOverlays();
  collapsePdDiff?.();
  collapsePdReasonExpand?.();
  // 仅提示词调试使用返回；标注请用顶栏「Job 列表」
  goView("templates");
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
