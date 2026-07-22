# iGEM 肽段数据库 — 设计 Spec

**日期：** 2026-07-10
**作者：** Zed (Brainstorming 流程产出)
**目标项目：** `/home/lenovo/Projects/iGEM-platform`

---

## 1. 目标

从 `/home/lenovo/public_databases` 中的 8 个公开数据库（5 蛋白 + 3 RNA）全量扫描，
**仅保留原始 FASTA 整条记录长度在 1–30 个氨基酸之间**的肽段（不枚举子片段），
清洗为严格 20 标准字母，存入本地 PostgreSQL 数据库，为后续 iGEM 漏斗筛选
（毒性 / 溶解度 / 表达量 / 活性等）建立底层数据表。

## 2. 范围

### 2.1 包含的数据源

| 文件 | 类型 | 处理方式 |
|---|---|---|
| `uniprot_all_2021_04.fa` | 蛋白 | 直接读，筛选 ≤30 aa |
| `uniref90_2022_05.fa` | 蛋白 (90% 聚类) | 直接读，筛选 ≤30 aa |
| `bfd-first_non_consensus_sequences.fasta` | 蛋白 (宏基因组) | 直接读，筛选 ≤30 aa |
| `mgy_clusters_2022_05.fa` | 蛋白 (MGnify 宏基因组) | 直接读，筛选 ≤30 aa |
| `pdb_seqres_2022_09_28.fasta` | 蛋白 (PDB 结构) | 直接读，提取 `pdb_id`/`pdb_chain` |
| `rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta` | RNA | 6-frame 翻译，筛选 ≤30 aa |
| `rnacentral_active_seq_id_90_cov_80_linclust.fasta` | RNA | 6-frame 翻译，筛选 ≤30 aa |
| `nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta` | RNA | 6-frame 翻译，筛选 ≤30 aa |

### 2.2 不包含

- ❌ `mmcif_files/` 目录（234 GB，序列层面已被 `pdb_seqres` 覆盖；解析慢；按需后续 JOIN）
- ❌ 子片段枚举（只存原始 ≤30 整条记录）
- ❌ 氨基酸频率列（按需 SQL 实时计算）
- ❌ GPU 加速（IO 瓶颈，CPU 字符串切分足够快）

## 3. 数据清洗规则

### 3.1 字符清洗（严格 20 字母）

仅保留以下 20 种标准氨基酸：
`ACDEFGHIKLMNPQRSTVWY`

**任一字符不在该集合内，整条记录丢弃。**

被丢弃的字符包括但不限于：
- `B` (N/D 歧义)
- `X` (未知)
- `Z` (Q/E 歧义)
- `U` / `O` (硒代/吡咯赖氨酸)
- `*` (终止符)
- `-` / `.` (gap)
- 小写字母
- 任何非字母字符

### 3.2 长度筛选

`1 <= len(sequence) <= 30` 的整条记录保留，其余丢弃。

### 3.3 RNA 翻译

- 每个 RNA 序列生成 6 个候选翻译产物（frame ∈ {+1,+2,+3,-1,-2,-3}）
- 反向互补链单独翻译
- 每个翻译产物独立走 3.1 + 3.2 规则
- 翻译表使用 NCBI 标准遗传密码（NCBI translation table 1），包含 `*`（终止）→ 截断
- header 含已知 ncRNA 关键词 (`rRNA`, `tRNA`, `ribosom`, `16S`, `23S`, `5S`, `5.8S`) 的跳过整条记录

## 4. 数据库设计

### 4.1 基础设施

- **DB 引擎：** PostgreSQL 16（apt 安装）
- **DB 名：** `igem_peptides`
- **用户：** `igem`，密码 `igem_local_2026`（仅本地 trust + md5）
- **本地化：** 本机 systemd 服务，监听 5432
- **配置调优（postgresql.conf）：**
  - `shared_buffers = 8GB`
  - `work_mem = 64MB`
  - `maintenance_work_mem = 2GB`
  - `effective_cache_size = 24GB`
  - `fsync = on`, `synchronous_commit = on`, `full_page_writes = on`（数据量大，先保正确性）

### 4.2 Schema（双表）

