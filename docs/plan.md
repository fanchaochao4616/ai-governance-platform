# 内容风控标注平台 — 活文档 Plan

**产品**：内容风控文本分类标注平台（PRD v0.5）  
**文档角色**：后续开发与复盘的 **单一事实来源（SSOT）入口**  
**最后更新**：2026-07-19  

---

## 0. 文档索引（拆分文件）

| 文件 | 内容 |
|------|------|
| **[plan.md](./plan.md)**（本文件） | 总览、原则、里程碑、能力地图、变更日志 |
| **[PRD_v0.5.md](./PRD_v0.5.md)** | 产品需求原文存档（完整 v0.5） |
| **[architecture.md](./architecture.md)** | 架构、状态机、实体、API、目录 |
| **[decisions.md](./decisions.md)** | 重要决策 ADR（含相对 PRD 的格式变更） |
| **[status.md](./status.md)** | 当前实现状态、限制、验证结果 |
| **[TODO.md](./TODO.md)** | 完成项 + 近期/中期/长期待办 + 技术债 |

**约定（必须遵守）**：

1. 重要决策、架构变更、状态跃迁、新增 TODO **必须先写入上述文档**（至少更新本 plan + 对应拆分文件），不要只留在聊天记录。  
2. 改行为前先查 `decisions.md`；改结构前先查 `architecture.md`。  
3. 每个里程碑结束更新 `status.md` 与 `TODO.md` 勾选。  
4. PRD 变更：新增 `docs/PRD_vX.Y.md` 并在本文件变更日志登记；不直接覆盖旧 PRD 除非明确废止。

---

## 1. 产品摘要

| 项 | 说明 |
|----|------|
| 目标用户 | 内容风控中台 AI/数据工程师、质检负责人（内部优先） |
| North Star | 低人工干预产出可训练高质量单标签数据 + Prompt 知识资产 |
| MVP 范围 | 单标签文本分类、万级、人工每轮主导、双 Agent、动态 Gold、模板库 |
| 非目标 | 多标签、多租户、K8s、BERT 训练管线本体 |

完整需求见 **[PRD_v0.5.md](./PRD_v0.5.md)**。

---

## 2. 已拍板的重要决策（摘要）

> 详情与备选方案见 **[decisions.md](./decisions.md)**。

| ID | 决策 | 状态 |
|----|------|------|
| D001 | FastAPI + 简单 Web UI | 已采纳 |
| D002 | SQLite 单机 | 已采纳（MVP） |
| D003 | **导入/导出主推 CSV + Excel**（相对 PRD 原文 JSONL 的变更） | 已采纳 |
| D004 | xAI / SpaceXAI，QC=`grok-4.5`，Annotator=`grok-4-1-fast` | 已采纳 |
| D005 | 人工最终停止权，系统不自动跳轮 | 已采纳 |
| D006 | 永久序号 `seq` 主键，对模型隐藏 | 已采纳 |
| D007 | 多轮平均：多数表决 + confidence 破平 | 已采纳 |
| D008 | 后台任务用进程内 BackgroundTasks | 已采纳（MVP） |
| D009 | Prompt 只增版本；回滚=复制新版本 | 已采纳 |
| D010 | 首期完整 P0 | 已采纳并落地 |
| D011 | `app/` 正式包 + 根目录共享 llm/config | 已采纳 |
| D017 | **不配置标签/说明**：细则 + 置信度阈值 → 满足/不满足 | 已采纳 |
| D018 | **不配置 Token 预算**（默认不限；只统计用量） | 已采纳 |
| D019 | **风控细则与初始 Prompt 合并**为一框 | 已采纳 |
| D020 | **判定阈值在全量标注后设置**（非创建 Job 时） | 已采纳 |
| D021 | **数据标注一键流水线** + Gold/全量过程可视化 | 已采纳 |
| D022 | **Prompt 模板库**新建/保存 + 版本控制 | 已采纳 |
| D023 | 顶栏「系统设置」占位（替代全局指标） | 已采纳 |
| D024 | **SQLite 账号鉴权**（登录/退出/改密） | 已采纳 |
| D025 | Job 详情：右侧设置 + QC/历史临时覆盖；底部可编辑 Prompt | 已采纳 |
| D026 | 分层抽 QC 时按 from/to **自动多轮平均**；无独立 Finalize 按钮 | 已采纳 |
| D027 | 重新标注范围 = 置信度层 **from/to**；人工 Prompt 优先于 LLM 改写 | 已采纳 |

---

## 3. 架构摘要

> 完整图示、状态机、API、目录见 **[architecture.md](./architecture.md)**。

```
Web UI → FastAPI /api/v1 → Services → Agents(xAI) + SQLite + data/
```

**主路径状态**：

