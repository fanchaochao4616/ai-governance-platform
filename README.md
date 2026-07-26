# 内容风控文本分类标注平台（PRD v0.5）

人工主导的多轮置信度筛选 + 双 Agent Prompt 优化 + 动态 Gold Test Set + Prompt 知识库。

## 文档（必读）

| 文档 | 说明 |
|------|------|
| [docs/plan.md](docs/plan.md) | **活文档入口**：决策/架构/状态/TODO 索引与约定 |
| [docs/PRD_v0.5.md](docs/PRD_v0.5.md) | 产品需求原文存档 |
| [docs/architecture.md](docs/architecture.md) | 架构、状态机、API、目录 |
| [docs/decisions.md](docs/decisions.md) | 重要决策 ADR |
| [docs/status.md](docs/status.md) | 当前实现状态与限制 |
| [docs/TODO.md](docs/TODO.md) | 待办与技术债 |

> 重要决策、架构变更、状态与 TODO **必须写入 `docs/`**，不要只留在对话里。

## 能力概览

- **置信度判定（无多分类标签）**：只填风控细则 + 判定阈值；模型输出 confidence，`≥阈值`→`1`，否则`0`  
- **双 Agent**：质检大模型优化 Prompt；标注小模型打分  
- **Gold Test Set 迭代**：达用户 Accuracy 阈值或 max iter / token 预算后停止  
- **永久序号 `seq`**：导入后不变；多轮历史按 seq 聚合；**对模型输入隐藏**  
- **人工每轮决策**：自定义 High/Medium/Low 置信度分层 → QC → 是否继续 / 下一轮范围  
- **多轮平均**：多数表决，平票按平均 confidence 破同分  
- **导入/导出**：CSV、Excel（`.xlsx`）  
- **Prompt 版本**：时间线、diff、回滚、沉淀为模板  

## 快速开始

### 推荐：conda 环境 `grok`

```powershell
cd E:\vibecoding
conda activate grok
pip install -r requirements.txt
# 若无 .env：copy .env.example .env 并填入 XAI_API_KEY
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 默认登录（SQLite 账号）

首次启动若 `users` 表为空，会自动创建管理员：

| 字段 | 默认值 |
|------|--------|
| 用户名 | `admin` |
| 密码 | `admin123` |

可在 `.env` 用 `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` 覆盖。  
顶栏用户信息支持 **修改密码**、**退出登录**；会话与用户表均在 SQLite。

### 备选：项目本地 `.venv`

```powershell
cd E:\vibecoding
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入 XAI_API_KEY
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器打开：http://localhost:8000  
API 文档：http://localhost:8000/docs  

## 推荐操作流程

1. **登录**（默认 `admin` / `admin123`）  
2. **新建 Job**：填写风控细则（= 初始 Prompt 种子）；可选绑定模板 — **不配**多分类标签 / Token 预算 / 创建时阈值  
3. 进入 Job 详情 → 右侧 **上传 Dataset + Gold**（label 仅 **0 / 1**）  
4. **确认开始数据标注** → 自动 Gold 优化 + 全量标注（看 KPI 进度）  
5. 主栏设 **判定阈值** → 应用；设 **分层切点** + QC 配额 → **应用分层并抽 QC**  
   - 同时会按 `from_round`–`to_round`（默认当前轮）做多轮平均  
6. **显示 QC** 在右侧标 0/1；主栏底部可 **改 Prompt** 与 **修改说明**  
7. **重新标注**：置信度层 from/to（如 Low–Medium）→ 导出 Excel/CSV  

### 列名约定

| 字段 | 可选表头 |
|------|----------|
| 文本 | `text` / `content` / `文本` / `内容` |
| 标签 | `label` / `gold_label` / `标签` |
| 外部 ID | `id` / `external_id` / `业务ID` |

### 导出

- `GET /api/v1/jobs/{id}/export?format=xlsx` — 多 sheet：`annotations` / `rounds` / `meta`  
- `GET /api/v1/jobs/{id}/export?format=csv` — zip：`annotations.csv` + `rounds.csv` + `meta.csv`  

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `XAI_API_KEY` | xAI API Key | QC 云端调用必填 |
| `XAI_BASE_URL` | QC API 地址 | `https://api.x.ai/v1` |
| `XAI_QC_MODEL` | 质检大模型 | `grok-4.5` |
| `ANNOTATOR_BASE_URL` | 标注小模型 API（OpenAI 兼容） | 默认同 `XAI_BASE_URL`；本机 Ollama 用 `http://127.0.0.1:11434/v1` |
| `ANNOTATOR_MODEL` / `XAI_ANNOTATOR_MODEL` | 标注小模型名 | 如 `qwen2.5:7b` |
| `ANNOTATOR_API_KEY` | 小模型密钥 | Ollama 填 `ollama` 即可 |
| `ANNOTATOR_TRUST_ENV` | 是否走系统代理 | 本地 Ollama 建议 `0` |
| `DEFAULT_QC_PER_BIN` | 每层默认 QC 数 | `20` |
| `ANNOTATOR_CONCURRENCY` | 标注并发 | 本地 7B 建议 `1`–`2` |

## 测试

```powershell
pip install pytest httpx
pytest -q
```

不依赖真实 LLM 的测试覆盖：置信度分层、多轮平均、CSV/Excel 解析、Job 创建/上传/导出。

## 目录结构

```text
app/
  main.py              # FastAPI 入口
  models.py            # SQLAlchemy 实体
  state_machine.py     # Job 状态
  agents/              # QCAgent / AnnotatorAgent
  services/            # 业务服务（含 io_tabular、export）
  api/                 # REST 路由
  web/                 # 简单 Web UI
config.py / llm_client.py
data/                  # SQLite、上传、导出（gitignore）
tests/
```

## 注意

- 万级数据标注为后台任务，请轮询 Job 状态 / 进度  
- Token 超预算会进入 `BUDGET_EXCEEDED`  
- 内部 pilot：默认 SQLite 单机；生产可迁 PostgreSQL  
