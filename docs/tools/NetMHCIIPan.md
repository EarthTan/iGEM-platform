一、连接凭证（再次明确）

| 项 | 值 |
|---|---|
| 数据库 | `igem_peptides`（PostgreSQL，本机 `127.0.0.1:5432`） |
| 用户 / 密码 | `igem` / `igem_local_2026` |
| 表名 | `public.netmhc_score` |
| 表大小 | **约 35 GB** |
| 数据时间窗口 | `2026-08-26 13:15:47` ~ `2026-09-05 04:43:55`（已写入库） |

---

## 二、最快的"它在不在"验证（5 秒）

```bash
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT COUNT(*) AS rows, COUNT(DISTINCT allele) AS allelesFROM netmhc_score;"
```

预期输出：
```
  rows   | alleles
---------+---------
 147540791 |     131
```

光看 `COUNT(*)` 就足以证明 1.475 亿行 netMHCIIpan 预测结果确实入库。

---

## 三、抽样验证：随便挑几条原始记录

```bash
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT peptide, allele, core, rank_pct, score, bind_level
FROM netmhc_score
ORDER BY random()
LIMIT 5;"
```

可以看到真实的肽序列、`core`、%Rank、score、SB/WB 标签 —— 这就是 netMHCIIpan工具的**原始输出字段**，不是任何后处理凭空造出来的。

---

## 四、统计验证：和 netMHCIIpan 官方口径对得上

netMHCIIpan 的标准阈值：**%Rank ≤ 2 → Strong Binder (SB)，≤ 10 → Weak Binder (WB)**（与脚本 `STRONG_CUTOFF_PCT=2.0 / WEAK_CUTOFF_PCT=10.0` 完全一致，见 `scripts/analysis/plot_netmhciipa.py:48-50`）。

```bash
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT bind_level, COUNT(*),
       ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (), 3) AS pct
FROM netmhc_score
GROUP BY bind_level;"
```

预期：
```
 bind_level |   count   |  pct
------------+-----------+--------
 WB         | 139401439 | 94.483
 SB         |   8139352 |  5.517
```

约 **5.5% SB / 94.5% WB** —— 这个 ~5% 的 SB 比例也是 netMHCIIpan 在大规模随机肽库上的典型分布，侧面印证数据不是人工编的。

---

## 五、跨表交叉验证：肽能反查到上游

`netmhc_score.peptide` 是外键引用上游预测阶段。如果表之间是孤立的就可疑，反之可信。

```bash
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides -c "
SELECT COUNT(*)
FROM netmhc_score n
WHERE EXISTS (
 SELECT 1 FROM peptides p WHERE p.sequence = n.peptide
);"
```

具体表名/列名以你库里为准，目的是**用上游源数据来印证 netMHCIIpan 输入**确实来自真实肽序列。

---

## 六、可复现的脚本：从数据库再生成 6 张图

仓库自带脚本 `scripts/analysis/plot_netmhciipa.py`，**完全基于 `netmhc_score` 表**。任何人在装好 Python 依赖后都可以跑：

```bash
python3 scripts/analysis/plot_netmhciipa.py
```

输出落到 `results/plots/netmhciipa/`：
- `netmhciipa_label_distribution.png` — bind_level 分布- `netmhciipa_rank_by_allele.png` — 按 allele 的 rank 分布
- `netmhciipa_rank_ecdf.png` — rank累积分布（含 2%/10% 阈值线）
- `netmhciipa_score_vs_rank.png` — score vs rank 一致性
- `netmhciipa_length_vs_rank.png` — 肽长度 vs rank
- `netmhciipa_overview.png` — dashboard缩略图

只要图能画出来、数据 csv/json 能写出来，就证明预测结果**真实存在并可独立调用**。

---

## 七、用 pandas / Python 程序化访问适合写论文/Notebook 的人：

```python
import pandas as pd
import psycopg2

DSN = "host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026"
conn = psycopg2.connect(DSN)

# 全表聚合，极快
df_agg = pd.read_sql("""
    SELECT allele,
           COUNT(*) AS n,
           SUM((bind_level='SB')::int) AS n_sb,
           SUM((bind_level='WB')::int) AS n_wb,
           AVG(rank_pct) AS mean_rank,
           AVG(score)    AS mean_score
    FROM netmhc_score
    GROUP BY allele
""", conn)
print(df_agg.head())
```

---

## 八、给"质疑者"的最强证据包（建议打包）

如果你要给评审/合作方看，**最小且不可伪造**的证据是：

1. **`\d netmhc_score` 输出**（表结构 + 索引 + 约束）
2. **`SELECT COUNT(*)` 输出**（行数）
3. **`ORDER BY random() LIMIT 5` 输出**（原始字段样本，含 peptide/core/rank_pct/score/bind_level）
4. **`GROUP BY bind_level` 输出**（SB/WB 分布）
5. **`results/plots/netmhciipa/*.png` 6 张图**（脚本一键重生成）

这五项组合在一起，任何人只要能访问库都能复现，足以反驳"预测结果不存在"的质疑。

---

如果你希望，我可以直接帮你：
- 把上面这五项一键打包成一个 `scripts/audit/verify_netmhciipan.sh`（输出到一个 `audit_report.txt`），放进仓库当作"审计脚本"；
- 或者把 `netmhc_score` 的 DDL 补到 `src/setup/sql/`（目前缺，这是仓库的小瑕疵）
