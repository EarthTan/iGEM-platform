# Frontend Architecture Review

> **Review date**: 2026-05-28
> **Reviewer**: Architecture Review
> **Documents reviewed**: `03-frontend-architecture.md`, `02-system-architecture.md`, `05-implementation-plan.md`
> **Status**: Critical Issues Found

---

## Executive Summary

The frontend architecture plan is fundamentally sound but suffers from **significant specification gaps** that will cause implementation ambiguities. Most critically:

1. React Query hooks have no defined return types or error handling patterns
2. Zustand store shapes are incomplete — no persistence strategy specified
3. Tier A/B/C config panels have no field definitions whatsoever
4. SSE implementation has a critical flaw (infinite polling loop)
5. No loading/error/empty state specifications for any component

**Estimated critical gaps**: 23 items requiring clarification before Phase 1 implementation.

---

## 1. API Integration Layer — Critical Gaps

### 1.1 `api/auth.ts` — Missing Specifications

The plan mentions "React Query hooks for auth" but defines no function signatures.

**Required interfaces**:

```typescript
// api/auth.ts

// Request types
interface LoginRequest {
  email: string;
  password: string;
}

interface RegisterRequest {
  email: string;
  password: string;
}

// Response types
interface AuthResponse {
  access_token: string;
  token_type: "Bearer";
  user: User;
}

interface User {
  id: string;
  email: string;
  created_at: string; // ISO8601
}

// Hook signatures (MISSING from plan)
interface UseLoginOptions {
  onSuccess?: (data: AuthResponse) => void;
  onError?: (error: AuthError) => void;
}

interface UseRegisterOptions {
  onSuccess?: (data: AuthResponse) => void;
  onError?: (error: AuthError) => void;
}

// Auth error classification (MISSING)
type AuthError =
  | { type: "VALIDATION_ERROR"; message: string; field?: string }
  | { type: "INVALID_CREDENTIALS"; message: string }
  | { type: "NETWORK_ERROR"; message: string }
  | { type: "UNKNOWN_ERROR"; message: string };

// REQUIRED: Define these hooks
export function useLogin(options?: UseLoginOptions): UseMutationResult<AuthResponse, AuthError, LoginRequest>
export function useRegister(options?: UseRegisterOptions): UseMutationResult<AuthResponse, AuthError, RegisterRequest>
export function useCurrentUser(): UseQueryResult<User, AuthError>
export function useLogout(): UseMutationResult<void, Error>
```

**Critical issue**: The plan mentions JWT refresh in axios interceptor but:
- No token refresh endpoint specified in Auth API (section 4.1 of 02-system-architecture.md is missing refresh endpoint)
- No `401` retry logic defined
- No token expiry handling

### 1.2 `api/jobs.ts` — Incomplete Hooks

**Required interfaces**:

```typescript
// api/jobs.ts

// Request types
interface CreateJobRequest {
  config_data: JobConfig;
  config_id?: string; // optional: reuse existing config
}

interface JobConfig {
  function_type: "antioxidant" | "antimicrobial" | "antiglycation";
  tier: "A" | "B" | "C";
  top_n?: number;           // Tier B+
  linkers?: string[];       // Tier B+
  structure_tool?: "omegafold" | "esmfold";  // Tier B+
  weights?: Record<string, number>;   // Tier C
  thresholds?: Record<string, number>; // Tier C
}

// Response types
interface Job {
  id: string;
  user_id: string;
  config_id: string;
  status: "pending" | "running" | "completed" | "failed" | "paused";
  current_round: string | null;  // e.g., "round03"
  processed_count: number;
  total_count: number | null;
  output_path: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

interface JobListResponse {
  jobs: Job[];
  total: number;
  page: number;
  page_size: number;
}

// CRITICAL: These hook signatures are NOT in the plan
export function useJobs(options?: {
  page?: number;
  page_size?: number;
  status?: Job["status"];
}): UseQueryResult<JobListResponse, ApiError>

export function useJob(jobId: string): UseQueryResult<Job, ApiError>

export function useCreateJob(options?: {
  onSuccess?: (job: Job) => void;
  onError?: (error: ApiError) => void;
}): UseMutationResult<Job, ApiError, CreateJobRequest>

export function useDeleteJob(jobId: string): UseMutationResult<void, ApiError>

// Polling configuration (MISSING from plan)
export function useJobPolling(jobId: string): UseQueryResult<Job, ApiError> {
  // Should implement:
  // - refetchInterval: 5000 when status === "running"
  // - refetchInterval: false when status === "completed" | "failed" | "paused"
  // - StaleTime: 0 while running (force refresh)
  // - StaleTime: 5min when completed
}
```

### 1.3 `api/results.ts` — Completely Missing

