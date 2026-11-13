# Changelog

All notable changes to the Hood Glaucoma Progression Report generator.
Versions follow `MAJOR.MINOR.PATCH`: PATCH = fixes that do not change a
report's content, MINOR = new features or changes to how reports look,
MAJOR = incompatible changes to inputs or layout.

## 1.0.1 — 2026-11-13

- Change maps: missing data (unsegmented A-scans, the optic-disc region) is
  now filled with a smooth estimate from the surrounding valid data instead of
  being left grey or filled with nearest values, so there are no gaps or
  streaks; the deviation maps above cover these areas too. *Leave the optic
  disc grey* is now an opt-in option.
- Change maps: removed a thin dark line along the rotated edge.

## 1.0.0 — 2026-11-13

First release replacing the Photoshop action/template.

- Single-report GUI (Streamlit) and command line (`make_report.py`).
- Batch generation from folders (`Batch` tab, `batch_reports.py`).
- Hood report PDF crops: B-scans, deviation (probability) or thickness maps,
  colour bars; header read from the HEYEX XML with PDF fallback.
- cpRNFL comparison and difference plot from the ONH-RC circle XMLs
  (port of `circle_scan.R`, short-term variability limits).
- RNFL/GCL thickness change maps from the posterior-pole XMLs, drawn at the
  same scale, position, rotation and clipping as the HEYEX maps above them;
  missing data inpainted, optic disc kept grey, 0.15 mm smoothing.
- Field view / Retina view follows the map type and is printed on the report.
- Left-eye handling derived from the scan geometry (unverified on OS data).
