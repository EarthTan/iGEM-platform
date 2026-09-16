-- 15_extend_constructs_status_wip.sql
-- 扩展 constructs.status 的 CHECK 约束，加入 'WIP'（抗黑素方向提案 §5 用）。
--
-- 用法：psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/15_extend_constructs_status_wip.sql
--
-- 为什么需要：抗菌/抗氧化的 status 仅 'passed' / 'failed_safety' / 'failed_score'；
-- 抗黑素方向按提案 §5 "所有 construct 标 WIP"——必须先扩展 CHECK 才能落库。

BEGIN;

-- 1. 删除旧约束
ALTER TABLE constructs DROP CONSTRAINT IF EXISTS constructs_status_check;

-- 2. 加新约束（含 'WIP'）
ALTER TABLE constructs
    ADD CONSTRAINT constructs_status_check
    CHECK (status IN ('passed', 'failed_safety', 'failed_score', 'WIP'));

COMMIT;

-- 验证
SELECT pg_get_constraintdef(oid) AS new_constraint_def
  FROM pg_constraint
 WHERE conname = 'constructs_status_check';