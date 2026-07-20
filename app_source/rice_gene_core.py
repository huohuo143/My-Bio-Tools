"""Shared data models and sequence validation for rice gene analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import gzip
import hashlib
import io
from pathlib import Path
import re
from typing import Iterable

from Bio import SeqIO
from Bio.Seq import Seq

from rice_efp import EfpExpressionRecord


DATA_DIR = Path(__file__).resolve().parent / "data" / "Rice_Genome_Annotation_Project"
CDS_FASTA_PATH = DATA_DIR / "IRGSP-1.0_cds_2025-03-19.fasta.gz"
GENE_FASTA_PATH = DATA_DIR / "IRGSP-1.0_gene_2025-03-19.fasta.gz"
TRANSCRIPT_FASTA_PATH = DATA_DIR / "IRGSP-1.0_transcript_2025-03-19.fasta.gz"

GENOMIC = "Gene genomic"
CDS = "CDS"
PROTEIN = "Protein"
FIVE_UTR = "5′UTR"
THREE_UTR = "3′UTR"
PROMOTER = "Promoter"
SEQUENCE_TYPES = (GENOMIC, CDS, PROTEIN, FIVE_UTR, THREE_UTR, PROMOTER)

DNA_ALPHABET = set("ACGTRYSWKMBDHVN")
PROTEIN_ALPHABET = set("ABCDEFGHIKLMNPQRSTVWXYZJUO")
RAP_TRANSCRIPT_PATTERN = re.compile(r"^Os\d{2}t\d{7}(?:-\d+)?$", re.IGNORECASE)


@dataclass
class SequenceRecord:
    input_id: str
    resolved_rap_gene: str = ""
    resolved_msu_id: str = ""
    transcript_id: str = ""
    sequence_type: str = ""
    sequence: str = ""
    source: str = ""
    assembly: str = ""
    coordinates: str = ""
    strand: str = ""
    status: str = "matched"
    validation_note: str = ""

    @property
    def length(self) -> int:
        sequence = self.sequence[:-1] if self.sequence_type == PROTEIN and self.sequence.endswith("*") else self.sequence
        return len(sequence)

    def summary_row(self) -> dict[str, object]:
        return {
            "input_id": self.input_id,
            "resolved_rap_gene": self.resolved_rap_gene,
            "resolved_msu_id": self.resolved_msu_id,
            "transcript_id": self.transcript_id,
            "sequence_type": self.sequence_type,
            "length": self.length,
            "source": self.source,
            "assembly": self.assembly,
            "coordinates": self.coordinates,
            "strand": self.strand,
            "status": self.status,
            "validation_note": self.validation_note,
        }


@dataclass
class PredictionRegion:
    region_type: str
    start: int | None = None
    end: int | None = None
    score: float | None = None
    sequence: str = ""
    note: str = ""


@dataclass(frozen=True)
class PredictionProviderAttempt:
    provider: str
    status: str
    job_id: str = ""
    url: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass
class PredictionResult:
    protein_id: str
    tool: str
    version: str
    status: str = "failed"
    classification: str = ""
    summary: str = ""
    parameters: dict[str, object] = field(default_factory=dict)
    regions: list[PredictionRegion] = field(default_factory=list)
    result_url: str = ""
    raw_text: str = ""
    raw_html: str = ""
    error: str = ""
    provider: str = ""
    provider_job_id: str = ""
    fallback_used: bool = False
    probabilities: dict[str, float] = field(default_factory=dict)
    attempts: list[PredictionProviderAttempt] = field(default_factory=list)

    def summary_row(self) -> dict[str, object]:
        return {
            "protein_id": self.protein_id,
            "tool": self.tool,
            "version": self.version,
            "status": self.status,
            "classification": self.classification,
            "summary": self.summary,
            "parameters": "; ".join(f"{key}={value}" for key, value in self.parameters.items()),
            "region_count": len(self.regions),
            "provider": self.provider,
            "provider_job_id": self.provider_job_id,
            "fallback_used": self.fallback_used,
            "result_url": self.result_url,
            "error": self.error,
        }

    def region_rows(self) -> list[dict[str, object]]:
        return [
            {
                "protein_id": self.protein_id,
                "tool": self.tool,
                "region_type": region.region_type,
                "start": region.start,
                "end": region.end,
                "score": region.score,
                "sequence": region.sequence,
                "note": region.note,
            }
            for region in self.regions
        ]

    def probability_rows(self) -> list[dict[str, object]]:
        return [
            {
                "protein_id": self.protein_id,
                "tool": self.tool,
                "provider": self.provider,
                "label": label,
                "probability": value,
            }
            for label, value in self.probabilities.items()
        ]

    def attempt_rows(self) -> list[dict[str, object]]:
        return [
            {
                "protein_id": self.protein_id,
                "tool": self.tool,
                **asdict(attempt),
            }
            for attempt in self.attempts
        ]


@dataclass
class PredictionExecution:
    results: list[PredictionResult] = field(default_factory=list)
    raw_artifacts: dict[str, bytes] = field(default_factory=dict)


@dataclass
class AnalysisBundle:
    mode: str
    input_type: str
    inputs: list[str]
    mapping_rows: list[dict[str, object]] = field(default_factory=list)
    sequences: list[SequenceRecord] = field(default_factory=list)
    predictions: list[PredictionResult] = field(default_factory=list)
    analysis_options: dict[str, object] = field(default_factory=dict)
    ricedata_rows: list[dict[str, object]] = field(default_factory=list)
    efp_rows: list[EfpExpressionRecord] = field(default_factory=list)
    lab_omics_datasets: list[dict[str, object]] = field(default_factory=list)
    lab_omics_comparisons: list[dict[str, object]] = field(default_factory=list)
    lab_omics_samples: list[dict[str, object]] = field(default_factory=list)
    lab_omics_differential: list[dict[str, object]] = field(default_factory=list)
    lab_omics_profiles: list[dict[str, object]] = field(default_factory=list)
    lab_omics_status: list[dict[str, object]] = field(default_factory=list)
    lab_omics_published_evidence: list[dict[str, object]] = field(default_factory=list)
    lab_omics_consensus_scores: list[dict[str, object]] = field(default_factory=list)
    lab_omics_qc_metrics: list[dict[str, object]] = field(default_factory=list)
    lab_omics_dataset_context: list[dict[str, object]] = field(default_factory=list)
    lab_omics_dataset_registry: list[dict[str, object]] = field(default_factory=list)
    lab_omics_dataset_summaries: list[dict[str, object]] = field(default_factory=list)
    protein_domains: list[dict[str, object]] = field(default_factory=list)
    functional_sites: list[dict[str, object]] = field(default_factory=list)
    transcript_models: list[dict[str, object]] = field(default_factory=list)
    gene_features: list[dict[str, object]] = field(default_factory=list)
    promoter_tfbs: list[dict[str, object]] = field(default_factory=list)
    upstream_tfs: list[dict[str, object]] = field(default_factory=list)
    variants: list[dict[str, object]] = field(default_factory=list)
    haplotypes: list[dict[str, object]] = field(default_factory=list)
    mirna_targets: list[dict[str, object]] = field(default_factory=list)
    rnai_offtargets: list[dict[str, object]] = field(default_factory=list)
    literature_rows: list[dict[str, object]] = field(default_factory=list)
    genetic_evidence: list[dict[str, object]] = field(default_factory=list)
    ricedata_references: list[dict[str, object]] = field(default_factory=list)
    sequence_plot_rows: list[dict[str, object]] = field(default_factory=list)
    mechanism_claims: list[dict[str, object]] = field(default_factory=list)
    ai_synthesis: dict[str, object] = field(default_factory=dict)
    interpretations: list[dict[str, object]] = field(default_factory=list)
    interpretation_status: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    generated_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_fasta_or_sequence(text: str, default_id: str = "query") -> list[tuple[str, str]]:
    """Parse FASTA or a single raw sequence without guessing molecule type."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if cleaned.startswith(">"):
        records = []
        for record in SeqIO.parse(io.StringIO(cleaned), "fasta"):
            records.append((record.id or default_id, re.sub(r"\s+", "", str(record.seq)).upper()))
        return records
    return [(default_id, re.sub(r"\s+", "", cleaned).upper())]


