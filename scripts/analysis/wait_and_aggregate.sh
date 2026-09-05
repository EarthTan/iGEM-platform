#!/usr/bin/env bash
# wait_and_aggregate.sh
# 等 mhcflurry multi-allele 跑完后自动调聚合分析脚本
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 找最新的 multi-allele log
LOG=$(ls -t logs/enrich_run/mhcflurry_multi_*.log | head -1)
echo "watching: $LOG"

# 找进程
PID=$(pgrep -f 'run_multi_allele.sh' || true)
echo "pid: $PID"

# 等进程退出
while kill -0 "$PID" 2>/dev/null; do
    sleep 15
    if=$(grep -c "final tool" "$LOG" 2>/dev/null || echo 0)
    of=$(grep -c "allele loop" "$LOG" 2>/dev/null || echo 0)
    echo "[$(date +%H:%M:%S)] alleles done: $if / started: $of"
done
echo "[$(date +%H:%M:%S)] process exited."

# 调聚合
/tmp/plot_venv/bin/python scripts/analysis/plot_mhcflurry_multi.py 2>&1 | tail -20