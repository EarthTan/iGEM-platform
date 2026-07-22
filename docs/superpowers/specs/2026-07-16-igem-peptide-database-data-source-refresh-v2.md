# iGEM 肽段数据库 — 数据源刷新 v2 Spec

**日期:** 2026-07-16
**作者:** Zed
**目标项目:** `/home/lenovo/Projects/iGEM-platform`
**上一版:** [`2026-07-10-igem-peptide-database-design.md`](./2026-07-10-igem-peptide-database-design.md) (首次设计)
**关联:** [`2026-07-13-igem-peptide-database-data-source-refresh.md`](./2026-07-13-igem-peptide-database-data-source-refresh.md) (v1,被本 spec 替代)
**本文定位:** 路径隔离到 `public_databases_v2/` + 5 个老源 deprecated + BFD 顶替路线 + 删 RNA 翻译。

---

## 0. 与前版的关系

| 来源 | 本 spec 处理 |
|---|---|
| v1 spec §3.1 中已实际验证能下的 2 个 URL(uniref90, pdb) | **保留,沿用** |
| v1 spec §3.1 中已拆分文件确认的 uniprot (sprot + trembl) | **改写为 2 个独立 source_id** |
| v1 spec §3.3 RNA 6-frame 翻译 | **整段删除** |
| v1 spec §3.1 中死/改名的 5 源(mgy/rfam/rnacentral/nt_rna) | **deprecated, enum 保留** |
| bfd (frozen) | v1 不动;v2 加 `mgy_2024` 并行作为顶替候选 |

## 1. 背景与动机

### 1.1 2026-07 实测发现的事实

| 老 source_id | 2021–2023 形态 | 2026 实际下载可达情况 |
|---|---|---|
| `uniprot` | `uniprot_all_*.fa.gz` 单文件 | 拆分为 `uniprot_sprot.fasta.gz` + `uniprot_trembl.fasta.gz` |
| `uniref90` | `uniref90_*.fasta.gz` | URL 仍 200 ✅ |
| `bfd` | frozen | frozen(本地 17GB 不动) |
| `mgy` (mgy_clusters) | `mgy_clusters_*.fa.gz` | **改名/撤档**,父目录只剩生物项目子目录,无聚类文件 |
| `pdb` | weekly `pdb_seqres.txt.gz` | URL 仍 200 ✅ |
| `rfam` | `rfam_*_clust_seq_id_90_cov_80_rep_seq.fasta` | **产品已下架**;现只有 `RF00001.fa.gz..RF*.fa.gz` 单家族文件 3000+ |
| `rnacentral` | `rnacentral_active_*_linclust.fasta` | **已撤档**,`database_files/` 和 `md5/` 子目录实测无内容 |
| `nt_rna` | `nt_rna_*_clust_*.fasta` | **从未存在**,`nt.gz` 原始太大,不切实际 |

### 1.2 2026-07 决策记录

- ✅ **5 个老源 deprecated**(mgy / rfam / rnacentral / nt_rna / 老的 uniprot 合并版)
- ✅ **uniprot 拆分**为 `uniprot_sprot` + `uniprot_trembl` 两个独立 source_id
- ✅ **RNA 6-frame 翻译规则整段删除**(Rfam/rnacentral/nt_rna 都不再扫)
- ✅ **物理隔离**:新下载落 `/media/lenovo/Data/public_databases_v2/`,原 `/media/lenovo/Data/public_databases/` 8 个老文件保留原状(627 GB)
- ✅ **bfd 不动**(frozen);`mgy_2024` 作为 bfd 的**顶替候选**,URL 待定

## 2. 范围

### 2.1 新数据源(4 真活 + 1 frozen)

| `source_id` | 文件 | 状态 | URL | 预估大小 |
|---|---|---|---|---|
| `uniprot_sprot` | `uniprot_sprot.fasta.gz` | track-latest | `https://ftp.ebi.ac.uk/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz` | ~90 MB |
| `uniprot_trembl` | `uniprot_trembl.fasta.gz` | track-latest | `https://ftp.ebi.ac.uk/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_trembl.fasta.gz` | ~150 GB |
| `uniref90` | `uniref90.fasta.gz` | track-latest | `https://ftp.ebi.ac.uk/pub/databases/uniprot/current_release/uniref/uniref90/uniref90.fasta.gz` | ~85 GB |
| `pdb` | `pdb_seqres.txt.gz` | track-latest | `https://files.rcsb.org/pub/pdb/derived_data/pdb_seqres.txt.gz` | ~300 MB |
| `bfd` | `bfd-first_non_consensus_sequences.fasta` | **frozen** | (本地不动) | 17 GB |
| `mgy_2024` | TBD | **TBD** | 待 EBI MGnify 2024 catalogue 公开下载 URL 落实 | TBD |

### 2.2 Deprecated 源(enum 保留,不再下载)

- `mgy`(mgy_clusters 老版,改名/撤档)
- `rfam`(clust 聚类产品下架)
- `rnacentral`(active linclust 已撤)
- `nt_rna`(clust 聚类版从未存在)
- `uniprot`(v1 合并版,被 §2.1 中的 sprot+trembl 顶替)