def normalize_cds(sequence: str) -> tuple[str, list[str]]:
    normalized = re.sub(r"\s+", "", sequence).upper().replace("U", "T")
    errors: list[str] = []
    invalid = sorted(set(normalized) - DNA_ALPHABET)
    if invalid:
        errors.append("CDS 含非法字符：" + "".join(invalid))
    if len(normalized) % 3:
        errors.append(f"CDS 长度 {len(normalized)} 不是 3 的倍数，未猜测阅读框。")
    if not normalized:
        errors.append("CDS 为空。")
    return normalized, errors


def translate_cds(sequence: str) -> tuple[str, list[str]]:
    normalized, errors = normalize_cds(sequence)
    if errors:
        return "", errors
    protein = str(Seq(normalized).translate(table=1))
    internal = protein[:-1] if protein.endswith("*") else protein
    if "*" in internal:
        errors.append("CDS 翻译结果含内部终止密码子，未运行蛋白定位预测。")
    return protein[:-1] if protein.endswith("*") else protein, errors


def normalize_protein(sequence: str) -> tuple[str, list[str]]:
    normalized = re.sub(r"\s+", "", sequence).upper()
    if normalized.endswith("*"):
        normalized = normalized[:-1]
    errors: list[str] = []
    invalid = sorted(set(normalized) - PROTEIN_ALPHABET)
    if invalid:
        errors.append("蛋白序列含非法字符：" + "".join(invalid))
    if len(normalized) < 10:
        errors.append("蛋白序列短于 10 aa，不满足多数预测服务的最低长度。")
    if not normalized:
        errors.append("蛋白序列为空。")
    return normalized, errors


