"""OCR lane: deferred OCR in the PDF extractor, job priorities, worker lane plans."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from doc_manager.domain.enums import DEFAULT_JOB_PRIORITY, JobType
from doc_manager.extraction.errors import OcrRequired
from doc_manager.extraction.pdf import PdfExtractor, PdfOcrOptions
from doc_manager.jobs.worker import _lane_plan


def _pdf(path: Path, pages: list[str]) -> None:
    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        if body:
            page.insert_text((72, 72), body)
    doc.save(path)
    doc.close()


def test_deferred_ocr_raises_instead_of_running_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_ocr(self: PdfExtractor, page: object) -> str:
        raise AssertionError("OCR must not run in deferred mode")

    monkeypatch.setattr(PdfExtractor, "_ocr_page", _no_ocr)
    pdf = tmp_path / "scan.pdf"
    _pdf(pdf, ["has text", ""])
    extractor = PdfExtractor(PdfOcrOptions(), defer_ocr=True)
    assert extractor.version == "pdf-1-ocr"  # same version as the OCR lane's run
    with pytest.raises(OcrRequired):
        extractor.extract(pdf)


def test_deferred_ocr_passes_text_pdfs_through(tmp_path: Path) -> None:
    pdf = tmp_path / "text.pdf"
    _pdf(pdf, ["page one", "page two"])
    result = PdfExtractor(PdfOcrOptions(), defer_ocr=True).extract(pdf)
    assert [p.text.strip() for p in result.pages] == ["page one", "page two"]


def test_ocr_runs_last_and_quick_jobs_first() -> None:
    assert DEFAULT_JOB_PRIORITY[JobType.ocr_file] < 0
    assert DEFAULT_JOB_PRIORITY.get(JobType.index_file, 0) == 0
    assert DEFAULT_JOB_PRIORITY[JobType.scan_location] > 0
    assert DEFAULT_JOB_PRIORITY[JobType.sync_name_index] > 0


GENERAL = (JobType.scan_location, JobType.index_file)
OCR = (JobType.ocr_file,)


def test_general_lane_skips_ocr_until_idle_long_enough() -> None:
    assert _lane_plan("general", GENERAL, OCR, idle_for=5, ocr_join_idle_seconds=60) == [GENERAL]
    assert _lane_plan("general", GENERAL, OCR, idle_for=60, ocr_join_idle_seconds=60) == [
        GENERAL + OCR
    ]


def test_ocr_lane_prefers_ocr_then_helps_elsewhere() -> None:
    assert _lane_plan("ocr", GENERAL, OCR, idle_for=0, ocr_join_idle_seconds=60) == [OCR, GENERAL]


def test_any_lane_claims_everything_by_priority() -> None:
    assert _lane_plan("any", GENERAL, OCR, idle_for=0, ocr_join_idle_seconds=60) == [GENERAL + OCR]
