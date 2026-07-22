-- 05_self_check.sql — v2 spec §7
-- 总览:总行数、按 source 分布、按 length 分布、字符集异常 = 0、重复率、扫描状态
\echo === 总行数 + 按 source ===
SELECT source, count(*) AS rows, count(DISTINCT sequence) AS unique_seqs,
       count(DISTINCT (sequence, length)) AS uniq_seq_len
  FROM peptides GROUP BY source ORDER BY rows DESC;

\echo === 按 length 分布 (前 30) ===
SELECT length, count(*) AS rows FROM peptides GROUP BY length ORDER BY length LIMIT 31;

\echo === 字符集异常 = 0 应满足 ===
SELECT count(*) AS invalid_seqs FROM peptides
 WHERE sequence !~ '^[ACDEFGHIKLMNPQRSTVWY]+$';

\echo === 重复率(同 sequence 跨 source) ===
SELECT sequence, count(DISTINCT source) AS sources FROM peptides
 GROUP BY sequence HAVING count(DISTINCT source) > 1 ORDER BY sources DESC LIMIT 10;

\echo === dataset_manifest 状态 ===
SELECT source, source_version, status, rows_inserted,
       (scan_finished_at - scan_started_at) AS took,
       source_file_bytes, substring(source_file_sha256 for 12) AS sha_prefix,
       COALESCE(error_message, '') LIKE '%' AS has_error
  FROM dataset_manifest ORDER BY scan_started_at DESC;