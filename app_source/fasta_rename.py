"""Rename FASTA identifiers from a two-column mapping."""

from __future__ import annotations

from dataclasses import dataclass
import gzip

import streamlit as st

from app_ui import page_header, tool_explanation


@dataclass(frozen=True)
class MappingParseResult:
    mapping: dict[str, str]
    invalid_lines: list[int]
    duplicate_ids: list[str]


@dataclass(frozen=True)
class RenameResult:
    text: str
    sequence_count: int
    renamed_count: int
    unchanged_ids: list[str]


def parse_mapping_details(tsv_text: str) -> MappingParseResult:
    mapping: dict[str, str] = {}
    invalid_lines: list[int] = []
    duplicate_ids: list[str] = []
    for line_number, raw in enumerate(tsv_text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        delimiter = "\t" if "\t" in line else "," if "," in line else None
        if delimiter is None:
            invalid_lines.append(line_number)
            continue
        old, new = [value.strip() for value in line.split(delimiter, 1)]
        if not old or not new:
            invalid_lines.append(line_number)
            continue
        if old in mapping:
            duplicate_ids.append(old)
        mapping[old] = new
    return MappingParseResult(mapping, invalid_lines, sorted(set(duplicate_ids)))


def parse_mapping(tsv_text: str) -> dict[str, str]:
    """Backward-compatible mapping parser used by existing workflows."""
    return parse_mapping_details(tsv_text).mapping


def rename_fasta_with_stats(fasta_text: str, mapping: dict[str, str]) -> RenameResult:
    output_lines: list[str] = []
    unchanged_ids: list[str] = []
    sequence_count = 0
    renamed_count = 0

    for line in fasta_text.splitlines():
        if not line.startswith(">"):
            output_lines.append(line)
            continue
        sequence_count += 1
        header = line[1:].strip()
        if not header:
            output_lines.append(line)
            continue
        parts = header.split(maxsplit=1)
        old_id = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        new_id = mapping.get(old_id)
        if new_id is None:
            unchanged_ids.append(old_id)
            new_id = old_id
        else:
            renamed_count += 1
        output_lines.append(f">{new_id}{' ' + rest if rest else ''}")

    final_newline = "\n" if fasta_text.endswith(("\n", "\r")) else ""
    return RenameResult("\n".join(output_lines) + final_newline, sequence_count, renamed_count, unchanged_ids)


def rename_fasta_headers(fasta_text: str, mapping: dict[str, str]) -> str:
    return rename_fasta_with_stats(fasta_text, mapping).text


def decode_upload(uploaded_file) -> str:
    raw = uploaded_file.getvalue()
    if getattr(uploaded_file, "name", "").lower().endswith(".gz"):
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise UnicodeError(f"压缩文件无法解压：{exc}") from exc
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError("无法识别文件编码")


def run() -> None:
    page_header(
        "Sequence utility",
        "FASTA ID 批量重命名",
        "用两列对应表替换 FASTA 标题中的第一个 ID 字段，保留后续描述并报告未匹配序列。",
        ["保留原描述", "重复映射检查", "未匹配报告"],
    )
    tool_explanation(__name__)

    col1, col2 = st.columns(2)
    with col1:
        fasta_file = st.file_uploader("上传 FASTA", type=["fa", "fasta", "fna", "faa", "txt", "gz"])
    with col2:
        mapping_file = st.file_uploader("上传两列对应表", type=["txt", "tsv", "csv"])
        manual_mapping = st.text_area(
            "或粘贴对应表",
            height=130,
            placeholder="Os01t0100100-01\tGeneA\nOs01t0100200-01\tGeneB",
        )

    if not st.button("开始重命名", type="primary"):
        return
    if fasta_file is None:
        st.error("请先上传 FASTA 文件。")
        return
    try:
        fasta_text = decode_upload(fasta_file)
        mapping_text = decode_upload(mapping_file) if mapping_file is not None else manual_mapping
    except UnicodeError as exc:
        st.error(str(exc))
        return
    if not mapping_text.strip():
        st.error("请上传或粘贴 ID 对应表。")
        return

    parsed = parse_mapping_details(mapping_text)
    if not parsed.mapping:
        st.error("未解析出有效映射。对应表必须是“旧 ID + Tab/逗号 + 新 ID”。")
        return
    result = rename_fasta_with_stats(fasta_text, parsed.mapping)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("FASTA 序列", f"{result.sequence_count:,}")
    m2.metric("成功重命名", f"{result.renamed_count:,}")
    m3.metric("未匹配", f"{len(result.unchanged_ids):,}")
    m4.metric("有效映射", f"{len(parsed.mapping):,}")

    if parsed.invalid_lines:
        st.warning(f"对应表第 {', '.join(map(str, parsed.invalid_lines[:12]))} 行格式无效，已跳过。")
    if parsed.duplicate_ids:
        st.warning(f"发现 {len(parsed.duplicate_ids)} 个重复旧 ID；使用最后一次出现的映射。")

    st.download_button(
        "下载重命名后的 FASTA",
        result.text.encode("utf-8"),
        file_name="renamed_sequences.fasta",
        mime="text/plain",
        type="primary",
    )
    if result.unchanged_ids:
        with st.expander(f"查看 {len(result.unchanged_ids)} 个未匹配序列 ID"):
            st.code("\n".join(result.unchanged_ids), language=None)
    with st.expander("预览前 30 行"):
        st.code("\n".join(result.text.splitlines()[:30]), language=None)