The plan mentions "React Query hooks for results" but provides zero specifications.

**Required interfaces**:

```typescript
// api/results.ts

interface RankingItem {
  rank: number;
  sequence: string;        // 15aa truncated display, full in tooltip
  linker_type: string;    // e.g., "Flex_GGGGSx2"
  position_scheme: "N端" | "C端" | "两端";
  composite_score: number; // 4 decimal places

  // Expanded scores (visible on row expand)
  anoxpepred_score?: number;
  bepipred3_score?: number;
  sodope_score?: number;
  temstapro_score?: number;
  plddt_score?: number;
  sasa_score?: number;
  agrescan3d_score?: number;
}

interface RankingResponse {
  items: RankingItem[];
  total: number;
  page: number;
  page_size: number;
}

interface FunnelStage {
  name: string;    // e.g., "Round 0", "Stage 4"
  count: number;
  color: string;   // hex color
  is_estimate?: boolean;  // true for unfinished stages
}

interface FunnelResponse {
  stages: FunnelStage[];
  current_round: string | null;
}

interface ConstructDetail {
  id: string;
  sequence: string;
  linker_type: string;
  position_scheme: string;
  pdb_url: string;  // /api/results/{job_id}/pdb/{construct_id}
  scores: Record<string, number>;
}

// MISSING hook signatures
export function useRanking(
  jobId: string,
  options?: { page?: number; page_size?: number; sort_by?: string; sort_order?: "asc" | "desc" }
): UseQueryResult<RankingResponse, ApiError>

export function useFunnel(jobId: string): UseQueryResult<FunnelResponse, ApiError>

export function useConstructDetail(jobId: string, constructId: string): UseQueryResult<ConstructDetail, ApiError>
```

### 1.4 API Error Handling Standardization

**Required: Define unified error type across all API hooks**

```typescript
// api/client.ts

export interface ApiError {
  type: "NETWORK_ERROR" | "UNAUTHORIZED" | "FORBIDDEN" | "NOT_FOUND" | "VALIDATION_ERROR" | "SERVER_ERROR";
  message: string;
  status_code: number;
  details?: Record<string, string[]>; // field-level validation errors (422)
}

// Axios interceptor must:
// 1. Extract error response, classify by status code
// 2. On 401: clear auth store, redirect to /login
// 3. On 403: show toast "无权限"
// 4. On 404: show toast or render 404 page
// 5. On 422: extract validation details, map to form fields
// 6. On 5xx: show toast "服务器错误"
```

**The plan's error handling table (section 8.2 in 03-frontend-architecture.md) is a good start but incomplete** — it doesn't specify:
- How errors propagate to React Query hooks
- How to access field-level validation errors (for form display)
- Retry strategy for network errors

---

## 2. State Management — Incomplete Store Definitions

### 2.1 `authStore` — Missing Persistence and Actions

The plan provides a skeleton but is missing critical fields:

```typescript
// store/authStore.ts

interface AuthStore {
  // State
  user: User | null;
  token: string | null;
  isLoading: boolean;
  error: string | null;

  // Actions (plan mentions login/logout only)
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  register: (email: string, password: string) => Promise<void>;

  // MISSING: Token refresh
  refreshToken: () => Promise<void>;

  // MISSING: Initialization (check localStorage on app load)
  initialize: () => Promise<void>;

  // Computed (missing from plan)
  isAuthenticated: () => boolean;
}

// Persistence config (MISSING from plan)
const useAuthStore = create(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isLoading: false,
      error: null,

      login: async (email, password) => {
        set({ isLoading: true, error: null });
        try {
          const response = await api.post<AuthResponse>("/auth/login", { email, password });
          set({
            user: response.user,
            token: response.access_token,
            isLoading: false,
          });
        } catch (e) {
          set({ error: e.message, isLoading: false });
          throw e;
        }
      },

      logout: () => {
        set({ user: null, token: null });
        // IMPORTANT: Clear token from localStorage is handled by persist
      },

      initialize: async () => {
        // Called on app mount to re-hydrate auth state
        const stored = localStorage.getItem("auth-storage");
        if (stored) {
          const { state } = JSON.parse(stored);
          if (state.token) {
            // Optionally validate token with /auth/me endpoint
            try {
              const user = await api.get<User>("/auth/me");
              set({ user });
            } catch {
              // Token invalid, clear it
              set({ user: null, token: null });
            }
          }
        }
      },

      isAuthenticated: () => !!get().token,
    }),
    {
      name: "auth-storage",
      partialize: (state) => ({ token: state.token, user: state.user }),
      // CRITICAL: Never persist isLoading or error (security)
    }
  )
);
```

