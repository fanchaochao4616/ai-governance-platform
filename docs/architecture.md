# 架构说明

> 主入口：[plan.md](./plan.md) · 决策：[decisions.md](./decisions.md) · PRD：[PRD_v0.5.md](./PRD_v0.5.md)

---

## 1. 系统上下文

```
用户(浏览器) → Web UI (static) → FastAPI (/api/v1/*)
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              Domain Services    Agents (xAI)     SQLite + files
              (job/round/qc/     QCAgent          data/app.db
               prompt/export)    AnnotatorAgent   data/uploads
                                                  data/exports
```

---

## 2. 逻辑分层

| 层 | 路径 | 职责 |
|----|------|------|
| UI | `app/web/` | 登录壳、Job 配置、详情三栏、模板库 |
| API | `app/api/routes_*.py` | REST、上传、后台任务入队、鉴权依赖 |
| Domain | `app/services/` | 业务规则、状态迁移、指标、IO、鉴权 |
| Agents | `app/agents/` | Prompt 组装、结构化输出、模型角色 |
| Infra | `app/db.py`, `llm_client.py`, `config.py` | DB、LLM、环境配置 |
| Models | `app/models.py`, `app/schemas.py` | 持久化实体 + API 契约 |
| FSM | `app/state_machine.py` | Job/Round 状态枚举与转移约束 |

---

## 3. 核心实体关系

```
Job 1──* AnnotationRecord   (seq 永久主键，job 内唯一)
Job 1──* GoldTestItem       (initial | qc_correction)
Job 1──* Round
Job 1──* PromptVersion
Round 1──* QCSample
Round 1──* QCFeedback       (按 round 关联)
PromptTemplate 1──* PromptTemplateVersion
User 1──* Session           (SQLite 鉴权)
EventLog                    (埋点/审计)
```

### 3.1 AnnotationRecord.rounds（JSON）

每轮一条：

```json
{
  "round": 1,
  "label": "1",
  "confidence": 0.91,
  "reasoning": "...",
  "prompt_version_id": 3,
  "model": "grok-4-1-fast",
  "created_at": "ISO-8601"
}
```

`final_label` / `conflict` 在多轮平均（`POST .../finalize`）时写入。

---

## 4. Job 状态机

```
CREATED
  → GOLD_OPTIMIZING
  → GOLD_READY | GOLD_FAILED | BUDGET_EXCEEDED | FAILED
  → ROUND_LABELING
  → AWAIT_DECISION_THRESHOLD   # 看置信度分布后设阈值，批量得 满足/不满足
  → AWAIT_CONFIDENCE_BINS
  → AWAIT_QC
  → AWAIT_DECISION
  → PROMPT_IMPROVING → ROUND_LABELING   (continue)
  → COMPLETED                           (stop + finalize)
  → CANCELLED | FAILED | BUDGET_EXCEEDED
```

**原则**：`AWAIT_*` 必须人工推进；系统不自动 skip 轮次。

Round 状态：`LABELING` → `AWAIT_THRESHOLD` → `AWAIT_BINS` → `AWAIT_QC` → `AWAIT_DECISION` → `COMPLETED` | `FAILED`

---

## 5. 主业务流程（UI 当前）

```
登录 → 新建 Job（细则 + 可选模板）
        ↓
上传 Dataset + Gold → 确认开始数据标注
        ↓
Gold 优化 → 全量标注（live-progress KPI）
        ↓
应用判定阈值（0/1）→ 分层切点 + 应用分层并抽 QC
        ├─ 同时按 from_round–to_round 多轮平均（finalize）
        └─ 右侧展示 QC 样本（0/1）
        ↓
主栏编辑 Prompt / 修改说明 → 重新标注（置信度层 from–to）
        ├─ 若 Prompt 有改动：落新版本，跳过 LLM 自动改写
        └─ 仅指定层子集进入新 Round
        ↓
导出 Excel / CSV（zip）
```

可选：右侧 **历史版本** → Diff / 回滚。

---

## 6. Job 详情 UI 布局