```sql
-- 主表：每行 = (sequence, source) 对
CREATE TABLE peptides (
    id                BIGSERIAL PRIMARY KEY,
    sequence          VARCHAR(30) NOT NULL,
    length            SMALLINT NOT NULL CHECK (length BETWEEN 1 AND 30),
    source            VARCHAR(32) NOT NULL,
    source_accession  TEXT,
    seq_md5           CHAR(32) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT peptides_seq_source_uniq UNIQUE (sequence, source)
);

CREATE INDEX peptides_seq_md5_idx ON peptides (seq_md5);
CREATE INDEX peptides_source_idx  ON peptides (source);
CREATE INDEX peptides_length_idx  ON peptides (length);
CREATE INDEX peptides_seq_idx     ON peptides (sequence);

-- 元数据表：1:1 关联 peptides(id)
CREATE TABLE peptide_metadata (
    peptide_id      BIGINT PRIMARY KEY REFERENCES peptides(id) ON DELETE CASCADE,
    parent_header   TEXT NOT NULL,
    parent_length   INTEGER,
    taxonomy        TEXT,
    pdb_id          VARCHAR(8),
    pdb_chain       VARCHAR(4),
    rna_frame       SMALLINT,
    source_file     TEXT NOT NULL,
    source_offset   BIGINT
);

CREATE INDEX peptide_meta_pdb_idx ON peptide_metadata (pdb_id) WHERE pdb_id IS NOT NULL;
CREATE INDEX peptide_meta_tax_idx ON peptide_metadata (taxonomy) WHERE taxonomy IS NOT NULL;
```

### 4.3 字段语义

| 字段 | 含义 | 填充规则 |
|---|---|---|
| `peptides.sequence` | 清洗后的纯 20 字母序列 | 大写 |
| `peptides.length` | 序列长度 | 1..30 |
| `peptides.source` | 8 个枚举值之一 | `uniprot`, `uniref90`, `bfd`, `mgy`, `pdb`, `rnacentral`, `rfam`, `nt_rna` |
| `peptides.source_accession` | header 第一 token | 例 `sp|P12345|TOXIN_ECOLI` |
| `peptides.seq_md5` | 序列 MD5 (uppercase hex) | 用于跨源去重 |
| `peptide_metadata.parent_header` | FASTA header 完整原文 | 含描述 |
| `peptide_metadata.parent_length` | 原始记录长度 | 通常 = length |
| `peptide_metadata.taxonomy` | 从 header 提取 | UniProt 格式 `OS=Homo sapiens OX=9606` |
| `peptide_metadata.pdb_id` | 4 字符 PDB id | 例 `101m`，仅 pdb 来源 |
| `peptide_metadata.pdb_chain` | 链标识 | 例 `A` |
| `peptide_metadata.rna_frame` | RNA 翻译框 | -3..3 |
| `peptide_metadata.source_file` | 原始文件路径 | 例 `/home/lenovo/public_databases/uniprot_all_2021_04.fa` |
| `peptide_metadata.source_offset` | 文件偏移（断点续扫） | bytes |

## 5. 代码组织

所有代码位于 `/home/lenovo/Projects/iGEM-platform/src/setup/`：

```
src/setup/
├── 00_install.sh          # apt + pip 安装
├── 01_pg_hba.conf          # pg_hba.conf 补丁
├── 02_postgresql.conf.patch # 配置调优补丁
├── 03_init_db.sql          # CREATE DATABASE / USER / GRANT
├── 04_create_tables.sql    # 上面 DDL
├── lib/
│   ├── __init__.py
│   ├── aa.py               # 严格 20 字母清洗, MD5, 长度判断
│   ├── fasta_iter.py       # pyfastx 流式迭代 + 断点 offset 记录
│   ├── rna_translate.py    # 6-frame 翻译
│   ├── header_parse.py     # UniProt / PDB / RNA header 解析
│   └── db_writer.py        # COPY 批量写入 + ON CONFLICT
├── scan_protein.py         # 蛋白库扫描器入口
├── scan_rna.py             # RNA 库扫描器入口
├── run_all.sh              # 串行调度 8 个文件
├── self_check.sql          # 跑完后的自检 SQL
└── README.md               # 使用说明
```

## 6. 流水线

### 6.1 扫描流程

1. 读 `progress.json`，跳过已完成文件
2. 对每个 FASTA 文件：
   - 用 `pyfastx.Fasta(path, build_index=False)` 流式迭代
   - 每条记录：清洗 → 长度判断 → header 解析 → 准备 row
   - 缓冲 5000 行 → `cursor.copy_from` 批量写入
   - 写入失败 → 记录当前 offset 到 `progress.json`，下次续扫
   - 写完后该文件标记 `done`