**Critical issue**: The plan says "Token 有效期 7 天" but doesn't specify:
- Refresh token mechanism (or is it just re-login after 7 days?)
- How to detect expired token before requests

### 2.2 `configStore` — Tier A/B/C State Missing

The plan shows a partial interface but is missing the complete tier structure:

```typescript
// store/configStore.ts

type FunctionType = "antioxidant" | "antimicrobial" | "antiglycation";
type TierLevel = "A" | "B" | "C";
type StructureTool = "omegafold" | "esmfold";

// Tier A fields (basic)
interface TierAConfig {
  function_type: FunctionType;
}

// Tier B fields (intermediate) — extends Tier A
interface TierBConfig extends TierAConfig {
  top_n: number;           // default: 10000
  linkers: string[];       // default: ["Flex_GGGGSx2", "Rigid_GGGS"]
  structure_tool: StructureTool;  // default: "omegafold"
}

// Tier C fields (advanced) — extends Tier B
interface TierCConfig extends TierBConfig {
  weights: {
    anoxpepred?: number;    // default: 1.0
    bepipred3?: number;     // default: 1.0
    sodope?: number;        // default: 1.0
    temstapro?: number;     // default: 1.0
    plddt?: number;         // default: 1.0
    sasa?: number;          // default: 1.0
    agrescan3d?: number;    // default: 1.0
  };
  thresholds: {
    plddt_min?: number;     // default: 85
    agrescan3d_max?: number; // default: 10
    sasa_max?: number;       // default: 200
  };
}

// Full config store
interface ConfigStore {
  // Current tier level
  tier: TierLevel;

  // Tier A fields
  function_type: FunctionType;

  // Tier B fields
  top_n: number;
  linkers: string[];
  structure_tool: StructureTool;

  // Tier C fields
  weights: Record<string, number>;
  thresholds: Record<string, number>;

  // Preset management
  presetName: string | null;
  savedConfigs: SavedConfig[];

  // Actions (MISSING from plan)
  setTier: (tier: TierLevel) => void;
  setFunctionType: (ft: FunctionType) => void;
  setTopN: (n: number) => void;
  setLinkers: (linkers: string[]) => void;
  setStructureTool: (tool: StructureTool) => void;
  setWeights: (weights: Record<string, number>) => void;
  setThresholds: (thresholds: Record<string, number>) => void;

  // Preset actions
  loadPreset: (configId: string) => Promise<void>;
  savePreset: (name: string) => Promise<void>;

  // Compute full config for API
  toJobConfig: () => JobConfig;

  // Reset to defaults
  reset: () => void;
}

interface SavedConfig {
  id: string;
  name: string;
  is_public: boolean;
  config_data: TierCConfig;
  created_at: string;
}
```

**Critical gap**: The plan says "Tier A/B/C 切换显示的配置面板" but:
- No default values specified
- No validation rules (e.g., top_n range)
- No "reset to defaults" action

---

## 3. Component Communication — Props Interfaces Missing

### 3.1 `FunnelVisualization` — Incomplete Props

**Current state in plan**: Only shows data format, no props interface.

**Required TypeScript**:

```typescript
// components/pipeline/FunnelVisualization.tsx

import { FunnelStage } from "../../api/results";

interface FunnelVisualizationProps {
  jobId: string;

  // Alternatively, pass data directly (if already fetched)
  funnelData?: FunnelStage[];

  // Callback when stage is clicked (optional)
  onStageClick?: (stage: FunnelStage) => void;

  // Height/width customization
  height?: number;
  width?: number;
}

// Internal state
interface FunnelVisualizationState {
  hoveredStage: string | null;
  selectedStage: string | null;
}

// Loading state (MISSING from plan)
interface FunnelLoadingState {
  // Show skeleton bars while data loads
  // Mimic approximate funnel shape
  skeletonBars: { width: string }[];
}

// Empty state (MISSING from plan)
interface FunnelEmptyState {
  message: "Job has no funnel data yet";
  // Possibly show a placeholder funnel
}

// Error state (MISSING from plan)
interface FunnelErrorState {
  message: string;
  onRetry?: () => void;
}
```

**Critical issue**: "What if data isn't loaded yet?"
- Plan provides no loading skeleton design
- No error boundary specification
- No "data unavailable" placeholder

### 3.2 `RankingTable` — Sorting/Pagination/Expansion Not Specified

**Required TypeScript**:

