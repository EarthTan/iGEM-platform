# 前端架构

> **文档编号**: 03
> **抽象层级**: 前端技术栈 / 页面结构 / 组件设计
> **状态**: v2（根据代码审查更新）
> **更新说明**: 添加 TypeScript 接口定义；修正 SSE hook 实现；完善 Tier A/B/C 字段；增加状态设计；修复 ProtectedRoute 实现

---

## 一、技术栈

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 构建工具 | Vite | 5.x | 快启动 + HMR |
| 框架 | React | 18.x | Hooks + Concurrent Features |
| 路由 | React Router | v6 | 嵌套路由，/jobs/:id 详情 |
| 数据获取 | React Query | v5 | 轮询 + 缓存 + 后台刷新 |
| 状态管理 | Zustand | 4.x | Auth 状态、UI 状态 |
| 3D 可视化 | NGL Viewer | 2.x | PDB 结构展示 |
| 漏斗可视化 | Recharts | 2.x | 堆叠条形图 |
| UI 组件 | 待定* | — | 可选：Radix UI / shadcn/ui |
| 样式 | Tailwind CSS | 3.x | 快速样式 |

> *注：UI 组件库可在 Phase 1 后再选定，前期用 Headless UI + Tailwind 组合。

---

## 二、路由结构

```tsx
// 路由配置（react-router v6）
<Route path="/" element={<AppLayout />}>
  <Route index element={<HomePage />} />
  <Route path="login" element={<LoginPage />} />
  <Route path="register" element={<RegisterPage />} />

  {/* 受保护路由（需登录） */}
  <Route path="jobs" element={<ProtectedRoute />}>
    <Route index element={<JobsPage />} />
    <Route path=":jobId" element={<JobDetailPage />} />
  </Route>

  {/* Phase 2 */}
  <Route path="compare" element={<ComparePage />} />
  <Route path="structures/:constructId" element={<StructurePage />} />
</Route>
```

### 页面说明

| 路由 | 页面 | Phase |
|------|------|-------|
| `/` | 首页：配置参数 + 触发运行 | 1 |
| `/login` | 登录 | 1 |
| `/register` | 注册 | 1 |
| `/jobs` | 任务列表（历史 run）| 1 |
| `/jobs/:jobId` | 任务详情：漏斗 + 日志 + 排名 | 1 |
| `/compare` | 结果对比（Phase 2）| 2 |
| `/structures/:constructId` | PDB 结构浏览（Phase 2）| 2 |

---

## 三、核心组件树

```
AppLayout
├── Header (Logo + 用户菜单 + 退出)
│
├── HomePage (/)
│   └── PipelineRunnerCard
│       ├── FunctionTypeSelector     # A/B/C 三层级配置
│       ├── ConfigPanelTierA         # 层级 A：功能类型单选
│       ├── ConfigPanelTierB         # 层级 B：+ TopN/Linker/3D工具
│       ├── ConfigPanelTierC         # 层级 C：完整权重阈值
│       ├── PresetSelector           # 可选：从已保存方案加载
│       └── RunButton                # 触发运行
│
├── JobsPage (/jobs)
│   ├── FilterBar (状态筛选：全部/进行中/已完成/失败)
│   └── JobList
│       └── JobStatusCard (×N)
│           ├── StatusBadge          # pending/running/completed/failed
│           ├── FunnelMini          # 迷你漏斗图（卡片内）
│           └── QuickActions         # 查看/中断/删除
│
├── JobDetailPage (/jobs/:jobId)
│   ├── JobHeader                   # Job名称/状态/用时/操作按钮
│   ├── FunnelVisualization         # 完整漏斗可视化（大图）
│   │   └── FunnelBar (×8 rounds)
│   ├── LogPanel                    # 可展开日志流
│   │   └── LogViewer (虚拟滚动)
│   └── RankingTable
│       ├── SortableHeader
│       └── RankingRow (×N)
│           ├── SequenceCell        # 功能肽序列（可复制）
│           ├── LinkerCell          # Linker 类型
│           ├── PositionCell        # N端/C端/两端
│           ├── CompositeScoreCell  # 综合分
│           └── ExpandableScores   # 展开各服务详细得分
│
└── StructurePage (Phase 2, /structures/:constructId)
    ├── NGLViewer                   # NGL Viewer 组件
    ├── StructureInfoPanel         # 基本信息侧栏
    └── ExportButtons               # 下载 PDB / 截图
```

---

## 四、TypeScript 类型定义

### 4.1 API 类型接口

