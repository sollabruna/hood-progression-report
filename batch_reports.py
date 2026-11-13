#!/usr/bin/env python3
"""Batch-generate progression reports from folders of Hood PDFs and HEYEX XMLs.

Example:
    python batch_reports.py --input "~/Hood reports" "~/HEYEX/circle" "~/HEYEX/posterior pole" \
        --output ~/Desktop/progression_reports --pairs latest --maps thickness

Files are grouped by patient ID + eye + exam date (read from each file), so a
visit's PDF and XMLs must share the same study ID and date.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hood_progression import ReportOptions, batch  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", nargs="+", required=True, help="folders (searched recursively) and/or files")
    ap.add_argument("--output", required=True, help="output folder")
    ap.add_argument("--pairs", choices=batch.PAIR_MODES, default="latest")
    ap.add_argument("--maps", choices=["probability", "thickness"], default="probability")
    ap.add_argument("--formats", nargs="+", choices=["png", "pdf"], default=["png", "pdf"])
    ap.add_argument("--require-xml", action="store_true", help="skip visits lacking a circle or posterior-pole XML")
    ap.add_argument("--dry-run", action="store_true", help="only list what would be generated")
    ap.add_argument("--smooth-mm", type=float, default=0.15)
    ap.add_argument("--scale", type=float, default=2.0)
    a = ap.parse_args(argv)

    files = batch.collect_files(a.input)
    print(f"{len(files)} PDF/XML files found")
    visits = batch.index_visits(files, lambda m: print("  " + m))
    items = batch.plan_pairs(visits, a.pairs, a.require_xml)
    for it in items:
        print(f"  {it.patient_id} {it.eye}: {it.baseline.date} -> {it.followup.date}  [{it.status}{(': ' + it.message) if it.message else ''}]")
    if a.dry_run:
        return 0
    opts = ReportOptions(map_mode=a.maps, scale=a.scale, change_smooth_mm=a.smooth_mm)
    batch.run_batch(items, a.output, opts, tuple(a.formats),
                    lambda i, n, it: print(f"[{i}/{n}] {it.patient_id} {it.eye} {it.baseline.date} -> {it.followup.date}"))
    rows = batch.summary_rows(items)
    out = Path(a.output).expanduser()
    with open(out / "batch_summary.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["status"])
        w.writeheader()
        w.writerows(rows)
    n_ok = sum(r["status"] == "ok" for r in rows)
    print(f"{n_ok}/{len(rows)} reports written to {out} (see batch_summary.csv)")
    return 0 if n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
