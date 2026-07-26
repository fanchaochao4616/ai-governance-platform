# TODO / 待办清单

> 主入口：[plan.md](./plan.md) · 状态：[status.md](./status.md)  
> **约定**：新增重要工作先改本文件或 plan.md，避免只存在于对话上下文。

图例：`[ ]` 待做 · `[~]` 进行中 · `[x]` 完成 · `[-]` 取消/不做

---

## 已完成（MVP P0 + 初版 UX）

- [x] 保存 PRD v0.5 至仓库 `docs/PRD_v0.5.md`
- [x] 实现计划与决策/架构/状态文档化
- [x] FastAPI 应用骨架 + SQLite 模型
- [x] CSV/Excel 导入与导出
- [x] 永久序号 seq 分配与多轮历史
- [x] 双 Agent（QC + Annotator）+ Gold 优化闭环
- [x] 全量/子集标注 + 置信度分层 + QC
- [x] 人工决策 + Prompt 改进 + 多轮平均
- [x] Prompt 版本 diff/回滚 + 模板库（版本/激活/Diff）
- [x] 基础 Web UI + metrics/events
- [x] 置信度阈值 → 二元 0/1；细则合并初始 Prompt；创建时无 Token 预算
- [x] 判定阈值在全量标注后设置
- [x] 一键「数据标注」+ live-progress KPI
- [x] SQLite 登录 / 退出 / 改密；左侧导航 + 系统设置占位
- [x] Job 详情密集布局：阈值/分层、QC 与历史占用右侧栏、底部可编辑 Prompt
- [x] from_round/to_round 默认当前轮；应用分层并抽 QC 时自动多轮平均
- [x] 重新标注范围改为 from/to 置信度层；历史 Diff 查看/收起交互
- [x] 初版代码清理（死 UI/CSS、无用 API/脚手架）
- [x] 单元/API 测试（18 passed）+ README

---

## 近期建议（P1 / pilot 前）

### 联调与数据

- [ ] 使用真实 `XAI_API_KEY` 跑通 **小样本 E2E**（10–50 条 + 5–10 gold）
- [ ] 准备内部风控细则样例（可放 `docs/examples/`）
- [ ] 补充示例 CSV/XLSX 模板文件（dataset + gold，label 仅 0/1）
- [ ] 记录一次真实 Job 的 token 消耗与轮次，校准默认参数

### 体验与流程一致性

- [ ] **拆分**「应用分层并抽 QC」与「多轮平均 finalize」：当前合并可能导致过早 `COMPLETED`
- [ ] 后台任务失败后的 **「重试当前步骤」** API + UI
- [ ] QC 未全部 review 时提交的校验与提示
- [ ] bins 区间覆盖 [0,1] 的强校验（当前允许空洞并警告）
- [ ] 导出前提示 final_label 是否已写入

### 文档

- [ ] Onboarding：Gold 准备指南、分层建议、反馈/改 Prompt 话术
- [ ] 运维：备份 `data/app.db`、磁盘与 token 建议

---

## 中期（P2 / 工程化）

- [ ] 引入任务队列（RQ/Celery/ARQ）替代进程内 BackgroundTasks
- [ ] PostgreSQL 适配 + 迁移脚本（Alembic）
- [ ] 鉴权增强（SSO / 角色）与操作审计
- [ ] 结构化日志 + 请求 trace id
- [ ] 标注批处理断点续跑（按 seq 范围 checkpoint）
- [ ] JSONL 导入可选兼容（若业务需要）
- [ ] 系统设置页：默认模型、QC 配额、会话策略等
- [ ] Dashboard：badcase 分布、模板复用率

---

## 长期（对齐 North Star）

- [ ] 与 BERT 训练流水线对接（导出格式/数据契约）
- [ ] 多标签 / 层级标签评估（若政策需要）
- [ ] 数据 at-rest 加密与密钥管理
- [ ] 多租户与项目空间隔离
- [ ] 置信度校准（temperature scaling / 历史 agreement）

---

## 技术债

- [ ] FastAPI `TestClient` / httpx 弃用警告跟进
- [ ] SQLAlchemy JSON 字段变更可考虑 `MutableList` 或始终整体赋值
- [ ] `routes_jobs.start` / `start-full-label` 兼容接口是否下线或收紧门禁
- [ ] 统一错误码与前端 toast 文案
- [x] 根目录 `agents/` 与 smoke_test 已删除（仅保留 `app/agents/`）

---

## 决策待办（见 decisions.md 预留）

- [ ] D012 JSONL 是否支持  
- [ ] D013 PG 迁移时间点  
- [ ] D014 队列选型  
- [ ] D015 鉴权增强方案  
- [ ] D016 加密方案  
- [ ] D029 抽 QC 与 finalize 是否拆开  
