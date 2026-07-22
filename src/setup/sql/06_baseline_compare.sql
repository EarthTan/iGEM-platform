-- 06_baseline_compare.sql — 老基线 vs 新源覆盖对比
\echo === 老 uniprot_all_2021 vs 新 sprot+trembl (按 length) ===
WITH legacy AS (
  SELECT length, count(*) AS n FROM peptides WHERE source='legacy_uniprot_all_2021' GROUP BY length
), v2 AS (
  SELECT length, count(*) AS n FROM peptides
   WHERE source IN ('uniprot_sprot','uniprot_trembl') GROUP BY length
)
SELECT COALESCE(l.length, v.length) AS length,
       COALESCE(l.n,0) AS legacy_2021,
       COALESCE(v.n,0) AS v2_2026,
       round(100.0 * COALESCE(v.n,0) / GREATEST(COALESCE(l.n,0),1), 1) AS pct_v2_vs_legacy
  FROM legacy l FULL OUTER JOIN v2 v USING (length)
 ORDER BY length;

\echo === 老 pdb_2022 vs 新 pdb (按 length) ===
SELECT COALESCE(l.length, v.length) AS length,
       COALESCE(l.n,0) AS legacy_2022,
       COALESCE(v.n,0) AS v2_2026
  FROM (SELECT length, count(*) n FROM peptides WHERE source='legacy_pdb_2022' GROUP BY length) l
  FULL OUTER JOIN
       (SELECT length, count(*) n FROM peptides WHERE source='pdb' GROUP BY length) v
  USING (length) ORDER BY length;

\echo === 老 uniref90_2022 vs 新 uniref90 ===
SELECT COALESCE(l.length, v.length) AS length,
       COALESCE(l.n,0) AS legacy_2022,
       COALESCE(v.n,0) AS v2_2026
  FROM (SELECT length, count(*) n FROM peptides WHERE source='legacy_uniref90_2022' GROUP BY length) l
  FULL OUTER JOIN
       (SELECT length, count(*) n FROM peptides WHERE source='uniref90' GROUP BY length) v
  USING (length) ORDER BY length;

\echo === 仅老源有、新源没有的 sequence 数(被淘汰的肽段) ===
SELECT count(*) AS dropped_after_2022_only_in_legacy FROM peptides
 WHERE source='legacy_uniprot_all_2021'
   AND sequence NOT IN (SELECT sequence FROM peptides WHERE source IN ('uniprot_sprot','uniprot_trembl'));

\echo === 仅新源有、老源没有的 sequence 数(2021-2026 间新增) ===
SELECT count(*) AS new_in_2026_not_in_2021 FROM peptides
 WHERE source IN ('uniprot_sprot','uniprot_trembl')
   AND sequence NOT IN (SELECT sequence FROM peptides WHERE source='legacy_uniprot_all_2021');