```typescript
// API 响应基础类型
interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// User
interface User {
  id: string;
  email: string;
  created_at: string;
}

// Job
interface Job {
  id: string;
  user_id: string;
  config_id: string | null;
  name: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'paused';
  current_round: string | null;
  processed_count: number;
  total_count: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  output_path: string;
  config_snapshot: ConfigData;
}

interface JobEvent {
  id: string;
  job_id: string;
  round: string | null;
  event: 'job_created' | 'round_started' | 'round_completed' | 'job_completed' | 'job_failed' | 'progress';
  message: string | null;
  timestamp: string;
}

// Config
interface Config {
  id: string;
  user_id: string;
  name: string;
  is_public: boolean;
  config_data: ConfigData;
  created_at: string;
  updated_at: string;
}

// Ranking
interface RankingItem {
  rank: number;
  construct_id: number;
  sequence: string;
  linker: string;
  position: 'N端' | 'C端' | '两端';
  channel: 'top' | 'middle' | 'bottom';
  composite_score: number;
  anoxpepred?: number;
  sodope?: number;
  plddt?: number;
  sasa_exposed?: boolean;
}

// Funnel
interface FunnelStage {
  name: string;
  count: number;
  color: string;
}

// ConfigData（对应后端 Pydantic ConfigData）
interface ConfigData {
  function_type: 'antioxidant' | 'antimicrobial' | 'antiglycation';
  tier: 'A' | 'B' | 'C';
  top_n?: number;          // Tier B/C
  linkers?: string[];      // Tier B/C，e.g. ["Flex_GGGGSx2"]
  structure_tool?: 'omegafold' | 'esmfold';  // Tier B/C
  weights?: Record<string, number>;   // Tier C
  thresholds?: Record<string, number>; // Tier C
  round_splits?: Record<string, number>;  // Tier C，每轮 split 比例
  channel_strategy?: 'greedy' | 'balanced' | 'exhaustive'; // Tier C
}
```

### 4.2 API Hook 返回类型（React Query）

```typescript
// jobs.ts
export function useJobs(page = 1, pageSize = 20, status?: Job['status']) {
  return useQuery({
    queryKey: ['jobs', page, pageSize, status],
    queryFn: () => api.get<{ items: Job[]; total: number }>(`/api/jobs?page=${page}&page_size=${pageSize}&status=${status}`),
  });
}

export function useJob(jobId: string) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 5000 : false,
  });
}

export function useRanking(jobId: string, page = 1, pageSize = 20) {
  return useQuery({
    queryKey: ['ranking', jobId, page, pageSize],
    queryFn: () => api.get<PaginatedResponse<RankingItem>>(`/api/results/${jobId}/ranking?page=${page}&page_size=${pageSize}`),
    enabled: !!jobId,
  });
}

export function useFunnel(jobId: string) {
  return useQuery({
    queryKey: ['funnel', jobId],
    queryFn: () => api.get<{ stages: FunnelStage[] }>(`/api/results/${jobId}/funnel`),
    enabled: !!jobId,
  });
}

// configs.ts
export function useConfigs() {
  return useQuery({
    queryKey: ['configs'],
    queryFn: () => api.get<Config[]>('/api/configs'),
  });
}

export function usePublicConfigs() {
  return useQuery({
    queryKey: ['configs', 'public'],
    queryFn: () => api.get<Config[]>('/api/configs/public'),
  });
}
```

### 4.3 Zustand Store 类型

```typescript
// authStore.ts
interface AuthStore {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isLoading: false,
      login: async (email, password) => {
        set({ isLoading: true });
        const { data } = await api.post<{ access_token: string; user: User }>('/api/auth/login', { email, password });
        localStorage.setItem('access_token', data.access_token);
        set({ user: data.user, token: data.access_token, isLoading: false });
      },
      logout: () => {
        localStorage.removeItem('access_token');
        set({ user: null, token: null });
      },
    }),
    { name: 'auth-storage' }
  )
);

// configStore.ts（跨 Tier 共享配置状态）
interface ConfigStore {
  tier: 'A' | 'B' | 'C';
  functionType: ConfigData['function_type'];
  // Tier A fields
  // Tier B additional fields
  topN: number;
  linkers: string[];
  structureTool: 'omegafold' | 'esmfold';
  // Tier C additional fields
  weights: Record<string, number>;
  thresholds: Record<string, number>;
  roundSplits: Record<string, number>;
  channelStrategy: 'greedy' | 'balanced' | 'exhaustive';
  // Actions
  setTier: (tier: 'A' | 'B' | 'C') => void;
  setFunctionType: (type: ConfigData['function_type']) => void;
  setTopN: (n: number) => void;
  setLinkers: (linkers: string[]) => void;
  setWeights: (weights: Record<string, number>) => void;
  reset: () => void;
}

const defaultConfig: ConfigStore = {
  tier: 'A',
  functionType: 'antioxidant',
  topN: 20,
  linkers: ['Flex_GGGGSx2'],
  structureTool: 'omegafold',
  weights: {},
  thresholds: {},
  roundSplits: {},
  channelStrategy: 'balanced',
  setTier: () => {},
  setFunctionType: () => {},
  setTopN: () => {},
  setLinkers: () => {},
  setWeights: () => {},
  reset: () => {},
};
```

