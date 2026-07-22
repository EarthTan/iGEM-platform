#!/usr/bin/env bash
# start_services.sh — 起 9 个 iGEM-silk FASTA 微服务(nohup + health wait)
# 串行启动避免 uv sync 抢带宽;GPU 服务顺序错开

set -uo pipefail
SILK="/home/lenovo/Projects/iGEM-silk"
LOG_DIR="/tmp/svc_enrich_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"
echo "logs: $LOG_DIR"

# 顺序:fast 类先起(快速验证管线),再起 ESM 类
ORDER=(
    "Tipred:8007"
    "algpred2:8008"
    "SoDoPE_paper_2020:8012"
    "AnOxPePred:8001"
    "HemoPI2:8004"
    "pLM4CPPs:8006"
    "MHCflurry:8005"
    "TemStaPro:8010"
    "ToxinPred3:8003"
)

for spec in "${ORDER[@]}"; do
    tool="${spec%%:*}"
    port="${spec##*:}"
    log="$LOG_DIR/${tool}.log"
    echo "[$(date +%T)] starting $tool (port=$port)"
    cd "$SILK/tools/$tool"
    if [ ! -d .venv ]; then
        uv sync --frozen > "$log" 2>&1 || { echo "  uv sync FAIL $tool"; continue; }
    fi
    nohup .venv/bin/python service.py > "$log" 2>&1 &
    pid=$!
    # 健康探测 60s
    for i in $(seq 1 60); do
        sleep 1
        if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
            echo "  $tool ready after ${i}s (pid=$pid)"
            break
        fi
        if ! kill -0 $pid 2>/dev/null; then
            echo "  $tool DIED, log:"
            tail -20 "$log" | sed 's/^/    /'
            break
        fi
    done
    # 如果 60s 没 ready,标记失败但继续
    if ! curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
        echo "  WARN: $tool not healthy in 60s, log tail:"
        tail -10 "$log" | sed 's/^/    /'
    fi
done

echo "[$(date +%T)] done. services log dir: $LOG_DIR"
echo "[$(date +%T)] listening ports:"
ss -lntp 2>/dev/null | grep -E ":80(01|02|03|04|05|06|07|08|09|10|12)\b" | head
