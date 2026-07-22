-- iGEM Schema V2 — 沿用 v1 §4.2 + §4.3 字段语义不变
BEGIN;

CREATE TABLE IF NOT EXISTS peptides (
    id                BIGSERIAL PRIMARY KEY,
    sequence          VARCHAR(30) NOT NULL,
    length            SMALLINT NOT NULL CHECK (length BETWEEN 1 AND 30),
    source            VARCHAR(32) NOT NULL,
    source_version    DATE NOT NULL,
    source_accession  TEXT,
    seq_md5           CHAR(32) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT peptides_seq_source_ver_uniq UNIQUE (sequence, source, source_version)
);

CREATE INDEX IF NOT EXISTS peptides_seq_md5_idx     ON peptides (seq_md5);
CREATE INDEX IF NOT EXISTS peptides_source_ver_idx  ON peptides (source, source_version);
CREATE INDEX IF NOT EXISTS peptides_length_idx      ON peptides (length);
CREATE INDEX IF NOT EXISTS peptides_seq_idx         ON peptides (sequence);

CREATE TABLE IF NOT EXISTS peptide_metadata (
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

CREATE INDEX IF NOT EXISTS peptide_meta_pdb_idx ON peptide_metadata (pdb_id) WHERE pdb_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS peptide_meta_tax_idx ON peptide_metadata (taxonomy) WHERE taxonomy IS NOT NULL;

CREATE TABLE IF NOT EXISTS dataset_manifest (
    id                  BIGSERIAL PRIMARY KEY,
    source              VARCHAR(32) NOT NULL,
    source_version      DATE NOT NULL,
    source_file         TEXT NOT NULL,
    source_file_sha256  CHAR(64),
    source_file_bytes   BIGINT,
    scan_started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    scan_finished_at    TIMESTAMPTZ,
    status              VARCHAR(16) NOT NULL
        CHECK (status IN ('pending','in_progress','done','failed')),
    rows_inserted       BIGINT NOT NULL DEFAULT 0,
    last_offset         BIGINT,
    error_message       TEXT,
    CONSTRAINT dataset_manifest_uniq UNIQUE (source, source_version, source_file)
);

CREATE INDEX IF NOT EXISTS dataset_manifest_status_idx ON dataset_manifest (status);
CREATE INDEX IF NOT EXISTS dataset_manifest_source_idx ON dataset_manifest (source, source_version);

COMMIT;