---

## 五、数据获取模式（React Query）

### 5.1 轮询策略

```tsx
// 任务详情页 — 运行时每 5s 轮询
const { data: job } = useQuery({
  queryKey: ['job', jobId],
  queryFn: () => api.getJob(jobId),
  refetchInterval: (query) =>
    query.state.data?.status === 'running' ? 5000 : false,
  // completed/failed 时停止轮询
})
```

### 5.2 SSE 订阅（cursor-based，防止重复推送）

```tsx
// useJobEvents.ts — 修复：cursor-based SSE，追踪 lastEventId
import { useState, useEffect, useRef, useCallback } from 'react';

interface UseJobEventsOptions {
  onEvent?: (event: JobEvent) => void;
}

function useJobEvents(jobId: string, options: UseJobEventsOptions = {}) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const connect = useCallback(() => {
    // 清理旧连接
    if (esRef.current) {
      esRef.current.close();
    }

    // 带 lastEventId 的 SSE 连接（cursor-based）
    const url = lastEventIdRef.current
      ? `/api/jobs/${jobId}/events?last_event_id=${lastEventIdRef.current}`
      : `/api/jobs/${jobId}/events`;

    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      const event: JobEvent = JSON.parse(e.data);
      setEvents(prev => [...prev, event]);
      lastEventIdRef.current = event.id;  // 游标前移
      options.onEvent?.(event);
    };

    es.onerror = () => {
      setError('SSE connection error, reconnecting...');
      es.close();
      // 1 秒后重连，保留 lastEventId 避免重复
      setTimeout(connect, 1000);
    };

    return es;
  }, [jobId, options]);

  useEffect(() => {
    const es = connect();
    return () => es.close();
  }, [connect]);

  return { events, error };
}

export default useJobEvents;
```

**SSE 重连时传递 lastEventId**：防止在断线重连后收到重复的旧事件。服务端 cursor-based 实现见 `02-system-architecture.md` [六]。

### 5.3 缓存策略

| 数据类型 | 缓存时间 | 说明 |
|----------|----------|------|
| 用户信息 | `StaleTime: Infinity` | 登录后不变，主动 invalidate |
| 任务列表 | `CacheTime: 5min` | 后台刷新，列表变化时更新 |
| 任务详情（进行中）| `StaleTime: 0` | 每 5s 强制刷新 |
| 排名结果 | `StaleTime: 30min` | 完成后不常变化 |
| PDB 文件 | `StaleTime: 24h` | 只读，几乎不变 |

---

## 五、关键组件设计

### 5.1 PipelineRunnerCard（首页核心）

```
┌─────────────────────────────────────────────────────┐
│ 功能类型                                              │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│ │● 抗氧化  │ │○ 抗菌    │ │○ 抗糖化  │            │
│ └──────────┘ └──────────┘ └──────────┘            │
│                                                     │
│ 配置层级: [A] [B] [C]                               │
│ ┌─────────────────────────────────────────────────┐│
│ │  Tier A:                                         ││
│ │    抗氧化活性  ○                                  ││
│ │                               [保存方案] [运行] ││
│ └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

**Tier 切换逻辑**：
- 点击 A/B/C 切换显示的配置面板
- 表单状态用 Zustand 管理（跨面板共享 `function_type` 等公共字段）

### 5.2 FunnelVisualization（漏斗可视化）

用 Recharts 的 `BarChart` 堆叠实现：

```tsx
// 数据格式
const funnelData = [
  { round: 'Round 0', count: 19_900_000, color: '#94a3b8' },
  { round: 'Round 1', count: 250_000, color: '#60a5fa' },
  { round: 'Round 3', count: 5_000, color: '#34d399' },
  { round: 'Stage 4', count: 250, color: '#fbbf24' },
  { round: 'Stage 5', count: 250, color: '#f97316' },  // 结构数量同 Stage 4
  { round: 'Stage 6', count: 250, color: '#ef4444' },
]

<BarChart data={funnelData}>
  <XAxis dataKey="round" />
  <YAxis />
  <Tooltip formatter={(v) => v.toLocaleString()} />
  <Bar dataKey="count" fill="#60a5fa" />
