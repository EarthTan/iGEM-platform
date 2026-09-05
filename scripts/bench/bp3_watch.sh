#!/usr/bin/env bash
# bp3_watch.sh — 实时查看 BepiPred-3.0 runner 进度
# 用法: watch -n 30 ./scripts/bench/bp3_watch.sh
#       或: ./scripts/bench/bp3_watch.sh   (一次性)
set -e
DSN="host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026"
export PGPASSWORD=igem_local_2026

# 找最新进度行
PROGRESS=$(grep '\[progress\]' /home/lenovo/Projects/iGEM-platform/logs/bp3_runner.log 2>/dev/null | tail -1)
ERR_COUNT=$(grep -c 'ERROR' /home/lenovo/Projects/iGEM-platform/logs/bp3_runner.log 2>/dev/null || echo 0)

# DB 实时统计
TOTAL=$(psql -h 127.0.0.1 -U igem -d igem_peptides -tAc \
    "SELECT count(*) FROM peptides WHERE length BETWEEN 1 AND 30" 2>/dev/null)
DONE=$(psql -h 127.0.0.1 -U igem -d igem_peptides -tAc \
    "SELECT count(*) FROM peptide_enrichment WHERE tool='bepipred3'" 2>/dev/null)
LAST_ID=$(psql -h 127.0.0.1 -U igem -d igem_peptides -tAc \
    "SELECT max(peptide_id) FROM peptide_enrichment WHERE tool='bepipred3'" 2>/dev/null)

# 进程状态
PID=$(pgrep -f 'bp3_runner.py' | head -1)
if [ -n "$PID" ]; then
    PROC=$(ps -p $PID -o etime= 2>/dev/null | tr -d ' ')
    GPU=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | head -1)
    echo "[$(date '+%H:%M:%S')] BepiPred-3.0 runner | uptime=$PROC | GPU $GPU"
else
    echo "[$(date '+%H:%M:%S')] BepiPred-3.0 runner | NOT RUNNING"
fi

# 进度条
if [ -n "$TOTAL" ] && [ "$TOTAL" -gt 0 ] 2>/dev/null; then
    PCT=$(python3 -c "print(f'{100.0*$DONE/$TOTAL:.3f}')" 2>/dev/null)
    REMAIN=$((TOTAL - DONE))
    BAR=$(python3 -c "
bar_len = 40
filled = int($PCT / 100 * bar_len)
print('[' + '█' * filled + '░' * (bar_len - filled) + ']')" 2>/dev/null)
    echo "$BAR $DONE / $TOTAL ($PCT%)  remaining=$REMAIN  last_id=$LAST_ID"
fi

# 最近进度
if [ -n "$PROGRESS" ]; then
    echo "  └─ $PROGRESS"
fi