```typescript
// components/pipeline/RankingTable.tsx

import { RankingItem } from "../../api/results";

type SortField = "rank" | "composite_score" | "sequence" | "linker_type" | "position_scheme";
type SortOrder = "asc" | "desc";

interface RankingTableProps {
  jobId: string;

  // Pre-fetched data (optional — if not provided, fetches internally)
  rankingData?: RankingItem[];

  // Pagination
  pageSize?: number;  // default: 20

  // Sorting
  defaultSortBy?: SortField;
  defaultSortOrder?: SortOrder;

  // Row expansion
  expandableScores?: boolean;  // default: true

  // Callbacks
  onConstructClick?: (construct: RankingItem) => void;  // navigate to structure page
  onSortChange?: (field: SortField, order: SortOrder) => void;
}

// Internal state
interface RankingTableState {
  currentPage: number;
  sortBy: SortField;
  sortOrder: SortOrder;
  expandedRows: Set<number>;  // rank numbers that are expanded

  // Loading
  isLoading: boolean;
  isFetchingMore: boolean;  // for infinite scroll or "load more"

  // Error
  error: ApiError | null;
}

// Row component props
interface RankingRowProps {
  item: RankingItem;
  isExpanded: boolean;
  onToggle: () => void;
  onViewStructure: () => void;
}
```

**Critical gaps in plan**:
- No pagination UI (page numbers? infinite scroll?)
- No sort direction indicators (arrows in column headers)
- No row expansion animation (expand/collapse)
- No "copy sequence" button functionality

### 3.3 `LogPanel` — SSE Streaming Not Fully Specified

**Required TypeScript**:

```typescript
// components/pipeline/LogPanel.tsx

interface LogEntry {
  timestamp: string;    // ISO8601
  round: string;        // e.g., "round01"
  level: "info" | "warning" | "error" | "debug";
  message: string;
}

interface LogPanelProps {
  jobId: string;

  // Auto-scroll behavior
  autoScroll?: boolean;  // default: true

  // Virtual scroll
  maxEntries?: number;   // default: 1000, older entries dropped

  // Filter
  roundFilter?: string;  // show only specific round
  levelFilter?: LogEntry["level"][];

  // Expand/collapse
  defaultExpanded?: boolean;  // default: false
  maxHeight?: string;    // default: "400px"
}

// SSE hook signature (from plan's useJobEvents — but has CRITICAL BUG)
interface UseJobEventsOptions {
  jobId: string;
  onEvent?: (event: JobEvent) => void;
  enabled?: boolean;  // don't connect if job is completed/failed
}

interface JobEvent {
  type: "round_started" | "round_completed" | "progress" | "error" | "job_completed" | "job_failed";
  round?: string;
  processed_count?: number;
  total_count?: number;
  message?: string;
  timestamp: string;
}
```

**CRITICAL BUG in plan's SSE implementation** (from 02-system-architecture.md § 六):

```python
# FROM PLAN (BUGGY — infinite loop, memory leak):
async def generate():
    while True:
        events = db.query(job_events).filter(job_id=job_id).all()  # ALWAYS RETURNS ALL EVENTS
        for event in events:  # DUPLICATES every iteration!
            yield f"data: {event.json()}\n\n"
        await asyncio.sleep(2)

# CORRECT implementation:
# Must track last_seen_event_id and only yield NEW events
async def generate():
    last_id = None
    while True:
        query = db.query(job_events).filter(job_id=job_id)
        if last_id:
            query = query.filter(id > last_id)
        events = query.order_by(job_events.timestamp).limit(100).all()

        for event in events:
            yield f"data: {event.json()}\n\n"
            last_id = event.id

        await asyncio.sleep(1)  # shorter interval for responsiveness
```

**LogPanel requirements**:
- Virtual scrolling (recharts VirtualizedList or react-window) for 1000+ lines
- Auto-scroll to bottom when new entries arrive
- "Pause" button to stop auto-scroll (user scrolling up)
- "Download logs" button (export as .txt)
- "Filter by round" dropdown
- Color coding by log level

---

## 4. Missing UI States — Critical Gaps

### 4.1 Loading States

**No loading state specifications anywhere in the plan**.

**Required specifications per component**:

```typescript
// Loading state types (standardize across app)

type LoadingVariant = "skeleton" | "spinner" | "progress";

interface SkeletonConfig {
  variant: "text" | "circular" | "rectangular";
  width?: string | number;
  height?: string | number;
  animation?: "pulse" | "wave" | "none";
}

// Per-component loading states (MISSING from plan):
// HomePage: Skeleton for ConfigPanelTierA/B/C
// JobsPage: Skeleton cards (3-6 cards) while loading job list
// JobDetailPage: Skeleton for FunnelVisualization, LogPanel collapsed, RankingTable empty
// JobDetailPage (running): Pulsing status badge, animated progress bar
```

