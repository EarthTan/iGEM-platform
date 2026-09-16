#!/usr/bin/env bash
# run-amp-esm.sh — 启动 AMP-ESM 微服务并跑全量 enrichment
#
# 关键设计:
#   - 服务用 nohup ... & disown 启动,即使外层 shell 退出也继续
#   - 用 /health 探活 90s(冷启 v0.1.0 TF/Keras GPU 需 ~30s,v0.2.0 ESM-2 需 ~90s)
#   - 默认 v0.1.0 TF/Keras(GPU,batch=1000,实测 ~340 seq/s)
#   - 20M 肽跑完估计 ~16 小时(v0.1.0 GPU)/ ~33 天(v0.2.0)
#   - API 硬限:每请求最多 1000 条(FastaToolService BatchPredictRequest.max_length=1000)
#   - 断点续:DB upsert 即 commit,checkpoint 双保险(进程被杀重启后自动从 last id 继续)
#
# 用法:
#   bash src/setup/enrich/run-amp-esm.sh
#
# 覆盖:
#   AMP_ESM_VERSION=v0.1.0   → TF/Keras 5-fold BiLSTM(GPU 默认,~340 seq/s)
#   AMP_ESM_VERSION=v0.2.0   → ESM-2 t33 650M 5-fold (旧,~7 seq/s)
#   AMP_ESM_MODE=imbalanced  → imbalanced weights(默认)
#   AMP_ESM_MODE=balanced    → balanced weights
#   BATCH=1000               → worker 每批条数(v0.1.0 GPU,API 上限)

set -uo pipefail

ROOT="/home/lenovo/Projects/iGEM-platform"
SILK="/home/lenovo/Projects/iGEM-silk"
TOOL_DIR="$SILK/tools/AMP-ESM"
LOG_DIR="$ROOT/logs/enrich_run"
SVC_LOG="/tmp/svc_amp_esm.log"
BATCH="${BATCH:-1000}"

mkdir -p "$LOG_DIR"

AMP_ESM_VERSION="${AMP_ESM_VERSION:-v0.1.0}"
AMP_ESM_MODE="${AMP_ESM_MODE:-imbalanced}"
PORT="${PORT:-8011}"

echo "[$(date +%FT%T)] ============================================"
echo "[$(date +%FT%T)] AMP-ESM enrichment launcher"
echo "[$(date +%FT%T)] version=$AMP_ESM_VERSION mode=$AMP_ESM_MODE port=$PORT batch=$BATCH"
echo "[$(date +%FT%T)] ============================================"

# ── 0. 确认 8011 没被占用,如果是就 kill(避免重启时 port 冲突)
if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "[$(date +%T)] WARN: existing amp-esm service on :$PORT, killing"
    pids=$(ss -lntp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $0}' \
           | grep -oP 'pid=\K[0-9]+' | sort -u)
    for pid in $pids; do
        kill "$pid" 2>/dev/null || true
    done
    sleep 3
fi

# ── 1. 启动 micro-service (nohup + disown,父 shell 退出不影响)
echo "[$(date +%T)] starting AMP-ESM service → $SVC_LOG"
cd "$TOOL_DIR"
if [ ! -d .venv ]; then
    uv sync --frozen
fi
AMP_ESM_VERSION="$AMP_ESM_VERSION" AMP_ESM_MODE="$AMP_ESM_MODE" PORT="$PORT" \
    TF_CPP_MIN_LOG_LEVEL=2 \
    nohup .venv/bin/python service.py > "$SVC_LOG" 2>&1 &
SVC_PID=$!
disown $SVC_PID 2>/dev/null || true
echo "[$(date +%T)] service pid=$SVC_PID (disowned)"

# ── 2. 等 /health 通过
echo "[$(date +%T)] waiting for /health ..."
HEALTHY=0
for i in $(seq 1 180); do
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        HEALTHY=1
        echo "[$(date +%T)] service healthy after ${i}s"
        break
    fi
    if ! kill -0 $SVC_PID 2>/dev/null; then
        echo "[$(date +%T)] service DIED, log tail:"
        tail -40 "$SVC_LOG" | sed 's/^/    /'
        exit 1
    fi
    sleep 1
done
if [[ $HEALTHY -ne 1 ]]; then
    echo "[$(date +%T)] FAIL: service not healthy in 180s"
    tail -20 "$SVC_LOG"
    exit 2
fi

# ── 3. 启动 enrichment worker(同样 nohup + disown)
echo "[$(date +%T)] starting enrich worker → $LOG_DIR/amp-esm.log"
cd "$ROOT"
nohup python3 -u src/setup/enrich/enrich.py \
    --tool amp-esm --batch "$BATCH" --concurrent 1 \
    > "$LOG_DIR/amp-esm.log" 2>&1 &
WORKER_PID=$!
disown $WORKER_PID 2>/dev/null || true
echo "[$(date +%T)] worker pid=$WORKER_PID (disowned)"

sleep 3
if ! kill -0 $WORKER_PID 2>/dev/null; then
    echo "[$(date +%T)] worker DIED, log tail:"
    tail -40 "$LOG_DIR/amp-esm.log" | sed 's/^/    /'
    exit 3
fi

echo "[$(date +%T)] ============================================"
echo "[$(date +%T)] BOTH started."
echo "[$(date +%T)]   service log: $SVC_LOG"
echo "[$(date +%T)]   worker  log: $LOG_DIR/amp-esm.log"
echo "[$(date +%T)]   monitor:    tail -f $LOG_DIR/amp-esm.log"
echo "[$(date +%T)]   coverage:   PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c 'SELECT * FROM v_peptide_enrichment_coverage WHERE tool=\$\$amp-esm\$\$;'"
echo "[$(date +%T)]   to stop:    kill $WORKER_PID $SVC_PID"
echo "[$(date +%T)]   resume:     bash src/setup/enrich/run-amp-esm.sh   # 自动从 last id 继续"
echo "[$(date +%T)] ============================================"
