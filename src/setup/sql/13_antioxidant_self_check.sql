-- 13_antioxidant_self_check.sql
-- 抗氧化预计算库落库后的自检，验证 pipeline §1–§5 落库正确。
--
-- 用法：psql -f src/setup/sql/13_antioxidant_self_check.sql

\echo === 1. 总数 + 通道分布 ===
SELECT channel, status, count(*) AS n
  FROM constructs
 WHERE direction='antioxidant'
 GROUP BY channel, status
 ORDER BY channel, status;

\echo === 2. Top 通道 composite_topical 必须非空 + 降序排列 ===
SELECT min((delivery_scores->>'topical')::float8)    AS min_topical,
       max((delivery_scores->>'topical')::float8)    AS max_topical,
       min((delivery_scores->>'injection')::float8)  AS min_injection,
       max((delivery_scores->>'injection')::float8)  AS max_injection
  FROM constructs
 WHERE direction='antioxidant' AND channel='top';

\echo === 3. failed_safety 不应出现（pipeline 只落 passed） ===
SELECT count(*) FROM constructs
 WHERE direction='antioxidant' AND status='failed_safety';

\echo === 4. 9 项分数必须全部为非空（对通过安全门控的肽而言） ===
SELECT count(*) FILTER (WHERE scores ? 'aopxsvm')           AS n_aopxsvm,
       count(*) FILTER (WHERE scores ? 'anoxpepred_frs')    AS n_anoxpepred_frs,
       count(*) FILTER (WHERE scores ? 'anoxpepred_chel')   AS n_anoxpepred_chel,
       count(*) FILTER (WHERE scores ? 'toxinpred3')        AS n_tox,
       count(*) FILTER (WHERE scores ? 'hemopi2')           AS n_hemo,
       count(*) FILTER (WHERE scores ? 'mhcflurry')         AS n_mhci,
       count(*) FILTER (WHERE scores ? 'netmhcipan_pctrank') AS n_mhcii,
       count(*) FILTER (WHERE scores ? 'plm4cpps')          AS n_cpp,
       count(*) FILTER (WHERE scores ? 'algpred2')          AS n_sens,
       count(*) FILTER (WHERE scores ? 'bepipred')          AS n_bepi,
       count(*) FILTER (WHERE scores ? 'aggrescan_a3v')     AS n_agg
  FROM constructs
 WHERE direction='antioxidant';

\echo === 5. Bottom 通道的 aopxsvm 必须显著低于 Top ===
SELECT channel,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (scores->>'aopxsvm')::float8) AS median_aopxsvm
  FROM constructs
 WHERE direction='antioxidant'
 GROUP BY channel
 ORDER BY channel;