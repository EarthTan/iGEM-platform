"""老 8 文件路径表(只读,不动原盘)"""
from pathlib import Path

LEGACY_ROOT = Path("/media/lenovo/Data/public_databases")

# source_id → 真实路径 + source_version (DATE 形式)
LEGACY_SOURCES = {
    "legacy_uniprot_all_2021": {
        "path": str(LEGACY_ROOT / "uniprot_all_2021_04.fa"),
        "source_version": "2021-04-01",
    },
    "legacy_uniref90_2022": {
        "path": str(LEGACY_ROOT / "uniref90_2022_05.fa"),
        "source_version": "2022-05-01",
    },
    "legacy_pdb_2022": {
        "path": str(LEGACY_ROOT / "pdb_seqres_2022_09_28.fasta"),
        "source_version": "2022-09-28",
    },
    "legacy_rfam_2022": {
        "path": str(LEGACY_ROOT / "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta"),
        "source_version": "2022-09-28",
    },
    "legacy_rnacentral_2023": {
        "path": str(LEGACY_ROOT / "rnacentral_active_seq_id_90_cov_80_linclust.fasta"),
        "source_version": "2023-02-23",
    },
    "legacy_nt_rna_2023": {
        "path": str(LEGACY_ROOT / "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta"),
        "source_version": "2023-02-23",
    },
    "legacy_mgy_2022": {
        "path": str(LEGACY_ROOT / "mgy_clusters_2022_05.fa"),
        "source_version": "2022-05-01",
    },
    "legacy_bfd_2022": {
        "path": str(LEGACY_ROOT / "bfd-first_non_consensus_sequences.fasta"),
        "source_version": "2022-01-01",
    },
}