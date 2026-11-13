# Hood Glaucoma Progression Report generator

Python replacement for the Photoshop action/template. Takes two *Hood Glaucoma
Report* PDFs (baseline, follow-up) plus the matching HEYEX XML exports and
produces the one-page progression report as PNG and PDF, with a switch between
the Hood report's **deviation (probability) maps** and its **thickness maps**.

```
hood_progression_report/
├── app.py                 Streamlit GUI: single report tab + batch tab
├── make_report.py         command-line version (one report)
├── batch_reports.py       command-line batch generation from folders
├── run_app.command        macOS double-click launcher (creates .venv, self-updates)
├── run_app.bat            Windows launcher
├── update.py              checks GitHub releases and applies updates
├── VERSION, CHANGELOG.md, RELEASING.md
├── requirements.txt
├── assets/                HVSL logo, short-term variability CSV, bundled fonts
└── hood_progression/
    ├── hoodpdf.py         rasterise the Hood PDF, crop components, read header text
    ├── hexml.py           parse HEYEX XML (circle scan -> cpRNFL; volume -> RNFL/GCL grids)
    ├── crnfl.py           cpRNFL comparison + difference plot  (port of circle_scan.R)
    ├── changemap.py       RNFL/GCL thickness change maps       (port of generate_*_change_map.R)
    ├── batch.py           folder scanning, visit grouping, pairing, batch run
    └── report.py          page layout (geometry from Progression_Report_Template.psd)
```

## Inputs

| Input | Used for |
|---|---|
| Baseline + follow-up **Hood Glaucoma Report PDF** | B-scans, RNFL/GCL deviation *or* thickness maps, colour bars, header fallback |
| Baseline + follow-up **ONH-RC (circle) XML** | cpRNFL curves + difference plot (3.5 mm circle), patient ID / sex / DOB / exam date / eye |
| Baseline + follow-up **posterior-pole volume XML** | RNFL and GCL thickness change maps, FoDi tilt angle, ETDRS grid position |

XMLs can be dropped in any order: they are classified (`RadialCircles` vs
`Volume`) and ordered by exam date automatically. Missing XMLs leave the
corresponding panel blank rather than failing.

## Install (team members)

1. Install Python 3.10+ (python.org or Homebrew) if you don't have it.
2. Download the latest release: https://github.com/sollabruna/hood-progression-report/releases
   → *Source code (zip)*, unzip it somewhere permanent (not Downloads).
3. Double-click `run_app.command` (macOS) or `run_app.bat` (Windows). The first
   launch builds a private Python environment (a minute or two) and opens the
   app in your browser.

Every launch checks GitHub for a newer release and installs it automatically
before starting, so you never need to re-download. The version in use is shown
in the sidebar and printed in the report footer. To skip the check (offline),
set `HOOD_NO_UPDATE=1` in the environment.

Developers: `git clone` the repository instead; the launcher then updates via
`git fetch` to the newest tag. See `RELEASING.md` for how to publish a version.

Manual setup, if you prefer a terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## GUI

```bash
streamlit run app.py               # or double-click run_app.command / run_app.bat
```

### First run on a Mac that received this folder by download / AirDrop / zip

macOS quarantines downloaded scripts, so the first double-click of
`run_app.command` shows *"Apple could not verify … is free of malware"*.
Either:

* click **Done**, open **System Settings → Privacy & Security**, scroll down
  and click **Open Anyway** next to the `run_app.command` message (on older
  macOS: right-click the file → **Open** → **Open**); or
* in Terminal, from inside this folder:

  ```bash
  xattr -d com.apple.quarantine run_app.command
  chmod +x run_app.command        # execute permission is lost by some zip/share tools
  ./run_app.command
  ```

Python 3.10+ must be installed (python.org or Homebrew). The first run builds
a `.venv` and installs the dependencies (a minute or two); later runs start in
a few seconds and open the app in the browser.

**Sharing the folder:** leave `.venv/` out (it is tied to the computer it was
built on). If it does get copied, the launcher notices it doesn't work and
rebuilds it; deleting `.venv` by hand and relaunching does the same.

**Single report tab:** upload the two PDFs, the two circle XMLs and the two
posterior-pole XMLs (separate drop zones, since they usually live in different
folders; each pair is ordered by exam date automatically), choose
*probability* or *thickness*, edit any header field, click **Generate
report**, then download PNG or PDF. The *Advanced* panel exposes render
scale, which circle to use, the FoDi angle, change-map smoothing / missing-data
handling / limits / palette, the ETDRS overlay, and the B-scan crop.

**Batch tab:** enter the folders holding the Hood PDFs and the HEYEX exports
(any number, subfolders included) or drop the files, set an output folder,
and choose which pairs to make: baseline → latest visit, baseline → every
later visit, or each visit → the next. Files are grouped by **patient ID +
eye + exam date** read from each file, so a visit's PDF and XMLs must carry
the same study ID and date. A table shows what was found; **Generate all
reports** writes PNG/PDF per pair into the output folder and offers a ZIP.
Visits that lack an XML still get a report with that panel blank (or tick
*Skip visits that lack …*).

## Command line

```bash
python make_report.py \
  --hood-baseline V1.pdf --hood-followup V2.pdf \
  --xml circle_1.xml circle_2.xml pp_1.xml pp_2.xml \
  --maps thickness --both --out COL037_OD
```