</BarChart>
```

**运行时动态更新**：随着 `current_round` 变化，未完成阶段的 `count` 显示为预估（从上一个 round 的实际值递减推算）。

### 5.3 RankingTable（排名表格）

**默认显示列**：
| 列名 | 宽度 | 说明 |
|------|------|------|
| 排名 | 60px | 整数，从小到大 |
| 功能肽序列 | flex | 15aa 截断 + tooltip 显示完整序列 |
| Linker 类型 | 100px | 如 `Flex_GGGGSx2` |
| 位置方案 | 80px | `N端` / `C端` / `两端` |
| 综合分 | 80px | 保留 4 位小数 |

**展开列（点击行展开）**：
- AnOxPePred / BepiPred3 / SoDoPE / TemStaPro / pLDDT / SASA / Aggrescan3D 各自一列

**排序**：默认按综合分降序，点击列头可切换升/降序。

### 5.4 NGL Viewer（Phase 2，结构浏览）

```tsx
import NGL from "ngl";

function StructureViewer({ pdbUrl }: { pdbUrl: string }) {
  const stageRef = useRef<NGL.Stage | null>(null);

  useEffect(() => {
    const stage = new NGL.Stage(pdbUrl, {
      backgroundColor: "white",
    });
    stageRef.current = stage;
    stage.loadFile(pdbUrl);
    stage.autoView();

    return () => stage.dispose();
  }, [pdbUrl]);

  return <div ref={containerRef} style={{ width: "100%", height: "500px" }} />;
}
```

---

## 六、ProtectedRoute 实现

```tsx
// components/layout/ProtectedRoute.tsx
import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

export function ProtectedRoute() {
  const { user, token } = useAuthStore();

  if (!token || !user) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

// App.tsx 中的路由配置
function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      {/* 受保护路由组 */}
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:jobId" element={<JobDetailPage />} />
      </Route>
    </Routes>
  );
}
```

---

## 七、加载/错误/空状态设计

每个 API hook 应返回标准结构：

```typescript
// hooks/useApiResponse.ts
interface UseApiState<T> {
  data: T | null;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
}

// useJob hook 返回值的使用示例
function JobDetailPage() {
  const { data: job, isLoading, isError, error } = useJob(jobId);

  if (isLoading) return <JobDetailSkeleton />;
  if (isError) return <ErrorMessage message={error?.message || 'Failed to load job'} />;
  if (!data) return <EmptyState message="Job not found" />;

  return <JobDetailView job={data} />;
}

// 通用骨架屏
function JobDetailSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-8 w-48 bg-gray-200 rounded" />
      <div className="h-64 w-full bg-gray-100 rounded" />
      <div className="h-96 w-full bg-gray-100 rounded" />
    </div>
  );
}
```

---

## 八、项目结构（frontend/）

```
platform/frontend/
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── api/
    │   ├── client.ts          # axios 实例 + 拦截器（JWT）
    │   ├── auth.ts            # React Query hooks for auth
    │   ├── jobs.ts            # React Query hooks for jobs
    │   ├── results.ts         # React Query hooks for results
    │   └── configs.ts         # React Query hooks for configs
    ├── components/
    │   ├── ui/                # 基础 UI 组件（Button/Input/Card）
    │   ├── layout/            # AppLayout/Header
    │   ├── pipeline/          # PipelineRunnerCard/FunnelChart/RankingTable
    │   └── structure/         # NGLViewer (Phase 2)
    ├── pages/
    │   ├── HomePage.tsx
    │   ├── LoginPage.tsx
    │   ├── RegisterPage.tsx
    │   ├── JobsPage.tsx
    │   ├── JobDetailPage.tsx
    │   ├── ComparePage.tsx     # Phase 2
    │   └── StructurePage.tsx   # Phase 2
    ├── hooks/
    │   ├── useAuth.ts
    │   ├── useJobEvents.ts     # SSE 订阅
    │   └── useConfig.ts
    ├── store/
    │   ├── authStore.ts       # Zustand
    │   └── configStore.ts     # Zustand
    └── lib/
        └── utils.ts           # cn() / formatScore / ...
```

---

## 八、前端与后端的接口约定

### 8.1 JWT 鉴权

- `POST /api/auth/login` 返回 `{ access_token, user: { id, email } }`
- 前端存 `access_token` 到 `localStorage`，每次请求 `Authorization: Bearer <token>` 头部
- Token 有效期 7 天，前端在 `axios` 拦截器中自动刷新

### 8.2 错误处理

| HTTP 状态码 | 前端行为 |
|-------------|----------|
| 401 | 清理本地 token，跳转登录页 |
| 403 | Toast 提示"无权限" |
| 404 | 404 页面或 Toast |
| 422 | 显示后端返回的 validation 错误 |
| 500 | Toast 提示"服务器错误" |

### 8.3 文件路径安全

后端返回的 PDB URL 格式：
```
/api/results/{job_id}/pdb/{construct_id}
```

前端 NGL 直接加载此 URL（后端作为静态文件服务，不暴露真实文件路径）。