**Specific skeletons needed**:
```typescript
// FunnelVisualization skeleton
const FunnelSkeleton = () => (
  <div className="space-y-2">
    {[100, 85, 60, 40, 30, 25].map((w, i) => (
      <Skeleton key={i} variant="rectangular" width={`${w}%`} height="40px" animation="wave" />
    ))}
    <p className="text-sm text-gray-500">Loading funnel data...</p>
  </div>
);

// RankingTable skeleton
const RankingTableSkeleton = ({ rows = 10 }: { rows?: number }) => (
  <TableBody>
    {Array.from({ length: rows }).map((_, i) => (
      <TableRow key={i}>
        <TableCell><Skeleton width="40px" /></TableCell>
        <TableCell><Skeleton width="200px" /></TableCell>
        <TableCell><Skeleton width="80px" /></TableCell>
        <TableCell><Skeleton width="80px" /></TableCell>
        <TableCell><Skeleton width="100px" /></TableCell>
      </TableRow>
    ))}
  </TableBody>
);
```

### 4.2 Error States

**No error state specifications**.

**Required error state design**:

```typescript
// Error state types
type ErrorContext = "page" | "component" | "inline";

interface ErrorState {
  variant: ErrorContext;

  // For "page" — full page error
  title?: string;        // e.g., "Something went wrong"
  message?: string;     // e.g., "Please try again later"
  actionLabel?: string;  // e.g., "Retry" | "Go Home"
  onAction?: () => void;

  // For "component" — inline in the component
  inlineMessage?: string;
  retryButton?: boolean;

  // Error details (for debugging/display)
  errorCode?: string;
  timestamp?: string;
}

// Per-component error states (MISSING from plan):
// - Network error (fetch failed) — retry button
// - 401 error — redirect to login (handled by interceptor, but UI feedback needed)
// - 403 error — toast notification "无权限"
// - 404 error — "Job not found" with back button
// - 422 error — inline field errors
// - 500 error — "Server error, please try again later"
// - Unknown error — generic error with report link (Phase 2)
```

**Implementation requirements**:
```typescript
// api/client.ts error classification must populate these:
export function handleApiError(error: AxiosError): ApiError {
  if (!error.response) {
    return { type: "NETWORK_ERROR", message: "Network connection failed", status_code: 0 };
  }

  switch (error.response.status) {
    case 401:
      return { type: "UNAUTHORIZED", message: "Session expired, please login again", status_code: 401 };
    case 403:
      return { type: "FORBIDDEN", message: "Access denied", status_code: 403 };
    case 404:
      return { type: "NOT_FOUND", message: "Resource not found", status_code: 404 };
    case 422:
      return {
        type: "VALIDATION_ERROR",
        message: "Validation failed",
        status_code: 422,
        details: error.response.data?.detail || {},
      };
    default:
      return { type: "SERVER_ERROR", message: "Server error occurred", status_code: error.response.status };
  }
}
```

### 4.3 Empty States

**No empty state specifications**.

**Required empty states**:

```typescript
// Empty state types
interface EmptyState {
  variant: "no-data" | "no-results" | "initial";  // no-data = haven't fetched yet, no-results = fetched but empty

  title: string;
  description?: string;

  // Optional action
  actionLabel?: string;
  onAction?: () => void;

  // Illustration (optional)
  icon?: string;  // emoji or SVG path
}

// Per-component empty states (MISSING from plan):
// JobsPage: "No jobs yet" — "Run your first job" button (navigate to home)
// JobsPage (filtered): "No jobs with status X" — clear filter button
// RankingTable: "No results yet" — "Job still running" or "No constructs passed filters"
// LogPanel: "No logs available" — (only show when job is completed and has no log file)
```

**Design recommendations**:
```typescript
// Empty state component
const EmptyState = ({ variant, title, description, actionLabel, onAction }: EmptyState) => (
  <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
    {variant === "no-results" && <SearchEmptyIcon />}
    {variant === "no-data" && <InboxIcon />}
    <h3 className="mt-4 text-lg font-medium text-gray-900">{title}</h3>
    {description && <p className="mt-2 text-sm text-gray-500">{description}</p>}
    {actionLabel && onAction && (
      <Button onClick={onAction} variant="primary" className="mt-4">
        {actionLabel}
      </Button>
    )}
  </div>
);

// Usage
{isEmpty && <EmptyState variant="no-results" title="No jobs found" description="Try a different filter" actionLabel="Clear filter" onAction={clearFilter} />}
```

### 4.4 Authentication Flow — Protected Routes Incomplete

**The plan shows `ProtectedRoute` in the router config but provides no implementation details**.

**Required ProtectedRoute implementation**:

```typescript
// components/layout/ProtectedRoute.tsx

interface ProtectedRouteProps {
  children: React.ReactNode;
  redirectTo?: string;  // default: "/login"
}

// Must check:
// 1. Auth store initialized (may be loading from localStorage)
// 2. Token exists
// 3. Token not expired (optional: validate with /auth/me)

// Edge cases:
// - App just loaded, authStore initializing (show full-page spinner, NOT redirect)
// - Token exists but expired (401 from /auth/me) → clear store, redirect to login
// - No token → redirect to login, preserve return URL
```

```typescript
// App.tsx — complete auth flow
function App() {
  const initializeAuth = useAuthStore((s) => s.initialize);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [isInitializing, setIsInitializing] = useState(true);

  useEffect(() => {
    const init = async () => {
      await initializeAuth();
      setIsInitializing(false);
    };
    init();
  }, []);

  if (isInitializing) {
    return <FullPageSpinner message="Loading..." />;
  }

  return (
    <QueryClientProvider>
      <Router>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route
            path="/jobs"
            element={
              <ProtectedRoute>
                <JobsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/jobs/:jobId"
            element={
              <ProtectedRoute>
                <JobDetailPage />
              </ProtectedRoute>
            }
          />
          <Route path="/" element={<HomePage />} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
```

**Critical issue**: The plan's router shows:
```tsx
<Route path="jobs" element={<ProtectedRoute />}>
```
But `ProtectedRoute` must wrap the **child** routes with an `Outlet`, not replace them:
```tsx
<Route path="jobs" element={<ProtectedRoute />}>
  <Route index element={<JobsPage />} />
  <Route path=":jobId" element={<JobDetailPage />} />
</Route>
```

---

## 5. Implementation Details — Component Specifications

### 5.1 `ConfigPanelTierA` — No Fields Specified

**This is the most critical gap in the entire plan — Tier A has literally zero field definitions**.

**Required interface**:

```typescript
// components/pipeline/ConfigPanelTierA.tsx

interface ConfigPanelTierAProps {
  value: FunctionType;
  onChange: (ft: FunctionType) => void;
  disabled?: boolean;
}

// Function type options (MISSING from plan)
type FunctionType = "antioxidant" | "antimicrobial" | "antiglycation";

const FUNCTION_TYPE_OPTIONS: { value: FunctionType; label: string; description: string }[] = [
  {
    value: "antioxidant",
    label: "抗氧化活性",
    description: "预测肽链的抗氧化能力，适用于延缓衰老相关应用",
  },
  {
    value: "antimicrobial",
    label: "抗菌活性",
    description: "预测肽链的抗菌能力，适用于抑菌剂开发",
  },
  {
    value: "antiglycation",
    label: "抗糖化活性",
    description: "预测肽链的抗糖化能力，适用于抗糖化护肤产品",
  },
];
```

**Required UI**:
- Radio group with visual cards (not just radio buttons)
- Each option shows: name, one-line description
- Selected state: border highlight + checkmark
- Hover state: subtle background change

### 5.2 `ConfigPanelTierB` — Incomplete

**Only mentions "TopN/Linker/3D工具" — no actual fields**.

**Required interface**:

```typescript
// components/pipeline/ConfigPanelTierB.tsx

interface ConfigPanelTierBProps {
  // Inherited from Tier A (from configStore)
  function_type: FunctionType;

  // Tier B fields
  top_n: number;
  linkers: string[];
  structure_tool: StructureTool;

  onTopNChange: (n: number) => void;
  onLinkersChange: (linkers: string[]) => void;
  onStructureToolChange: (tool: StructureTool) => void;

  disabled?: boolean;
}

// TopN slider
interface TopNConfig {
  min: 1000;
  max: 50000;
  step: 1000;
  default: 10000;
  marks?: { value: number; label: string }[];
}

// Linker options (MISSING from plan — what linkers are available?)
const LINKER_OPTIONS: { value: string; label: string }[] = [
  { value: "Flex_GGGGSx2", label: "Flex_GGGGSx2 (柔性连接)" },
  { value: "Flex_GGGGSx3", label: "Flex_GGGGSx3 (柔性连接)" },
  { value: "Rigid_GGGS", label: "Rigid_GGGS (刚性连接)" },
  { value: "Rigid_GPGGS", label: "Rigid_GPGGS (刚性连接)" },
  { value: "Rigid_PPP", label: "Rigid_PPP (极刚性)" },
];

// 3D structure tool options
const STRUCTURE_TOOL_OPTIONS: { value: StructureTool; label: string }[] = [
  { value: "omegafold", label: "OmegaFold" },
  { value: "esmfold", label: "ESMFold" },
];
```

**Required UI components**:
- TopN: Slider with numeric input, shows current value
- Linkers: Multi-select checkbox group
- StructureTool: Radio group (2 options)

### 5.3 `ConfigPanelTierC` — Completely Missing

**The plan says "Tier C: 完整权重阈值" but provides no field definitions**.