```
┌─────────┬──────────────────────────────┬─────────────────┐
│ 侧导航  │ 主栏：阈值/分层 + QC 操作     │ 右侧设置栏：     │
│ Jobs    │      + 底部「提示词修改」     │  KPI / 上传     │
│ 新建    │                             │  下载导出       │
│ 模板库  │                             │  或 QC / 历史   │
└─────────┴──────────────────────────────┴─────────────────┘
顶栏：← 返回列表 | 标题 | 系统设置 | 用户
```

---

## 7. AI 调用设计

| Agent | 模型配置 | 输入 | 输出 |
|-------|----------|------|------|
| QCAgent | `XAI_QC_MODEL` | schema、细则、badcase、feedback | prompt_text + improvement_suggestion |
| AnnotatorAgent | `XAI_ANNOTATOR_MODEL` | **仅 text** + 细则 + prompt（**无 taxonomy**） | `{confidence, reasoning}` → 阈值映射 `1`/`0` |

- `llm_client`：JSON 解析失败可重试  
- Token 经 `on_usage` 回写；预算>0 时超限 `BUDGET_EXCEEDED`  
- Guardrail：confidence clamp [0,1]；**禁止 seq 进入模型消息**  

---

## 8. 关键 API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST/GET | `/api/v1/auth/*` | 登录、登出、me、改密 |
| POST | `/api/v1/jobs` | 创建 Job |
| POST | `/api/v1/jobs/{id}/dataset` | 上传 CSV/XLSX |
| POST | `/api/v1/jobs/{id}/gold` | 上传 Gold |
| POST | `/api/v1/jobs/{id}/start-annotation` | **一键** Gold→全量（后台） |
| POST | `/api/v1/jobs/{id}/start` | 兼容：仅 Gold |
| POST | `/api/v1/jobs/{id}/start-full-label` | 兼容：仅全量 |
| GET | `/api/v1/jobs/{id}/live-progress` | 过程 KPI |
| POST | `/api/v1/jobs/{id}/decision-threshold` | 设判定阈值 → 0/1 |
| GET | `/api/v1/jobs/{id}/confidence-distribution` | 置信度分布 |
| POST | `/api/v1/jobs/{id}/rounds/{r}/confidence-bins` | 分层 + 抽 QC |
| GET/POST | `.../qc-samples` / `.../qc` | QC 列表 / 提交 |
| POST | `.../decision` | 继续重标（可带 `prompt_text`） |
| POST | `/api/v1/jobs/{id}/finalize` | 多轮平均（UI 在抽 QC 时调用） |
| GET | `/api/v1/jobs/{id}/export?format=csv\|xlsx` | 导出 |
| * | `/api/v1/templates*` | 模板库 CRUD/版本/diff/激活 |
| * | `/api/v1/jobs/{id}/prompt-versions*` | 版本列表/diff/回滚/新建 |

完整 OpenAPI：启动后访问 `/docs`。

---

## 9. 目录地图

```text
E:\vibecoding\
  docs/                 # PRD、plan、架构、决策、TODO、状态
  app/
    main.py
    models.py / schemas.py / state_machine.py / db.py
    agents/             # qc_agent, annotator
    services/           # 业务 + auth + pipeline
    api/                # 路由（含 routes_auth）
    web/                # index.html + static/
  config.py
  llm_client.py
  tests/
  data/                 # gitignored：db / uploads / exports
```

---

## 10. 运行时与配置

| 变量 | 含义 |
|------|------|
| `XAI_API_KEY` | 调用 LLM 必需 |
| `XAI_QC_MODEL` / `XAI_ANNOTATOR_MODEL` | 双模型 |
| `DATA_DIR` / `DB_PATH` | 数据与库路径 |
| `DEFAULT_QC_PER_BIN` | 每层默认 QC 配额 |
| `ANNOTATOR_CONCURRENCY` | 标注并发 |
| `DEFAULT_ADMIN_USERNAME` / `PASSWORD` | 首启管理员 |

启动：`python -m uvicorn app.main:app --reload --port 8000`
