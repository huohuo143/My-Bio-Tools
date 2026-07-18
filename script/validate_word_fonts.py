#!/usr/bin/env python3
"""Build a representative report and audit Chinese/Western Word fonts."""

from __future__ import annotations

import io
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from report_builder import CHINESE_FONT, WESTERN_FONT, build_report_artifacts  # noqa: E402
from rice_efp import EfpExpressionRecord  # noqa: E402
from rice_gene_core import AnalysisBundle, CDS, SequenceRecord  # noqa: E402


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REQUIRED_PARTS = ("word/document.xml", "word/header1.xml", "word/footer1.xml")
REQUIRED_STYLES = {"Normal", "Title", "Heading1", "Heading2", "Heading3", "ListBullet", "ListNumber"}


def _audit_rfonts(font, location: str) -> None:
    expected = {
        "eastAsia": CHINESE_FONT,
        "ascii": WESTERN_FONT,
        "hAnsi": WESTERN_FONT,
        "cs": WESTERN_FONT,
    }
    for attribute, value in expected.items():
        observed = font.get(f"{{{W_NS}}}{attribute}")
        if observed != value:
            raise AssertionError(f"{location}: {attribute}={observed!r}, expected {value!r}")


def audit_docx(payload: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in REQUIRED_PARTS:
            root = ET.fromstring(archive.read(name))
            fonts = list(root.iter(f"{{{W_NS}}}rFonts"))
            if not fonts:
                raise AssertionError(f"{name}: no explicit rFonts")
            for index, font in enumerate(fonts, 1):
                _audit_rfonts(font, f"{name} rFonts[{index}]")

        styles_root = ET.fromstring(archive.read("word/styles.xml"))
        checked: set[str] = set()
        for style in styles_root.iter(f"{{{W_NS}}}style"):
            style_id = style.get(f"{{{W_NS}}}styleId")
            if style_id not in REQUIRED_STYLES:
                continue
            fonts = list(style.iter(f"{{{W_NS}}}rFonts"))
            if not fonts:
                raise AssertionError(f"style {style_id}: no explicit rFonts")
            for index, font in enumerate(fonts, 1):
                _audit_rfonts(font, f"style {style_id} rFonts[{index}]")
            checked.add(style_id)
        if checked != REQUIRED_STYLES:
            raise AssertionError(f"missing audited styles: {sorted(REQUIRED_STYLES - checked)}")

        xml_text = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
            if name.endswith(".xml")
        )
        if "Arial Unicode MS" in xml_text:
            raise AssertionError("legacy Chinese font remains in DOCX XML")


def build_fixture() -> AnalysisBundle:
    return AnalysisBundle(
        mode="单基因深度分析",
        input_type="RAP/MSU ID",
        inputs=["Os01g0100100"],
        mapping_rows=[
            {
                "input_id": "Os01g0100100",
                "input_type": "RAP gene",
                "resolved_rap_gene": "Os01g0100100",
                "resolved_msu_id": "LOC_Os01g01010",
                "status": "matched",
            }
        ],
        sequences=[
            SequenceRecord(
                input_id="Os01g0100100",
                resolved_rap_gene="Os01g0100100",
                resolved_msu_id="LOC_Os01g01010",
                transcript_id="Os01t0100100-01",
                sequence_type=CDS,
                sequence="ATGAAATAA",
                source="RAP-DB bundled IRGSP-1.0 CDS",
                assembly="IRGSP-1.0",
            )
        ],
        ricedata_rows=[
            {
                "check": "Os01g0100100",
                "GeneName": "水稻候选基因",
                "GeneSymbol": "OsTEST1",
                "RAP_Locus": "Os01g0100100",
                "MSU_Locus": "LOC_Os01g01010",
                "生物学功能": "用于验证中文华文仿宋与西文 Times New Roman 的混合排版。",
                "status": "matched",
                "error": "",
            }
        ],
        efp_rows=[
            EfpExpressionRecord(
                input_id="Os01g0100100",
                msu_locus="LOC_Os01g01010",
                data_source="rice_rma",
                data_source_label="Developmental atlas (RMA)",
                tissue="Seedling Root 幼苗根",
                expression_level=9.48,
                standard_deviation=0.07,
                samples="Root_1, Root_2, Root_3",
                probe_id="Os.1.1.S1_at",
            )
        ],
        warnings=["字体验证样例：中文应为华文仿宋；Western text and IDs should use Times New Roman."],
        sources=["IRGSP-1.0", "BAR Rice eFP"],
        generated_at="2026-07-18T12:00:00+08:00",
    )


def main() -> int:
    output_dir = ROOT / "analysis_results" / "v1.9.1_word_font_validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = build_report_artifacts(build_fixture(), "word_font_validation")
    payload = artifacts["docx"]
    if not isinstance(payload, bytes):
        raise TypeError("report builder did not return DOCX bytes")
    audit_docx(payload)
    output_path = output_dir / "word_font_validation_v1.9.1.docx"
    output_path.write_bytes(payload)
    print(f"Word font validation passed: East Asia={CHINESE_FONT}; ASCII/HAnsi={WESTERN_FONT}")
    print(f"DOCX: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
