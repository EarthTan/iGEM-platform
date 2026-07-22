# enrich/ — iGEM-peptide enrichment pipeline

用 iGEM-silk 下 10 个 FASTA 微服务(3D 类跳过)对 `peptides` 表全部 ≤30aa 序列打分,结果写到 `peptide_enrichment` 表。

## 快速开始

### 1. 起微服务

```bash
# 先进入 iGEM-silk repo(假设 ~/Projects/iGEM-silk)
cd /home/lenovo/Projects/iGEM-silk
for t in sodope tipred algpred2 anoxpepred hemopi2 plm4cpps temstapro mhcflurry; do
    if [ ! -d "tools/$t/.venv" ]; then
        (cd tools/$t && uv sync --frozen)
    fi
    (cd tools/$t && nohup .venv/bin/python service.py > /tmp/svc_$t.log 2>&1 &)
done
# ToxinPred3 在最后慢慢起
(cd tools/ToxinPred3 && uv sync --frozen && nohup .venv/bin/python service.py > /tmp/svc_toxinpred3.log 2>&1 &)
```

### 2. 端到端冒烟(用最便宜的 SoDoPE 跑 200 条)

```bash
cd /home/lenovo/Projects/iGEM-platform
python3 -u src/setup/enrich/enrich.py --tool sodope --limit 200 --batch 100
```

期望看到:
- "[check service ready]" 没有 fail
- "batch done ... rate=... seq/s"
- "LIMIT hit, stop"
- "final total=200 ..."

### 3. 全量跑(7.4 天)

```bash
cd /home/lenovo/Projects/iGEM-platform
nohup bash src/setup/enrich/run.sh > logs/enrich_run/master.log 2>&1 &
```

### 4. 观测覆盖度

```bash
python3 -u src/setup/enrich/enrich.py --tool sodope --check-coverage
# 或 psql
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides \
    -c "SELECT * FROM v_peptide_enrichment_coverage;"
```

## 设计要点

- **不污染原表**:`peptides` / `peptide_metadata` 只读,结果写 `peptide_enrichment`。
- **断点续**:DB upsert 即 commit,checkpoint 文件双保险。重启自动从 last id 继续。
- **9 个工具串行**:不开并发(GPU 单 batch 已经吃满算力)。
- **predict_batch 走 baseline 模板**:不打算 fork ToxinPred3(用户决定暂不优化)。

## 文件

- `enrich.py` — worker 入口
- `lib/db.py` — psycopg fetch_remaining / upsert_results / coverage
- `lib/clients.py` — 10 个 httpx client,统一 .score() 接口
- `lib/dispatch.py` — tool → client 路由
- `lib/resume.py` — checkpoint
- `run.sh` — 9 工具串行调度
- `logs/checkpoints/` — {tool}.json
- `logs/enrich_run/` — master + per-tool 日志

## 重启 / 中断恢复

- kill 后重启:`bash run.sh` 自动跑下一工具
- 同一工具重启:worker 启动读 `logs/checkpoints/{tool}.json` 从 `last_peptide_id+1` 继续
- 想从 0 重跑:`rm logs/checkpoints/{tool}.json` 后启动

## 失败处理

| 现象 | 处置 |
|---|---|
| 单 batch 5xx | 记到 logs/errors/{tool}/,继续下一 batch |
| 服务挂 | 检查 /tmp/svc_{tool}.log,起好后 worker 自动恢复 |
| 整台死机 | 重启后 `bash run.sh`,自动续 |
| ToxinPred3 OOM(基本不会,12.94/s 已稳) | 调小 batch=50 重启 |
