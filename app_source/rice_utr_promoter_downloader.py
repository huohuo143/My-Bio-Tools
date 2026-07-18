"""Download rice 5' UTR, 3' UTR and promoter sequences from Ensembl REST."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import io
import re
import threading
import time
from urllib.parse import quote, urlencode
import zipfile

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import streamlit as st
from urllib3.util.retry import Retry

from app_ui import format_bytes, page_header, tool_website
from RAP_MSU_convert import MAPPING_PATH, load_mapping_index


ENSEMBL_REST_URL = "https://rest.ensembl.org"
SPECIES = "oryza_sativa"
EXPECTED_ASSEMBLY = "IRGSP-1.0"
MAX_BATCH_SIZE = 50
PROMOTER_MIN_BP = 500
PROMOTER_MAX_BP = 4000
PROMOTER_DEFAULT_BP = 2000
RESULT_CACHE_SECONDS = 900

FIVE_UTR = "5′ UTR"
THREE_UTR = "3′ UTR"
PROMOTER = "Promoter"
SEQUENCE_TYPES = (FIVE_UTR, THREE_UTR, PROMOTER)
TRANSCRIPT_SCOPE_ALL = "全部 transcript"
TRANSCRIPT_SCOPE_CANONICAL = "仅 canonical transcript"

RAP_GENE_PATTERN = re.compile(r"^Os(?P<chromosome>0[1-9]|1[0-2])g(?P<number>\d{7})$", re.IGNORECASE)
RAP_TRANSCRIPT_PATTERN = re.compile(
    r"^Os(?P<chromosome>0[1-9]|1[0-2])t(?P<number>\d{7})(?P<isoform>-\d+)?$",
    re.IGNORECASE,
)
MSU_PATTERN = re.compile(
    r"^LOC_Os(?P<chromosome>0[1-9]|1[0-2])g(?P<number>\d{5})(?P<isoform>\.\d+)?$",
    re.IGNORECASE,
)
DNA_ALPHABET = set("ACGTRYSWKMBDHVN")

SUMMARY_COLUMNS = [
    "input_id",
    "input_type",
    "id_resolution_status",
    "mapping_count",
    "resolved_rap_gene",
    "requested_rap_transcript",
    "rap_transcript",
    "gene_status",
    "transcript_status",
    "assembly",
    "annotation_source",
    "chromosome",
    "gene_start",
    "gene_end",
    "strand",
    "promoter_requested_bp",
    "promoter_start",
    "promoter_end",
    "promoter_actual_bp",
    "five_utr_length_nt",
    "three_utr_length_nt",
    "cdna_length_nt",
    "validation_note",
    "error",
    "gene_lookup_url",
    "promoter_source_url",
    "cdna_source_url",
]


@dataclass(frozen=True)
class ResolvedTarget:
    input_id: str
    input_type: str
    rap_gene_id: str = ""
    requested_transcript_id: str = ""
    mapping_count: int = 0
    status: str = "invalid_id"
    note: str = ""
    error: str = ""

    @property
    def is_resolved(self) -> bool:
        return bool(self.rap_gene_id) and self.status in {"matched", "mapped_one_to_many"}


@dataclass
class TranscriptUTRRecord:
    transcript_id: str
    five_utr_sequence: str = ""
    three_utr_sequence: str = ""
    cdna_length: int | None = None
    status: str = "failed"
    validation_note: str = ""
    error: str = ""
    source_url: str = ""


@dataclass
class GeneSequencePayload:
    rap_gene_id: str
    assembly: str = ""
    annotation_source: str = ""
    chromosome: str = ""
    gene_start: int | None = None
    gene_end: int | None = None
    strand: int | None = None
    promoter_requested_bp: int = 0
    promoter_start: int | None = None
    promoter_end: int | None = None
    promoter_sequence: str = ""
    transcripts: list[TranscriptUTRRecord] = field(default_factory=list)
    status: str = "failed"
    validation_note: str = ""
    error: str = ""
    gene_lookup_url: str = ""
    promoter_source_url: str = ""

    @property
    def has_sequence(self) -> bool:
        return bool(
            self.promoter_sequence
            or any(item.five_utr_sequence or item.three_utr_sequence for item in self.transcripts)
        )


@dataclass
class RiceSequenceResult:
    target: ResolvedTarget
    payload: GeneSequencePayload | None = None

    @property
    def has_sequence(self) -> bool:
        return self.payload is not None and self.payload.has_sequence


_thread_local = threading.local()
_cache_lock = threading.Lock()
_payload_cache: dict[tuple[object, ...], tuple[float, GeneSequencePayload]] = {}
_assembly_cache: tuple[float, str, dict[str, int], str] | None = None


def parse_input_ids(text: str) -> list[str]:
    """Parse common delimiters, deduplicate case-insensitively and preserve order."""
    seen: set[str] = set()
    identifiers: list[str] = []
    for raw in re.split(r"[\s,;，；]+", text.strip()):
        value = raw.strip()
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            identifiers.append(value)
    return identifiers


def canonicalize_rap_gene(identifier: str) -> str | None:
    match = RAP_GENE_PATTERN.fullmatch(identifier.strip())
    if match is None:
        return None
    return f"Os{match.group('chromosome')}g{match.group('number')}"


def canonicalize_rap_transcript(identifier: str) -> str | None:
    match = RAP_TRANSCRIPT_PATTERN.fullmatch(identifier.strip())
    if match is None:
        return None
    return (
        f"Os{match.group('chromosome')}t{match.group('number')}"
        f"{match.group('isoform') or ''}"
    )


def canonicalize_msu(identifier: str) -> str | None:
    match = MSU_PATTERN.fullmatch(identifier.strip())
    if match is None:
        return None
    return (
        f"LOC_Os{match.group('chromosome')}g{match.group('number')}"
        f"{match.group('isoform') or ''}"
    )


def rap_gene_from_transcript(transcript_id: str) -> str:
    return transcript_id.replace("t", "g", 1).split("-", 1)[0]


def resolve_input_ids(
    identifiers: list[str],
    msu_to_rap: dict[str, tuple[str, ...]],
) -> list[ResolvedTarget]:
    """Resolve mixed RAP/MSU IDs without hiding one-to-many mappings."""
    msu_lookup = {key.casefold(): tuple(values) for key, values in msu_to_rap.items()}
    resolved: list[ResolvedTarget] = []
    for identifier in identifiers:
        rap_gene = canonicalize_rap_gene(identifier)
        if rap_gene:
            resolved.append(
                ResolvedTarget(
                    input_id=identifier,
                    input_type="RAP gene",
                    rap_gene_id=rap_gene,
                    mapping_count=1,
                    status="matched",
                )
            )
            continue

        rap_transcript = canonicalize_rap_transcript(identifier)
        if rap_transcript:
            resolved.append(
                ResolvedTarget(
                    input_id=identifier,
                    input_type="RAP transcript",
                    rap_gene_id=rap_gene_from_transcript(rap_transcript),
                    requested_transcript_id=rap_transcript,
                    mapping_count=1,
                    status="matched",
                    note="按指定 RAP transcript 提取 UTR；启动子仍按所属 RAP gene 的 5′端定义。",
                )
            )
            continue

        msu_id = canonicalize_msu(identifier)
        if msu_id:
            base = msu_id.split(".", 1)[0]
            mapped = msu_lookup.get(msu_id.casefold())
            if mapped is None:
                mapped = msu_lookup.get(base.casefold(), ())
            rap_ids = tuple(
                item for item in (canonicalize_rap_gene(value) for value in mapped) if item
            )
            if not rap_ids:
                resolved.append(
                    ResolvedTarget(
                        input_id=identifier,
                        input_type="MSU",
                        mapping_count=0,
                        status="not_mapped",
                        error="内置 RAP–MSU 对照表未找到对应 RAP gene。",
                    )
                )
                continue
            mapping_status = "mapped_one_to_many" if len(rap_ids) > 1 else "matched"
            note = (
                f"MSU ID 映射到 {len(rap_ids)} 个 RAP gene，已分别输出并保留映射关系。"
                if len(rap_ids) > 1
                else "MSU ID 已通过内置对照表映射到 RAP gene；输出采用 RAP/IRGSP-1.0 注释。"
            )
            for mapped_rap in rap_ids:
                resolved.append(
                    ResolvedTarget(
                        input_id=identifier,
                        input_type="MSU",
                        rap_gene_id=mapped_rap,
                        mapping_count=len(rap_ids),
                        status=mapping_status,
                        note=note,
                    )
                )
            continue

        resolved.append(
            ResolvedTarget(
                input_id=identifier,
                input_type="Unknown",
                status="invalid_id",
                error="ID 格式未知；支持 RAP gene/transcript 与 MSU LOC_Os locus/model ID。",
            )
        )
    return resolved


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "MyBioTools/1.4 (+local rice sequence utility)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return session


def get_session() -> requests.Session:
    session = getattr(_thread_local, "ensembl_session", None)
    if session is None:
        session = create_session()
        _thread_local.ensembl_session = session
    return session


def _json_get(
    path: str,
    params: dict[str, object] | None = None,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 30.0),
) -> tuple[dict[str, object], str]:
    url = f"{ENSEMBL_REST_URL}{path}"
    response = (session or get_session()).get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Ensembl REST 返回的不是 JSON object。")
    query = urlencode(params or {})
    source = f"{url}?{query}" if query else url
    return payload, source


def fetch_assembly_metadata(
    session: requests.Session | None = None,
) -> tuple[str, dict[str, int], str]:
    """Fetch chromosome lengths once so promoter coordinates are clipped safely."""
    global _assembly_cache
    now = time.monotonic()
    with _cache_lock:
        cached = _assembly_cache
        if cached is not None and now - cached[0] <= RESULT_CACHE_SECONDS:
            return cached[1], copy.deepcopy(cached[2]), cached[3]
    payload, source = _json_get(
        f"/info/assembly/{SPECIES}",
        params={"bands": 0},
        session=session,
    )
    assembly = str(payload.get("default_coord_system_version") or payload.get("assembly_name") or "")
    lengths: dict[str, int] = {}
    for region in payload.get("top_level_region", []):
        if not isinstance(region, dict) or region.get("coord_system") != "chromosome":
            continue
        name = str(region.get("name") or "")
        length = region.get("length")
        if name and isinstance(length, int) and length > 0:
            lengths[name] = length
    if not lengths:
        raise ValueError("Ensembl REST 未返回 IRGSP 染色体长度。")
    with _cache_lock:
        _assembly_cache = (now, assembly, copy.deepcopy(lengths), source)
    return assembly, lengths, source


def promoter_region(
    chromosome: str,
    gene_start: int,
    gene_end: int,
    strand: int,
    promoter_length: int,
    chromosome_lengths: dict[str, int] | None = None,
) -> tuple[int, int, int, str]:
    """Return clipped genomic coordinates and sequence strand for a gene promoter."""
    if promoter_length < PROMOTER_MIN_BP or promoter_length > PROMOTER_MAX_BP:
        raise ValueError(f"启动子长度必须在 {PROMOTER_MIN_BP}–{PROMOTER_MAX_BP} bp。")
    if strand not in {1, -1}:
        raise ValueError("基因链方向必须为 1 或 -1。")
    chromosome_length = (chromosome_lengths or {}).get(chromosome)
    if strand == 1:
        start = max(1, gene_start - promoter_length)
        end = gene_start - 1
    else:
        start = gene_end + 1
        end = gene_end + promoter_length
        if chromosome_length:
            end = min(end, chromosome_length)
    if start > end:
        raise ValueError("该基因位于染色体边界，无法取得所请求的上游区段。")
    region = f"{chromosome}:{start}..{end}:{strand}"
    return start, end, strand, region


def _feature_length(features: list[dict[str, object]], feature_type: str) -> int:
    total = 0
    for feature in features:
        normalized = str(feature.get("type") or feature.get("object_type") or "").casefold()
        if normalized.replace("-", "_") != feature_type:
            continue
        start = feature.get("start")
        end = feature.get("end")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            total += end - start + 1
    return total


def extract_utr_sequences(
    transcript: dict[str, object],
    cdna_sequence: str,
) -> tuple[str, str, str]:
    """Slice transcript-oriented cDNA using Ensembl UTR feature lengths."""
    sequence = re.sub(r"\s+", "", cdna_sequence).upper()
    invalid = sorted(set(sequence) - DNA_ALPHABET)
    if invalid:
        raise ValueError("cDNA 含非常规字符：" + "".join(invalid))
    features = [item for item in transcript.get("UTR", []) if isinstance(item, dict)]
    five_length = _feature_length(features, "five_prime_utr")
    three_length = _feature_length(features, "three_prime_utr")
    if five_length + three_length > len(sequence):
        raise ValueError("UTR 注释总长度超过 cDNA 长度。")

    exons = [item for item in transcript.get("Exon", []) if isinstance(item, dict)]
    exon_length = sum(
        int(item["end"]) - int(item["start"]) + 1
        for item in exons
        if isinstance(item.get("start"), int)
        and isinstance(item.get("end"), int)
        and int(item["end"]) >= int(item["start"])
    )
    notes: list[str] = []
    if exon_length and exon_length != len(sequence):
        notes.append(f"cDNA 长度 {len(sequence)} 与 exon 总长度 {exon_length} 不一致")
    if five_length == 0:
        notes.append("该 transcript 未注释 5′UTR")
    if three_length == 0:
        notes.append("该 transcript 未注释 3′UTR")

    five_utr = sequence[:five_length] if five_length else ""
    three_utr = sequence[-three_length:] if three_length else ""
    return five_utr, three_utr, "；".join(notes)


def _select_transcripts(
    gene_payload: dict[str, object],
    requested_transcript_id: str,
    transcript_scope: str,
) -> list[dict[str, object]]:
    transcripts = [
        item for item in gene_payload.get("Transcript", []) if isinstance(item, dict) and item.get("id")
    ]
    transcripts.sort(key=lambda item: str(item.get("id")))
    if requested_transcript_id:
        return [
            item
            for item in transcripts
            if str(item.get("id", "")).casefold() == requested_transcript_id.casefold()
        ]
    if transcript_scope == TRANSCRIPT_SCOPE_CANONICAL and transcripts:
        canonical = str(gene_payload.get("canonical_transcript") or "").split(".", 1)[0]
        for item in transcripts:
            if str(item.get("id", "")).casefold() == canonical.casefold():
                return [item]
        for item in transcripts:
            if item.get("is_canonical"):
                return [item]
        return [transcripts[0]]
    return transcripts


def fetch_selected_transcript_ids(
    rap_gene_id: str,
    requested_transcript_id: str,
    transcript_scope: str,
    session: requests.Session | None = None,
) -> tuple[list[str], str]:
    """Resolve the requested/canonical/all transcript IDs without downloading sequence."""
    gene, lookup_url = _json_get(
        f"/lookup/id/{quote(rap_gene_id, safe='')}",
        params={"expand": 1, "utr": 1},
        session=session,
    )
    assembly = str(gene.get("assembly_name") or "")
    if assembly != EXPECTED_ASSEMBLY:
        raise ValueError(
            f"注释版本为 {assembly or '未知'}，预期 {EXPECTED_ASSEMBLY}；已停止混用 transcript。"
        )
    selected = _select_transcripts(gene, requested_transcript_id, transcript_scope)
    return [str(item.get("id") or "") for item in selected if item.get("id")], lookup_url


def fetch_gene_payload(
    rap_gene_id: str,
    requested_transcript_id: str,
    transcript_scope: str,
    selected_types: tuple[str, ...],
    promoter_length: int,
    chromosome_lengths: dict[str, int] | None = None,
    session: requests.Session | None = None,
) -> GeneSequencePayload:
    """Fetch one RAP gene and derive requested transcript- and gene-level sequences."""
    payload = GeneSequencePayload(
        rap_gene_id=rap_gene_id,
        promoter_requested_bp=promoter_length if PROMOTER in selected_types else 0,
    )
    errors: list[str] = []
    notes: list[str] = []
    try:
        gene, lookup_url = _json_get(
            f"/lookup/id/{quote(rap_gene_id, safe='')}",
            params={"expand": 1, "utr": 1},
            session=session,
        )
        payload.gene_lookup_url = lookup_url
        if str(gene.get("object_type") or "").casefold() != "gene":
            raise ValueError("返回对象不是 Gene。")
        payload.assembly = str(gene.get("assembly_name") or "")
        payload.annotation_source = str(gene.get("source") or gene.get("logic_name") or "")
        payload.chromosome = str(gene.get("seq_region_name") or "")
        payload.gene_start = int(gene["start"])
        payload.gene_end = int(gene["end"])
        payload.strand = int(gene["strand"])
        if payload.assembly != EXPECTED_ASSEMBLY:
            raise ValueError(
                f"注释版本为 {payload.assembly or '未知'}，预期 {EXPECTED_ASSEMBLY}；已停止混用坐标。"
            )
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", "")
        payload.error = f"Ensembl 未找到或拒绝该 RAP gene（HTTP {status_code}）。"
        payload.status = "not_found" if status_code == 400 else "request_failed"
        return payload
    except requests.RequestException as exc:
        payload.error = f"基因信息请求失败：{type(exc).__name__}: {exc}"
        payload.status = "request_failed"
        return payload
    except Exception as exc:
        payload.error = f"基因信息解析失败：{type(exc).__name__}: {exc}"
        payload.status = "parse_failed"
        return payload

    if PROMOTER in selected_types:
        try:
            start, end, sequence_strand, region = promoter_region(
                payload.chromosome,
                int(payload.gene_start),
                int(payload.gene_end),
                int(payload.strand),
                promoter_length,
                chromosome_lengths,
            )
            region_payload, source = _json_get(
                f"/sequence/region/{SPECIES}/{quote(region, safe=':.-')}",
                params={"coord_system_version": EXPECTED_ASSEMBLY},
                session=session,
            )
            sequence = re.sub(r"\s+", "", str(region_payload.get("seq") or "")).upper()
            expected_length = end - start + 1
            if len(sequence) != expected_length:
                raise ValueError(
                    f"启动子实际长度 {len(sequence)} 与请求坐标长度 {expected_length} 不一致。"
                )
            invalid = sorted(set(sequence) - DNA_ALPHABET)
            if invalid:
                raise ValueError("启动子序列含非常规字符：" + "".join(invalid))
            payload.promoter_start = start
            payload.promoter_end = end
            payload.promoter_sequence = sequence
            payload.promoter_source_url = source
            if len(sequence) < promoter_length:
                notes.append(
                    f"启动子因染色体边界由 {promoter_length} bp 截短为 {len(sequence)} bp"
                )
            if sequence_strand == -1:
                notes.append("负链启动子已反向互补为 gene 5′→3′方向")
        except requests.RequestException as exc:
            errors.append(f"启动子请求失败：{type(exc).__name__}: {exc}")
        except Exception as exc:
            errors.append(f"启动子提取失败：{type(exc).__name__}: {exc}")

    if FIVE_UTR in selected_types or THREE_UTR in selected_types:
        transcripts = _select_transcripts(gene, requested_transcript_id, transcript_scope)
        if requested_transcript_id and not transcripts:
            errors.append(f"所属 RAP gene 中未找到指定 transcript：{requested_transcript_id}")
        elif not transcripts:
            errors.append("该 RAP gene 没有可用 transcript 注释。")
        for transcript in transcripts:
            transcript_id = str(transcript.get("id") or "")
            record = TranscriptUTRRecord(transcript_id=transcript_id)
            try:
                cdna_payload, source = _json_get(
                    f"/sequence/id/{quote(transcript_id, safe='')}",
                    params={"type": "cdna"},
                    session=session,
                )
                cdna = str(cdna_payload.get("seq") or "")
                record.source_url = source
                record.cdna_length = len(re.sub(r"\s+", "", cdna))
                (
                    record.five_utr_sequence,
                    record.three_utr_sequence,
                    record.validation_note,
                ) = extract_utr_sequences(transcript, cdna)
                record.status = "matched"
            except requests.HTTPError as exc:
                status_code = getattr(exc.response, "status_code", "")
                record.status = "not_found"
                record.error = f"cDNA 请求失败（HTTP {status_code}）。"
            except requests.RequestException as exc:
                record.status = "request_failed"
                record.error = f"cDNA 请求失败：{type(exc).__name__}: {exc}"
            except Exception as exc:
                record.status = "parse_failed"
                record.error = f"UTR 提取失败：{type(exc).__name__}: {exc}"
            payload.transcripts.append(record)
            if record.error:
                errors.append(f"{transcript_id}: {record.error}")

    requested_parts = 0
    successful_parts = 0
    if PROMOTER in selected_types:
        requested_parts += 1
        successful_parts += int(bool(payload.promoter_sequence))
    if FIVE_UTR in selected_types or THREE_UTR in selected_types:
        requested_parts += max(1, len(payload.transcripts))
        successful_parts += sum(item.status == "matched" for item in payload.transcripts)
    if successful_parts == requested_parts and not errors:
        payload.status = "matched"
    elif successful_parts > 0:
        payload.status = "partial"
    else:
        payload.status = "failed"
    payload.validation_note = "；".join(notes)
    payload.error = "；".join(errors)
    return payload


def cached_fetch_gene_payload(
    target: ResolvedTarget,
    transcript_scope: str,
    selected_types: tuple[str, ...],
    promoter_length: int,
    chromosome_lengths: dict[str, int],
) -> GeneSequencePayload:
    cache_key = (
        target.rap_gene_id,
        target.requested_transcript_id,
        transcript_scope,
        selected_types,
        promoter_length,
    )
    now = time.monotonic()
    with _cache_lock:
        cached = _payload_cache.get(cache_key)
        if cached is not None and now - cached[0] <= RESULT_CACHE_SECONDS:
            return copy.deepcopy(cached[1])
    payload = fetch_gene_payload(
        target.rap_gene_id,
        target.requested_transcript_id,
        transcript_scope,
        selected_types,
        promoter_length,
        chromosome_lengths=chromosome_lengths,
    )
    if payload.status in {"matched", "partial", "not_found"}:
        with _cache_lock:
            _payload_cache[cache_key] = (now, copy.deepcopy(payload))
    return payload


def batch_fetch_sequences(
    targets: list[ResolvedTarget],
    transcript_scope: str,
    selected_types: tuple[str, ...],
    promoter_length: int,
    chromosome_lengths: dict[str, int],
    max_workers: int = 3,
) -> list[RiceSequenceResult]:
    if len({target.input_id.casefold() for target in targets}) > MAX_BATCH_SIZE:
        raise ValueError(f"单次最多处理 {MAX_BATCH_SIZE} 个输入 ID。")
    results: list[RiceSequenceResult | None] = [None] * len(targets)
    jobs = [(index, target) for index, target in enumerate(targets) if target.is_resolved]
    for index, target in enumerate(targets):
        if not target.is_resolved:
            results[index] = RiceSequenceResult(target=target)
    workers = max(1, min(int(max_workers), 4, len(jobs) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                cached_fetch_gene_payload,
                target,
                transcript_scope,
                selected_types,
                promoter_length,
                chromosome_lengths,
            ): (index, target)
            for index, target in jobs
        }
        for future in as_completed(futures):
            index, target = futures[future]
            try:
                results[index] = RiceSequenceResult(target=target, payload=future.result())
            except Exception as exc:
                results[index] = RiceSequenceResult(
                    target=target,
                    payload=GeneSequencePayload(
                        rap_gene_id=target.rap_gene_id,
                        status="failed",
                        error=f"批量任务失败：{type(exc).__name__}: {exc}",
                    ),
                )
    return [item for item in results if item is not None]


def _combined_note(target: ResolvedTarget, payload: GeneSequencePayload | None, transcript: TranscriptUTRRecord | None) -> str:
    values = [target.note]
    if payload is not None:
        values.append(payload.validation_note)
    if transcript is not None:
        values.append(transcript.validation_note)
    return "；".join(value for value in values if value)


def _combined_error(target: ResolvedTarget, payload: GeneSequencePayload | None, transcript: TranscriptUTRRecord | None) -> str:
    values = [target.error]
    if payload is not None:
        values.append(payload.error)
    if transcript is not None:
        values.append(transcript.error)
    return "；".join(dict.fromkeys(value for value in values if value))


def summary_frame(results: list[RiceSequenceResult]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for result in results:
        target = result.target
        payload = result.payload
        transcripts: list[TranscriptUTRRecord | None] = (
            list(payload.transcripts) if payload is not None and payload.transcripts else [None]
        )
        for transcript in transcripts:
            rows.append(
                {
                    "input_id": target.input_id,
                    "input_type": target.input_type,
                    "id_resolution_status": target.status,
                    "mapping_count": target.mapping_count,
                    "resolved_rap_gene": target.rap_gene_id,
                    "requested_rap_transcript": target.requested_transcript_id,
                    "rap_transcript": transcript.transcript_id if transcript else "",
                    "gene_status": payload.status if payload else target.status,
                    "transcript_status": transcript.status if transcript else "",
                    "assembly": payload.assembly if payload else "",
                    "annotation_source": payload.annotation_source if payload else "",
                    "chromosome": payload.chromosome if payload else "",
                    "gene_start": payload.gene_start if payload else None,
                    "gene_end": payload.gene_end if payload else None,
                    "strand": "+" if payload and payload.strand == 1 else "-" if payload and payload.strand == -1 else "",
                    "promoter_requested_bp": payload.promoter_requested_bp if payload else None,
                    "promoter_start": payload.promoter_start if payload else None,
                    "promoter_end": payload.promoter_end if payload else None,
                    "promoter_actual_bp": len(payload.promoter_sequence) if payload and payload.promoter_sequence else None,
                    "five_utr_length_nt": len(transcript.five_utr_sequence) if transcript else None,
                    "three_utr_length_nt": len(transcript.three_utr_sequence) if transcript else None,
                    "cdna_length_nt": transcript.cdna_length if transcript else None,
                    "validation_note": _combined_note(target, payload, transcript),
                    "error": _combined_error(target, payload, transcript),
                    "gene_lookup_url": payload.gene_lookup_url if payload else "",
                    "promoter_source_url": payload.promoter_source_url if payload else "",
                    "cdna_source_url": transcript.source_url if transcript else "",
                }
            )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def _write_fasta_record(output: io.StringIO, header: str, sequence: str) -> None:
    output.write(f">{header}\n")
    for index in range(0, len(sequence), 60):
        output.write(sequence[index:index + 60] + "\n")


def format_fasta(results: list[RiceSequenceResult], sequence_type: str) -> str:
    if sequence_type not in SEQUENCE_TYPES:
        raise ValueError(f"未知序列类型：{sequence_type}")
    output = io.StringIO()
    seen: set[tuple[str, str]] = set()
    for result in results:
        target = result.target
        payload = result.payload
        if payload is None:
            continue
        if sequence_type == PROMOTER:
            sequence = payload.promoter_sequence
            if not sequence:
                continue
            header = (
                f"{payload.rap_gene_id}|input={target.input_id}|type=promoter|"
                f"requested={payload.promoter_requested_bp}bp|length={len(sequence)}|"
                f"assembly={payload.assembly}|chr={payload.chromosome}:"
                f"{payload.promoter_start}-{payload.promoter_end}|"
                f"strand={'+' if payload.strand == 1 else '-'}|orientation=gene_5to3"
            )
            key = (header, sequence)
            if key not in seen:
                seen.add(key)
                _write_fasta_record(output, header, sequence)
            continue

        for transcript in payload.transcripts:
            sequence = (
                transcript.five_utr_sequence if sequence_type == FIVE_UTR else transcript.three_utr_sequence
            )
            if not sequence:
                continue
            type_label = "5UTR" if sequence_type == FIVE_UTR else "3UTR"
            header = (
                f"{transcript.transcript_id}|gene={payload.rap_gene_id}|input={target.input_id}|"
                f"type={type_label}|length={len(sequence)}|assembly={payload.assembly}|"
                "orientation=transcript_5to3"
            )
            key = (header, sequence)
            if key not in seen:
                seen.add(key)
                _write_fasta_record(output, header, sequence)
    return output.getvalue()


def output_filename(sequence_type: str, promoter_length: int) -> str:
    if sequence_type == FIVE_UTR:
        return "rice_5UTR_sequences.fasta"
    if sequence_type == THREE_UTR:
        return "rice_3UTR_sequences.fasta"
    if sequence_type == PROMOTER:
        return f"rice_promoter_{promoter_length}bp_sequences.fasta"
    raise ValueError(f"未知序列类型：{sequence_type}")


def build_download_zip(
    results: list[RiceSequenceResult],
    selected_types: tuple[str, ...],
    promoter_length: int,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for sequence_type in selected_types:
            archive.writestr(
                output_filename(sequence_type, promoter_length),
                format_fasta(results, sequence_type).encode("utf-8"),
            )
        archive.writestr(
            "rice_utr_promoter_summary.csv",
            summary_frame(results).to_csv(index=False).encode("utf-8-sig"),
        )
        manifest = (
            "My Bio Tools - rice UTR and promoter sequence download\n"
            f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
            f"Sequence source: {ENSEMBL_REST_URL}\n"
            f"Species: {SPECIES}\n"
            f"Required assembly: {EXPECTED_ASSEMBLY}\n"
            "Annotation source is recorded per result in rice_utr_promoter_summary.csv.\n"
            f"RAP-MSU mapping: {MAPPING_PATH.name}\n"
            f"Selected sequence types: {', '.join(selected_types)}\n"
            f"Promoter length: {promoter_length if PROMOTER in selected_types else 'not requested'}\n"
            "UTRs are transcript-specific and follow transcript 5'-to-3' orientation.\n"
            "Promoters are immediately upstream of the RAP gene boundary/TSS and follow gene 5'-to-3' orientation.\n"
            "Promoter regions are not masked and may overlap neighboring annotated features.\n"
            "MSU IDs are explicitly mapped to RAP genes; one-to-many mappings are retained.\n"
        )
        archive.writestr("README.txt", manifest.encode("utf-8"))
    return output.getvalue()


def run() -> None:
    page_header(
        "IRGSP-1.0 / Ensembl Plants",
        "水稻 UTR 与启动子序列下载",
        "混合输入 RAP 或 MSU ID，批量下载 transcript 特异的 5′UTR、3′UTR，以及 500–4000 bp 可调的 gene 上游启动子序列。",
        ["RAP + MSU", "5′UTR / 3′UTR", "Promoter 500–4000 bp"],
    )
    tool_website(__name__)
    st.info(
        "该工具会联网访问 Ensembl REST。MSU ID 先用内置 RAP–MSU 对照表映射；"
        "UTR 按 RAP transcript 输出，启动子按 RAP gene 5′端上游定义，负链自动反向互补。"
    )
    st.warning(
        "版本边界：本页采用 IRGSP-1.0 兼容坐标，以保持 RAP/MSU ID 可追溯。"
        "它不是 NCBI 当前的 ASM3414082v1 坐标；两套组装及注释不会在本页静默混用。"
    )
    identifiers_text = st.text_area(
        "RAP / MSU 基因 ID（可混合输入）",
        height=180,
        placeholder="Os01g0100100\nOs01t0100100-01\nLOC_Os01g01010.1",
        help="支持 RAP gene、RAP transcript、MSU locus/model；逗号、分号、空格或换行均可分隔。",
    )
    selected_types_list = st.multiselect(
        "下载序列类型",
        list(SEQUENCE_TYPES),
        default=list(SEQUENCE_TYPES),
    )
    selected_types = tuple(selected_types_list)
    promoter_length = st.slider(
        "启动子长度（bp）",
        min_value=PROMOTER_MIN_BP,
        max_value=PROMOTER_MAX_BP,
        value=PROMOTER_DEFAULT_BP,
        step=100,
        disabled=PROMOTER not in selected_types,
        help="定义为 RAP gene 5′端紧邻上游序列；不自动排除相邻基因重叠区。",
    )
    transcript_scope = st.radio(
        "RAP gene / MSU 输入时的 transcript 范围",
        [TRANSCRIPT_SCOPE_ALL, TRANSCRIPT_SCOPE_CANONICAL],
        horizontal=True,
        disabled=FIVE_UTR not in selected_types and THREE_UTR not in selected_types,
        help="若直接输入 RAP transcript ID，则始终只提取指定 transcript。",
    )
    st.caption(
        "全部 transcript：分别输出该基因每个已注释转录本的 5′/3′UTR，适合查看可变剪接；"
        "仅 canonical transcript：只输出数据库指定的代表性转录本，结果更简洁、请求更少。"
        "两者的启动子相同；直接输入 RAP transcript ID 时此选项不生效。"
    )
    max_workers = st.slider(
        "并发请求数",
        min_value=1,
        max_value=4,
        value=3,
        help="默认 3 个并发；公共 API 短时繁忙时可降为 1。",
    )

    if not st.button("获取并生成下载文件", type="primary"):
        return
    identifiers = parse_input_ids(identifiers_text)
    if not identifiers:
        st.error("请提供至少一个 RAP 或 MSU ID。")
        return
    if len(identifiers) > MAX_BATCH_SIZE:
        st.error(f"本次输入 {len(identifiers)} 个 ID；单次最多处理 {MAX_BATCH_SIZE} 个。")
        return
    if not selected_types:
        st.error("请至少选择一种序列类型。")
        return
    if not MAPPING_PATH.is_file() and any(canonicalize_msu(item) for item in identifiers):
        st.error(f"内置 RAP–MSU 对照表缺失：{MAPPING_PATH.name}")
        return

    with st.spinner("正在解析 ID 并获取 IRGSP-1.0 注释与序列…"):
        _, msu_to_rap = load_mapping_index() if MAPPING_PATH.is_file() else ({}, {})
        targets = resolve_input_ids(identifiers, msu_to_rap)
        chromosome_lengths: dict[str, int] = {}
        assembly_warning = ""
        if PROMOTER in selected_types:
            try:
                assembly, chromosome_lengths, _ = fetch_assembly_metadata()
                if assembly != EXPECTED_ASSEMBLY:
                    st.error(
                        f"Ensembl 当前默认组装为 {assembly or '未知'}，预期 {EXPECTED_ASSEMBLY}；已停止以避免坐标混用。"
                    )
                    return
            except Exception as exc:
                assembly_warning = (
                    f"染色体长度元数据获取失败，将由序列接口直接校验边界：{type(exc).__name__}: {exc}"
                )
        started = time.perf_counter()
        results = batch_fetch_sequences(
            targets,
            transcript_scope,
            selected_types,
            promoter_length,
            chromosome_lengths,
            max_workers=max_workers,
        )
        elapsed = time.perf_counter() - started

    summary = summary_frame(results)
    complete = sum(
        result.payload is not None and result.payload.status == "matched" for result in results
    )
    partial = sum(
        result.payload is not None and result.payload.status == "partial" for result in results
    )
    unresolved = sum(not result.target.is_resolved for result in results)
    failed = len(results) - complete - partial - unresolved
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("输入 ID", f"{len(identifiers):,}")
    m2.metric("完整成功", f"{complete:,}")
    m3.metric("部分成功", f"{partial:,}")
    m4.metric("失败/未映射", f"{failed + unresolved:,}")
    m5.metric("处理时间", f"{elapsed:.2f} s")
    st.dataframe(summary, width="stretch", hide_index=True)

    if assembly_warning:
        st.warning(assembly_warning)
    one_to_many = sum(target.status == "mapped_one_to_many" for target in targets)
    if one_to_many:
        st.warning("存在 MSU → RAP 一对多映射；结果已分别输出，请结合 summary CSV 复核。")

    available_types = {
        sequence_type: format_fasta(results, sequence_type)
        for sequence_type in selected_types
    }
    available_types = {key: value for key, value in available_types.items() if value}
    if available_types:
        zip_bytes = build_download_zip(results, selected_types, promoter_length)
        st.success(
            "序列已按 IRGSP-1.0 坐标生成；FASTA header 与汇总表保留输入 ID、RAP 映射、链方向和来源。"
        )
        st.download_button(
            f"下载 UTR / 启动子结果 ZIP（{format_bytes(len(zip_bytes))}）",
            zip_bytes,
            file_name="rice_utr_promoter_download.zip",
            mime="application/zip",
            type="primary",
        )
        columns = st.columns(len(available_types))
        for column, (sequence_type, fasta) in zip(columns, available_types.items()):
            column.download_button(
                f"下载 {sequence_type} FASTA",
                fasta.encode("utf-8"),
                file_name=output_filename(sequence_type, promoter_length),
                mime="text/plain",
            )
        with st.expander("预览首批 FASTA"):
            for sequence_type, fasta in available_types.items():
                st.markdown(f"**{sequence_type}**")
                st.code("\n".join(fasta.splitlines()[:10]), language=None)
    else:
        st.warning("没有生成可下载序列；请根据 summary 中的映射状态、错误与来源 URL 检查。")

    st.download_button(
        "下载任务汇总 CSV",
        summary.to_csv(index=False).encode("utf-8-sig"),
        file_name="rice_utr_promoter_summary.csv",
        mime="text/csv",
    )
    st.caption(
        "说明：UTR 是否存在取决于具体 transcript 注释；summary 会记录实际 annotation_source。"
        "启动子未做重复序列屏蔽，也不会自动排除相邻基因或其他功能元件重叠。"
    )


__all__ = [
    "ENSEMBL_REST_URL",
    "EXPECTED_ASSEMBLY",
    "FIVE_UTR",
    "GeneSequencePayload",
    "MAX_BATCH_SIZE",
    "PROMOTER",
    "ResolvedTarget",
    "RiceSequenceResult",
    "SEQUENCE_TYPES",
    "THREE_UTR",
    "TRANSCRIPT_SCOPE_ALL",
    "TRANSCRIPT_SCOPE_CANONICAL",
    "TranscriptUTRRecord",
    "batch_fetch_sequences",
    "build_download_zip",
    "canonicalize_msu",
    "canonicalize_rap_gene",
    "canonicalize_rap_transcript",
    "extract_utr_sequences",
    "fetch_assembly_metadata",
    "fetch_gene_payload",
    "fetch_selected_transcript_ids",
    "format_fasta",
    "output_filename",
    "parse_input_ids",
    "promoter_region",
    "resolve_input_ids",
    "summary_frame",
]