`CREATED → GOLD_OPTIMIZING → GOLD_READY → ROUND_LABELING → AWAIT_CONFIDENCE_BINS → AWAIT_QC → AWAIT_DECISION → (PROMPT_IMPROVING → ROUND_LABELING)* → finalize → COMPLETED`

**核心不变量**：

1. `seq` 导入后不变；多轮只追加 history。  
2. LLM 输入永不包含 `seq`。  
3. 每轮 `AWAIT_*` 等人；continue 时只重标指定置信度范围。  
4. Prompt 全版本化，可 diff/回滚。  
5. Token 超预算硬停 `BUDGET_EXCEEDED`。

---

## 4. 实现状态摘要

> 明细与限制见 **[status.md](./status.md)**。

| 维度 | 状态 |
|------|------|
| MVP P0 功能 | ✅ 已实现 |
| 初版 UX / 鉴权 | ✅ 已实现（2026-07-19） |
| 自动化测试 | ✅ 18 passed |
| 真实 LLM E2E | ⬜ 待人工用 key 联调 |
| 生产加固（队列/PG/SSO） | ⬜ 见 TODO P2 |

---

## 5. 里程碑（M1–M8）

| ID | 内容 | 状态 |
|----|------|------|
| M1 | 骨架、模型、CSV/Excel 上传、seq | ✅ |
| M2 | chat_json、双 Agent、Gold 闭环 | ✅ |
| M3 | 全量标注、bins、QC | ✅ |
| M4 | 决策、多轮、平均、导出 | ✅ |
| M5 | Prompt 知识库、Web UI | ✅ |
| M6 | 监控、测试、README | ✅ |
| M7 | 鉴权 + Job 详情密集 UX | ✅ |
| M8 | 初版收尾与代码清理 | ✅ |

下一阶段见 **[TODO.md](./TODO.md)**（P1：真实 E2E、拆分抽 QC/finalize、示例数据、重试）。

---

## 6. 能力 ↔ 代码地图

| 能力 | 主要代码 |
|------|----------|
| 配置 / 双模型 | `config.py` |
| LLM 客户端 | `llm_client.py` |
| 状态机 | `app/state_machine.py` |
| 实体 | `app/models.py` |
| 鉴权 | `auth_service.py`, `routes_auth.py` |
| 表格 IO | `app/services/io_tabular.py` |
| Job / Dataset / Gold | `job_service`, `dataset_service`, `gold_service` |
| 一键标注流水线 | `pipeline_service.py` + `start-annotation` |
| Gold 优化 | `gold_optimize.py` + `app/agents/*` |
| 标注 / 平均 | `annotation_service.py`, `confidence.py` |
| QC / 决策 | `qc_service.py`, `decision_service.py` |
| Prompt / 模板 | `prompt_service.py`, `template_service.py` |
| 导出 | `export_service.py` |
| API | `app/api/routes_*.py` |
| UI | `app/web/index.html`, `static/app.js`, `styles.css` |
| 测试 | `tests/` |

---

## 7. 运行与验证速查

```powershell
cd E:\vibecoding
conda activate grok
# 配置 .env → XAI_API_KEY
python -m uvicorn app.main:app --reload --port 8000
pytest -q
```

- UI：http://localhost:8000  
- OpenAPI：http://localhost:8000/docs  
- 默认账号：`admin` / `admin123`  

---

## 8. 变更日志（工程与文档）

| 日期 | 变更 |
|------|------|
| 2026-07-18 | PRD v0.5 定稿（用户交付研发） |
| 2026-07-18 | 用户确认：FastAPI+Web UI、完整 P0、SQLite |
| 2026-07-18 | 用户确认：数据集与导出支持 **Excel + CSV** |
| 2026-07-18 | MVP P0 代码落地；pytest 12 passed |
| 2026-07-18 | 建立 `docs/`：PRD 存档 + plan 拆分（架构/决策/状态/TODO） |
| 2026-07-18 | **D017–D022**：二元阈值、细则合并 Prompt、一键标注、模板版本等 |
| 2026-07-19 | **D024–D027**：鉴权、详情 UX、抽 QC 自动平均、人工 Prompt 优先 |
| 2026-07-19 | 初版收尾：死代码清理；pytest **18 passed**；status/TODO/architecture 同步 |

---

## 9. 维护检查清单（每次会话结束前）

- [ ] 若有新决策 → 写入 `decisions.md` 并在本文件 §2 摘要一行  
- [ ] 若有架构/API/状态机变更 → 更新 `architecture.md`  
- [ ] 若功能完成或阻塞 → 更新 `status.md`  
- [ ] 若有新工作项 → 更新 `TODO.md`  
- [ ] 若 PRD 修订 → 新版本文件 + 本变更日志  

---

*本 plan 为活文档。与代码冲突时：以已合并代码行为为准，并立即回写文档消除漂移。*