**Required interface**:

```typescript
// components/pipeline/ConfigPanelTierC.tsx

interface ConfigPanelTierCProps {
  // Inherited from Tier B
  function_type: FunctionType;
  top_n: number;
  linkers: string[];
  structure_tool: StructureTool;

  // Tier C specific
  weights: WeightsConfig;
  thresholds: ThresholdsConfig;

  onWeightsChange: (weights: WeightsConfig) => void;
  onThresholdsChange: (thresholds: ThresholdsConfig) => void;

  disabled?: boolean;
}

interface WeightsConfig {
  // All services get configurable weight
  anoxpepred: number;   // default: 1.0, min: 0, max: 10, step: 0.1
  bepipred3: number;
  sodope: number;
  temstapro: number;
  plddt: number;
  sasa: number;
  agrescan3d: number;
}

interface ThresholdsConfig {
  // Per-service thresholds
  plddt_min?: number;       // min pLDDT score (default: 85)
  agrescan3d_max?: number;   // max Aggrescan3D score (default: 10)
  sasa_max?: number;         // max SASA (default: 200)
  anoxpepred_min?: number;   // min AnOxPePred score (default: 0.5)
  // ... more thresholds
}

// Note: Which services have thresholds vs weights depends on function_type
// antioxidant: weights for AnOxPePred, pLDDT, SASA, Aggrescan3D
// antimicrobial: weights for BepiPred3, SoDoPE, pLDDT
// antiglycation: weights for TemStaPro, pLDDT, SASA
```

**Required UI**:
- Two-column layout: weights on left, thresholds on right
- Each weight/threshold: slider + numeric input
- "Reset to defaults" button per section
- Visual indicator of which services are relevant for current function_type

### 5.4 `PipelineRunnerCard` — Missing Implementation Details

**The plan shows a diagram but no props/state**.

```typescript
// components/pipeline/PipelineRunnerCard.tsx

interface PipelineRunnerCardProps {
  // from configStore
  tier: TierLevel;
  functionType: FunctionType;
  // ... other fields

  // Callbacks
  onRun: (jobConfig: JobConfig) => Promise<Job>;  // POST /api/jobs
  onSavePreset: (name: string) => Promise<void>;  // POST /api/configs

  // Loading state
  isRunning?: boolean;  // disable all inputs while job is being created

  // Navigation after run
  onJobCreated: (job: Job) => void;  // navigate to /jobs/:jobId
}

interface PipelineRunnerCardState {
  activeTab: "A" | "B" | "C";  // which tier panel is shown
  isCreatingJob: boolean;
  createJobError: ApiError | null;

  // Preset loading
  presetDropdownOpen: boolean;
  selectedPresetId: string | null;
}
```

---

## 6. Tier A/B/C Config Panel — Complete Field Definitions

### Summary Table

| Field | Tier | Type | Default | Validation |
|-------|------|------|---------|-----------|
| `function_type` | A | `antioxidant \| antimicrobial \| antiglycation` | required | Must select one |
| `top_n` | B | `number` | `10000` | 1000–50000 |
| `linkers` | B | `string[]` | `["Flex_GGGGSx2"]` | At least 1 |
| `structure_tool` | B | `omegafold \| esmfold` | `omegafold` | Must select one |
| `weights.anoxpepred` | C | `number` | `1.0` | 0–10, step 0.1 |
| `weights.bepipred3` | C | `number` | `1.0` | 0–10, step 0.1 |
| `weights.sodope` | C | `number` | `1.0` | 0–10, step 0.1 |
| `weights.temstapro` | C | `number` | `1.0` | 0–10, step 0.1 |
| `weights.plddt` | C | `number` | `1.0` | 0–10, step 0.1 |
| `weights.sasa` | C | `number` | `1.0` | 0–10, step 0.1 |
| `weights.agrescan3d` | C | `number` | `1.0` | 0–10, step 0.1 |
| `thresholds.plddt_min` | C | `number` | `85` | 0–100 |
| `thresholds.agrescan3d_max` | C | `number` | `10` | 0–100 |
| `thresholds.sasa_max` | C | `number` | `200` | 0–500 |
| `thresholds.anoxpepred_min` | C | `number` | `0.5` | 0–1 |

### TypeScript Full Config Type