Writes `COL037_OD.png` and `COL037_OD.pdf`. Omit `--out` to get a name like
`COL037_OD_01112016_to_09072023_thick.png`.

Batch:

```bash
python batch_reports.py --input "~/Hood reports" "~/HEYEX/circle" "~/HEYEX/posterior pole" \
  --output ~/Desktop/progression_reports --pairs latest --maps thickness
```

Add `--dry-run` to list the pairs without rendering. A `batch_summary.csv`
is written next to the reports.

## How it works / where the numbers come from

* **Crops** are defined as fractions of the Hood report page
  (`hoodpdf.REGIONS`) and then shrunk to the non-white content inside the box,
  so small layout shifts between HEYEX versions are absorbed. Measured on
  Software Version 6.15.0.6 reports. If a future version moves things, adjust
  `REGIONS_PX` (coordinates on a 3300 x 2550 px, 300 dpi render).
* **Thickness** (µm) = (lower boundary − upper boundary) px × axial scale
  (3.872 µm/px from `ScaleY`), as in `xmlToThickness.R`. RNFL = RNFL − ILM,
  GCL = GCL − RNFL.
* **Scan geometry**: B-scan length = `ScaleX` × 768 (9.0 mm for 30°); stack
  height from the B-scan start points (7.5 mm for 25°). The overall X extent
  of the Start/End coordinates is *not* used for width: because the stack is
  rotated, it spans ~10% more than one B-scan and would mis-scale the map.
* **Left eyes**: orientation is taken from the data, not assumed. B-scans are
  ordered superior-to-inferior from their Y coordinates, A-scans left-to-right
  from the B-scan direction, and the rotation sign comes from the B-scan
  direction vector (magnitude from `TiltAngle`), so an OS scan tilts the
  opposite way, with the disc on the left as in HEYEX. The cpRNFL profile is
  TSNIT for both eyes (Heidelberg scans CW for OD, CCW for OS); if an export's
  `Direction` disagrees, the profile is reversed. The FoDi shift of the cpRNFL
  axis uses −TiltAngle for OD and +TiltAngle for OS (adjustable in the GUI).
  **This was written from the Heidelberg conventions but has only been
  verified on right-eye data — check the first OS case against its Hood
  report.** Unsegmented edge A-scans are
  filled from their neighbour; the unsegmented optic-disc blob is closed and
  left grey.
* **cpRNFL plot**: 0° at the temporal start of the circle, x-axis reversed
  (N–NS–TS–T–TI–NI–N), shifted by the FoDi angle (default −TiltAngle from the
  volume XML, i.e. −7.7° for the test eye, matching the R script). Red = below
  the 5th-percentile short-term variability limit (`assets/short_term_var_crnfl.csv`).
* **Change maps**: follow-up − baseline on the 61 × 768 grid. Missing data
  (unsegmented A-scans at the B-scan ends, missing B-scans, small holes) is
  filled with a smooth estimate from the surrounding valid data before
  subtracting, so the map has no gaps; the optic disc is left grey as a clean cut-out
  (convex hull of the unsegmented disc region, shared by both maps), as in
  HEYEX's thickness maps. Untick *Leave the optic disc grey* to fill it too. The map is then smoothed with a
  Gaussian of 0.10 mm by default (adjustable; 0 = off) to remove the
  B-scan striping. Rotated about
  its centre by the fovea-to-disc tilt angle (`TiltAngle` in the volume XML)
  and drawn at the **same scale, position and clipping as the HEYEX map above
  it**: the coloured overlay in the follow-up Hood map is detected
  (`hoodpdf.detect_scan_overlay`); its height is the rotated scan's bounding
  box and gives the px-per-mm; the scan is centred horizontally in the IR
  square (HEYEX clips both sides of the rotated scan) and vertically on the
  overlay, and the map is clipped to the same square frame. Edge A-scans without segmentation are
  filled from their nearest valid neighbour so the map reaches the scan
  boundary like HEYEX's overlay; interior gaps (optic disc) stay grey. With
  probability maps the report is in **Field view** (change maps flipped
  vertically); with thickness maps it is in **Retina view**. Untick *Clip
  change maps to the same frame* in the GUI to show the whole scan instead
  (the panel then grows). Default scale is the HEYEX one (green = thinning,
  black = no change, red = thickening, white beyond ±40 µm RNFL / ±30 µm
  GCL); a red/white/blue diverging palette is also available. Not mirrored for
  OS (same as HEYEX and the R code). Optional 1/3/6 mm ETDRS grid (off by
  default).
* **Header** comes from the XML (study IDs are already de-identified), falling
  back to the PDF text; everything is editable in the GUI.
* **Page**: 2250 × 1653 template units, rendered at `scale` (default 2 →
  4500 × 3306 px). PDF export is 11 in wide (letter landscape).

## Known limitations

* Crop boxes assume the standard single-page Hood report layout (letter,
  landscape). Other print layouts will need new `REGIONS_PX`.
* The short-term variability limits are the lab's existing 768-point table
  for the 3.5 mm circle; using the 4.1/4.7 mm circle reuses the same table.
* The loess smoothing of the threshold lines in R is approximated with a
  moving average.
