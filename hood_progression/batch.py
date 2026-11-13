"""Batch generation: scan folders of Hood PDFs and HEYEX XMLs, group by patient/eye/date,
pair visits, and write one progression report per pair.

Visits are keyed on (patient ID, eye, exam date).  A visit is usable when it has
its Hood PDF; the circle and posterior-pole XMLs are optional (missing ones leave
that panel blank, as in the single-report GUI).

Pairing modes:
    "latest"      earliest visit -> most recent visit            (one report per eye)
    "all"         earliest visit -> every later visit            (n-1 reports)
    "consecutive" each visit -> the next one                     (n-1 reports)
"""
from __future__ import annotations

import datetime as dt
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import hexml, hoodpdf
from .report import ProgressionReport, ReportInputs, ReportOptions

PAIR_MODES = ("latest", "all", "consecutive")


@dataclass
class Visit:
    patient_id: str
    eye: str  # "OD" / "OS"
    date: dt.date
    hood_pdf: Optional[Path] = None
    circle_xml: Optional[Path] = None
    volume_xml: Optional[Path] = None

    @property
    def key(self):
        return (self.patient_id, self.eye, self.date)


@dataclass
class BatchItem:
    patient_id: str
    eye: str
    baseline: Visit
    followup: Visit
    status: str = "pending"  # pending / ok / skipped / error
    message: str = ""
    outputs: list[Path] = field(default_factory=list)


def _norm_id(s: str) -> str:
    return (s or "").strip().upper()


def collect_files(sources: Iterable[str | Path], recursive: bool = True) -> list[Path]:
    """Expand folders (and individual files) into a flat list of PDF/XML paths."""
    out: list[Path] = []
    for src in sources:
        p = Path(src).expanduser()
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out += [f for f in it if f.is_file() and f.suffix.lower() in (".pdf", ".xml")]
        elif p.is_file() and p.suffix.lower() in (".pdf", ".xml"):
            out.append(p)
    return sorted(set(out))


def index_visits(files: Iterable[Path], log: Optional[Callable[[str], None]] = None) -> dict[tuple, Visit]:
    """Read patient ID / eye / date from every file and bucket them into visits."""
    log = log or (lambda m: None)
    visits: dict[tuple, Visit] = {}

    def visit(pid, eye, date) -> Visit:
        k = (pid, eye, date)
        if k not in visits:
            visits[k] = Visit(pid, eye, date)
        return visits[k]

    for f in files:
        try:
            if f.suffix.lower() == ".pdf":
                h = hoodpdf.HoodReport(f, dpi=72).header  # low dpi: only the text is needed here
                d = h.exam_date()
                if not (h.patient_id and h.eye and d):
                    log(f"skip {f.name}: not a Hood report (no patient ID / eye / exam date in text)")
                    continue
                v = visit(_norm_id(h.patient_id), h.eye, d)
                if v.hood_pdf:
                    log(f"note {f.name}: second Hood PDF for {v.patient_id} {v.eye} {d}; keeping {v.hood_pdf.name}")
                else:
                    v.hood_pdf = f
            else:
                kind = hexml.guess_export_kind(f)
                if kind == "unknown":
                    log(f"skip {f.name}: not a HEYEX circle or volume export")
                    continue
                info = hexml.read_patient_info(f)
                if not (info.patient_id and info.laterality and info.exam_date):
                    log(f"skip {f.name}: missing patient ID / eye / date")
                    continue
                v = visit(_norm_id(info.patient_id), info.eye, info.exam_date)
                attr = "circle_xml" if kind == "circle" else "volume_xml"
                if getattr(v, attr):
                    log(f"note {f.name}: second {kind} XML for {v.patient_id} {v.eye} {info.exam_date}; keeping {getattr(v, attr).name}")
                else:
                    setattr(v, attr, f)
        except Exception as e:  # noqa: BLE001
            log(f"skip {f.name}: {e}")
    return visits


def plan_pairs(visits: dict[tuple, Visit], mode: str = "latest", require_xml: bool = False) -> list[BatchItem]:
    """Group visits per (patient, eye) and build the baseline/follow-up pairs."""
    if mode not in PAIR_MODES:
        raise ValueError(f"mode must be one of {PAIR_MODES}")
    groups: dict[tuple, list[Visit]] = defaultdict(list)
    for v in visits.values():
        if v.hood_pdf is None:
            continue
        if require_xml and not (v.circle_xml and v.volume_xml):
            continue
        groups[(v.patient_id, v.eye)].append(v)
    items: list[BatchItem] = []
    for (pid, eye), vs in sorted(groups.items()):
        vs.sort(key=lambda v: v.date)
        if len(vs) < 2:
            items.append(BatchItem(pid, eye, vs[0], vs[0], status="skipped", message="only one visit with a Hood PDF"))
            continue
        if mode == "latest":
            pairs = [(vs[0], vs[-1])]
        elif mode == "all":
            pairs = [(vs[0], v) for v in vs[1:]]
        else:
            pairs = list(zip(vs[:-1], vs[1:]))
        items += [BatchItem(pid, eye, a, b) for a, b in pairs]
    return items


def run_batch(
    items: list[BatchItem],
    out_dir: str | Path,
    options: Optional[ReportOptions] = None,
    formats: tuple[str, ...] = ("png", "pdf"),
    progress: Optional[Callable[[int, int, BatchItem], None]] = None,
) -> list[BatchItem]:
    """Render every pending item into ``out_dir``; status/message/outputs are filled in place."""
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    options = options or ReportOptions()
    todo = [it for it in items if it.status == "pending"]
    for i, it in enumerate(todo, 1):
        if progress:
            progress(i, len(todo), it)
        try:
            inputs = ReportInputs(
                hood_baseline=it.baseline.hood_pdf, hood_followup=it.followup.hood_pdf,
                circle_baseline=it.baseline.circle_xml, circle_followup=it.followup.circle_xml,
                volume_baseline=it.baseline.volume_xml, volume_followup=it.followup.volume_xml,
            )
            rep = ProgressionReport(inputs, options)
            page = rep.render()
            for ext in formats:
                p = out_dir / rep.suggested_filename(ext)
                rep.save(p, page)
                it.outputs.append(p)
            missing = [n for n, v in (("circle XML", it.baseline.circle_xml and it.followup.circle_xml),
                                      ("posterior-pole XML", it.baseline.volume_xml and it.followup.volume_xml)) if not v]
            it.status = "ok"
            it.message = ("missing " + ", ".join(missing) + "; " if missing else "") + "; ".join(rep.warnings[:3])
        except Exception as e:  # noqa: BLE001
            it.status = "error"
            it.message = f"{type(e).__name__}: {e}"
            it.outputs = []
            traceback.print_exc()
    return items


def summary_rows(items: list[BatchItem]) -> list[dict]:
    return [
        {
            "patient": it.patient_id, "eye": it.eye,
            "baseline": it.baseline.date.isoformat(), "follow-up": it.followup.date.isoformat(),
            "status": it.status, "note": it.message,
            "files": ", ".join(p.name for p in it.outputs),
        }
        for it in items
    ]