3. UNIQUE 约束冲突 → `ON CONFLICT (sequence, source) DO NOTHING`

### 6.2 断点续扫

`progress.json` 结构：
```json
{
  "files": {
    "/home/lenovo/public_databases/uniprot_all_2021_04.fa": {
      "status": "done",
      "rows_inserted": 9876543,
      "last_offset": null
    },
    "...": {"status": "in_progress", "last_offset": 12345678, "rows_inserted": 100}
  }
}
```

### 6.3 执行顺序

```
1. apt install postgresql-16 python3-psycopg2 python3-pip
2. pip3 install --break-system-packages pyfastx psycopg[binary]
3. sudo -u postgres psql -f 03_init_db.sql
4. psql -d igem_peptides -f 04_create_tables.sql
5. bash run_all.sh
6. psql -d igem_peptides -f self_check.sql
```

## 7. 验证（self_check.sql）

```sql
-- 总行数
SELECT count(*) FROM peptides;

-- 按来源分布
SELECT source, count(*) FROM peptides GROUP BY source ORDER BY count(*) DESC;

-- 按长度分布
SELECT length, count(*) FROM peptides GROUP BY length ORDER BY length;

-- 长度异常（应为空）
SELECT count(*) FROM peptides WHERE length < 1 OR length > 30;

-- 非标准字符（应为空）
SELECT count(*) FROM peptides WHERE sequence ~ '[^ACDEFGHIKLMNPQRSTVWY]';

-- 重复率
SELECT count(*) AS total,
       count(DISTINCT (sequence, source)) AS unique_pairs,
       count(DISTINCT sequence) AS unique_seqs
FROM peptides;

-- 抽样验证
SELECT p.*, m.parent_header, m.pdb_id, m.taxonomy
FROM peptides p JOIN peptide_metadata m ON m.peptide_id = p.id
ORDER BY random() LIMIT 20;
```

## 8. 估算

| 来源 | 原始记录数 | ≤30 aa 占比 | 期望入表行数 |
|---|---|---|---|
| uniprot_all | ~200M | ~5% | ~10M |
| uniref90 | ~200M | ~5% | ~10M |
| bfd | ~30M | ~3% | ~1M |
| mgy | ~600M | ~3% | ~18M |
| pdb_seqres | ~200K | ~1% | ~2K |
| rfam | ~5K seq (短 ncRNA) | ~30% | ~10K |
| rnacentral | ~30M | ~20% | ~6M |
| nt_rna | ~70M | ~15% | ~10M |
| **合计** | — | — | **~5500 万行** |

## 9. 漏斗筛选预留

后续每一步漏斗建一张新表（如 `funnel_step1_toxicity`），主键均为 `peptide_id` 外键到 `peptides.id`。当前 schema 已为以下高频查询预留索引：
- 按长度筛：`peptides(length)`
- 按来源筛：`peptides(source)`
- 跨源去重：`peptides(seq_md5)`
- 按物种筛：`peptide_metadata(taxonomy)`
- 按结构筛：`peptide_metadata(pdb_id)`

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 700 GB 扫描耗时（估 24–48h） | 断点续扫；分文件独立进度；UNIQUE ON CONFLICT 幂等 |
| 5500 万行 INSERT 慢 | `cursor.copy_from` 批量写入；关闭索引更新到导入后 |
| PostgreSQL 没装 | `apt install postgresql-16`；用户已提供 sudo 密码 |
| mmCIF 没扫 | seqres 已覆盖；schema 预留 `pdb_id` 字段 |
| RNA 6-frame 翻译噪声大 | header 关键词跳过已知 ncRNA；20 字母清洗过滤大部分 |

## 11. 验收标准

1. ✅ `igem_peptides` 数据库可连接，schema 与 §4.2 一致
2. ✅ 8 个文件全部扫描完成（或显式记录跳过原因）
3. ✅ `self_check.sql` 输出符合预期：长度 1–30、字符严格 20 字母、行数 ~5500 万
4. ✅ 抽样 20 条人工核对 FASTA header 与 DB 记录一致
5. ✅ README.md 描述如何重新跑、续跑、自检