def sequence_digest(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii", errors="strict")).hexdigest()


@lru_cache(maxsize=1)
def build_reference_sequence_indexes(
    cds_path: str = str(CDS_FASTA_PATH),
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Build compact SHA-256 indexes for exact RAP CDS/protein reverse lookup."""
    cds_index: dict[str, list[str]] = {}
    protein_index: dict[str, list[str]] = {}
    with gzip.open(cds_path, "rt", encoding="utf-8") as handle:
        for record in SeqIO.parse(handle, "fasta"):
            cds = str(record.seq).upper().replace("U", "T")
            cds_index.setdefault(sequence_digest(cds), []).append(record.id)
            if len(cds) % 3 == 0:
                protein = str(Seq(cds).translate(table=1))
                if protein.endswith("*"):
                    protein = protein[:-1]
                if protein and "*" not in protein:
                    protein_index.setdefault(sequence_digest(protein), []).append(record.id)
    return (
        {key: tuple(values) for key, values in cds_index.items()},
        {key: tuple(values) for key, values in protein_index.items()},
    )


def exact_reference_matches(sequence: str, input_type: str) -> list[str]:
    cds_index, protein_index = build_reference_sequence_indexes()
    if input_type == "CDS FASTA":
        normalized, errors = normalize_cds(sequence)
        if errors:
            return []
        return list(cds_index.get(sequence_digest(normalized), ()))
    normalized, errors = normalize_protein(sequence)
    if errors:
        return []
    return list(protein_index.get(sequence_digest(normalized), ()))


def transcript_to_gene(transcript_id: str) -> str:
    if RAP_TRANSCRIPT_PATTERN.fullmatch(transcript_id):
        return re.sub("t", "g", transcript_id, count=1, flags=re.IGNORECASE).split("-", 1)[0]
    return ""


def deduplicate_sequence_records(records: Iterable[SequenceRecord]) -> list[SequenceRecord]:
    seen: set[tuple[str, str, str]] = set()
    result: list[SequenceRecord] = []
    for record in records:
        key = (record.sequence_type, record.transcript_id or record.resolved_rap_gene or record.resolved_msu_id, record.sequence)
        if not record.sequence or key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def sequence_records_to_fasta(records: Iterable[SequenceRecord], sequence_type: str) -> str:
    output = io.StringIO()
    for index, record in enumerate(records, start=1):
        if record.sequence_type != sequence_type or not record.sequence:
            continue
        identifier = record.transcript_id or record.resolved_rap_gene or record.resolved_msu_id or record.input_id or f"sequence_{index}"
        metadata = [
            f"type={sequence_type}",
            f"length={record.length}",
            f"source={record.source}",
        ]
        output.write(f">{identifier}|{'|'.join(metadata)}\n")
        for offset in range(0, len(record.sequence), 60):
            output.write(record.sequence[offset:offset + 60] + "\n")
    return output.getvalue()


def prediction_consistency(predictions: Iterable[PredictionResult]) -> list[str]:
    """Summarize agreement only within comparable computational prediction pairs."""
    items = list(predictions)
    usable = {
        result.tool: result
        for result in items
        if result.status in {"matched", "partial"}
    }
    total = len(items)
    lines = [f"成功返回 {len(usable)}/{total} 项预测；失败或超时项目不参与一致性判断。"]

    def pair_line(
        label: str,
        left_tool: str,
        right_tool: str,
        left_positive,
        right_positive,
    ) -> str:
        left = usable.get(left_tool)
        right = usable.get(right_tool)
        if not left or not right:
            return f"{label}：至少一项结果不可用，无法进行双工具比较。"
        left_hit = bool(left_positive(left))
        right_hit = bool(right_positive(right))
        agreement = "一致为阳性" if left_hit and right_hit else "一致为阴性" if not left_hit and not right_hit else "结果不一致"
        return f"{label}：{left_tool} 与 {right_tool} {agreement}。"

    lines.append(
        pair_line(
            "分泌信号",
            "SignalP 6.0",
            "TargetP 2.0",
            lambda item: item.classification.upper() not in {"", "OTHER", "NO_SP", "RESULT RETURNED"},
            lambda item: item.classification.upper() == "SP",
        )
    )
    lines.append(
        pair_line(
            "跨膜结构",
            "TMHMM 2.0",
            "DeepTMHMM 1.0",
            lambda item: item.classification.casefold() == "tm protein" or bool(item.regions),
            lambda item: "TM" in item.classification.upper() or any("TM" in region.region_type.upper() for region in item.regions),
        )
    )
    lines.append(
        pair_line(
            "核定位信号",
            "cNLS Mapper",
            "NLStradamus 1.8",
            lambda item: "detected" in item.classification.casefold() or bool(item.regions),
            lambda item: "detected" in item.classification.casefold() or bool(item.regions),
        )
    )
    lines.append("上述一致性仅指计算工具间的相互支持，不构成实验定位结论。")
    return lines


def safe_file_stem(value: str, fallback: str = "analysis") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned[:80] or fallback


__all__ = [
    "AnalysisBundle",
    "CDS",
    "CDS_FASTA_PATH",
    "FIVE_UTR",
    "GENE_FASTA_PATH",
    "GENOMIC",
    "PROMOTER",
    "PROTEIN",
    "PredictionExecution",
    "PredictionProviderAttempt",
    "PredictionRegion",
    "PredictionResult",
    "SEQUENCE_TYPES",
    "SequenceRecord",
    "THREE_UTR",
    "TRANSCRIPT_FASTA_PATH",
    "build_reference_sequence_indexes",
    "deduplicate_sequence_records",
    "exact_reference_matches",
    "normalize_cds",
    "normalize_protein",
    "parse_fasta_or_sequence",
    "prediction_consistency",
    "safe_file_stem",
    "sequence_records_to_fasta",
    "transcript_to_gene",
    "translate_cds",
]
