# iGEM 数据刷新 — 工作流 + 踩坑清单

> 最后更新: 2026-07-17
> 状态: 10/10 源扫描完成, 20.25M 行 peptides 入库

## 数据现状

```
public_databases/        ← 老 627GB 原状不动(只读基线)
public_databases_v2/     ← 新 4 真活源 + 1 frozen 引用
  ├── manifest/MANIFEST.json
  ├── pdb/pdb_seqres.txt.gz
  ├── uniprot_sprot/uniprot_sprot.fasta.gz
  ├── uniprot_trembl/uniprot_trembl.fasta.gz
  ├── uniref90/uniref90.fasta.gz
  ├── mgy_2024/           ← URL TBD, 等 EBI 网页端
  └── bfd/                ← frozen, 引用 public_databases/bfd-*.fasta
```

PG: `igem_peptides` @ `127.0.0.1:5432`,8 GB DB,20,248,885 行。

## 必读

**开始任何 scan / DB 操作前,先读 `src/setup/lib/pitfalls.py`**(13 条踩坑清单 + 机器可校验断言)。

**这份 README 是冗余备份**,真正生效的是代码:
- `lib/pitfalls.py:validate_environment()` — 启动期必跑
- `lib/pitfalls.py:assert_safe_scan(path, nbytes, other_running_scans)` — scan 前必跑
- `lib/pitfalls.py:get_running_scan_count()` — 同盘并发检测

## 重新跑全扫描

```bash
# 排好队的串行脚本(不要手动并发,会触发 pitfall #7)
nohup bash -c '
  for src in pdb uniprot_sprot uniref90 uniprot_trembl; do
    python3 -u src/setup/scan_protein.py --source $src
  done
  for src in legacy_pdb_2022 legacy_rfam_2022 legacy_bfd_2022 \
             legacy_uniprot_all_2021 legacy_uniref90_2022 legacy_mgy_2022; do
    python3 -u src/setup/scan_protein.py --legacy $src
  done
' > /tmp/scan_all.log 2>&1 &

# 看进度
tail -f /tmp/scan_all.log

# 跑完看状态
psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/05_self_check.sql
```

## 关键 pitfall 速查(只列最致命的几条)

| # | 坑 | 后果 | 规避 |
|---|---|---|---|
| 1 | `gzip.open` 扫大 .gz | 慢 30 倍, 30GB 要几小时 | 用 `subprocess.Popen(["zcat"])` |
| 2 | `pyfastx(build_index=True)` | 首次迭代卡 1+ 小时建 `.fxi` | 不用 pyfastx, 走 zcat |
| 3 | Python-level byte iteration | 1MB seq 校验要 30+ ms | `bytes.upper()` C-level |
| 5 | 文件大小估算 | 估算 150GB 实际 38GB | 必须 sha256 + bytes |
| 7 | 同盘并发 scan | 速度掉到 1/3 | 串行, `get_running_scan_count()` 拦 |
| 8 | `ON CONFLICT DO NOTHING` | 重跑不报错只不增 rows | UNIQUE(seq,source,version) |
| 9 | `source_version` 格式 | 非 YYYY-MM-DD 存 NULL | 见 pitfalls.py:9 |
| 13 | 路径 | Data 盘 vs home 盘 | 用 `pitfalls.PUBLIC_DATA_ROOT` 常量 |

## 增源/换源流程

1. **URL 必须手动 curl HEAD 验证 200**,agent 的 fetch tool 拒绝 EBI/NCBI
2. 改 `src/setup/lib/manifest.py` 或 MANIFEST.json,加新 source
3. 跨机下载用 aria2c(单源 30GB ~4h @ 1-4 MiB/s),下完 `sha256sum` 跟 MANIFEST 对
4. 移动到 `public_databases_v2/<source_id>/`
5. `python3 src/setup/scan_protein.py --source <id>`
6. `psql -f src/setup/sql/05_self_check.sql`

## DB 操作约定

- **永远不要 DELETE/全表 UPDATE**,WHERE 必须带 source_id
- **新加 source_id 必须 ≤32 字符**(VARCHAR(32) 限制)
- **跨源对比**用 `WHERE source IN (...)`,不要写 JOIN 后再 filter
- **大查询 LIMIT + OFFSET** 必须有,`source='legacy_mgy_2022'` 1900 万行,全表扫会卡死

## 文件索引

| 文件 | 作用 |
|---|---|
| `src/setup/scan_protein.py` | 主扫描器(zcat + 手动 FASTA parser + batch flush) |
| `src/setup/scan_all.sh` | 全源包装器 |
| `src/setup/lib/pitfalls.py` | **踩坑清单 + 运行时断言** |
| `src/setup/lib/manifest.py` | MANIFEST.json 读写 |
| `src/setup/lib/legacy_sources.py` | 老源路径表 |
| `src/setup/sql/04_create_tables_v2.sql` | DDL |
| `src/setup/sql/05_self_check.sql` | 健康检查 |
| `src/setup/sql/06_baseline_compare.sql` | 老基线对比 |

## 已知遗留问题

- **MGnify 2024 URL 未落实**(mgy_2024.status='TBD')
- **legacy_rnacentral_2023 / legacy_nt_rna_2023 未扫**(RNA 序列,无 ≤30aa 肽段,v2 spec 不做 6-frame 翻译)
- **PG 没配自动备份**(DB 8GB 全损风险,建议加 pg_dump cron)