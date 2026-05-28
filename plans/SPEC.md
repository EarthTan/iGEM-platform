# SPEC.md — iGEM Silk Platform

> **状态**: 初稿，待用户确认
> **位置**: `docs/superpowers/plans/SPEC.md`

---

## 产品概述

iGEM Silk Platform 是一个**丝素蛋白融合功能肽设计的可视化 Web 平台**。科学家在网页上配置参数、触发完整的 8 轮 Pipeline 运行、实时查看漏斗进度与日志、获得最终排名并浏览 3D 结构。

---

## 目标用户

- **实验人员（A）**：不懂代码，通过网页操作完整 Pipeline
- **科学家（B）**：需要调整参数配置、对比不同设计方案

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + Vite + React Router v6 + React Query + Zustand + NGL Viewer |
| 后端 | FastAPI + SQLAlchemy 2.0 (async) + PyJWT |
| 数据库 | PostgreSQL（平台元数据）+ DuckDB（Pipeline 结果，只读）|
| 任务队列 | FastAPI BackgroundTasks + 独立 Scheduler 进程 |
| 实时推送 | Server-Sent Events (SSE) |
| 部署 | Docker Compose（PostgreSQL + Backend + Scheduler）|

---

## 功能范围

### Phase 1（MVP）

| 功能 | 说明 |
|------|------|
| 用户注册/登录 | JWT 认证，Token 持久化到 localStorage |
| 配置参数（3 层级）| A：功能类型；B：+ TopN/Linker/3D工具；C：完整权重阈值 |
| 触发 Pipeline 运行 | POST /api/jobs，后端创建任务，Scheduler 启动 subprocess |
| 漏斗进度可视化 | 实时条形图，8 个 stage 逐步缩小 |
| 日志流 | 可展开查看 run.log 实时内容 |
| 排名结果表格 | Linker类型 / 位置方案 / 功能肽序列 / 展开各服务得分 |
| 任务中断 | DELETE /api/jobs/:id，Scheduler 停止 subprocess |
| 任务历史列表 | 分页展示所有 run，支持状态筛选 |

### Phase 2

| 功能 | 说明 |
|------|------|
| 配置方案保存/复用 | 私有配置 + 公开市场，可复制他人配置 |
| 结果对比 | 多 run 横向比较排名和漏斗形状 |
| PDB 结构浏览 | NGL Viewer，旋转/缩放/导出 |

---

## 路由设计

| 路由 | 页面 | Phase |
|------|------|-------|
| `/` | 首页（配置 + 触发）| 1 |
| `/login` | 登录 | 1 |
| `/register` | 注册 | 1 |
| `/jobs` | 任务列表 | 1 |
| `/jobs/:jobId` | 任务详情（漏斗 + 日志 + 排名）| 1 |
| `/configs` | 配置管理（私有/公开）| 2 |
| `/compare` | 结果对比 | 2 |
| `/structures/:constructId` | PDB 浏览 | 2 |

---

## 核心 API

### Auth API

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET | `/api/auth/me` | 当前用户信息 |

### Jobs API

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/jobs` | 创建任务 |
| GET | `/api/jobs` | 列表（分页 + 状态过滤）|
| GET | `/api/jobs/:id` | 详情 + 进度 |
| DELETE | `/api/jobs/:id` | 中断任务 |
| GET | `/api/jobs/:id/events` | SSE 实时事件流 |

### Results API

| 方法 | 端点 | 描述 |
|------|------|------|
| GET | `/api/results/:job_id/ranking` | 排名列表 |
| GET | `/api/results/:job_id/funnel` | 漏斗数据 |
| GET | `/api/results/:job_id/pdb/:construct_id` | PDB 文件流 |

### Configs API

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/configs` | 创建配置 |
| GET | `/api/configs` | 我的私有配置 |
| GET | `/api/configs/public` | 公开配置市场 |
| PUT | `/api/configs/:id` | 更新配置 |
| DELETE | `/api/configs/:id` | 删除配置 |
| POST | `/api/configs/:id/copy` | 复制配置 |

---

## 数据存储

**PostgreSQL（平台管理）**：
- `users`：用户账号
- `jobs`：任务状态/进度/配置快照
- `job_events`：SSE 事件记录
- `configs`：配置方案（私有/公开）

**DuckDB（Pipeline 产出，只读）**：
- `output4/pipeline.db`（各 job 独立目录）
- `output4/pdb/*.pdb`（3D 结构文件）

**文件系统桥接**：`jobs.output_path` 字段指向各 job 的独立输出目录。

---

## Pipeline Scheduler 设计

独立常驻进程，负责：
1. 监听 PostgreSQL `jobs.status = 'pending'`
2. 启动 subprocess 执行 round 脚本（`uv run python -m main.stages4.s4_round*`）
3. GPU 资源管理（单 GPU 独占）
4. 断点续跑（复用 stages4 现有 checkpoint 机制）
5. 每 round 完成后更新 `jobs` 表进度

**与 FastAPI 完全解耦**：无直接进程间通信，通过 PostgreSQL 共享状态。

---

## 验收标准

### Phase 1

- [ ] 注册/登录/JWT 认证工作正常
- [ ] 首页可选择配置层级并触发运行
- [ ] 任务状态页实时显示漏斗进度（8 个 stage）
- [ ] 日志流可展开查看
- [ ] 排名表格显示真实数据（Linker / 位置 / 展开各服务得分）
- [ ] Pipeline 完整执行 8 个 round 不崩溃

### Phase 2

- [ ] 配置方案可保存/加载/公开分享/复制
- [ ] 对比页可横向比较多 run
- [ ] NGL Viewer 可交互浏览 PDB 结构

---

## 关键风险

1. stages4 round 脚本需要增加 `--job-id` 和 `--output` 参数支持（目前不支持）
2. 单 GPU 资源冲突（Scheduler 需独占 GPU 服务）
3. 多 job 并发写同一 DuckDB（每个 job 独立目录解决）
4. Docker 网络访问微服务（bridge IP 方案）