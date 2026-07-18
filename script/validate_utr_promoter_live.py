#!/usr/bin/env python3
"""Live acceptance test for the UTR/promoter backend used by unified analysis."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from RAP_MSU_convert import load_mapping_index  # noqa: E402
from rice_utr_promoter_downloader import (  # noqa: E402
    FIVE_UTR,
    PROMOTER,
    THREE_UTR,
    TRANSCRIPT_SCOPE_ALL,
    batch_fetch_sequences,
    build_download_zip,
    fetch_assembly_metadata,
    resolve_input_ids,
    summary_frame,
)


QUERY_IDS = ["Os01g0100300", "LOC_Os01g01010.1"]
PROMOTER_LENGTH = 500
EXPECTED = {
    "Os01g0100300": {
        "rap_gene": "Os01g0100300",
        "strand": -1,
        "transcript": "Os01t0100300-00",
        "five_utr_length": 0,
        "three_utr_length": 0,
    },
    "LOC_Os01g01010.1": {
        "rap_gene": "Os01g0100100",
        "strand": 1,
        "transcript": "Os01t0100100-01",
        "five_utr_length": 381,
        "three_utr_length": 445,
    },
}


def digest(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest() if sequence else ""


def main() -> int:
    os.chdir(APP_SOURCE)
    _, msu_to_rap = load_mapping_index()
    targets = resolve_input_ids(QUERY_IDS, msu_to_rap)
    assembly, chromosome_lengths, assembly_url = fetch_assembly_metadata()
    results = batch_fetch_sequences(
        targets,
        TRANSCRIPT_SCOPE_ALL,
        (FIVE_UTR, THREE_UTR, PROMOTER),
        PROMOTER_LENGTH,
        chromosome_lengths,
        max_workers=2,
    )
    by_input = {result.target.input_id: result for result in results}

    checks: dict[str, bool] = {
        "assembly_is_irgsp_1_0": assembly == "IRGSP-1.0",
        "both_input_ids_resolved": set(by_input) == set(QUERY_IDS),
    }
    records: list[dict[str, object]] = []
    for input_id in QUERY_IDS:
        result = by_input[input_id]
        payload = result.payload
        expected = EXPECTED[input_id]
        transcript = payload.transcripts[0] if payload and payload.transcripts else None
        checks[f"{input_id}:gene_status_matched"] = bool(payload and payload.status == "matched")
        checks[f"{input_id}:rap_mapping"] = bool(payload and payload.rap_gene_id == expected["rap_gene"])
        checks[f"{input_id}:strand"] = bool(payload and payload.strand == expected["strand"])
        checks[f"{input_id}:promoter_length"] = bool(
            payload and len(payload.promoter_sequence) == PROMOTER_LENGTH
        )
        checks[f"{input_id}:transcript"] = bool(
            transcript and transcript.transcript_id == expected["transcript"]
        )
        checks[f"{input_id}:five_utr_length"] = bool(
            transcript and len(transcript.five_utr_sequence) == expected["five_utr_length"]
        )
        checks[f"{input_id}:three_utr_length"] = bool(
            transcript and len(transcript.three_utr_sequence) == expected["three_utr_length"]
        )
        if expected["strand"] == -1:
            checks[f"{input_id}:negative_strand_orientation_note"] = bool(
                payload and "反向互补" in payload.validation_note
            )
        records.append(
            {
                "input_id": input_id,
                "input_type": result.target.input_type,
                "resolution_status": result.target.status,
                "resolved_rap_gene": result.target.rap_gene_id,
                "gene_status": payload.status if payload else "",
                "assembly": payload.assembly if payload else "",
                "annotation_source": payload.annotation_source if payload else "",
                "chromosome": payload.chromosome if payload else "",
                "gene_start": payload.gene_start if payload else None,
                "gene_end": payload.gene_end if payload else None,
                "strand": payload.strand if payload else None,
                "promoter_start": payload.promoter_start if payload else None,
                "promoter_end": payload.promoter_end if payload else None,
                "promoter_length": len(payload.promoter_sequence) if payload else 0,
                "promoter_sha256": digest(payload.promoter_sequence) if payload else "",
                "transcript": transcript.transcript_id if transcript else "",
                "five_utr_length": len(transcript.five_utr_sequence) if transcript else None,
                "five_utr_sha256": digest(transcript.five_utr_sequence) if transcript else "",
                "three_utr_length": len(transcript.three_utr_sequence) if transcript else None,
                "three_utr_sha256": digest(transcript.three_utr_sequence) if transcript else "",
                "validation_note": payload.validation_note if payload else "",
                "error": payload.error if payload else result.target.error,
                "gene_lookup_url": payload.gene_lookup_url if payload else "",
                "promoter_source_url": payload.promoter_source_url if payload else "",
                "cdna_source_url": transcript.source_url if transcript else "",
            }
        )

    success = all(checks.values())
    report = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "query_ids": QUERY_IDS,
        "promoter_length": PROMOTER_LENGTH,
        "assembly": assembly,
        "assembly_metadata_url": assembly_url,
        "records": records,
        "checks": checks,
        "summary_rows": summary_frame(results).to_dict(orient="records"),
    }

    output_dir = ROOT / "analysis_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "utr_promoter_live_validation_20260717.json"
    archive_path = output_dir / "utr_promoter_RAP_MSU_verified_sequences_20260717.zip"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive_path.write_bytes(
        build_download_zip(results, (FIVE_UTR, THREE_UTR, PROMOTER), PROMOTER_LENGTH)
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Validation report: {report_path}")
    print(f"Verified sequence bundle: {archive_path}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