```typescript
// types/config.ts

export type FunctionType = "antioxidant" | "antimicrobial" | "antiglycation";
export type TierLevel = "A" | "B" | "C";
export type StructureTool = "omegafold" | "esmfold";

export interface TierCConfig {
  weights: {
    anoxpepred: number;
    bepipred3: number;
    sodope: number;
    temstapro: number;
    plddt: number;
    sasa: number;
    agrescan3d: number;
  };
  thresholds: {
    plddt_min: number;
    agrescan3d_max: number;
    sasa_max: number;
    anoxpepred_min?: number;
  };
}

export interface TierBConfig extends TierCConfig {
  top_n: number;
  linkers: string[];
  structure_tool: StructureTool;
}

export interface TierAConfig extends TierBConfig {
  function_type: FunctionType;
}

// Full job config for API
export interface JobConfig extends TierAConfig {
  tier: TierLevel;  // convenience field
}

// Preset config for saving
export interface PresetConfig {
  id: string;
  name: string;
  is_public: boolean;
  config: TierCConfig;  // only Tier C fields stored (rest derived from tier)
  created_at: string;
}
```

---

## 7. Critical Issues Summary

### High Priority (Must Fix Before Phase 1)

1. **SSE Implementation Bug** (02-system-architecture.md § 六): Infinite loop returns all events every iteration. Must track `last_id` and only return new events.

2. **Missing Tier A/B/C Field Definitions**: Cannot implement ConfigPanel without this. This is the single largest gap.

3. **No Loading/Error/Empty States**: Every component needs these specified before implementation.

4. **Missing `useJobEvents` Implementation**: The plan shows the hook signature but has a buggy SSE pattern.

5. **ProtectedRoute Incomplete**: Shows in router but no `Outlet` implementation.

6. **API Hook Return Types Missing**: All hooks need exact TypeScript signatures with error types.

### Medium Priority (Should Fix Before Phase 1)

7. **Auth Token Refresh**: Plan says "7 days" but no refresh mechanism specified.

8. **Zustand Persistence Details**: Which fields persisted? Only token/user, not isLoading/error.

9. **RankingTable Sorting/Pagination**: Full specification needed before implementation.

10. **LogPanel Virtual Scrolling**: Must specify max entries and scroll library.

### Lower Priority (Phase 1 Can Address)

11. **NGL Viewer Wrapper Component**: Partial spec provided, needs refinement.

12. **ComparePage (Phase 2)**: Not required for MVP.

13. **Config Preset Sharing (Phase 2)**: Not required for MVP.

---

## 8. Recommendations

### Immediate Actions Before Phase 1

1. **Create `types/config.ts`** with complete Tier A/B/C field definitions
2. **Fix SSE implementation** in backend plan
3. **Define unified `ApiError` type** and error handling in axios interceptor
4. **Write `ProtectedRoute` component** with proper `Outlet` usage
5. **Create loading/error/empty state components** as reusable UI components
6. **Define all React Query hook signatures** in `api/auth.ts`, `api/jobs.ts`, `api/results.ts`

### Suggested Project Structure Addition

```typescript
// src/components/ui/
LoadingStates.tsx      // SkeletonCard, Spinner, ProgressBar
ErrorStates.tsx        // ErrorBanner, InlineError, PageError
EmptyStates.tsx       // EmptyState component with variants

// src/types/
config.ts              // JobConfig, TierAConfig, TierBConfig, TierCConfig
api.ts                 // ApiError, ApiResponse base types
```

### Phase 1 Scope Adjustment

Given the critical gaps, recommend adding **2-3 days** for specification work before coding begins on:
- Config panel field definitions (Tier A/B/C)
- API hook type definitions
- Loading/error state component library

---

## Appendix: Missing TypeScript Interfaces Summary

The following interfaces are referenced but NOT defined in the plan documents:

| File | Missing Interface |
|------|-------------------|
| `api/auth.ts` | `UseLoginOptions`, `UseRegisterOptions`, `AuthError` |
| `api/jobs.ts` | `CreateJobRequest`, `JobConfig`, `JobListResponse` |
| `api/results.ts` | `RankingItem`, `RankingResponse`, `FunnelStage`, `FunnelResponse`, `ConstructDetail` |
| `store/authStore.ts` | `initialize`, `refreshToken`, `isAuthenticated`, persistence config |
| `store/configStore.ts` | Complete Tier A/B/C fields, `toJobConfig`, `loadPreset`, `savePreset` |
| `components/pipeline/FunnelVisualization.tsx` | `FunnelLoadingState`, `FunnelEmptyState`, `onStageClick` |
| `components/pipeline/RankingTable.tsx` | `SortField`, `SortOrder`, `RankingRowProps`, pagination UI |
| `components/pipeline/LogPanel.tsx` | `LogEntry`, `LogLevel`, virtual scroll spec |
| `components/pipeline/ConfigPanelTierA.tsx` | All fields missing |
| `components/pipeline/ConfigPanelTierB.tsx` | All fields missing |
| `components/pipeline/ConfigPanelTierC.tsx` | All fields missing |
| `components/layout/ProtectedRoute.tsx` | Not implemented |