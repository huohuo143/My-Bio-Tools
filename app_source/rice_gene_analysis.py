"""Unified rice gene sequence retrieval, prediction and reporting UI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import html
import re

import pandas as pd
import streamlit as st

from analysis_explanations import (
    DEEP_ANALYSIS_EXPLANATIONS,
    PREDICTOR_EXPLANATIONS,
    SEQUENCE_AND_RESOURCE_EXPLANATIONS,
    WORKFLOW_EXPLANATIONS,
    explanation_rows,
)
from app_ui import format_bytes, page_header, tool_website
from analysis_jobs import JOB_MANAGER, ProgressReporter, RiceGeneAnalysisRequest
from codex_chatgpt import (
    CODEX_ACCOUNT_MODEL,
    CODEX_DEFAULT_REASONING,
    CODEX_DEFAULT_SPEED,
    CODEX_FAST_SPEED,
    CODEX_MODEL_OPTIONS,
    CodexInvocationCancelled,
    codex_model_label,
    codex_reasoning_label,
    codex_reasoning_options,
    codex_speed_label,
    codex_speed_options,
    detect_codex_client,
    probe_codex_connection,
)
from job_ui import STATUS_LABELS, render_progress_breakdown
from llm_providers import (
    CLOUD_API_PROVIDERS,
    CLOUD_PROVIDER_PRESETS,
    cloud_preference_keys,
    cloud_provider,
)
from model_preferences import (
    DEFAULT_INTERPRETATION_PREFERENCES,
    get_model_connection_test_result,
    load_interpretation_preferences,
    model_connection_fingerprint,
    normalize_interpretation_preferences,
    record_model_connection_test_result,
    save_interpretation_preferences,
    start_model_connection_test,
)
from prediction_services import PREDICTORS, TOOL_URLS, run_selected_predictions
from prediction_visualization import build_prediction_chart_artifacts
from sequence_visualization import build_sequence_relationship_artifacts
from RAP_MSU_convert import MAPPING_PATH, load_mapping_index
from RiceData_crawler import batch_fetch_gene_records
from RGAP_sequence_downloader import batch_fetch_rgap_sequences
from report_builder import build_report_artifacts
from report_interpretation import (
    MODE_LLM,
    MODE_RULES,
    PROVIDER_CODEX_CHATGPT,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI_COMPATIBLE,
    generate_interpretations,
    probe_model_connection,
)
from mechanism_evidence import build_mechanism_claims
from protein_domain_analysis import analyze_protein_domains, build_domain_artifacts, MATCHES_API, INTERPRO_URL
from gene_structure_analysis import fetch_gene_models, build_gene_structure_artifacts, ENSEMBL_REST_URL
from promoter_regulation_analysis import predict_tfbs, build_tfbs_artifacts, PLANTREGMAP_URL
from variation_analysis import parse_vcf, fetch_ricevarmap_variants, build_variation_artifacts, RICEVARMAP_V3_URL
from mirna_rnai_analysis import run_psrnatarget, PSRNATARGET_URL
from literature_evidence_analysis import (
    fetch_europe_pmc, fetch_rapdb_genetic_evidence, import_manual_evidence,
    genetic_evidence_from_ricedata, enrich_ricedata_references, EUROPE_PMC_URL, RAPDB_URL,
)
from rice_efp import (
    DEFAULT_EFP_DATA_SOURCES,
    EFP_DATA_SOURCES,
    EFP_SOURCE_GLOSSARY,
    EFP_MAX_GENES,
    EFP_URL,
    batch_fetch_efp_records,
    build_efp_chart_artifacts,
    canonicalize_msu_gene,
    duplicate_expression_count,
    efp_source_display_label,
    expression_top_rows,
)
from lab_omics import (
    LabOmicsUnavailable,
    build_lab_omics_artifacts,
    canonical_msu_loci,
    query_lab_omics,
)
from rice_gene_core import (
    AnalysisBundle,
    CDS,
    FIVE_UTR,
    GENOMIC,
    PROMOTER,
    PROTEIN,
    SEQUENCE_TYPES,
    THREE_UTR,
    PredictionResult,
    SequenceRecord,
    deduplicate_sequence_records,
    exact_reference_matches,
    normalize_cds,
    normalize_protein,
    parse_fasta_or_sequence,
    prediction_consistency,
    safe_file_stem,
    transcript_to_gene,
    translate_cds,
)
from rice_seq_extractor import FASTA_FILES, extract_bundled_sequences, record_matches
from rice_utr_promoter_downloader import (
    FIVE_UTR as UTR_FIVE,
    PROMOTER as UTR_PROMOTER,
    THREE_UTR as UTR_THREE,
    TRANSCRIPT_SCOPE_ALL,
    TRANSCRIPT_SCOPE_CANONICAL,
    batch_fetch_sequences as batch_fetch_utr_sequences,
    canonicalize_msu,
    fetch_assembly_metadata,
    fetch_selected_transcript_ids,
    parse_input_ids,
    resolve_input_ids,
)


INPUT_ID = "RAP/MSU ID"
INPUT_CDS = "CDS FASTA"
INPUT_PROTEIN = "Protein FASTA"
MODE_SINGLE = "单基因深度分析"
MODE_BATCH = "批量分析"
MAX_SEQUENCE_BATCH = 100
MAX_PREDICTION_BATCH = 20
DEEP_ANALYSES = {
    "protein_domains": "蛋白结构域与功能位点",
    "gene_structure": "基因结构与转录本可视化",
    "promoter_regulation": "启动子与候选上游调控",
    "variation": "自然变异与单倍型",
    "mirna_rnai": "miRNA/RNAi 分析",
    "literature_evidence": "文献与已知遗传证据",
}
DEFAULT_DEEP_ANALYSES = ("protein_domains", "gene_structure", "promoter_regulation", "literature_evidence")

SOURCES = [
    "RAP-DB / IRGSP-1.0 bundled reference FASTA",
    "Rice Genome Annotation Project: https://rice.uga.edu/",
    "Ensembl REST API: https://rest.ensembl.org/",
    "DTU Health Tech bioinformatic services: https://services.healthtech.dtu.dk/",
    "cNLS Mapper: https://nls-mapper.iab.keio.ac.jp/",
    "NLStradamus 1.8: Nguyen Ba et al. 2009, BMC Bioinformatics",
]


@st.cache_data(ttl=30, show_spinner=False)
def _cached_codex_client_status() -> dict[str, object]:
    return detect_codex_client().public_dict()


def _connection_fingerprint(
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    reasoning: str = "",
    speed: str = "",
) -> str:
    return model_connection_fingerprint(
        provider,
        base_url,
        model,
        api_key,
        reasoning=reasoning,
        speed=speed,
    )


def _remember_connection_result(fingerprint: str, ok: bool, message: str) -> None:
    result = record_model_connection_test_result(
        fingerprint,
        ok=ok,
        message=message,
    )
    st.session_state.rice_model_connection_result = result


@st.fragment(run_every=1.0)
def _render_connection_result(fingerprint: str) -> bool:
    result = get_model_connection_test_result(fingerprint)
    if result is None:
        session_result = st.session_state.get("rice_model_connection_result", {})
        result = session_result if session_result.get("fingerprint") == fingerprint else None
    if result is None:
        st.info("尚未验证当前模型配置。")
        return False
    status = str(result.get("status") or "")
    if status == "testing":
        st.info(str(result.get("message") or "正在自动测试模型连接…"), icon="⏳")
        return False
    if status == "needs_api_key":
        st.info(str(result.get("message") or "请输入 API Key 后验证连接。"), icon="🔐")
        return False
    if status == "ok" or result.get("ok"):
        st.success(f"连接通畅 · {result.get('message', '')}", icon="✅")
        return True
    st.error(f"连接失败 · {result.get('message', '')}", icon="❌")
    return False


_INTERPRETATION_MODE_LABELS = {
    MODE_RULES: "科研规则解读（离线，推荐）",
    MODE_LLM: "大模型增强解读（可选）",
}
_INTERPRETATION_PROVIDER_LABELS = {
    PROVIDER_CODEX_CHATGPT: "ChatGPT 账号（Codex，免 API Key，推荐）",
    PROVIDER_OLLAMA: "本机 Ollama（数据不出本机）",
    **{preset.provider_id: preset.label for preset in CLOUD_PROVIDER_PRESETS},
}


def _initialize_interpretation_preferences() -> dict[str, str]:
    """Restore saved choices into Streamlit before any model widget is created."""
    loaded = load_interpretation_preferences()
    widget_defaults = {
        "rice_interpretation_mode": loaded["mode"],
        "rice_interpretation_provider": loaded["provider"],
        "rice_codex_model": loaded["codex_model"],
        "rice_codex_reasoning": loaded["codex_reasoning"],
        "rice_codex_speed": loaded["codex_speed"],
        "rice_ollama_base_url": loaded["ollama_base_url"],
        "rice_ollama_model": loaded["ollama_model"],
    }
    for preset in CLOUD_PROVIDER_PRESETS:
        base_key, model_key = cloud_preference_keys(preset.provider_id)
        widget_defaults[f"rice_{base_key}"] = loaded[base_key]
        widget_defaults[f"rice_{model_key}"] = loaded[model_key]
    for key, value in widget_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if "rice_saved_interpretation_preferences" not in st.session_state:
        st.session_state.rice_saved_interpretation_preferences = loaded
    return loaded


def _current_interpretation_preferences() -> dict[str, str]:
    raw = dict(DEFAULT_INTERPRETATION_PREFERENCES)
    raw.update(
        {
            "mode": st.session_state.get("rice_interpretation_mode", MODE_RULES),
            "provider": st.session_state.get("rice_interpretation_provider", PROVIDER_CODEX_CHATGPT),
            "codex_model": st.session_state.get("rice_codex_model", CODEX_ACCOUNT_MODEL),
            "codex_reasoning": st.session_state.get("rice_codex_reasoning", CODEX_DEFAULT_REASONING),
            "codex_speed": st.session_state.get("rice_codex_speed", CODEX_DEFAULT_SPEED),
            "ollama_base_url": st.session_state.get("rice_ollama_base_url", raw["ollama_base_url"]),
            "ollama_model": st.session_state.get("rice_ollama_model", raw["ollama_model"]),
        }
    )
    for preset in CLOUD_PROVIDER_PRESETS:
        base_key, model_key = cloud_preference_keys(preset.provider_id)
        raw[base_key] = st.session_state.get(f"rice_{base_key}", raw[base_key])
        raw[model_key] = st.session_state.get(f"rice_{model_key}", raw[model_key])
    return normalize_interpretation_preferences(raw)


def _persist_interpretation_preferences() -> dict[str, str]:
    current = _current_interpretation_preferences()
    previous = st.session_state.get("rice_saved_interpretation_preferences")
    if current != previous:
        current = save_interpretation_preferences(current)
        st.session_state.rice_saved_interpretation_preferences = current
    return current


def _efp_glossary_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source, label in EFP_DATA_SOURCES.items():
        info = EFP_SOURCE_GLOSSARY.get(source, {})
        rows.append(
            {
                "数据源": efp_source_display_label(source),
                "官网代码": source,
                "组织/处理": info.get("scope", ""),
                "实验设计": info.get("design", ""),
                "数值尺度": info.get("scale", ""),
                "提交 ID": info.get("id_namespace", ""),
                "官方来源": info.get("reference", ""),
                "重复/汇总结构": info.get("replicate_note", ""),
                "适合回答": info.get("best_for", ""),
                "获得的数据": info.get("outputs", ""),
                "注意": info.get("caution", ""),
            }
        )
    return rows


def _render_efp_source_guide(key_prefix: str) -> None:
    """Render a readable single-source guide plus the complete comparison matrix."""
    detail_tab, matrix_tab = st.tabs(["单个数据源详解", "12 个数据源对照表"])
    with detail_tab:
        source = st.selectbox(
            "选择要查看的数据源",
            list(EFP_DATA_SOURCES),
            format_func=efp_source_display_label,
            key=f"{key_prefix}_efp_guide_source",
        )
        info = EFP_SOURCE_GLOSSARY[source]
        cards = (
            ("组织/处理范围", info.get("scope", "")),
            ("实验设计", info.get("design", "")),
            ("数值尺度与提交 ID", f"{info.get('scale', '')}；提交 {info.get('id_namespace', '')} ID"),
            ("官方来源/论文", info.get("reference", "")),
            ("重复或汇总结构", info.get("replicate_note", "")),
            ("适合回答", info.get("best_for", "")),
            ("获得的数据", info.get("outputs", "")),
            ("解读边界", info.get("caution", "")),
        )
        card_html = "".join(
            '<div class="bio-guide-item">'
            f'<div class="bio-guide-label">{html.escape(label)}</div>'
            f'<div class="bio-guide-copy">{html.escape(copy)}</div>'
            "</div>"
            for label, copy in cards
        )
        st.markdown(f'<div class="bio-guide-grid">{card_html}</div>', unsafe_allow_html=True)
        config_url = f"https://bar.utoronto.ca/transcriptomics/efp_rice/data/{source}.xml"
        st.caption(f"官网代码：{source} · [BAR 官方配置 XML]({config_url})")
    with matrix_tab:
        st.caption("适合快速横向核对来源范围、数值尺度和证据边界；完整字段可在 Excel 的 eFP_Source_Glossary sheet 中筛选。")
        st.dataframe(_efp_glossary_rows(), width="stretch", hide_index=True, height=440)


def _mapping_rows(targets) -> list[dict[str, object]]:
    return [
        {
            "input_id": target.input_id,
            "input_type": target.input_type,
            "resolved_rap_gene": target.rap_gene_id,
            "resolved_msu_id": target.input_id if target.input_type == "MSU" else "",
            "requested_transcript": target.requested_transcript_id,
            "mapping_count": target.mapping_count,
            "status": target.status,
            "note": target.note,
            "error": target.error,
        }
        for target in targets
    ]


def _resolve_local_transcript_selection(
    targets,
    transcript_scope: str,
    max_workers: int,
    preselected: dict[str, set[str]] | None = None,
) -> tuple[dict[str, set[str]], list[str]]:
    selected = {key: set(values) for key, values in (preselected or {}).items()}
    warnings: list[str] = []
    for target in targets:
        if target.is_resolved and target.requested_transcript_id:
            selected.setdefault(target.rap_gene_id, set()).add(target.requested_transcript_id)
    if transcript_scope != TRANSCRIPT_SCOPE_CANONICAL:
        return selected, warnings

    unresolved = {
        target.rap_gene_id: target
        for target in targets
        if target.is_resolved
        and not target.requested_transcript_id
        and target.rap_gene_id not in selected
    }
    workers = max(1, min(max_workers, 4, len(unresolved) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_selected_transcript_ids,
                target.rap_gene_id,
                "",
                TRANSCRIPT_SCOPE_CANONICAL,
            ): gene_id
            for gene_id, target in unresolved.items()
        }
        for future in as_completed(futures):
            gene_id = futures[future]
            try:
                transcript_ids, _ = future.result()
                if transcript_ids:
                    selected[gene_id] = set(transcript_ids)
                else:
                    warnings.append(f"{gene_id} 未取得 canonical transcript 注释。")
            except Exception as exc:
                warnings.append(
                    f"{gene_id} canonical transcript 查询失败，将明确使用本地首个 transcript："
                    f"{type(exc).__name__}: {exc}"
                )
    return selected, warnings


def _local_rap_records(
    targets,
    selected_types: tuple[str, ...],
    transcript_scope: str,
    transcript_selection: dict[str, set[str]],
) -> tuple[list[SequenceRecord], list[str]]:
    records: list[SequenceRecord] = []
    warnings: list[str] = []
    rap_targets = [target for target in targets if target.is_resolved]
    if not rap_targets:
        return records, warnings
    queries = tuple(dict.fromkeys(target.requested_transcript_id or target.rap_gene_id for target in rap_targets))
    need_cds = CDS in selected_types or PROTEIN in selected_types
    jobs = []
    if GENOMIC in selected_types:
        jobs.append((GENOMIC, FASTA_FILES["Gene genomic sequence"]))
    if need_cds:
        jobs.append((CDS, FASTA_FILES["CDS"]))
    for sequence_type, path in jobs:
        found, missing, _ = extract_bundled_sequences(str(path), queries)
        if missing:
            warnings.append(f"内置 {sequence_type} 数据未匹配：{', '.join(missing[:10])}")
        for target in rap_targets:
            matching = [
                item
                for item in found
                if record_matches(item[0], target.requested_transcript_id or target.rap_gene_id)
            ]
            if sequence_type == CDS and transcript_scope == TRANSCRIPT_SCOPE_CANONICAL:
                allowed = {
                    item.casefold()
                    for item in transcript_selection.get(target.rap_gene_id, set())
                }
                if allowed:
                    matching = [item for item in matching if item[0].casefold() in allowed]
                    if not matching:
                        warnings.append(
                            f"{target.rap_gene_id} 的 canonical transcript 不在内置 CDS FASTA 中，"
                            "未用其他转录本替代。"
                        )
                elif matching:
                    matching = [sorted(matching, key=lambda item: item[0])[0]]
                    warnings.append(
                        f"{target.rap_gene_id} 无法在线确认 canonical transcript；"
                        f"已明确回退到本地排序首个 transcript {matching[0][0]}。"
                    )
            for record_id, _, sequence in matching:
                if sequence_type == CDS and CDS in selected_types:
                    records.append(
                        SequenceRecord(
                            input_id=target.input_id,
                            resolved_rap_gene=target.rap_gene_id,
                            resolved_msu_id=target.input_id if target.input_type == "MSU" else "",
                            transcript_id=record_id,
                            sequence_type=CDS,
                            sequence=sequence,
                            source="RAP-DB bundled IRGSP-1.0 CDS",
                            assembly="IRGSP-1.0",
                        )
                    )
                if sequence_type == CDS and PROTEIN in selected_types:
                    protein, errors = translate_cds(sequence)
                    records.append(
                        SequenceRecord(
                            input_id=target.input_id,
                            resolved_rap_gene=target.rap_gene_id,
                            resolved_msu_id=target.input_id if target.input_type == "MSU" else "",
                            transcript_id=record_id,
                            sequence_type=PROTEIN,
                            sequence=protein,
                            source="Translated from RAP-DB IRGSP-1.0 CDS",
                            assembly="IRGSP-1.0",
                            status="matched" if not errors else "invalid_cds",
                            validation_note="；".join(errors),
                        )
                    )
                if sequence_type == GENOMIC:
                    records.append(
                        SequenceRecord(
                            input_id=target.input_id,
                            resolved_rap_gene=target.rap_gene_id,
                            resolved_msu_id=target.input_id if target.input_type == "MSU" else "",
                            transcript_id=record_id,
                            sequence_type=GENOMIC,
                            sequence=sequence,
                            source="RAP-DB bundled IRGSP-1.0 gene sequence",
                            assembly="IRGSP-1.0",
                        )
                    )
    return records, warnings


def _rgap_records(identifiers: list[str], selected_types: tuple[str, ...], max_workers: int) -> tuple[list[SequenceRecord], list[str]]:
    records: list[SequenceRecord] = []
    warnings: list[str] = []
    msu_ids = [identifier for identifier in identifiers if canonicalize_msu(identifier)]
    if not msu_ids or not any(item in selected_types for item in (GENOMIC, CDS, PROTEIN)):
        return records, warnings
    rgap_results = batch_fetch_rgap_sequences(msu_ids, max_workers=max_workers)
    for result in rgap_results:
        if result.status not in {"matched", "partial"}:
            warnings.append(f"{result.query_id} 的 RGAP 序列获取失败：{result.error or result.status}")
        payloads = [
            (GENOMIC, result.genomic_sequence, result.genomic_header),
            (CDS, result.cds_sequence, result.cds_header),
            (PROTEIN, result.protein_sequence.rstrip("*"), result.protein_header),
        ]
        for sequence_type, sequence, header in payloads:
            if sequence_type not in selected_types or not sequence:
                continue
            records.append(
                SequenceRecord(
                    input_id=result.query_id,
                    resolved_msu_id=result.locus_id or result.query_id,
                    transcript_id=header.split()[0] if header else result.query_id,
                    sequence_type=sequence_type,
                    sequence=sequence,
                    source="Rice Genome Annotation Project (rice.uga.edu)",
                    assembly="MSU/RGAP annotation",
                    status=result.status,
                    validation_note=result.validation_note,
                )
            )
    return records, warnings


def _utr_promoter_records(
    targets,
    selected_types: tuple[str, ...],
    promoter_length: int,
    transcript_scope: str,
    max_workers: int,
) -> tuple[list[SequenceRecord], list[str]]:
    selected_utr_types = tuple(
        value
        for value, internal in ((UTR_FIVE, FIVE_UTR), (UTR_THREE, THREE_UTR), (UTR_PROMOTER, PROMOTER))
        if internal in selected_types
    )
    if not selected_utr_types:
        return [], []
    chromosome_lengths: dict[str, int] = {}
    warnings: list[str] = []
    if UTR_PROMOTER in selected_utr_types:
        try:
            _, chromosome_lengths, _ = fetch_assembly_metadata()
        except Exception as exc:
            warnings.append(f"染色体边界元数据获取失败：{type(exc).__name__}: {exc}")
    results = batch_fetch_utr_sequences(
        targets,
        transcript_scope,
        selected_utr_types,
        promoter_length,
        chromosome_lengths,
        max_workers=max_workers,
    )
    records: list[SequenceRecord] = []
    for result in results:
        target = result.target
        payload = result.payload
        if payload is None:
            warnings.append(f"{target.input_id} 未取得 UTR/启动子：{target.error or target.status}")
            continue
        strand = "+" if payload.strand == 1 else "-" if payload.strand == -1 else ""
        if PROMOTER in selected_types and payload.promoter_sequence:
            records.append(
                SequenceRecord(
                    input_id=target.input_id,
                    resolved_rap_gene=target.rap_gene_id,
                    resolved_msu_id=target.input_id if target.input_type == "MSU" else "",
                    sequence_type=PROMOTER,
                    sequence=payload.promoter_sequence,
                    source="Ensembl REST API",
                    assembly=payload.assembly,
                    coordinates=f"{payload.chromosome}:{payload.promoter_start}-{payload.promoter_end}",
                    strand=strand,
                    status=payload.status,
                    validation_note=payload.validation_note,
                )
            )
        for transcript in payload.transcripts:
            for sequence_type, sequence in (
                (FIVE_UTR, transcript.five_utr_sequence),
                (THREE_UTR, transcript.three_utr_sequence),
            ):
                if sequence_type not in selected_types or not sequence:
                    continue
                records.append(
                    SequenceRecord(
                        input_id=target.input_id,
                        resolved_rap_gene=target.rap_gene_id,
                        resolved_msu_id=target.input_id if target.input_type == "MSU" else "",
                        transcript_id=transcript.transcript_id,
                        sequence_type=sequence_type,
                        sequence=sequence,
                        source="Ensembl REST API",
                        assembly=payload.assembly,
                        strand=strand,
                        status=transcript.status,
                        validation_note=transcript.validation_note,
                    )
                )
        if payload.error:
            warnings.append(f"{target.input_id} 部分序列失败：{payload.error}")
    return records, warnings


def analyze_id_inputs(
    identifiers: list[str],
    selected_types: tuple[str, ...],
    promoter_length: int,
    transcript_scope: str,
    mode: str,
    max_workers: int = 3,
) -> AnalysisBundle:
    _, msu_to_rap = load_mapping_index() if MAPPING_PATH.is_file() else ({}, {})
    targets = resolve_input_ids(identifiers, msu_to_rap)
    bundle = AnalysisBundle(
        mode=mode,
        input_type=INPUT_ID,
        inputs=identifiers,
        mapping_rows=_mapping_rows(targets),
        sources=list(SOURCES),
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )
    utr_records, utr_warnings = _utr_promoter_records(
        targets,
        selected_types,
        promoter_length,
        transcript_scope,
        max_workers,
    )
    preselected: dict[str, set[str]] = {}
    for record in utr_records:
        if record.transcript_id and record.resolved_rap_gene:
            preselected.setdefault(record.resolved_rap_gene, set()).add(record.transcript_id)
    if CDS in selected_types or PROTEIN in selected_types:
        transcript_selection, selection_warnings = _resolve_local_transcript_selection(
            targets,
            transcript_scope,
            max_workers,
            preselected,
        )
    else:
        transcript_selection = preselected
        selection_warnings = []
    local_records, local_warnings = _local_rap_records(
        targets,
        selected_types,
        transcript_scope,
        transcript_selection,
    )
    rgap_records, rgap_warnings = _rgap_records(identifiers, selected_types, max_workers)
    bundle.sequences = deduplicate_sequence_records([*local_records, *rgap_records, *utr_records])
    bundle.warnings.extend(
        [*selection_warnings, *local_warnings, *rgap_warnings, *utr_warnings]
    )
    for row in bundle.mapping_rows:
        gene_id = str(row.get("resolved_rap_gene") or "")
        row["selected_transcripts"] = ", ".join(sorted(transcript_selection.get(gene_id, set())))
    for target in targets:
        if not target.is_resolved:
            bundle.warnings.append(f"{target.input_id}: {target.error or target.status}")
    if any(row.get("status") == "mapped_one_to_many" for row in bundle.mapping_rows):
        bundle.warnings.append("存在 MSU→RAP 一对多映射；结果已分别保留，未静默合并。")
    return bundle


def analyze_sequence_inputs(
    text: str,
    input_type: str,
    selected_types: tuple[str, ...],
    promoter_length: int,
    transcript_scope: str,
    mode: str,
    selected_candidate: str = "",
    max_workers: int = 3,
) -> AnalysisBundle:
    parsed = parse_fasta_or_sequence(text)
    bundle = AnalysisBundle(
        mode=mode,
        input_type=input_type,
        inputs=[identifier for identifier, _ in parsed],
        sources=list(SOURCES),
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    )
    for identifier, raw_sequence in parsed:
        if input_type == INPUT_CDS:
            sequence, errors = normalize_cds(raw_sequence)
            protein, translation_errors = translate_cds(sequence)
            errors = list(dict.fromkeys([*errors, *translation_errors]))
            supplied_type = CDS
        else:
            sequence, errors = normalize_protein(raw_sequence)
            protein = sequence
            supplied_type = PROTEIN
        matches = exact_reference_matches(sequence, input_type) if not errors else []
        chosen = selected_candidate if selected_candidate in matches else matches[0] if len(matches) == 1 else ""
        status = "matched" if chosen else "ambiguous" if len(matches) > 1 else "not_mapped"
        bundle.mapping_rows.append(
            {
                "input_id": identifier,
                "input_type": input_type,
                "resolved_rap_gene": transcript_to_gene(chosen) if chosen else "",
                "resolved_msu_id": "",
                "requested_transcript": chosen,
                "mapping_count": len(matches),
                "status": "invalid_input" if errors else status,
                "note": "参考 CDS/蛋白精确匹配。" if chosen else "候选：" + ", ".join(matches),
                "error": "；".join(errors),
            }
        )
        bundle.sequences.append(
            SequenceRecord(
                input_id=identifier,
                resolved_rap_gene=transcript_to_gene(chosen) if chosen else "",
                transcript_id=chosen or identifier,
                sequence_type=supplied_type,
                sequence=sequence,
                source="User supplied sequence",
                assembly="",
                status="matched" if not errors else "invalid_input",
                validation_note="；".join(errors),
            )
        )
        if input_type == INPUT_CDS and protein:
            bundle.sequences.append(
                SequenceRecord(
                    input_id=identifier,
                    resolved_rap_gene=transcript_to_gene(chosen) if chosen else "",
                    transcript_id=chosen or identifier,
                    sequence_type=PROTEIN,
                    sequence=protein,
                    source="Translated from user supplied CDS",
                    status="matched" if not errors else "invalid_input",
                    validation_note="；".join(errors),
                )
            )
        if chosen:
            referenced = analyze_id_inputs(
                [chosen],
                selected_types,
                promoter_length,
                transcript_scope,
                mode,
                max_workers,
            )
            bundle.sequences.extend(referenced.sequences)
            bundle.warnings.extend(referenced.warnings)
        elif len(matches) > 1:
            bundle.warnings.append(f"{identifier} 精确匹配到多个 RAP transcript，未自动选择：{', '.join(matches)}")
        elif not errors:
            bundle.warnings.append(f"{identifier} 未精确匹配 IRGSP 参考序列；基因组、UTR 与启动子不可用。")
    bundle.sequences = deduplicate_sequence_records(bundle.sequences)
    return bundle


def _protein_inputs(bundle: AnalysisBundle) -> list[tuple[str, str]]:
    proteins: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for record in bundle.sequences:
        if record.sequence_type != PROTEIN or record.status == "invalid_input" or not record.sequence:
            continue
        identifier = record.transcript_id or record.resolved_msu_id or record.input_id
        key = (identifier, record.sequence)
        if key not in seen:
            seen.add(key)
            proteins.append(key)
    return proteins


def _gene_targets(bundle: AnalysisBundle) -> list[tuple[str, str]]:
    targets = []
    for row in bundle.mapping_rows:
        gene = transcript_to_gene(str(row.get("resolved_rap_gene") or "")) or str(row.get("resolved_rap_gene") or "")
        if gene and str(row.get("status") or "") not in {"failed", "ambiguous"}:
            targets.append((str(row.get("input_id") or gene), gene))
    return list(dict.fromkeys(targets))


def _promoter_inputs(bundle: AnalysisBundle) -> list[dict[str, object]]:
    return [{"input_id": record.input_id, "rap_gene": record.resolved_rap_gene, "transcript_id": record.transcript_id or record.input_id, "sequence": record.sequence}
            for record in bundle.sequences if record.sequence_type == PROMOTER and record.sequence and record.status not in {"failed", "invalid_input"}]


def _transcript_inputs(bundle: AnalysisBundle) -> list[dict[str, str]]:
    ids = [str(row.get("transcript_id") or "") for row in bundle.transcript_models if row.get("transcript_id") and row.get("status") == "matched"]
    if not ids:
        ids = [str(row.get("resolved_rap_gene") or "") for row in bundle.mapping_rows if row.get("resolved_rap_gene")]
    if not ids or not FASTA_FILES.get("Transcript", None):
        return []
    records, _, _ = extract_bundled_sequences(str(FASTA_FILES["Transcript"]), tuple(dict.fromkeys(ids)))
    return [{"input_id": record_id, "transcript_id": record_id, "sequence": sequence} for record_id, _, sequence in records]


def _stamp_deep_records(bundle: AnalysisBundle, request: RiceGeneAnalysisRequest) -> None:
    """Ensure every standardized deep-analysis record remains traceable."""
    queried_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    rap_default = next((transcript_to_gene(str(row.get("resolved_rap_gene") or "")) for row in bundle.mapping_rows if row.get("resolved_rap_gene")), "")
    msu_default = next((str(row.get("resolved_msu_id") or "") for row in bundle.mapping_rows if row.get("resolved_msu_id")), "")
    input_default = bundle.inputs[0] if bundle.inputs else ""
    params = str(bundle.analysis_options)
    collections = (bundle.protein_domains, bundle.functional_sites, bundle.transcript_models, bundle.gene_features, bundle.promoter_tfbs, bundle.upstream_tfs, bundle.variants, bundle.haplotypes, bundle.mirna_targets, bundle.rnai_offtargets, bundle.literature_rows, bundle.genetic_evidence, bundle.ricedata_references)
    for rows in collections:
        for row in rows:
            row.setdefault("input_id", input_default)
            row.setdefault("rap_gene", rap_default)
            row.setdefault("msu_id", msu_default)
            row.setdefault("transcript_id", str(row.get("protein_id") or ""))
            row.setdefault("assembly", "IRGSP-1.0")
            row.setdefault("source_url", "")
            row.setdefault("queried_at", queried_at)
            row.setdefault("parameters", params)
            row.setdefault("status", "matched")
            row.setdefault("error", "")


def add_predictions(
    bundle: AnalysisBundle,
    selected_predictors: list[str],
    signalp_mode: str,
    cnls_cutoff: float,
    nlstradamus_model: int,
    nlstradamus_cutoff: float,
    progress_callback=None,
    item_progress_callback=None,
    cancel_check=None,
) -> dict[str, bytes]:
    if not selected_predictors:
        return {}
    proteins = _protein_inputs(bundle)
    if not proteins:
        bundle.warnings.append("没有通过校验的蛋白序列，未运行定位预测。")
        return {}
    if len(proteins) > MAX_PREDICTION_BATCH:
        bundle.warnings.append(
            f"解析后得到 {len(proteins)} 条蛋白，超过预测上限 {MAX_PREDICTION_BATCH}；"
            "未提交任何在线或本地预测，请缩小 transcript/model 范围。"
        )
        return {}
    options = {
        "SignalP 6.0": {"organism": "Eukarya", "mode": signalp_mode},
        "TargetP 2.0": {"organism": "Plant"},
        "cNLS Mapper": {"cutoff": cnls_cutoff, "linker": "Within terminal 60-amino-acid regions"},
        "NLStradamus 1.8": {"model": nlstradamus_model, "cutoff": nlstradamus_cutoff},
    }
    execution = run_selected_predictions(
        proteins,
        selected_predictors,
        options,
        max_workers=2,
        progress_callback=progress_callback,
        item_progress_callback=item_progress_callback,
        cancel_check=cancel_check,
    )
    bundle.predictions = execution.results
    for result in bundle.predictions:
        if result.status not in {"matched", "partial"}:
            bundle.warnings.append(f"{result.protein_id} · {result.tool}: {result.error or result.status}")
    return execution.raw_artifacts


def _ricedata_query_ids(bundle: AnalysisBundle) -> list[str]:
    queries: list[str] = []
    for row in bundle.mapping_rows:
        value = str(row.get("resolved_rap_gene") or row.get("resolved_msu_id") or "").strip()
        if value:
            queries.append(transcript_to_gene(value) or value)
    if not queries and bundle.input_type == INPUT_ID:
        queries.extend(bundle.inputs)
    return list(dict.fromkeys(value for value in queries if value))


def _efp_targets(bundle: AnalysisBundle) -> list[tuple[str, str, str]]:
    rap_to_msu, _ = load_mapping_index() if MAPPING_PATH.is_file() else ({}, {})
    rap_lookup = {key.casefold(): values for key, values in rap_to_msu.items()}
    ricedata_msu: dict[str, list[str]] = {}
    for row in bundle.ricedata_rows:
        query = str(row.get("check") or row.get("RAP_Locus") or "")
        matches = re.findall(r"LOC_Os\d{2}g\d{5}", str(row.get("MSU_Locus") or ""), flags=re.I)
        if matches:
            ricedata_msu.setdefault(query.casefold(), []).extend(matches)

    targets: list[tuple[str, str, str]] = []
    for row in bundle.mapping_rows:
        input_id = str(row.get("input_id") or "")
        resolved_rap = str(row.get("resolved_rap_gene") or "")
        rap_gene = transcript_to_gene(resolved_rap) or resolved_rap
        direct_msu = canonicalize_msu_gene(str(row.get("resolved_msu_id") or input_id))
        candidates: list[str] = [direct_msu] if direct_msu else []
        if rap_gene:
            candidates.extend(rap_lookup.get(rap_gene.casefold(), ()))
            candidates.extend(ricedata_msu.get(rap_gene.casefold(), ()))
        candidates.extend(ricedata_msu.get(input_id.casefold(), ()))
        for candidate in candidates:
            canonical = canonicalize_msu_gene(candidate)
            if canonical:
                targets.append((input_id or rap_gene or canonical, canonical, rap_gene))
    return list(dict.fromkeys(targets))


def _lab_omics_loci(bundle: AnalysisBundle) -> list[str]:
    values: list[object] = [*bundle.inputs]
    for row in bundle.mapping_rows:
        values.extend((row.get("resolved_msu_id"), row.get("input_id")))
    for record in bundle.sequences:
        values.append(record.resolved_msu_id)
    for row in bundle.ricedata_rows:
        values.append(row.get("MSU_Locus"))
    return canonical_msu_loci(values)


def _identifier_keys(*values: object) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        keys.add(text.casefold())
        rap_gene = transcript_to_gene(text)
        if rap_gene:
            keys.add(rap_gene.casefold())
        msu_gene = canonicalize_msu_gene(text)
        if msu_gene:
            keys.add(msu_gene.casefold())
    return keys


def _matching_ricedata_row(target: dict[str, object], rows: list[dict[str, object]]) -> dict[str, object] | None:
    target_keys = _identifier_keys(
        target.get("input_id"), target.get("rap_gene"), target.get("msu_id"),
        target.get("gene_symbol"), *(target.get("aliases") or []),
    )
    for row in rows:
        row_keys = _identifier_keys(
            row.get("check"), row.get("RAP_Locus"), row.get("MSU_Locus"),
            row.get("GeneSymbol"), row.get("GeneName"),
        )
        if target_keys & row_keys:
            return row
    return None


def _collect_ricedata_references(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    references: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        for item in row.get("ricedata_references") or []:
            reference = {
                **dict(item),
                "gene_id": row.get("GeneID", ""),
                "input_id": row.get("check", ""),
                "rap_gene": row.get("RAP_Locus", ""),
                "msu_id": row.get("MSU_Locus", ""),
                "gene_symbol": row.get("GeneSymbol", ""),
                "source_type": "RiceData linked reference",
            }
            key = (str(reference.get("reference_id") or ""), str(reference.get("doi") or "").casefold())
            if key not in seen:
                seen.add(key)
                references.append(reference)
    return references


def _reference_literature_rows(references: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{
        "input_id": row.get("input_id", ""),
        "rap_gene": row.get("rap_gene", ""),
        "msu_id": row.get("msu_id", ""),
        "gene_symbol": row.get("gene_symbol", ""),
        "reference_id": row.get("reference_id", ""),
        "pmid": row.get("pmid", ""),
        "doi": row.get("doi", ""),
        "title": row.get("title", ""),
        "year": row.get("year", ""),
        "journal": row.get("journal", ""),
        "authors": row.get("authors", ""),
        "abstract_available": bool(row.get("abstract_text")),
        "abstract_text": row.get("abstract_text", ""),
        "matched_fields": row.get("matched_by", "ricedata_reference_id"),
        "evidence_tags": "RiceData-linked",
        "verification_status": row.get("verification_status", "RiceData 关联文献，需核验具体关系"),
        "source_type": "RiceData linked reference",
        "source_url": row.get("europe_pmc_url") or row.get("source_url", ""),
        "queried_at": row.get("queried_at", ""),
        "status": row.get("status", "matched"),
        "error": row.get("error", ""),
    } for row in references]


def _dedupe_literature_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    index: dict[tuple[str, str], int] = {}
    for row in rows:
        doi = str(row.get("doi") or "").casefold()
        pmid = str(row.get("pmid") or "").casefold()
        key = (doi, pmid) if doi or pmid else (str(row.get("title") or "").casefold(), "")
        if key in index:
            existing = output[index[key]]
            for field, value in row.items():
                if value and not existing.get(field):
                    existing[field] = value
            if row.get("source_type") == "RiceData linked reference":
                existing["source_type"] = "RiceData linked reference + Europe PMC"
                existing["verification_status"] = row.get("verification_status") or existing.get("verification_status")
            continue
        index[key] = len(output)
        output.append(dict(row))
    return output


def execute_analysis_request(
    request: RiceGeneAnalysisRequest,
    reporter: ProgressReporter,
) -> tuple[AnalysisBundle, dict[str, object]]:
    """Execute one immutable request without calling Streamlit from the worker thread."""
    reporter.update("mapping", 0, 1, "正在验证输入与解析 ID")
    identifiers = parse_input_ids(request.text) if request.input_type == INPUT_ID else parse_fasta_or_sequence(request.text)
    if not identifiers:
        raise ValueError("未找到有效输入")
    reporter.complete("mapping", f"已识别 {len(identifiers)} 个输入")

    implicit_types = []
    if request.selected_predictors or "protein_domains" in request.selected_deep_analyses:
        implicit_types.append(PROTEIN)
    if "promoter_regulation" in request.selected_deep_analyses:
        implicit_types.append(PROMOTER)
    retrieval_types = tuple(dict.fromkeys((*request.selected_types, *implicit_types)))
    reporter.update("sequences", 0, 1, "正在获取序列、UTR 与启动子")
    if request.input_type == INPUT_ID:
        bundle = analyze_id_inputs(
            list(identifiers),
            retrieval_types,
            request.promoter_length,
            request.transcript_scope,
            request.mode,
            request.max_workers,
        )
    else:
        bundle = analyze_sequence_inputs(
            request.text,
            request.input_type,
            retrieval_types,
            request.promoter_length,
            request.transcript_scope,
            request.mode,
            request.selected_candidate,
            request.max_workers,
        )
    reporter.complete(
        "sequences",
        f"已得到 {len(bundle.sequences)} 条序列记录",
        warning=bool(bundle.warnings),
    )
    bundle.analysis_options = {
        "project_name": request.project_name,
        "include_ricedata": request.include_ricedata,
        "ricedata_depth": request.ricedata_depth,
        "include_efp": request.include_efp,
        "efp_data_sources": list(request.efp_data_sources),
        "efp_mode": "Absolute",
        "include_lab_omics": request.include_lab_omics,
        "selected_deep_analyses": list(request.selected_deep_analyses),
        "promoter_pvalue": request.promoter_pvalue,
        "mirna_mode": request.mirna_mode,
        "mirna_expectation": request.mirna_expectation,
        "mirna_max_upe": request.mirna_max_upe,
        "mirna_offtargets": request.mirna_offtargets,
        "variation_vcf_name": request.variation_vcf_name,
        "sample_groups_name": request.sample_groups_name,
        "evidence_file_name": request.evidence_file_name,
        "interpretation_mode": request.interpretation_mode,
        "interpretation_provider": request.interpretation_provider,
        "interpretation_base_url": request.interpretation_base_url,
        "interpretation_model": request.interpretation_model,
        "interpretation_codex_reasoning": request.interpretation_codex_reasoning,
        "interpretation_codex_speed": request.interpretation_codex_speed,
    }

    if request.include_ricedata:
        ricedata_warning = False
        query_ids = _ricedata_query_ids(bundle)
        include_details = (
            request.ricedata_depth == "full"
            or (request.ricedata_depth == "adaptive" and request.mode == MODE_SINGLE)
        )
        if query_ids:
            bundle.ricedata_rows = batch_fetch_gene_records(
                query_ids,
                include_details=include_details,
                max_workers=request.max_workers,
                progress_callback=lambda done, total, label: reporter.update(
                    "ricedata", done, total, f"RiceData {done}/{total}：{label}"
                ),
                cancel_check=reporter.is_cancelled,
            )
            failed = [row for row in bundle.ricedata_rows if row.get("status") == "failed"]
            bundle.ricedata_references = _collect_ricedata_references(bundle.ricedata_rows)
            if failed:
                ricedata_warning = True
                bundle.warnings.append(f"RiceData 有 {len(failed)} 条记录失败，详见 status/error。")
        else:
            ricedata_warning = True
            bundle.warnings.append("未能从输入序列精确定位到水稻基因，RiceData 不可用。")
        reporter.complete(
            "ricedata",
            f"RiceData 返回 {len(bundle.ricedata_rows)} 条记录",
            warning=ricedata_warning,
        )

    efp_charts: dict[str, bytes] = {}
    if request.include_efp:
        efp_warning = False
        targets = _efp_targets(bundle)
        unique_genes = {(msu, rap) for _, msu, rap in targets}
        if len(unique_genes) > EFP_MAX_GENES:
            raise ValueError(f"eFP 每个项目最多查询 {EFP_MAX_GENES} 个基因，当前为 {len(unique_genes)} 个。")
        if targets:
            bundle.efp_rows = batch_fetch_efp_records(
                targets,
                request.efp_data_sources,
                max_workers=2,
                progress_callback=lambda done, total, label: reporter.update(
                    "efp", done, total, f"eFP {done}/{total}：{label}"
                ),
                item_progress_callback=lambda source, done, total, label, warning: reporter.update_item(
                    "efp", source, done, total, label, warning=warning
                ),
                cancel_check=reporter.is_cancelled,
            )
            failed = [record for record in bundle.efp_rows if record.status == "failed"]
            if failed:
                efp_warning = True
                bundle.warnings.append(f"Rice eFP 有 {len(failed)} 个数据源查询失败，详见 status/error。")
            duplicate_count = duplicate_expression_count(bundle.efp_rows)
            if duplicate_count:
                efp_warning = True
                bundle.warnings.append(
                    f"Rice eFP 官方定量表含 {duplicate_count} 条完全重复记录；"
                    "原始表原样保留，Top 汇总与图形已按完整记录键去重。"
                )
            reporter.update("efp", 0.95, 1, "正在生成 eFP 柱状图与热图")
            efp_charts = build_efp_chart_artifacts(bundle.efp_rows)
        else:
            efp_warning = True
            for source in request.efp_data_sources:
                reporter.complete_item(
                    "efp",
                    source,
                    "未取得可用的 MSU 映射",
                    warning=True,
                )
            bundle.warnings.append("未取得可用的 LOC_Osxxgxxxxx 映射，Rice eFP 不可用。")
        if EFP_URL not in bundle.sources:
            bundle.sources.append(EFP_URL)
        reporter.complete(
            "efp",
            f"eFP 生成 {len(efp_charts)} 个图形文件",
            warning=efp_warning,
        )

    prediction_charts: dict[str, bytes] = {}
    prediction_raw: dict[str, bytes] = {}
    if request.selected_predictors:
        prediction_raw = add_predictions(
            bundle,
            list(request.selected_predictors),
            request.signalp_mode,
            request.cnls_cutoff,
            request.nlstradamus_model,
            request.nlstradamus_cutoff,
            progress_callback=lambda done, total, label: reporter.update(
                "predictions", done, total, f"蛋白预测 {done}/{total}：{label}"
            ),
            item_progress_callback=lambda tool, done, total, label, warning: reporter.update_item(
                "predictions", tool, done, total, label, warning=warning
            ),
            cancel_check=reporter.is_cancelled,
        )
        if bundle.predictions:
            reporter.update("predictions", 0.98, 1, "正在生成蛋白定位综合图")
            prediction_charts = build_prediction_chart_artifacts(
                bundle.predictions,
                dict(_protein_inputs(bundle)),
            )
        for tool in request.selected_predictors:
            source_url = TOOL_URLS.get(tool, "")
            if source_url and source_url not in bundle.sources:
                bundle.sources.append(source_url)
        for result in bundle.predictions:
            if result.provider == "biolib" and result.result_url and result.result_url not in bundle.sources:
                bundle.sources.append(result.result_url)
        prediction_warning = any(
            result.status not in {"matched", "partial"} for result in bundle.predictions
        )
        if not bundle.predictions:
            prediction_warning = True
            for tool in request.selected_predictors:
                reporter.complete_item(
                    "predictions",
                    tool,
                    "没有可用的蛋白序列",
                    warning=True,
                )
        reporter.complete(
            "predictions",
            f"已完成 {len(bundle.predictions)} 条预测",
            warning=prediction_warning,
        )

    deep_charts: dict[str, bytes] = {}
    deep_raw: dict[str, bytes] = {}
    if request.include_lab_omics:
        try:
            reporter.update("lab_omics", 0, 1, "正在按MSU locus检索水稻多组学证据")
            loci = _lab_omics_loci(bundle)
            if not loci:
                raise ValueError("未取得可用于水稻多组学证据查询的MSU locus。")
            result = query_lab_omics(loci)
            bundle.lab_omics_datasets = list(result["datasets"])
            bundle.lab_omics_comparisons = list(result["comparisons"])
            bundle.lab_omics_samples = list(result["samples"])
            bundle.lab_omics_differential = list(result["differential"])
            bundle.lab_omics_profiles = list(result["profiles"])
            bundle.lab_omics_status = list(result["status"])
            bundle.lab_omics_published_evidence = list(result["published_evidence"])
            bundle.lab_omics_consensus_scores = list(result["consensus_scores"])
            bundle.lab_omics_qc_metrics = list(result["qc_metrics"])
            bundle.lab_omics_dataset_context = list(result["dataset_context"])
            bundle.lab_omics_dataset_registry = list(result["dataset_registry"])
            charts, raw = build_lab_omics_artifacts(result)
            deep_charts.update(charts)
            deep_raw.update(raw)
            bundle.analysis_options["lab_omics_schema"] = result.get("database_schema", "")
            bundle.analysis_options["omics_data_package_version"] = result.get("data_package_version", "")
            bundle.sources.append(
                "Rice multi-omics evidence database · schema v3 · replicated primary data + separately labelled published evidence · read-only"
            )
            reporter.complete(
                "lab_omics",
                f"命中可统计差异 {len(bundle.lab_omics_differential)} 条、定量 {len(bundle.lab_omics_profiles)} 条、论文证据 {len(bundle.lab_omics_published_evidence)} 条",
                warning=not bool(bundle.lab_omics_differential or bundle.lab_omics_profiles or bundle.lab_omics_published_evidence),
            )
        except (LabOmicsUnavailable, ValueError) as exc:
            bundle.warnings.append(f"水稻多组学证据：{exc}")
            reporter.complete("lab_omics", "数据库未解锁或ID未映射；其他分析继续", warning=True)
        except Exception as exc:
            bundle.warnings.append(f"水稻多组学证据模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("lab_omics", "模块失败，其他分析继续", warning=True)
    selected_deep = set(request.selected_deep_analyses)
    gene_targets = _gene_targets(bundle)

    if "protein_domains" in selected_deep:
        try:
            proteins = _protein_inputs(bundle)
            if len(proteins) > MAX_PREDICTION_BATCH:
                raise ValueError(f"深度蛋白分析最多 {MAX_PREDICTION_BATCH} 条蛋白。")
            bundle.protein_domains, bundle.functional_sites, raw, warnings = analyze_protein_domains(
                proteins,
                progress_callback=lambda done, total, label: reporter.update("protein_domains", done, total, f"InterPro {done}/{total}：{label}"),
                cancel_check=reporter.is_cancelled,
            )
            deep_raw.update({f"protein_domains/{name}": value for name, value in raw.items()})
            deep_charts.update({f"protein_domains/{name}": value for name, value in build_domain_artifacts(bundle.protein_domains, bundle.functional_sites).items()})
            bundle.warnings.extend(warnings)
            for source in (MATCHES_API, INTERPRO_URL):
                if source not in bundle.sources: bundle.sources.append(source)
            reporter.complete("protein_domains", f"结构域 {len(bundle.protein_domains)}，功能位点 {len(bundle.functional_sites)}", warning=bool(warnings))
        except Exception as exc:
            bundle.warnings.append(f"蛋白结构域模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("protein_domains", "模块失败，其他分析继续", warning=True)

    if "gene_structure" in selected_deep:
        try:
            if not gene_targets:
                raise ValueError("未精确反查到 RAP gene；不使用近似 BLAST 猜测。")
            bundle.transcript_models, bundle.gene_features, warnings = fetch_gene_models(
                gene_targets, request.transcript_scope,
                progress_callback=lambda done, total, label: reporter.update("gene_structure", done, total, f"Ensembl {done}/{total}：{label}"),
                cancel_check=reporter.is_cancelled,
            )
            deep_charts.update({f"gene_structure/{name}": value for name, value in build_gene_structure_artifacts(bundle.transcript_models, bundle.gene_features).items()})
            bundle.warnings.extend(warnings)
            if ENSEMBL_REST_URL not in bundle.sources: bundle.sources.append(ENSEMBL_REST_URL)
            reporter.complete("gene_structure", f"转录本 {len(bundle.transcript_models)}，特征 {len(bundle.gene_features)}", warning=bool(warnings))
        except Exception as exc:
            bundle.warnings.append(f"基因结构模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("gene_structure", "无精确 RAP 上下文或外站失败", warning=True)

    if "promoter_regulation" in selected_deep:
        try:
            bundle.promoter_tfbs, bundle.upstream_tfs, raw, warnings = predict_tfbs(_promoter_inputs(bundle), request.promoter_pvalue)
            deep_raw.update({f"promoter_regulation/{name}": value for name, value in raw.items()})
            deep_charts.update({f"promoter_regulation/{name}": value for name, value in build_tfbs_artifacts(bundle.promoter_tfbs).items()})
            bundle.warnings.extend(warnings)
            if PLANTREGMAP_URL not in bundle.sources: bundle.sources.append(PLANTREGMAP_URL)
            reporter.complete("promoter_regulation", f"TFBS {len(bundle.promoter_tfbs)}，候选 TF {len(bundle.upstream_tfs)}", warning=bool(warnings))
        except Exception as exc:
            bundle.warnings.append(f"启动子调控模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("promoter_regulation", "模块失败，其他分析继续", warning=True)

    if "variation" in selected_deep:
        try:
            warnings = []
            if request.variation_vcf_bytes and bundle.transcript_models:
                model = next((row for row in bundle.transcript_models if row.get("status") == "matched"), None)
                if model:
                    gene_seq = next((record.sequence for record in bundle.sequences if record.sequence_type == GENOMIC and record.sequence), "")
                    bundle.variants, bundle.haplotypes, warnings = parse_vcf(
                        request.variation_vcf_bytes, request.variation_vcf_name or "variants.vcf",
                        input_id=str(model.get("input_id") or ""), rap_gene=str(model.get("rap_gene") or ""), transcript_id=str(model.get("transcript_id") or ""),
                        gene_start=int(model.get("gene_start") or 0), gene_end=int(model.get("gene_end") or 0), strand=int(model.get("strand") or 1),
                        features=bundle.gene_features, promoter_length=request.promoter_length, reference_sequence=gene_seq,
                        sample_groups_payload=request.sample_groups_bytes, sample_groups_filename=request.sample_groups_name,
                    )
                    deep_raw[f"variation/{request.variation_vcf_name or 'variants.vcf'}"] = request.variation_vcf_bytes
                    if request.sample_groups_bytes:
                        deep_raw[f"variation/{request.sample_groups_name or 'sample_groups.csv'}"] = request.sample_groups_bytes
            else:
                msu_ids = [canonicalize_msu_gene(str(row.get("resolved_msu_id") or "")) for row in bundle.mapping_rows]
                if not any(msu_ids):
                    msu_ids = [msu for _, msu, _ in _efp_targets(bundle)]
                bundle.variants, raw, warnings = fetch_ricevarmap_variants([value for value in msu_ids if value])
                deep_raw.update({f"variation/{name}": value for name, value in raw.items()})
                if not bundle.variants:
                    warnings.append("本次无可用上传 VCF 且 RiceVarMap v3 未返回可解析数据；不伪造变异或单倍型。")
            deep_charts.update({f"variation/{name}": value for name, value in build_variation_artifacts(bundle.variants, bundle.haplotypes).items()})
            bundle.warnings.extend(warnings)
            if RICEVARMAP_V3_URL not in bundle.sources: bundle.sources.append(RICEVARMAP_V3_URL)
            reporter.complete("variation", f"变异 {len(bundle.variants)}，单倍型 {len(bundle.haplotypes)}", warning=bool(warnings))
        except Exception as exc:
            bundle.warnings.append(f"变异模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("variation", "模块失败，其他分析继续", warning=True)

    if "mirna_rnai" in selected_deep:
        try:
            targets = _transcript_inputs(bundle)
            bundle.mirna_targets, bundle.rnai_offtargets, raw, warnings = run_psrnatarget(
                targets, mode=request.mirna_mode, small_rna_text=request.custom_srna_text,
                expectation=request.mirna_expectation, max_upe=request.mirna_max_upe, off_target=request.mirna_offtargets,
            )
            deep_raw.update({f"mirna_rnai/{name}": value for name, value in raw.items()})
            bundle.warnings.extend(warnings)
            if PSRNATARGET_URL not in bundle.sources: bundle.sources.append(PSRNATARGET_URL)
            reporter.complete("mirna_rnai", f"靶点 {len(bundle.mirna_targets)}，脱靶 {len(bundle.rnai_offtargets)}", warning=bool(warnings))
        except Exception as exc:
            bundle.warnings.append(f"miRNA/RNAi 模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("mirna_rnai", "模块失败，其他分析继续", warning=True)

    if "literature_evidence" in selected_deep:
        try:
            targets = []
            for row in bundle.mapping_rows:
                targets.append({"input_id": row.get("input_id", ""), "rap_gene": transcript_to_gene(str(row.get("resolved_rap_gene") or "")), "msu_id": row.get("resolved_msu_id", ""), "gene_symbol": "", "gene_name": "", "aliases": []})
            for target in targets:
                matching = _matching_ricedata_row(target, bundle.ricedata_rows)
                if matching:
                    target["gene_symbol"] = matching.get("GeneSymbol", "")
                    target["gene_name"] = matching.get("GeneName", "")
                    target["msu_id"] = target.get("msu_id") or matching.get("MSU_Locus", "")
                    target["aliases"] = [matching.get("check", ""), matching.get("RAP_Locus", ""), matching.get("MSU_Locus", "")]
            bundle.ricedata_references = _collect_ricedata_references(bundle.ricedata_rows)
            bundle.ricedata_references, reference_raw, reference_warnings = enrich_ricedata_references(bundle.ricedata_references)
            bundle.literature_rows, raw, warnings = fetch_europe_pmc(targets)
            raw.update(reference_raw)
            warnings.extend(reference_warnings)
            bundle.genetic_evidence = genetic_evidence_from_ricedata(bundle.ricedata_rows, bundle.ricedata_references)
            directly_linked_dois = {
                doi.casefold()
                for evidence in bundle.genetic_evidence
                if str(evidence.get("verification_status") or "").startswith("直接支持")
                for doi in str(evidence.get("linked_dois") or "").split(",")
                if doi
            }
            for reference in bundle.ricedata_references:
                if str(reference.get("doi") or "").casefold() in directly_linked_dois:
                    reference["verification_status"] = "直接支持"
                    reference["matched_by"] = "evidence_citation_year"
                else:
                    reference["verification_status"] = "RiceData 关联文献，需核验与当前基因/证据的具体关系"
                    reference["matched_by"] = "ricedata_reference_id"
            bundle.literature_rows = _dedupe_literature_rows([
                *_reference_literature_rows(bundle.ricedata_references),
                *bundle.literature_rows,
            ])
            rap_evidence, rap_raw, rap_warnings = fetch_rapdb_genetic_evidence([str(target.get("rap_gene") or "") for target in targets])
            bundle.genetic_evidence.extend(rap_evidence)
            raw.update(rap_raw); warnings.extend(rap_warnings)
            if request.evidence_file_bytes:
                bundle.genetic_evidence.extend(import_manual_evidence(request.evidence_file_bytes, request.evidence_file_name or "manual_evidence.csv"))
                deep_raw[f"literature_evidence/{request.evidence_file_name or 'manual_evidence.csv'}"] = request.evidence_file_bytes
            deep_raw.update({f"literature_evidence/{name}": value for name, value in raw.items()})
            bundle.warnings.extend(warnings)
            for source in (EUROPE_PMC_URL, RAPDB_URL):
                if source not in bundle.sources: bundle.sources.append(source)
            reporter.complete("literature_evidence", f"文献 {len(bundle.literature_rows)}，遗传证据 {len(bundle.genetic_evidence)}", warning=bool(warnings))
        except Exception as exc:
            bundle.warnings.append(f"文献证据模块失败：{type(exc).__name__}: {exc}")
            reporter.complete("literature_evidence", "模块失败，其他分析继续", warning=True)

    try:
        bundle.sequence_plot_rows, sequence_charts, sequence_csv = build_sequence_relationship_artifacts(bundle)
        deep_charts.update({f"sequence_structure/{name}": value for name, value in sequence_charts.items()})
        deep_raw["sequence_structure/sequence_relationship_plot_data.csv"] = sequence_csv
    except Exception as exc:
        bundle.warnings.append(f"序列关系图生成失败：{type(exc).__name__}: {exc}")

    if request.interpretation_mode == MODE_LLM and bundle.ricedata_references:
        try:
            reporter.update("interpretation", 0, 1, "正在补齐关键论文摘要与 DOI 元数据")
            missing = [row for row in bundle.ricedata_references if not str(row.get("abstract_text") or "").strip()]
            if missing:
                ordered = sorted(
                    missing,
                    key=lambda row: -int(row.get("year") or 0) if str(row.get("year") or "").isdigit() else 0,
                )
                selected_references = list(dict.fromkeys(
                    str(row.get("reference_id") or row.get("doi") or "")
                    for row in [*ordered[:6], *ordered[-6:]]
                    if row.get("reference_id") or row.get("doi")
                ))
                selected_rows = [
                    row for row in missing
                    if str(row.get("reference_id") or row.get("doi") or "") in selected_references
                ]
                enriched, ai_reference_raw, ai_reference_warnings = enrich_ricedata_references(selected_rows)
                enriched_index = {
                    str(row.get("reference_id") or row.get("doi") or ""): row for row in enriched
                }
                bundle.ricedata_references = [
                    enriched_index.get(str(row.get("reference_id") or row.get("doi") or ""), row)
                    for row in bundle.ricedata_references
                ]
                bundle.literature_rows = _dedupe_literature_rows([
                    *_reference_literature_rows(bundle.ricedata_references),
                    *bundle.literature_rows,
                ])
                deep_raw.update({f"ai_interpretation/references/{name}": value for name, value in ai_reference_raw.items()})
                bundle.warnings.extend(ai_reference_warnings)
        except Exception as exc:
            bundle.warnings.append(f"AI 关键论文摘要补齐失败：{type(exc).__name__}: {exc}")

    _stamp_deep_records(bundle, request)
    reporter.update("interpretation", 0, 1, "正在整理功能、文献与机制证据")
    bundle.mechanism_claims = build_mechanism_claims(bundle)
    if request.interpretation_mode == MODE_LLM:
        reporter.update("interpretation", 0, 1, "正在生成结构化科研解读")
    try:
        bundle.interpretations, bundle.interpretation_status = generate_interpretations(
            bundle,
            mode=request.interpretation_mode,
            provider=request.interpretation_provider,
            base_url=request.interpretation_base_url,
            model=request.interpretation_model,
            api_key=request.interpretation_api_key,
            codex_reasoning=request.interpretation_codex_reasoning,
            codex_speed=request.interpretation_codex_speed,
            is_cancelled=reporter.is_cancelled,
        )
    except CodexInvocationCancelled:
        reporter.check_cancel()
        raise
    if bundle.interpretation_status.get("error"):
        bundle.warnings.append(
            "大模型增强解读失败，已自动保留离线科研规则解读："
            f"{bundle.interpretation_status['error']}"
        )
    if request.interpretation_mode == MODE_LLM:
        reporter.complete(
            "interpretation",
            "已回退到离线规则" if bundle.interpretation_status.get("error") else "大模型增强解读已完成",
            warning=bool(bundle.interpretation_status.get("error")),
        )
    if PROTEIN not in request.selected_types and request.input_type == INPUT_ID:
        bundle.sequences = [record for record in bundle.sequences if record.sequence_type != PROTEIN]
    reporter.update("report", 0, 1, "正在生成 Word、Excel 与 ZIP")
    primary = bundle.inputs[0] if bundle.inputs else "batch"
    artifacts = build_report_artifacts(
        bundle,
        primary,
        efp_charts=efp_charts,
        prediction_charts=prediction_charts,
        prediction_raw_artifacts=prediction_raw,
        deep_charts=deep_charts,
        deep_raw_artifacts=deep_raw,
    )
    artifacts["efp_charts"] = efp_charts
    artifacts["prediction_charts"] = prediction_charts
    artifacts["deep_charts"] = deep_charts
    reporter.complete("report", "Word、Excel 与 ZIP 已生成")
    return bundle, artifacts


def _show_results_legacy(bundle: AnalysisBundle, artifacts: dict[str, object]) -> None:
    overview_tab, ricedata_tab, efp_tab, sequence_tab, prediction_tab, regulation_tab, variation_tab, provenance_tab = st.tabs(
        ["概览", "注释与证据", "表达", "序列与基因结构", "蛋白分析", "调控", "变异", "来源与警告"]
    )
    with overview_tab:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("输入", len(bundle.inputs))
        c2.metric("序列", len(bundle.sequences))
        c3.metric("预测", len(bundle.predictions))
        failures = sum(result.status not in {"matched", "partial"} for result in bundle.predictions)
        c4.metric("预测失败", failures)
        st.dataframe(pd.DataFrame(bundle.mapping_rows), width="stretch", hide_index=True)
        st.subheader("规则化科研判断卡")
        st.markdown(f"- **已有证据**：数据库/人工遗传证据 {len(bundle.genetic_evidence)} 条；可核对文献 {len(bundle.literature_rows)} 篇。")
        st.markdown(f"- **计算支持**：结构域 {len(bundle.protein_domains)}，TFBS {len(bundle.promoter_tfbs)}，miRNA/sRNA 靶点 {len(bundle.mirna_targets)}。")
        st.markdown("- **证据冲突/缺口**：查看本页“来源与警告”中的 assembly、REF、外部任务与全文核对提示。")
        st.markdown("- **建议验证方向**：优先验证有多源支持的功能位点、近 TSS motif、编码变异和文献互作线索。")
    with ricedata_tab:
        if bundle.ricedata_rows:
            st.dataframe(pd.DataFrame(bundle.ricedata_rows), width="stretch", hide_index=True)
        else:
            st.info("本次未选择 RiceData，或没有可用的基因映射。")
        if bundle.literature_rows:
            st.subheader("文献元数据（需人工核对全文）")
            st.dataframe(pd.DataFrame(bundle.literature_rows), width="stretch", hide_index=True)
        if bundle.genetic_evidence:
            st.subheader("已知/人工导入遗传证据")
            st.dataframe(pd.DataFrame(bundle.genetic_evidence), width="stretch", hide_index=True)
    with efp_tab:
        if bundle.efp_rows:
            efp_frame = pd.DataFrame([record.summary_row() for record in bundle.efp_rows])
            st.dataframe(efp_frame, width="stretch", hide_index=True)
            top_frame = pd.DataFrame(expression_top_rows(bundle.efp_rows, limit=3))
            if not top_frame.empty:
                st.subheader("高表达组织 / 处理")
                st.dataframe(top_frame, width="stretch", hide_index=True)
            chart_artifacts = artifacts.get("efp_charts", {})
            if isinstance(chart_artifacts, dict):
                heatmaps = sorted(name for name in chart_artifacts if name.startswith("heatmap_") and name.endswith(".png"))
                for name in heatmaps:
                    st.image(chart_artifacts[name], caption=name.removesuffix(".png").replace("_", " "))
                genes = sorted({record.msu_locus for record in bundle.efp_rows if record.status == "matched"})
                sources = sorted({record.data_source for record in bundle.efp_rows if record.status == "matched"})
                if genes and sources:
                    c1, c2 = st.columns(2)
                    selected_gene = c1.selectbox("eFP 柱状图基因", genes, key=f"efp_gene_{id(bundle)}")
                    selected_source = c2.selectbox(
                        "eFP 数据源",
                        sources,
                        format_func=efp_source_display_label,
                        key=f"efp_source_{id(bundle)}",
                    )
                    name = f"bar_{selected_gene}_{selected_source}.png"
                    if name in chart_artifacts:
                        st.image(chart_artifacts[name], caption=f"{selected_gene} · {efp_source_display_label(selected_source)}")
        else:
            st.info("本次未选择 Rice eFP，或没有可用的 LOC_Os 映射。")
    with sequence_tab:
        st.dataframe(pd.DataFrame([record.summary_row() for record in bundle.sequences]), width="stretch", hide_index=True)
        for sequence_type in SEQUENCE_TYPES:
            matches = [record for record in bundle.sequences if record.sequence_type == sequence_type]
            if not matches:
                continue
            with st.expander(f"{sequence_type}（{len(matches)} 条）"):
                for record in matches[:5]:
                    st.markdown(f"**{record.transcript_id or record.input_id} · {record.length}**")
                    st.code(record.sequence[:3000] + ("…" if len(record.sequence) > 3000 else ""), language=None)
        if bundle.transcript_models:
            st.subheader("转录本模型")
            st.dataframe(pd.DataFrame(bundle.transcript_models), width="stretch", hide_index=True)
        deep_charts = artifacts.get("deep_charts", {})
        if isinstance(deep_charts, dict):
            for name in sorted(deep_charts):
                if name.startswith("gene_structure/") and name.endswith(".png"):
                    st.image(deep_charts[name], caption=name.split("/")[-1])
    with prediction_tab:
        if bundle.predictions:
            st.dataframe(pd.DataFrame([result.summary_row() for result in bundle.predictions]), width="stretch", hide_index=True)
            proteins = list(dict.fromkeys(result.protein_id for result in bundle.predictions))
            selected_protein = st.selectbox(
                "选择蛋白查看综合定位图",
                proteins,
                key=f"prediction_protein_{id(bundle)}",
            )
            chart_artifacts = artifacts.get("prediction_charts", {})
            if isinstance(chart_artifacts, dict):
                protein_stem = safe_file_stem(selected_protein, "protein")
                chart_name = f"combined_{protein_stem}.png"
                if chart_name in chart_artifacts:
                    st.image(chart_artifacts[chart_name], caption=f"{selected_protein} · integrated localization tracks")
                for name in sorted(chart_artifacts):
                    if name.startswith(f"scores_{protein_stem}_") and name.endswith(".png"):
                        st.image(chart_artifacts[name], caption=name.removesuffix(".png").replace("_", " "))
            region_rows = [row for result in bundle.predictions for row in result.region_rows()]
            if region_rows:
                st.subheader("预测区段")
                st.dataframe(pd.DataFrame(region_rows), width="stretch", hide_index=True)
            score_rows = [row for result in bundle.predictions for row in result.probability_rows()]
            if score_rows:
                st.subheader("分类概率 / scores")
                st.dataframe(pd.DataFrame(score_rows), width="stretch", hide_index=True)
            failed = [result for result in bundle.predictions if result.status not in {"matched", "partial"}]
            for result in failed:
                with st.container(border=True):
                    st.error(f"{result.protein_id} · {result.tool}：{result.error or result.status}")
                    attempts = result.attempt_rows()
                    if attempts:
                        st.dataframe(pd.DataFrame(attempts), width="stretch", hide_index=True)
                    if result.result_url:
                        st.link_button("打开手动提交入口", result.result_url)
            st.subheader("预测一致性说明")
            for item in prediction_consistency(bundle.predictions):
                st.markdown(f"- {item}")
        else:
            st.info("本次没有选择蛋白定位预测。")
        if bundle.protein_domains:
            st.subheader("蛋白结构域")
            st.dataframe(pd.DataFrame(bundle.protein_domains), width="stretch", hide_index=True)
        if bundle.functional_sites:
            st.subheader("功能位点")
            st.dataframe(pd.DataFrame(bundle.functional_sites), width="stretch", hide_index=True)
        deep_charts = artifacts.get("deep_charts", {})
        if isinstance(deep_charts, dict):
            for name in sorted(deep_charts):
                if name.startswith("protein_domains/") and name.endswith(".png"):
                    st.image(deep_charts[name], caption=name.split("/")[-1])
    with regulation_tab:
        st.caption("TFBS 结果均为 motif-based prediction；miRNA/RNAi 结果均为计算预测。")
        if bundle.promoter_tfbs:
            st.subheader("启动子 TFBS")
            st.dataframe(pd.DataFrame(bundle.promoter_tfbs), width="stretch", hide_index=True)
        if bundle.upstream_tfs:
            st.subheader("候选上游 TF")
            st.dataframe(pd.DataFrame(bundle.upstream_tfs), width="stretch", hide_index=True)
        if bundle.mirna_targets:
            st.subheader("miRNA/sRNA 靶点")
            st.dataframe(pd.DataFrame(bundle.mirna_targets), width="stretch", hide_index=True)
        if bundle.rnai_offtargets:
            st.subheader("RNAi 潜在脱靶")
            st.dataframe(pd.DataFrame(bundle.rnai_offtargets), width="stretch", hide_index=True)
        deep_charts = artifacts.get("deep_charts", {})
        if isinstance(deep_charts, dict):
            for name in sorted(deep_charts):
                if name.startswith("promoter_regulation/") and name.endswith(".png"):
                    st.image(deep_charts[name], caption=name.split("/")[-1])
    with variation_tab:
        if bundle.variants:
            st.dataframe(pd.DataFrame(bundle.variants), width="stretch", hide_index=True)
        else:
            st.info("未取得可解析变异。可上传 IRGSP-1.0 基因区段 VCF/VCF.GZ 作为稳定入口。")
        if bundle.haplotypes:
            st.subheader("单倍型汇总")
            st.dataframe(pd.DataFrame(bundle.haplotypes), width="stretch", hide_index=True)
        deep_charts = artifacts.get("deep_charts", {})
        if isinstance(deep_charts, dict):
            for name in sorted(deep_charts):
                if name.startswith("variation/") and name.endswith(".png"):
                    st.image(deep_charts[name], caption=name.split("/")[-1])
    with provenance_tab:
        for warning in bundle.warnings:
            st.warning(warning)
        st.markdown("**数据与服务来源**")
        for source in bundle.sources:
            st.markdown(f"- {source}")

    stem = str(artifacts["stem"])
    st.divider()
    col1, col2, col3 = st.columns(3)
    col1.download_button(
        "下载 Word 报告",
        artifacts["docx"],
        file_name=f"{stem}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    col2.download_button(
        "下载 Excel 数据",
        artifacts["xlsx"],
        file_name=f"{stem}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    col3.download_button(
        f"下载完整 ZIP（{format_bytes(len(artifacts['zip']))}）",
        artifacts["zip"],
        file_name=f"{stem}.zip",
        mime="application/zip",
        type="primary",
    )


def _render_interpretations(bundle: AnalysisBundle, sections: set[str] | None = None) -> None:
    rows = [row for row in bundle.interpretations if sections is None or str(row.get("section")) in sections]
    for row in rows:
        with st.container(border=True):
            title = str(row.get("title") or "结果解读")
            if str(row.get("section") or "").startswith("ai_"):
                title = f"🤖 {title}（AI辅助推断·待人工核验）"
            st.markdown(f"**{title}**")
            st.write(str(row.get("interpretation") or ""))
            st.caption(
                f"证据依据：{row.get('evidence_basis') or '—'}　·　"
                f"证据等级：{row.get('evidence_level') or '—'}　·　"
                f"置信度：{row.get('confidence') or '—'}"
            )
            st.markdown(f"**解读边界**：{row.get('limitations') or '—'}")
            st.markdown(f"**建议下一步**：{row.get('recommended_action') or '—'}")


def _render_ai_synthesis(bundle: AnalysisBundle) -> None:
    synthesis = bundle.ai_synthesis or {}
    status = bundle.interpretation_status
    requested = status.get("requested_mode") == MODE_LLM
    if not requested:
        st.info("本次未选择大模型增强，因此没有生成独立 AI 深度解读报告。")
        return
    report_mode = str(synthesis.get("report_mode") or "evidence_fallback")
    if report_mode != "ai":
        st.warning("AI 未完成合格的深度综合；当前展示可追溯的证据整理版，不新增无来源机制。")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("机制证据", len(bundle.mechanism_claims))
    c2.metric("入模证据", int(status.get("evidence_claims_sent") or 0))
    c3.metric("论文摘要", int(status.get("literature_abstracts_sent") or 0))
    c4.metric("报告模式", "AI 综合" if report_mode == "ai" else "证据版")
    st.caption(
        f"对象：{', '.join(bundle.inputs)} · 模型：{status.get('model_label') or status.get('model') or '未使用'} · "
        f"证据引用校验：{status.get('evidence_reference_validation') or '未运行'}"
    )
    st.subheader("三分钟读懂这个基因")
    with st.container(border=True):
        st.write(str(synthesis.get("executive_summary") or "当前没有可用摘要。"))
        identity = synthesis.get("gene_identity") if isinstance(synthesis.get("gene_identity"), dict) else {}
        core = synthesis.get("core_function") if isinstance(synthesis.get("core_function"), dict) else {}
        st.markdown(f"**它是什么**：{identity.get('summary') or '—'}")
        st.markdown(f"**分子角色**：{identity.get('molecular_role') or '—'}")
        st.markdown(f"**作用位置**：{identity.get('localization') or '—'}")
        st.markdown(f"**核心功能**：{core.get('summary') or '—'}")

    st.subheader("核心机制链")
    chains = synthesis.get("mechanism_chains") if isinstance(synthesis.get("mechanism_chains"), list) else []
    if chains:
        for item in chains:
            if not isinstance(item, dict):
                continue
            with st.container(border=True):
                st.markdown(f"**{item.get('title') or '机制链'}** · {item.get('context') or '未特异场景'}")
                st.markdown(
                    f"{item.get('upstream') or '—'} → **{item.get('molecular_event') or '—'}** → "
                    f"{item.get('downstream') or '—'} → {item.get('phenotype') or '—'}"
                )
                st.caption(f"证据：{', '.join(map(str, item.get('evidence_ids') or []))} · 等级：{item.get('confidence') or '—'}")
    else:
        st.info("当前证据不足以形成机制链。")

    st.subheader("已知机制 / 本次观察 / AI 假设")
    known_col, observed_col, hypothesis_col = st.columns(3)
    with known_col:
        st.markdown("**已知机制**")
        for item in synthesis.get("context_branches", []) if isinstance(synthesis.get("context_branches"), list) else []:
            if isinstance(item, dict):
                st.markdown(f"- **{item.get('context') or '场景'}**：{item.get('interpretation') or ''} `{'/'.join(map(str, item.get('evidence_ids') or []))}`")
    with observed_col:
        st.markdown("**本次观察**")
        for item in synthesis.get("omics_integration", []) if isinstance(synthesis.get("omics_integration"), list) else []:
            if isinstance(item, dict):
                st.markdown(f"- {item.get('observation') or ''}\n\n  {item.get('interpretation') or ''}")
    with hypothesis_col:
        st.markdown("**AI 假设**")
        hypotheses = synthesis.get("testable_hypotheses") if isinstance(synthesis.get("testable_hypotheses"), list) else []
        if not hypotheses:
            st.caption("证据版不自动新增假设。")
        for item in hypotheses:
            if isinstance(item, dict):
                st.markdown(f"- **{item.get('hypothesis') or ''}**\n\n  {item.get('rationale') or ''}")

    st.subheader("可检验假设与实验")
    for index, item in enumerate(synthesis.get("testable_hypotheses", []) if isinstance(synthesis.get("testable_hypotheses"), list) else [], 1):
        if not isinstance(item, dict):
            continue
        with st.expander(f"假设 {index}：{item.get('hypothesis') or '未命名'}", expanded=index == 1):
            st.markdown(f"**实验**：{item.get('experiment') or '—'}")
            st.markdown(f"**对照**：{item.get('controls') or '—'}")
            st.markdown(f"**读出**：{item.get('readouts') or '—'}")
            st.markdown(f"**判别结果**：{item.get('discriminating_result') or '—'}")

    st.subheader("证据与参考文献")
    referenced = set(map(str, synthesis.get("references", []))) if isinstance(synthesis.get("references"), list) else set()
    rows = [row for row in bundle.mechanism_claims if not referenced or str(row.get("evidence_id")) in referenced]
    if rows:
        frame = pd.DataFrame(rows)
        columns = [value for value in ["evidence_id", "evidence_level", "context", "statement", "dois", "source_type", "verification_status"] if value in frame.columns]
        st.dataframe(frame[columns], width="stretch", hide_index=True)
    st.caption("统一边界：数据库整理与摘要需回到全文；组学不等于因果；PTM 位点需结合总蛋白和位点占有率。")


def _show_results(bundle: AnalysisBundle, artifacts: dict[str, object]) -> None:
    """Render the v1.9.7 evidence-led result surface."""
    overview_tab, evidence_tab, expression_tab, sequence_tab, regulation_tab, ai_tab, conclusion_tab = st.tabs(
        ["总览", "功能与证据", "表达", "序列与结构", "调控与变异", "AI 深度解读", "结论与来源"]
    )
    deep_charts = artifacts.get("deep_charts", {}) if isinstance(artifacts.get("deep_charts", {}), dict) else {}

    with overview_tab:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("输入", len(bundle.inputs))
        c2.metric("功能/机制证据", len(bundle.mechanism_claims) or len(bundle.genetic_evidence))
        c3.metric("关联文献", len(bundle.literature_rows))
        c4.metric("有效序列", sum(record.status == "matched" for record in bundle.sequences))
        st.subheader("基因身份与数据完整度")
        if bundle.mapping_rows:
            st.dataframe(pd.DataFrame(bundle.mapping_rows), width="stretch", hide_index=True)
        else:
            st.info("未形成可用的 ID 映射。")
        with st.container(border=True):
            st.markdown(f"**摘要**　RiceData {len(bundle.ricedata_rows)} 条 · eFP {len(bundle.efp_rows)} 条 · 可统计多组学 {len(bundle.lab_omics_differential)} 条 · 论文组学证据 {len(bundle.lab_omics_published_evidence)} 条 · 定位预测 {len(bundle.predictions)} 条 · 变异 {len(bundle.variants)} 条")
            severe = [warning for warning in bundle.warnings if any(token in warning for token in ("assembly", "REF", "失败", "一对多", "不一致"))]
            st.caption(f"可能改变解释的主要警告：{len(severe)} 项。完整信息见“结论与来源”。")
        st.subheader("报告解读摘要")
        _render_interpretations(bundle, {"overall", "ai_overall"})

    with evidence_tab:
        st.caption("按“证据描述 → 支持/关联论文 → 来源 → 核验状态”集中展示。")
        evidence_rows = []
        references = {str(row.get("doi") or "").casefold(): row for row in bundle.ricedata_references}
        used: set[str] = set()
        for evidence in bundle.genetic_evidence:
            dois = [value.strip() for value in str(evidence.get("linked_dois") or "").split(",") if value.strip()]
            used.update(value.casefold() for value in dois)
            titles = [references.get(value.casefold(), {}).get("title") or value for value in dois]
            evidence_rows.append({
                "证据描述": evidence.get("evidence_text", ""),
                "支持/关联论文": "; ".join(str(value) for value in titles) or "未解析到关联论文",
                "DOI": ", ".join(dois),
                "来源": evidence.get("source_type", ""),
                "核验状态": evidence.get("verification_status", ""),
                "matched_by": evidence.get("matched_by", ""),
            })
        for reference in bundle.ricedata_references:
            if str(reference.get("doi") or "").casefold() in used:
                continue
            evidence_rows.append({
                "证据描述": "RiceData 关联文献（未直接映射到当前遗传证据）",
                "支持/关联论文": reference.get("title", ""),
                "DOI": reference.get("doi", ""),
                "来源": f"RiceData ref {reference.get('reference_id') or ''}",
                "核验状态": reference.get("verification_status", "需全文核验"),
                "matched_by": reference.get("matched_by", "ricedata_reference_id"),
            })
        if bundle.mechanism_claims:
            st.subheader("可追溯功能与机制证据")
            mechanism_frame = pd.DataFrame(bundle.mechanism_claims)
            columns = [value for value in ["evidence_id", "evidence_level", "context", "statement", "dois", "verification_status"] if value in mechanism_frame.columns]
            st.dataframe(mechanism_frame[columns], width="stretch", hide_index=True)
        if evidence_rows:
            st.dataframe(pd.DataFrame(evidence_rows), width="stretch", hide_index=True)
        else:
            st.info("本次未取得已知遗传证据或关联文献。")
        with st.expander("完整 RiceData 注释与文献元数据"):
            if bundle.ricedata_rows:
                st.dataframe(pd.DataFrame(bundle.ricedata_rows), width="stretch", hide_index=True)
            if bundle.literature_rows:
                st.dataframe(pd.DataFrame(bundle.literature_rows), width="stretch", hide_index=True)

    with expression_tab:
        st.caption("Absolute 为官网返回的原始尺度值，本工具不做二次标准化；它不是 fold change，不同数据源不可直接比较。")
        chart_artifacts = artifacts.get("efp_charts", {}) if isinstance(artifacts.get("efp_charts", {}), dict) else {}
        matched = [record for record in bundle.efp_rows if record.status == "matched"]
        if matched:
            top_frame = pd.DataFrame(expression_top_rows(bundle.efp_rows, limit=3))
            if not top_frame.empty:
                st.subheader("高表达组织 / 处理")
                st.dataframe(top_frame, width="stretch", hide_index=True)
            genes = sorted({record.msu_locus for record in matched})
            sources = sorted({record.data_source for record in matched})
            c1, c2 = st.columns(2)
            selected_gene = c1.selectbox("基因", genes, key=f"efp_gene_v171_{id(bundle)}")
            selected_source = c2.selectbox("数据源", sources, format_func=efp_source_display_label, key=f"efp_source_v171_{id(bundle)}")
            name = f"bar_{selected_gene}_{selected_source}.png"
            if name in chart_artifacts:
                st.image(chart_artifacts[name], caption=f"{selected_gene} · {efp_source_display_label(selected_source)}")
            with st.expander("完整 eFP 数值与数据源说明"):
                st.dataframe(pd.DataFrame([record.summary_row() for record in bundle.efp_rows]), width="stretch", hide_index=True)
                _render_efp_source_guide(f"result_{id(bundle)}")
        else:
            st.info("本次未选择 eFP，ID 未映射，或外部服务未返回定量表。")

        st.divider()
        st.subheader("水稻多组学证据")
        st.caption("主组学区仅包含有生物学重复的可统计数据；论文报告结果单独展示。正log2FC表示treatment/control上调。")
        st.markdown("#### 可统计组学数据")
        if bundle.lab_omics_differential or bundle.lab_omics_profiles:
            primary_datasets = [row for row in bundle.lab_omics_datasets if row.get("search_section") == "primary"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("数据集", len(primary_datasets))
            c2.metric("比较", len({str(row.get('comparison_id')) for row in bundle.lab_omics_differential}))
            c3.metric("差异记录", len(bundle.lab_omics_differential))
            c4.metric("定量记录", len(bundle.lab_omics_profiles))
            cross_heatmap = "lab_omics/heatmap_cross_project_log2fc.png"
            if cross_heatmap in deep_charts:
                st.image(deep_charts[cross_heatmap], caption="Gene × treatment source log2FC heatmap; ordered conditions; missing values in gray")
            differential_frame = pd.DataFrame(bundle.lab_omics_differential)
            if not differential_frame.empty:
                dataset_options = list(dict.fromkeys(str(value) for value in differential_frame["dataset_name"] if value))
                selected_dataset = st.selectbox("多组学数据集", dataset_options, key=f"lab_omics_dataset_{id(bundle)}")
                selected_rows = differential_frame[differential_frame["dataset_name"] == selected_dataset]
                columns = [
                    value for value in [
                        "msu_locus", "msu_model", "rap_gene", "comparison_name", "assay", "log2fc",
                        "ratio", "pvalue", "padj", "regulated", "site_position", "site_residue",
                        "modified_sequence", "descriptive", "replicate_note", "source_file", "source_sheet", "source_row",
                    ] if value in selected_rows.columns
                ]
                st.dataframe(selected_rows[columns], width="stretch", hide_index=True)
            project_pngs = [name for name in sorted(deep_charts) if name.startswith("lab_omics/project_") and name.endswith("_abundance_heatmap.png")]
            if project_pngs:
                chosen_plot = st.selectbox(
                    "项目内样本热图",
                    project_pngs,
                    format_func=lambda value: value.removeprefix("lab_omics/project_").removesuffix("_abundance_heatmap.png"),
                    key=f"lab_omics_project_plot_{id(bundle)}",
                )
                st.image(deep_charts[chosen_plot], caption="Within-project abundance pattern; row z-score only inside this dataset")
            with st.expander("完整项目、样本与定量明细"):
                if bundle.lab_omics_datasets:
                    st.dataframe(pd.DataFrame(primary_datasets), width="stretch", hide_index=True)
                if bundle.lab_omics_samples:
                    st.dataframe(pd.DataFrame(bundle.lab_omics_samples), width="stretch", hide_index=True)
                if bundle.lab_omics_profiles:
                    st.dataframe(pd.DataFrame(bundle.lab_omics_profiles), width="stretch", hide_index=True)
        else:
            st.info("当前基因在具有生物学重复的主组学数据中没有命中，或数据库尚未解锁。")
        st.markdown("#### 已发表论文证据")
        st.warning("本区结果不进入主热图、候选基因统计评分或自动机制结论；不能据此独立判断统计显著性或因果机制。")
        if bundle.lab_omics_published_evidence:
            published_frame = pd.DataFrame(bundle.lab_omics_published_evidence).fillna("【未报告】")
            published_columns = [
                column for column in [
                    "msu_locus", "msu_model", "rap_gene", "dataset_name", "assay", "feature_type",
                    "description", "ratio", "log2fc", "pvalue", "padj", "direction",
                    "replicate_note", "evidence_level", "evidence_boundary", "raw_data_availability",
                    "mapping_status", "mapping_note", "source_file", "source_sheet", "source_row", "source_page",
                    "dataset_risk_note",
                ] if column in published_frame.columns
            ]
            st.dataframe(published_frame[published_columns], width="stretch", hide_index=True)
            published_ids = {str(row.get("dataset_id")) for row in bundle.lab_omics_published_evidence}
            published_context = [row for row in bundle.lab_omics_dataset_context if str(row.get("dataset_id")) in published_ids]
            with st.expander("论文证据来源、重复结构与分析边界"):
                st.dataframe(pd.DataFrame(published_context).fillna("【未报告】"), width="stretch", hide_index=True)
        else:
            st.info("当前基因未命中已映射的论文证据。")
        if bundle.lab_omics_consensus_scores:
            with st.expander("公开RNA-seq跨项目启发式候选评分（非实验验证）"):
                st.dataframe(pd.DataFrame(bundle.lab_omics_consensus_scores), width="stretch", hide_index=True)
        with st.expander("完整多组学数据集注册表（13个可统计数据集 + 5个论文证据来源）"):
            st.dataframe(pd.DataFrame(bundle.lab_omics_dataset_registry).fillna("【未报告】"), width="stretch", hide_index=True)
        for status in bundle.lab_omics_status:
            if status.get("inclusion_status") == "absent":
                st.info(f"{status.get('display_name')}：暂无合格数据")
        st.subheader("多组学科研解读")
        _render_interpretations(bundle, {"lab_omics", "ai_lab_omics"})

    with sequence_tab:
        st.caption("序列关系图分开标注 RAP/MSU 来源、长度、assembly 与 CDS→protein 一致性；不同 genomic span 不强行叠加。")
        sequence_pngs = [name for name in sorted(deep_charts) if name.startswith("sequence_structure/") and name.endswith(".png")]
        if sequence_pngs:
            st.image(deep_charts[sequence_pngs[0]], caption="输入 ID → RAP/MSU → promoter/genomic/UTR/CDS/protein")
        else:
            st.info("本次未生成序列关系图。")
        if bundle.sequence_plot_rows:
            st.dataframe(pd.DataFrame(bundle.sequence_plot_rows), width="stretch", hide_index=True)
        for prefix, title in (("gene_structure/", "真实 exon/CDS/UTR 坐标"), ("protein_domains/", "蛋白结构域")):
            images = [name for name in sorted(deep_charts) if name.startswith(prefix) and name.endswith(".png")]
            if images:
                st.subheader(title)
                st.image(deep_charts[images[0]], caption=images[0].split("/")[-1])
        with st.expander("完整序列与转录本明细"):
            if bundle.sequences:
                st.dataframe(pd.DataFrame([record.summary_row() for record in bundle.sequences]), width="stretch", hide_index=True)
            if bundle.transcript_models:
                st.dataframe(pd.DataFrame(bundle.transcript_models), width="stretch", hide_index=True)

    with regulation_tab:
        st.caption("顺序：可能作用位置 → 上游调控 → 序列变异影响。以下结果除明确数据库证据外均为计算预测。")
        if bundle.predictions:
            st.subheader("可能作用位置")
            prediction_images = artifacts.get("prediction_charts", {}) if isinstance(artifacts.get("prediction_charts", {}), dict) else {}
            combined = [name for name in sorted(prediction_images) if name.startswith("combined_") and name.endswith(".png")]
            if combined:
                st.image(prediction_images[combined[0]], caption="Integrated localization prediction")
            st.dataframe(pd.DataFrame([result.summary_row() for result in bundle.predictions]), width="stretch", hide_index=True)
        else:
            st.info("本次未运行蛋白定位预测。")
        st.subheader("上游调控")
        promoter_images = [name for name in sorted(deep_charts) if name.startswith("promoter_regulation/") and name.endswith(".png")]
        if promoter_images:
            st.image(deep_charts[promoter_images[0]], caption="Promoter TFBS distribution")
        if bundle.upstream_tfs:
            st.dataframe(pd.DataFrame(bundle.upstream_tfs[:20]), width="stretch", hide_index=True)
        elif not bundle.promoter_tfbs:
            st.info("未选择启动子调控分析，或服务未返回结果。")
        st.subheader("序列变异影响")
        variation_images = [name for name in sorted(deep_charts) if name.startswith("variation/") and name.endswith(".png")]
        if variation_images:
            st.image(deep_charts[variation_images[0]], caption="Variant / haplotype summary")
        if bundle.variants:
            st.dataframe(pd.DataFrame(bundle.variants[:30]), width="stretch", hide_index=True)
        else:
            st.info("未取得可解析变异；没有样本 GT 时不推断单倍型。")
        with st.expander("miRNA/RNAi 与完整调控/变异明细"):
            if bundle.mirna_targets:
                st.dataframe(pd.DataFrame(bundle.mirna_targets), width="stretch", hide_index=True)
            if bundle.haplotypes:
                st.dataframe(pd.DataFrame(bundle.haplotypes), width="stretch", hide_index=True)
        st.subheader("单倍型科研解读")
        _render_interpretations(bundle, {"haplotype", "ai_haplotype"})

    with ai_tab:
        _render_ai_synthesis(bundle)

    with conclusion_tab:
        st.subheader("综合科研判断")
        _render_interpretations(bundle, {"overall", "lab_omics", "haplotype"})
        status = bundle.interpretation_status
        effective = "大模型增强 + 离线规则" if status.get("effective_mode") == MODE_LLM else "离线科研规则"
        provider = str(status.get("provider_label") or "")
        client_version = str(status.get("client_version") or "")
        codex_details: tuple[str, ...] = ()
        if status.get("provider") == PROVIDER_CODEX_CHATGPT:
            codex_details = tuple(
                value for value in (
                    str(status.get("model_label") or ""),
                    f"推理：{status.get('reasoning_label')}" if status.get("reasoning_label") else "",
                    f"速度：{status.get('speed_label')}" if status.get("speed_label") else "",
                ) if value
            )
        provider_detail = " · ".join(value for value in (provider, client_version, *codex_details) if value)
        st.caption(
            f"本次解读模式：{effective}"
            + (f"（{provider_detail}）" if provider_detail else "")
            + f"。{status.get('privacy') or ''}"
        )
        for warning in bundle.warnings:
            st.warning(warning)
        st.markdown("**数据与服务来源**")
        for source in bundle.sources:
            st.markdown(f"- {source}")

    stem = str(artifacts["stem"])
    st.divider()
    col1, col2, col3, col4 = st.columns(4)
    col1.download_button("下载 Word 报告", artifacts["docx"], file_name=f"{stem}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    ai_docx = artifacts.get("ai_docx")
    if isinstance(ai_docx, bytes) and ai_docx:
        col2.download_button("AI 深度解读 Word", ai_docx, file_name=f"{artifacts.get('ai_stem') or stem + '_ai'}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    else:
        col2.caption("本次无独立 AI 报告")
    col3.download_button("下载 Excel 数据", artifacts["xlsx"], file_name=f"{stem}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    col4.download_button(f"下载完整 ZIP（{format_bytes(len(artifacts['zip']))}）", artifacts["zip"], file_name=f"{stem}.zip", mime="application/zip", type="primary")


@st.fragment(run_every=1.0)
def _render_job_list() -> None:
    snapshots = JOB_MANAGER.snapshots()
    if not snapshots:
        st.info("尚未提交分析项目。提交后可切换到其他工具，任务会在 APP 保持打开时继续运行。")
        return
    for item in snapshots:
        label = STATUS_LABELS.get(item.status, item.status)
        if item.status == "queued" and item.queue_position:
            label += f" · 队列第 {item.queue_position} 位"
        with st.container(border=True):
            left, right = st.columns([4, 1])
            left.markdown(f"**{item.project_name}**")
            right.caption(label)
            render_progress_breakdown(item.progress_items)
            st.caption(f"提交：{item.created_at}" + (f" · 完成：{item.finished_at}" if item.finished_at else ""))
            controls = st.columns(4)
            if item.status in {"queued", "running"}:
                if controls[0].button("取消", key=f"cancel_{item.job_id}"):
                    JOB_MANAGER.cancel(item.job_id)
            if item.status in {"failed", "cancelled"}:
                if controls[1].button("重试", key=f"retry_{item.job_id}"):
                    new_id = JOB_MANAGER.retry(item.job_id)
                    if new_id:
                        st.session_state.selected_rice_job_id = new_id
            if item.status in {"completed", "completed_with_warnings"}:
                if controls[2].button("查看结果", key=f"view_{item.job_id}", type="primary"):
                    st.session_state.selected_rice_job_id = item.job_id
                    st.rerun(scope="app")
            if item.error:
                st.error(item.error)


def _render_selected_job_result() -> None:
    snapshots = JOB_MANAGER.snapshots()
    selected_job_id = st.session_state.get("selected_rice_job_id", "")
    selected = next((item for item in snapshots if item.job_id == selected_job_id), None)
    if selected and selected.status in {"completed", "completed_with_warnings"}:
        bundle, artifacts, _ = JOB_MANAGER.get_result(selected.job_id)
        if isinstance(bundle, AnalysisBundle) and isinstance(artifacts, dict):
            st.divider()
            st.subheader(f"项目结果：{selected.project_name}")
            _show_results(bundle, artifacts)
    elif selected and selected.status == "failed":
        _, _, traceback_text = JOB_MANAGER.get_result(selected.job_id)
        if traceback_text:
            with st.expander("技术详情"):
                st.code(traceback_text, language=None)


def run() -> None:
    page_header(
        "Rice gene workbench",
        "水稻基因一站式分析",
        "输入 RAP/MSU ID、CDS 或蛋白序列，在后台整合序列、表达、蛋白、转录本、调控、变异、miRNA 和文献证据，并生成 Word、Excel 与 ZIP。",
        ["后台队列", "六项深度模块", "单基因 + 批量", "可追溯报告"],
    )
    tool_website(__name__)
    st.info("坐标统一为 IRGSP-1.0。深度模块会按需查询 InterPro、Ensembl、PlantRegMap、RiceVarMap、psRNATarget 和 Europe PMC；任一外站失败只产生警告，不中断报告。")
    with st.expander("先看懂：整个一站式分析怎样工作、能得到什么"):
        st.markdown(
            "**总流程：** 输入与 ID 解析 → RAP/MSU 映射 → 按勾选项取得序列/数据库记录 → "
            "运行表达与预测模块 → 保留每项 status/error → 汇总为网页、Word、Excel 和完整 ZIP。"
        )
        st.dataframe(
            explanation_rows(SEQUENCE_AND_RESOURCE_EXPLANATIONS),
            width="stretch",
            hide_index=True,
        )
        st.caption("工作流、一致性检查、证据分层与报告交付")
        st.dataframe(
            explanation_rows(WORKFLOW_EXPLANATIONS),
            width="stretch",
            hide_index=True,
        )
    mode = st.radio("分析模式", [MODE_SINGLE, MODE_BATCH], horizontal=True)
    input_type = st.radio("输入类型", [INPUT_ID, INPUT_CDS, INPUT_PROTEIN], horizontal=True)

    if input_type == INPUT_ID:
        text = st.text_area(
            "RAP/MSU ID",
            height=160,
            placeholder="Os01g0100100\nOs01t0100100-01\nLOC_Os01g01010.1",
            help="支持 RAP gene/transcript 与 MSU locus/model；批量模式可换行、逗号或分号分隔。",
        )
    else:
        text = st.text_area(
            input_type,
            height=220,
            placeholder=">query\nATG..." if input_type == INPUT_CDS else ">query\nMST...",
        )

    selected_types = tuple(
        st.multiselect("需要的序列", list(SEQUENCE_TYPES), default=list(SEQUENCE_TYPES))
    )
    promoter_length = st.slider(
        "启动子长度（bp）",
        500,
        4000,
        2000,
        step=100,
        disabled=PROMOTER not in selected_types,
    )
    transcript_scope_label = st.radio(
        "转录本范围",
        ["Canonical transcript", "全部 transcript"],
        horizontal=True,
        help="直接输入明确的 transcript/model 时始终优先使用该模型。",
    )
    transcript_scope = TRANSCRIPT_SCOPE_CANONICAL if transcript_scope_label == "Canonical transcript" else TRANSCRIPT_SCOPE_ALL

    default_predictors = list(PREDICTORS) if mode == MODE_SINGLE else []
    selected_predictors = st.multiselect("蛋白定位预测（每项可独立选择）", list(PREDICTORS), default=default_predictors)
    with st.expander("蛋白定位预测：每个工具怎么做、得到什么"):
        st.dataframe(explanation_rows(PREDICTOR_EXPLANATIONS), width="stretch", hide_index=True)
        st.caption("这些结果均为 computational prediction。建议联合多个算法判断，再用亚细胞定位、分泌或膜拓扑实验验证。")
    with st.expander("预测高级参数"):
        signalp_mode = st.radio("SignalP 6.0 mode", ["fast", "slow-sequential"], horizontal=True)
        cnls_cutoff = st.select_slider("cNLS Mapper cutoff", options=[2.0, 3.0, 4.0, 5.0, 6.0, 7.0], value=5.0)
        nlstradamus_model_label = st.radio("NLStradamus model", ["two-state", "four-state"], horizontal=True)
        nlstradamus_cutoff = st.slider("NLStradamus posterior cutoff", 0.1, 1.0, 0.6, 0.05)
    max_workers = st.slider("序列来源并发请求", 1, 4, 3)

    st.subheader("六项深度分析")
    select_all_deep = st.checkbox("一键选择全部深度模块", value=False)
    chosen_deep = st.multiselect(
        "深度模块（每项可独立选择）",
        list(DEEP_ANALYSES),
        default=list(DEFAULT_DEEP_ANALYSES if mode == MODE_SINGLE else ()),
        format_func=lambda value: DEEP_ANALYSES[value],
        disabled=select_all_deep,
    )
    selected_deep_analyses = tuple(DEEP_ANALYSES) if select_all_deep else tuple(chosen_deep)
    with st.expander("六项深度模块：数据来源、处理方法、产出与证据边界"):
        st.dataframe(explanation_rows(DEEP_ANALYSIS_EXPLANATIONS), width="stretch", hide_index=True)
    with st.expander("深度分析高级参数与上传入口"):
        promoter_pvalue_text = st.selectbox("PlantRegMap p-value", ["1e-3", "1e-4", "1e-5", "1e-6", "1e-7"], index=1)
        variation_vcf = st.file_uploader("可选：基因区段 VCF / VCF.GZ", type=["vcf", "gz"])
        sample_groups = st.file_uploader("可选：样本分组表（CSV）", type=["csv"], key="rice_sample_groups")
        mirna_mode_label = st.radio("miRNA/RNAi 流程", ["已知水稻 miRNA", "自定义 miRNA/siRNA"], horizontal=True)
        custom_srna_text = st.text_area("自定义 sRNA FASTA", height=100, disabled=mirna_mode_label == "已知水稻 miRNA")
        mirna_expectation = st.number_input("psRNATarget expectation", min_value=0.0, max_value=10.0, value=5.0, step=0.5)
        mirna_max_upe = st.number_input("Maximum UPE", min_value=0.0, max_value=100.0, value=25.0, step=1.0)
        mirna_offtargets = st.checkbox("自定义 sRNA 全水稻 transcript library 脱靶任务", value=False, disabled=mirna_mode_label == "已知水稻 miRNA")
        evidence_file = st.file_uploader("可选：人工证据表 CSV/XLSX", type=["csv", "xlsx"])
    st.caption("深度模块每批最多 20 个基因/蛋白；外部服务失败只产生警告，不中断其他分析。")

    st.subheader("基因信息与表达谱")
    resource_col1, resource_col2, resource_col3 = st.columns(3)
    include_ricedata = resource_col1.checkbox("RiceData 基因信息", value=True)
    include_efp = resource_col2.checkbox("Rice eFP 定量表达谱", value=True)
    include_lab_omics = resource_col3.checkbox("水稻多组学证据", value=True)
    ricedata_depth_label = st.radio(
        "RiceData 检索深度",
        ["自动：单基因完整 / 批量快速", "快速基础信息", "完整功能信息"],
        horizontal=True,
        disabled=not include_ricedata,
    )
    ricedata_depth = {
        "自动：单基因完整 / 批量快速": "adaptive",
        "快速基础信息": "fast",
        "完整功能信息": "full",
    }[ricedata_depth_label]
    efp_data_sources = tuple(
        st.multiselect(
            "eFP 数据源（Absolute 模式）",
            list(EFP_DATA_SOURCES),
            default=list(DEFAULT_EFP_DATA_SOURCES),
            format_func=efp_source_display_label,
            disabled=not include_efp,
        )
    )
    st.caption(
        "eFP 原始表保留官网行；单基因按 Expression Level 绘制横向图，并将官网 Standard Deviation 字段作为误差线；批量生成基因×组织/处理热图。"
    )
    with st.expander("水稻多组学证据范围与解读边界", expanded=True):
        st.markdown(
            "在当前‘水稻基因一站式分析’内按 **MSU locus** 检索，不增加重复菜单。"
            "主组学区仅纳入具有可核实生物学重复的处理 vs 对照；论文报告结果进入独立证据区。"
        )
        st.markdown(
            "- 单基因：展示不同病毒、昆虫、组学、时间点和PTM位点明细。\n"
            "- 批量：生成基因 × 处理的已有 log2FC 热图。\n"
            "- 项目内热图：使用已有FPKM、TPM、count或归一化蛋白/PTM定量，只在该数据集内做row z-score。\n"
            "- 时间列保持原项目顺序；缺失值为灰色。\n"
            "- 无可核实生物学重复的旧实验室数据已从生产库移除。\n"
            "- 论文证据保留原附件、sheet、行号/PDF页码和未报告字段，不与统一重分析结果混用。"
        )
    with st.expander("Rice eFP 详解：APP 怎么获取数据，以及 12 个数据源分别代表什么", expanded=True):
        st.markdown(
            "**APP 的处理步骤**\n\n"
            "1. 先把普通 eFP 数据源路由到 `LOC_Osxxgxxxxx`（MSU）；Single-cell 数据源路由到 `OsXXgXXXXXXX`（RAP）。\n"
            "2. 向 BAR Rice eFP 提交 **Absolute** 模式请求，读取官网返回的 probe ID 和临时定量表。\n"
            "3. 原样保留 group、tissue/treatment、expression level、SD、samples、实验链接和查询状态；不做跨数据集二次标准化。\n"
            "4. 单基因按数据源绘制官网 Expression Level，并将官网 Standard Deviation 字段作为误差线；多个基因另生成 gene × tissue/treatment 热图。\n"
            "5. 网页显示主图与完整数值；Excel/ZIP 保存原始表、Top tissues、数据源词典、SVG 和 600 dpi PNG。"
        )
        st.warning(
            "Absolute 不是 fold change。RMA、MAS5 intensity 及未标明单位的官方 Expression Level 属于不同尺度；"
            "只能在同一数据源内部比较组织或处理模式，不能把不同数据源的绝对值直接比较。"
            "部分汇总型数据源的 SD 字段为 0，这不代表不存在细胞间或生物学变异。"
        )
        _render_efp_source_guide("settings")
        st.markdown(f"官方查询入口：[BAR Rice eFP]({EFP_URL})")

    st.subheader("结果解读")
    _initialize_interpretation_preferences()
    interpretation_mode = st.radio(
        "选择解读方式",
        list(_INTERPRETATION_MODE_LABELS),
        format_func=lambda value: _INTERPRETATION_MODE_LABELS[value],
        key="rice_interpretation_mode",
        horizontal=True,
        help="两种方式都会保留证据依据、解读边界与建议实验。大模型失败时自动回退到离线规则。",
    )
    interpretation_provider = ""
    interpretation_base_url = ""
    interpretation_model = ""
    interpretation_api_key = ""
    interpretation_codex_reasoning = CODEX_DEFAULT_REASONING
    interpretation_codex_speed = CODEX_DEFAULT_SPEED
    interpretation_cloud_consent = True
    interpretation_codex_ready = True
    interpretation_consent_nonce = int(st.session_state.get("rice_interpretation_consent_nonce", 0))
    if interpretation_mode == MODE_LLM:
        interpretation_provider = st.selectbox(
            "大模型来源",
            list(_INTERPRETATION_PROVIDER_LABELS),
            format_func=lambda value: _INTERPRETATION_PROVIDER_LABELS[value],
            key="rice_interpretation_provider",
            help="云端 API Key 只保留在本次 APP 会话中，退出后需要重新输入。",
        )
        if interpretation_provider == PROVIDER_CODEX_CHATGPT:
            codex_model_values = tuple(value for value, _label in CODEX_MODEL_OPTIONS)
            model_state_key = "rice_codex_model"
            if st.session_state.get(model_state_key, CODEX_ACCOUNT_MODEL) not in codex_model_values:
                st.session_state[model_state_key] = CODEX_ACCOUNT_MODEL
            model_col, reasoning_col, speed_col = st.columns(3)
            with model_col:
                interpretation_model = st.selectbox(
                    "Codex 模型",
                    codex_model_values,
                    format_func=codex_model_label,
                    key=model_state_key,
                    help="自动选择会跟随当前 ChatGPT/Codex 账号的默认模型。",
                )
            reasoning_values = codex_reasoning_options(interpretation_model)
            reasoning_state_key = "rice_codex_reasoning"
            if st.session_state.get(reasoning_state_key, CODEX_DEFAULT_REASONING) not in reasoning_values:
                st.session_state[reasoning_state_key] = CODEX_DEFAULT_REASONING
            with reasoning_col:
                interpretation_codex_reasoning = st.selectbox(
                    "推理能力",
                    reasoning_values,
                    format_func=codex_reasoning_label,
                    key=reasoning_state_key,
                    help="档位越高，复杂推断通常越充分，但耗时与额度消耗也可能增加。",
                )
            speed_values = codex_speed_options(interpretation_model)
            speed_state_key = "rice_codex_speed"
            if st.session_state.get(speed_state_key, CODEX_DEFAULT_SPEED) not in speed_values:
                st.session_state[speed_state_key] = CODEX_DEFAULT_SPEED
            with speed_col:
                interpretation_codex_speed = st.selectbox(
                    "响应速度",
                    speed_values,
                    format_func=codex_speed_label,
                    key=speed_state_key,
                    help="快速模式约提升 1.5 倍响应速度，并会消耗更多 ChatGPT/Codex 额度。",
                )
            if interpretation_codex_speed == CODEX_FAST_SPEED:
                st.info("已选择快速模式：响应约快 1.5 倍，但会消耗更多 ChatGPT/Codex 额度。")
            codex_status = _cached_codex_client_status()
            account_ready = bool(codex_status.get("authenticated"))
            st.caption(str(codex_status.get("message") or "尚未检测到可用的 ChatGPT/Codex 登录。"))
            codex_fingerprint = _connection_fingerprint(
                interpretation_provider,
                "chatgpt-account",
                interpretation_model,
                reasoning=interpretation_codex_reasoning,
                speed=interpretation_codex_speed,
            )
            if st.button(
                "验证 Codex 模型连接",
                key="verify_rice_codex_connection",
                disabled=not account_ready,
                use_container_width=True,
            ):
                _cached_codex_client_status.clear()
                with st.spinner("正在验证所选 Codex 模型…"):
                    try:
                        detail = probe_codex_connection(
                            model=interpretation_model,
                            reasoning_effort=interpretation_codex_reasoning,
                            speed=interpretation_codex_speed,
                        )
                        _remember_connection_result(codex_fingerprint, True, detail)
                    except Exception as exc:
                        _remember_connection_result(codex_fingerprint, False, str(exc))
            _render_connection_result(codex_fingerprint)
            interpretation_codex_ready = account_ready
            st.caption("验证会发送一条不含科研数据的最小测试消息，并消耗少量 ChatGPT/Codex 额度。")
            interpretation_cloud_consent = st.checkbox(
                "我同意将去标识化的报告结构化证据通过当前 ChatGPT 账号发送给 OpenAI，并消耗 ChatGPT/Codex 使用额度",
                value=False,
                key=f"rice_codex_consent_{interpretation_consent_nonce}",
            )
            st.caption(
                "此入口调用 ChatGPT 账号登录的 Codex CLI，不直接操控 ChatGPT Work 窗口；"
                "不会创建或保留 Codex 任务记录。"
            )
        elif interpretation_provider == PROVIDER_OLLAMA:
            interpretation_base_url = st.text_input(
                "模型服务地址",
                key="rice_ollama_base_url",
            )
            interpretation_model = st.text_input(
                "模型名称",
                key="rice_ollama_model",
                placeholder="例：qwen2.5:14b",
            )
        elif interpretation_provider in CLOUD_API_PROVIDERS:
            preset = cloud_provider(interpretation_provider)
            base_key, model_key = cloud_preference_keys(interpretation_provider)
            interpretation_base_url = st.text_input(
                "API 服务地址",
                key=f"rice_{base_key}",
                help="已预填官方 OpenAI-compatible 地址；私有部署或区域端点可自行修改。",
            )
            interpretation_model = st.text_input(
                "模型名称 / 接入点 ID",
                key=f"rice_{model_key}",
                placeholder=preset.model_placeholder,
            )
            interpretation_api_key = st.text_input(
                "API Key（仅用于本次请求，不保存、不写入报告）",
                type="password",
                key=f"rice_{preset.preference_prefix}_api_key",
            )
            interpretation_cloud_consent = st.checkbox(
                f"我同意将去标识化的报告结构化证据发送到 {preset.label}",
                value=False,
                key=f"rice_{preset.preference_prefix}_api_consent_{interpretation_consent_nonce}",
            )
            st.caption(f"[查看 {preset.label} 接口文档]({preset.api_docs_url})")
        if interpretation_provider == PROVIDER_OLLAMA or interpretation_provider in CLOUD_API_PROVIDERS:
            api_fingerprint = _connection_fingerprint(
                interpretation_provider,
                interpretation_base_url,
                interpretation_model,
                interpretation_api_key,
            )
            verify_label = "验证 Ollama 连接" if interpretation_provider == PROVIDER_OLLAMA else "验证 API 连接"
            if st.button(verify_label, key=f"verify_rice_{interpretation_provider}_connection", use_container_width=True):
                with st.spinner("正在发送最小连接测试…"):
                    try:
                        detail = probe_model_connection(
                            provider=interpretation_provider,
                            base_url=interpretation_base_url,
                            model=interpretation_model,
                            api_key=interpretation_api_key,
                        )
                        _remember_connection_result(api_fingerprint, True, detail)
                    except Exception as exc:
                        _remember_connection_result(api_fingerprint, False, str(exc))
            _render_connection_result(api_fingerprint)
            st.caption("验证只发送固定文本 Reply with OK，不包含科研数据。")
        st.caption("发送 Word、Excel 和 ZIP 中可解释内容的同源结构化证据；不发送二进制文件、原始序列、图片、样本名、密码、令牌、密钥和本地路径。")
    try:
        saved_interpretation_preferences = _persist_interpretation_preferences()
        # On restart this is already launched from main.py.  Calling it here is
        # also safe and automatically verifies a newly selected configuration.
        start_model_connection_test(
            saved_interpretation_preferences,
            api_key=interpretation_api_key,
            background=True,
        )
    except OSError as exc:
        st.warning(f"模型选择暂时无法保存：{exc}")
    project_name = st.text_input(
        "项目名称（可选）",
        placeholder="例：OsPTM 候选基因一站式分析",
    )

    pending = st.session_state.get("rice_gene_pending_candidates")
    selected_candidate = ""
    execute = st.button("开始分析并生成报告", type="primary")
    if pending and pending.get("input_type") == input_type and pending.get("text") == text:
        st.warning("输入序列精确匹配到多个 RAP transcript，请选择用于补齐基因上下文的候选。")
        selected_candidate = st.selectbox("候选 RAP transcript", pending["candidates"])
        execute = st.button("使用所选候选继续") or execute

    if execute:
        identifiers = parse_input_ids(text) if input_type == INPUT_ID else parse_fasta_or_sequence(text)
        input_count = len(identifiers)
        validation_error = ""
        if not input_count:
            validation_error = "请提供至少一个有效输入。"
        elif mode == MODE_SINGLE and input_count != 1:
            validation_error = "单基因深度分析每次只接受一个 ID 或一条 FASTA 记录。"
        elif input_count > MAX_SEQUENCE_BATCH:
            validation_error = f"序列批量最多 {MAX_SEQUENCE_BATCH} 个输入。"
        elif selected_predictors and input_count > MAX_PREDICTION_BATCH:
            validation_error = f"勾选预测时最多处理 {MAX_PREDICTION_BATCH} 条蛋白。"
        elif selected_deep_analyses and input_count > MAX_PREDICTION_BATCH:
            validation_error = f"勾选深度模块时最多处理 {MAX_PREDICTION_BATCH} 个基因/蛋白。"
        elif include_efp and input_count > EFP_MAX_GENES:
            validation_error = f"勾选 eFP 时每个项目最多 {EFP_MAX_GENES} 个基因；请拆分项目或取消 eFP。"
        elif include_efp and not efp_data_sources:
            validation_error = "已勾选 eFP，请至少选择一个数据源。"
        elif interpretation_provider == PROVIDER_CODEX_CHATGPT and not interpretation_codex_ready:
            validation_error = "ChatGPT/Codex 尚未安装、版本过旧或未使用 ChatGPT 登录；请按提示处理后刷新检测。"
        elif interpretation_mode == MODE_LLM and interpretation_provider != PROVIDER_CODEX_CHATGPT and not interpretation_base_url.strip():
            validation_error = "请填写大模型服务地址。"
        elif interpretation_mode == MODE_LLM and interpretation_provider != PROVIDER_CODEX_CHATGPT and not interpretation_model.strip():
            validation_error = "请填写大模型名称。"
        elif interpretation_provider in CLOUD_API_PROVIDERS and not interpretation_api_key.strip():
            validation_error = "使用云端 API 时请填写 API Key。"
        elif (interpretation_provider == PROVIDER_CODEX_CHATGPT or interpretation_provider in CLOUD_API_PROVIDERS) and not interpretation_cloud_consent:
            validation_error = "请先确认同意发送去标识化报告结构化证据，或改用离线规则/本机 Ollama。"
        elif not selected_types and not selected_predictors and not selected_deep_analyses and not include_ricedata and not include_efp and not include_lab_omics:
            validation_error = "请至少选择一种序列、预测工具或基因信息模块。"

        if validation_error:
            st.error(validation_error)
        else:
            if input_type != INPUT_ID and mode == MODE_SINGLE and not selected_candidate:
                first_sequence = identifiers[0][1]
                candidates = exact_reference_matches(first_sequence, input_type)
                if len(candidates) > 1:
                    st.session_state.rice_gene_pending_candidates = {
                        "input_type": input_type,
                        "text": text,
                        "candidates": candidates,
                    }
                    st.rerun()
            st.session_state.pop("rice_gene_pending_candidates", None)
            first_label = identifiers[0] if input_type == INPUT_ID else identifiers[0][0]
            generated_name = f"{first_label} · {datetime.now().strftime('%H:%M:%S')}"
            request = RiceGeneAnalysisRequest(
                project_name=project_name.strip() or generated_name,
                mode=mode,
                input_type=input_type,
                text=text,
                selected_types=selected_types,
                promoter_length=promoter_length,
                transcript_scope=transcript_scope,
                selected_predictors=tuple(selected_predictors),
                signalp_mode=signalp_mode,
                cnls_cutoff=float(cnls_cutoff),
                nlstradamus_model=1 if nlstradamus_model_label == "two-state" else 2,
                nlstradamus_cutoff=float(nlstradamus_cutoff),
                max_workers=int(max_workers),
                selected_candidate=selected_candidate,
                include_ricedata=include_ricedata,
                ricedata_depth=ricedata_depth,
                include_efp=include_efp,
                efp_data_sources=efp_data_sources,
                include_lab_omics=include_lab_omics,
                selected_deep_analyses=selected_deep_analyses,
                promoter_pvalue=float(promoter_pvalue_text),
                variation_vcf_name=variation_vcf.name if variation_vcf else "",
                variation_vcf_bytes=variation_vcf.getvalue() if variation_vcf else b"",
                sample_groups_name=sample_groups.name if sample_groups else "",
                sample_groups_bytes=sample_groups.getvalue() if sample_groups else b"",
                mirna_mode="known_mirna" if mirna_mode_label == "已知水稻 miRNA" else "custom_srna",
                custom_srna_text=custom_srna_text,
                mirna_expectation=float(mirna_expectation),
                mirna_max_upe=float(mirna_max_upe),
                mirna_offtargets=mirna_offtargets,
                evidence_file_name=evidence_file.name if evidence_file else "",
                evidence_file_bytes=evidence_file.getvalue() if evidence_file else b"",
                interpretation_mode=interpretation_mode,
                interpretation_provider=interpretation_provider,
                interpretation_base_url=interpretation_base_url.strip(),
                interpretation_model=interpretation_model.strip(),
                interpretation_api_key=interpretation_api_key.strip(),
                interpretation_codex_reasoning=interpretation_codex_reasoning,
                interpretation_codex_speed=interpretation_codex_speed,
            )
            job_id = JOB_MANAGER.submit(request, execute_analysis_request)
            if interpretation_provider in {PROVIDER_CODEX_CHATGPT, PROVIDER_OPENAI_COMPATIBLE}:
                st.session_state.rice_interpretation_consent_nonce = interpretation_consent_nonce + 1
            st.session_state.selected_rice_job_id = job_id
            st.success("项目已加入后台队列。现在可以切换到其他界面或最小化 APP。")

    st.divider()
    st.subheader("分析项目")
    _render_job_list()
    _render_selected_job_result()


__all__ = [
    "INPUT_CDS",
    "INPUT_ID",
    "INPUT_PROTEIN",
    "MODE_BATCH",
    "MODE_SINGLE",
    "add_predictions",
    "analyze_id_inputs",
    "analyze_sequence_inputs",
    "run",
]