deprecated 源保留在 `peptides.source` enum,表里允许出现,不改 schema。仅在 `update_sources.sh` 里**不再处理**。

### 2.3 整体期望入表行数(粗估)

| 源 | 原始记录 | ≤30 aa 占比 | 期望入表行 |
|---|---|---|---|
| uniprot_sprot | ~7M | ~5% | ~350K |
| uniprot_trembl | ~250M | ~5% | ~12M |
| uniref90 | ~200M | ~5% | ~10M |
| pdb_seqres | ~220K | ~1% | ~2K |
| bfd (frozen 旧,本地) | ~30M | ~3% | ~1M |
| **合计(不含 mgy_2024)** | | | **~23M** |

比 v1 spec 估的 ~5500 万低,**这才是现实**(原 spec 高估了 mg/RNA 翻译贡献)。

## 3. 物理隔离

```
/media/lenovo/Data/
├── public_databases/                  ← 原状保留,与 v2 完全隔离
│   ├── bfd-first_non_consensus_sequences.fasta (17GB, frozen 不动)
│   ├── mgy_clusters_2022_05.fa          (120GB, deprecated, 原状保留)
│   ├── nt_rna_2023_02_23_..._rep_seq.fasta (76GB, deprecated, 原状保留)
│   ├── pdb_seqres_2022_09_28.fasta     (223MB, deprecated, 原状保留)
│   ├── rfam_14_9_..._rep_seq.fasta     (218MB, deprecated, 原状保留)
│   ├── rnacentral_active_..._linclust.fasta (13GB, deprecated, 原状保留)
│   ├── uniprot_all_2021_04.fa          (102GB, deprecated, 原状保留)
│   ├── uniref90_2022_05.fa             (67GB, deprecated, 原状保留)
│   └── mmcif_files/                    (原状保留)
└── public_databases_v2/                ← v2 全新工作区
    ├── manifest/
    │   └── MANIFEST.json               (8 个源登记)
    ├── uniprot_sprot/                  (新下载)
    ├── uniprot_trembl/
    ├── uniref90/
    ├── pdb/
    ├── bfd/                            (空,引用 frozen 原文件)
    └── mgy_2024/                       (空,等 URL)
```

## 4. Schema(沿用 v1 §4.2 + §4.3,不改动)

`peptides.source` enum 字段仍接受 8 个值:**deprecated 源保留**(允许历史数据存在,新数据不再写入)。

## 5. 路径常量

```python
PUBLIC_DATA_ROOT = "/media/lenovo/Data/public_databases_v2"
```

所有脚本、SQL、配置只此一个常量。

## 6. 执行

### 6.1 P0(小文件先跑,~22 GB)
1. `pdb`
2. `uniprot_sprot`

### 6.2 P1(中型,~150 GB)
3. `uniref90`

### 6.3 P2(大,~150 GB)
4. `uniprot_trembl`

### 6.4 P3(bfd 不下载;`mgy_2024` 留 TBD)
5. bfd → 跳过(本地 frozen)
6. mgy_2024 → 等 URL

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| MGnify 2024 catalogue URL 未明 | 标 TBD,后续 discover;`update_sources.sh` 跳过 TBD |
| Data 盘是机械盘,下载/扫描 IO 慢 | 分批,utilize `aria2c -x 8` |
| 老 public_databases/ 误动 | 完全隔离,`PUBLIC_DATA_ROOT` 只指 v2 |
| uniprot_trembl 150GB,磁盘要预留 >200GB | `update_sources.sh` 下载前预检 |
| deprecated 源 schema 是否需要加 "deprecated" 列 | 否 — 用 source_version + 注释表 `dataset_manifest` 已记录版本信息 |

## 8. 验收

1. `/media/lenovo/Data/public_databases_v2/manifest/MANIFEST.json` 包含 6 个 source 条目(4 track-latest + 1 frozen + 1 TBD)
2. P0/P1 的 PDB + UniProt sprot 下载完成 + sha256 校验通过
3. PostgreSQL 跑 `04_create_tables_v2.sql`,schema 与 v1 §4.2 一致
4. 扫描(scan_protein,后续实现)入表后,`self_check.sql` 输出行数 ≥ 1500 万
5. `/media/lenovo/Data/public_databases/` 完全未动(对外的"原状"约定)

## 9. 与 v1 spec 的差异

| v1 spec | 本 v2 spec |
|---|---|
| §3.1 8 源全部"拉最新" | §2.1 4 真活 + 1 frozen, 5 个 deprecated |
| uniprot 单文件 | uniprot 拆分 sprot + trembl 2 文件 |
| RNA 6-frame 翻译 | **删除** |
| BFD frozen 不顶替 | BFD frozen + `mgy_2024` 顶替候选 |
| 路径 `/media/lenovo/Data/public_databases/` | `/media/lenovo/Data/public_databases_v2/` |
| MANIFEST 标 archive/... | 不挪老文件,`current_on_disk.file` 平铺 |

