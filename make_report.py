#!/usr/bin/env python3
"""Command-line generator for the Glaucoma Progression Report.

Example:
    python make_report.py \
        --hood-baseline  CUNAD016_OD_HoodInt_V1_1.pdf \
        --hood-followup  CUNAD016_OD_HoodInt_V2_1.pdf \
        --circle-baseline circle_1.xml --circle-followup circle_2.xml \
        --volume-baseline pp_1.xml --volume-followup pp_2.xml \
        --maps thickness --out report.png

Pass --xml a.xml b.xml c.xml d.xml instead of the four --circle/--volume
flags and the files are classified and ordered by exam date automatically.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hood_progression import ProgressionReport, ReportInputs, ReportOptions, hexml  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hood-baseline", required=True)
    ap.add_argument("--hood-followup", required=True)
    ap.add_argument("--circle-baseline")
    ap.add_argument("--circle-followup")
    ap.add_argument("--volume-baseline")
    ap.add_argument("--volume-followup")
    ap.add_argument("--xml", nargs="*", help="any HEYEX XML exports; auto-sorted into circle/volume, baseline/follow-up")
    ap.add_argument("--maps", choices=["probability", "thickness"], default="probability")
    ap.add_argument("--out", help="output file (.png or .pdf); default derived from patient/eye/dates")
    ap.add_argument("--both", action="store_true", help="write PNG and PDF")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--ftd-angle", type=float, help="override the fovea-disc angle (default: -TiltAngle from the volume XML)")
    ap.add_argument("--etdrs", action="store_true", help="draw the 1/3/6 mm ETDRS grid on the change maps")
    ap.add_argument("--palette", choices=["heidelberg", "diverging"], default="heidelberg")
    ap.add_argument("--no-rotate", action="store_true", help="do not rotate change maps to the fovea-disc angle")
    a = ap.parse_args(argv)

    circ, vol = [a.circle_baseline, a.circle_followup], [a.volume_baseline, a.volume_followup]
    if a.xml:
        groups = {"circle": [], "volume": []}
        for p in a.xml:
            k = hexml.guess_export_kind(p)
            if k in groups:
                groups[k].append((hexml.read_patient_info(p).exam_date, p))
        for k, lst in groups.items():
            lst.sort(key=lambda t: (t[0] is None, t[0]))
            if len(lst) >= 2:
                (circ if k == "circle" else vol)[:] = [lst[0][1], lst[1][1]]

    inputs = ReportInputs(a.hood_baseline, a.hood_followup, circ[0], circ[1], vol[0], vol[1])
    opts = ReportOptions(map_mode=a.maps, scale=a.scale, ftd_angle=a.ftd_angle, draw_etdrs=a.etdrs,
                         change_palette=a.palette, rotate_change_maps=not a.no_rotate)
    rep = ProgressionReport(inputs, opts)
    for w in rep.warnings:
        print("WARNING:", w, file=sys.stderr)
    page = rep.render()
    outs = []
    if a.both:
        base = Path(a.out).with_suffix("") if a.out else Path(rep.suggested_filename("png")).with_suffix("")
        outs = [base.with_suffix(".png"), base.with_suffix(".pdf")]
    else:
        outs = [Path(a.out) if a.out else Path(rep.suggested_filename("png"))]
    for o in outs:
        rep.save(o, page)
        print("wrote", o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
