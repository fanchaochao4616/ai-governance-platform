# 项目状态

> 主入口：[plan.md](./plan.md) · 待办：[TODO.md](./TODO.md)  
> **最后更新**：2026-07-27

---

## 总览

| 项 | 值 |
|----|-----|
| 产品 | 内容风控文本分类标注平台 |
| PRD | [PRD_v0.5.md](./PRD_v0.5.md) |
| 阶段 | **初版可交付（内部 pilot）** — 标注闭环 + 数据集库 + 多模式清洗 |
| 测试 | `pytest`（不依赖真实 LLM） |
| 交付形态 | FastAPI + 原生 Web UI + SQLite 鉴权 + xAI / 本地 Ollama |

---

## 里程碑完成情况

| ID | 里程碑 | 状态 | 说明 |
|----|--------|------|------|
| M1 | 骨架与数据层 | ✅ | models、Job CRUD、CSV/Excel 导入、seq |
| M2 | 双 Agent + Gold 闭环 | ✅ | QC/Annotator、`gold_optimize`、PromptVersion |
| M3 | 全量标注 + 置信度 + QC | ✅ | 批标注、bins、分层抽样、QC 提交与 gold 增长 |
| M4 | 决策 + 多轮 + 导出 | ✅ | decision、子集重标、多轮平均、CSV/XLSX |
| M5 | Prompt 知识库 + UI | ✅ | 模板版本/diff/激活 + Job 内 Prompt 历史 |
| M6 | 监控/测试/README | ✅ | events、metrics、tests、README |
| M7 | 鉴权 + 详情页密集 UX | ✅ | SQLite 登录；Job 三栏布局；QC/历史占用右侧栏 |
| M8 | 初版收尾与代码清理 | ✅ | 去掉死代码/脚手架；文档同步至当前行为 |
| M9 | 数据集库 + 多模式清洗 | ✅ | 训练包存储、TF-IDF/BGE/LLM 清洗、diff 回退、id_ref 导出 |

---

## 数据集与清洗（2026-07 增量）

| 能力 | 状态 | 代码入口 |
|------|------|----------|
| 数据集库上传/列表/下载 | ✅ | `dataset_manage_service`, `routes_datasets` |
| 文件包 data.jsonl + manifest | ✅ | `dataset_store` |
| TF-IDF / BGE 索引与检索 | ✅ | `dataset_vector` |
| 清洗 match（kw/regex/vector/llm） | ✅ | `dataset_clean_ops.match_records` |
| 删除 id + diff 历史 / 回退 | ✅ | `apply_delete`, `restore_op`, `get_op_diff` |
| 结果阈值 + 反选（前端本地） | ✅ | `app/web/static/app.js` |
| 无条件列表勾选删除 | ✅ | browse selectable + `manual` apply |
| 导出到库仅 id 引用 | ✅ | `save_effective_as_dataset` → `kind=id_ref` |

清洗约定：

- **不改写** 原始 `data.jsonl`；`clean/ops/*.json` 记删除/回退
- **删除选中** = 改 deleted 集合 + 写 diff（进度），无单独「保存进度」按钮
- **导出到数据集库** = `include_ids.json` + manifest，不复制全文

---

## 当前产品行为（以代码为准）

### 登录与壳

- SQLite 用户表 + 会话 token；首次空库自动建 `admin` / `admin123`（可用 env 覆盖）
- 左侧可收起导航：Jobs / 新建 Job / Prompt 模板库
- 顶栏：详情页显示「← 返回列表」；右侧「系统设置」（占位）+ 用户芯片（改密/退出）

### 新建 Job

- 风控细则（= 初始 Prompt 种子）；可选模板名称搜索绑定
- Gold 优化参数（目标 Accuracy、max iter 等）在右侧栏
- **不**配置：多分类标签、Token 预算、创建时判定阈值

### 数据标注流水线

1. 右侧上传 Dataset + Gold  
2. **确认开始数据标注** → `start-annotation`：Gold 优化 → 全量标注（`live-progress` KPI）  
3. 全量后设 **判定阈值**（滑条）→ 应用阈值得 0/1  
4. **分层切点**（双滑条）+ QC 配额 → **应用分层并抽 QC**  
   - 同时读取 `from_round` / `to_round`（默认=当前轮次）并 **自动多轮平均**  
   - 右侧数据下载区仅保留导出 Excel/CSV（**无**独立「应用多轮平均」按钮）  
5. **显示 QC**：右侧临时样本列表，人工 0/1  
6. 主栏底部 **提示词修改**（当前激活 Prompt 全文可编辑）+ 小号 **修改说明**  
7. **重新标注**：from/to 置信度层（如 Low→Medium 展开为层列表）；可提交人工 Prompt（有改动则落新版本，**不**再自动 LLM 改写）  
8. **历史版本**：右侧列表；查看 Diff 时只保留当前版本卡片并挤压高度；可回滚  

### 标签约定

- 二元：`1` / `0`（Gold 与 QC 同）；阈值：`confidence ≥ threshold → 1`

---

## PRD P0 能力对照

| 能力 | 状态 | 代码入口 |
|------|------|----------|
| 数据集与任务配置 | ✅ | `job_service`, UI 新建 Job |
| 双 Agent Gold 优化 | ✅ | `gold_optimize.py`, `app/agents/*` |
| 永久序号 + 多轮记录 | ✅ | `AnnotationRecord.seq` / `.rounds` |
| 动态置信度分层抽样 | ✅ | `qc_service`, `confidence.py` |
| 人工每轮决策 | ✅ | `decision_service`；UI 重新标注 |
| 动态 Gold（QC 纠正） | ✅ | `gold_service.add_qc_correction` |
| Prompt 改进 / 人工编辑 | ✅ | QCAgent + 底部 Prompt 编辑器 → `prompt_text` |
| Prompt 版本 diff/回滚 | ✅ | `prompt_service`；详情右侧历史 |
| 模板知识库 | ✅ | `template_service`, 模板库页 |
| 监控与导出 | ✅ | `metrics_service`, `export_service` |
| Token 统计（可选硬预算） | ✅ | `budget.py`（UI 默认不限） |
| seq 对模型不可见 | ✅ | Annotator 只收 text |
| 登录鉴权 | ✅ | `auth_service`, `routes_auth` |

---

## 已知限制（当前版本）

1. **后台任务无持久队列**：进程重启可能中断 Gold/标注，需人工重试。  
2. **SQLite 写锁**：万级高并发未做独立 worker。  
3. **系统设置**仅为占位，无平台级配置项。  
4. **JSONL** 非主推格式。  
5. **Gold 未达阈值**：`GOLD_FAILED` 仍可进流水线全量标注。  
6. **真实大模型 E2E**：自动化测试不打 xAI；需手工联调。  
7. **应用分层并抽 QC 会同时 finalize**：Job 进入 `COMPLETED`；若仍需继续多轮 QC，需知悉状态机影响（后续可再拆「仅抽 QC」与「平均完成」）。  

---

## 最近验证

```text
pytest -q  →  18 passed
覆盖：鉴权、状态机、置信度分层、多轮平均、CSV/Excel、Job API、模板版本
```

---

## 如何启动（现状）

```powershell
cd E:\vibecoding
conda activate grok
# .env 中配置 XAI_API_KEY
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- UI：http://localhost:8000  
- API：http://localhost:8000/docs  
- 默认账号：`admin` / `admin123`  
