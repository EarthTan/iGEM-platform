#!/usr/bin/env bash
# progress_watch.sh — 每 60 秒打 1 行到 stdout,写到 /tmp/enrich_progress.txt
set -uo pipefail
OUT=/tmp/enrich_progress.txt
LOG_DIR="/home/lenovo/Projects/iGEM-platform/logs/enrich_run"

# wait until master log exists
for i in $(seq 1 30); do
    LATEST=$(ls -t "$LOG_DIR"/master_*.log 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then break; fi
    sleep 2
done
if [ -z "$LATEST" ]; then
    echo "[progress_watch] no master log found, exit"
    exit 1
fi

# header
{
    echo "=== iGEM enrich progress watch ==="
    echo "started: $(date)"
    echo "master_log: $LATEST"
} > "$OUT"

while true; do
    TOOL=$(ps -ef | grep -oE 'enrich\.py --tool \w+' | awk '{print $NF}' | sort -u)
    LAST=$(tail -1 "$LATEST" 2>/dev/null)
    COVERAGE=$(PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -At -c "
        SELECT string_agg(tool || '=' || done_count || '/' || eligible_count, E'\n')
          FROM v_peptide_enrichment_coverage
         WHERE done_count > 0;
    " 2>/dev/null)
    {
        echo "--- [$(date '+%F %T')] ---"
        echo "tool: $TOOL"
        echo "last: $LAST"
        echo "coverage:"
        echo "$COVERAGE" | sed 's/^/  /'
    } >> "$OUT"
    sleep 60
done
