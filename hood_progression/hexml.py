"""Parsing of Heidelberg Spectralis (HEYEX) XML exports.

Two kinds of export are used by the progression report:

* **ONH-RC "circle" export** (``Type == RadialCircles``): 24 radial B-scans plus
  three circle scans (3.5 / 4.1 / 4.7 mm).  The 3.5 mm circle carries the ILM
  and RNFL segmentation used for the circumpapillary RNFL (cpRNFL) curve.
* **Posterior-pole volume export** (``Type == Volume``): 61 B-scans x 768
  A-scans with full layer segmentation.  Used for the RNFL and GCL thickness
  change maps.

The math mirrors the lab's original R code (``xmlToThickness.R`` and
``circle_scan.R``): thickness in µm = (lower boundary - upper boundary) in
pixels x axial scale (3.872 µm / px on the Spectralis).
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

AXIAL_UM_PER_PX_DEFAULT = 3.872  # Spectralis ScaleY (mm/px) * 1000
INVALID_SEG = 1000  # Heidelberg writes ~4.29e9 for un-segmented A-scans; R used > 1000


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def _text(el: Optional[ET.Element], default: str = "") -> str:
    return (el.text or "").strip() if el is not None else default


def _date_from(el: Optional[ET.Element]) -> Optional[dt.date]:
    """Read a <Date><Year/><Month/><Day/></Date> block; None if incomplete."""
    if el is None:
        return None
    d = el.find("Date") if el.find("Date") is not None else el
    try:
        y, m, dd = (int(_text(d.find(k))) for k in ("Year", "Month", "Day"))
        return dt.date(y, m, dd)
    except (TypeError, ValueError):
        return None


@dataclass
class PatientInfo:
    patient_id: str = ""
    last_name: str = ""
    first_names: str = ""
    sex: str = ""
    dob: Optional[dt.date] = None
    exam_date: Optional[dt.date] = None
    laterality: str = ""  # "R" or "L"
    series_type: str = ""

    @property
    def eye(self) -> str:
        return {"R": "OD", "L": "OS"}.get(self.laterality.upper(), self.laterality)

    def fmt_date(self, d: Optional[dt.date]) -> str:
        return d.strftime("%m/%d/%Y") if d else ""


def read_patient_info(xml_path: str | Path) -> PatientInfo:
    root = ET.parse(str(xml_path)).getroot()
    pat = root.find(".//Patient")
    study = root.find(".//Study")
    series = root.find(".//Series")
    info = PatientInfo()
    if pat is not None:
        info.patient_id = _text(pat.find("PatientID"))
        info.last_name = _text(pat.find("LastName"))
        info.first_names = _text(pat.find("FirstNames"))
        info.sex = _text(pat.find("Sex"))
        info.dob = _date_from(pat.find("Birthdate"))
    if study is not None:
        info.exam_date = _date_from(study.find("StudyDate"))
    if series is not None:
        info.laterality = _text(series.find("Laterality"))
        info.series_type = _text(series.find("Type"))
    return info


def _seglines(image: ET.Element) -> dict[str, np.ndarray]:
    """Return {layer name: array of boundary positions (px)} for one <Image>."""
    out: dict[str, np.ndarray] = {}
    seg = image.find("Segmentation")
    if seg is None:
        return out
    for sl in seg.findall("SegLine"):
        name = _text(sl.find("Name"))
        arr = np.array(_text(sl.find("Array")).split(), dtype=float)
        arr[arr > INVALID_SEG] = np.nan
        out[name] = arr
    return out


def _axial_um_per_px(image: ET.Element) -> float:
    sy = image.find("OphthalmicAcquisitionContext/ScaleY")
    try:
        v = float(_text(sy)) * 1000.0
        return v if v > 0 else AXIAL_UM_PER_PX_DEFAULT
    except ValueError:
        return AXIAL_UM_PER_PX_DEFAULT


# --------------------------------------------------------------------------- #
# Circle (ONH-RC) export -> cpRNFL profile
# --------------------------------------------------------------------------- #
@dataclass
class CircleScan:
    rnfl_um: np.ndarray  # 768 values, µm, starting at the temporal start point
    diameter_mm: float
    direction: str  # "CW" / "CCW"
    quality: Optional[int]
    info: PatientInfo


def read_circle_scan(xml_path: str | Path, circle: str = "small") -> CircleScan:
    """Extract the cpRNFL thickness profile from the ONH-RC export.

    ``circle`` is one of "small" (3.5 mm), "medium" (4.1 mm), "large" (4.7 mm).
    Circle images are recognised by having a <Center> element; they are sorted
    by their lateral scale (circumference / 768) so the index is robust to the
    order Heidelberg writes them in.
    """
    root = ET.parse(str(xml_path)).getroot()
    info = read_patient_info(xml_path)
    circles = []
    for img in root.iter("Image"):
        ctx = img.find("OphthalmicAcquisitionContext")
        if ctx is None or ctx.find("Center") is None:
            continue
        sx = float(_text(ctx.find("ScaleX")))
        w = int(_text(ctx.find("Width")) or 768)
        circumference_mm = sx * w
        circles.append((circumference_mm / np.pi, img))
    if not circles:
        raise ValueError(f"No circle scans found in {xml_path}")
    circles.sort(key=lambda t: t[0])
    idx = {"small": 0, "s": 0, "medium": 1, "m": 1, "large": 2, "l": 2}[circle.lower()]
    idx = min(idx, len(circles) - 1)
    diameter, img = circles[idx]
    segs = _seglines(img)
    if "ILM" not in segs or "RNFL" not in segs:
        raise ValueError("Circle scan lacks ILM/RNFL segmentation")
    um = _axial_um_per_px(img)
    rnfl = (segs["RNFL"] - segs["ILM"]) * um
    ctx = img.find("OphthalmicAcquisitionContext")
    q = ctx.find("ImageQuality")
    # Heidelberg scans the circle clockwise for OD and counter-clockwise for OS, so the
    # A-scan order is temporal -> superior -> nasal -> inferior (TSNIT) for both eyes.
    # If an export deviates from that, reverse the profile (keeping index 0 at the start point).
    direction = _text(ctx.find("Direction"), "CW").upper()
    expected = "CCW" if info.laterality.upper() == "L" else "CW"
    if direction and direction != expected:
        rnfl = np.concatenate([rnfl[:1], rnfl[1:][::-1]])
    return CircleScan(
        rnfl_um=rnfl,
        diameter_mm=float(diameter),
        direction=_text(ctx.find("Direction"), "CW"),
        quality=int(_text(q)) if q is not None and _text(q).isdigit() else None,
        info=info,
    )


# --------------------------------------------------------------------------- #
# Posterior-pole volume export -> thickness grids
# --------------------------------------------------------------------------- #
@dataclass
class VolumeScan:
    rnfl_um: np.ndarray  # shape (n_bscans, n_ascans); row 0 = first (inferior-most) B-scan
    gcl_um: np.ndarray
    tilt_angle_deg: Optional[float]
    bscan_y_mm: np.ndarray  # fundus Y (mm) of each B-scan start point
    ascan_x_mm: tuple[float, float]  # fundus X extent (mm) of the B-scans
    scale_x_mm: float
    etdrs_center_px: Optional[tuple[int, int]]
    info: PatientInfo
    localizer_scale_mm_per_px: Optional[float] = None
    bscan_dir_mm: tuple[float, float] = (1.0, 0.0)  # mean (End - Start) of the B-scans, SLO mm
    layers: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def shape(self):
        return self.rnfl_um.shape

    @property
    def rotation_deg(self) -> float:
        """Signed rotation of the scan in the fundus image (positive = right end higher).

        Magnitude from HEYEX's TiltAngle when present, sign from the B-scan direction
        vector so it is correct for either eye; falls back to the vector alone.
        """
        dx, dy = self.bscan_dir_mm
        measured = float(np.degrees(np.arctan2(-dy, abs(dx)))) if (dx or dy) else 0.0
        mag = abs(self.tilt_angle_deg) if self.tilt_angle_deg is not None else abs(measured)
        return float(np.copysign(mag, measured)) if measured else mag

    @property
    def ascans_left_to_right(self) -> bool:
        return self.bscan_dir_mm[0] >= 0

    @property
    def first_bscan_is_inferior(self) -> bool:
        """True when B-scan 0 has the largest fundus Y (lower on the image)."""
        ys = self.bscan_y_mm[~np.isnan(self.bscan_y_mm)]
        return bool(ys.size > 1 and ys[0] > ys[-1])

    @property
    def width_mm(self) -> float:
        """Physical B-scan length: lateral scale x number of A-scans (9.0 mm for a 30° scan)."""
        return self.scale_x_mm * self.rnfl_um.shape[1]

    @property
    def height_mm(self) -> float:
        """Physical extent of the B-scan stack (first to last B-scan), i.e. 7.5 mm for 25°.

        The Start-point Y coordinates are foreshortened by cos(tilt) when the
        pattern is rotated, so they are divided back out.
        """
        ys = self.bscan_y_mm[~np.isnan(self.bscan_y_mm)]
        if ys.size < 2:
            return self.width_mm * 25.0 / 30.0
        tilt = np.deg2rad(self.tilt_angle_deg or 0.0)
        return float(ys.max() - ys.min()) / max(np.cos(tilt), 1e-6)


def read_volume_scan(xml_path: str | Path) -> VolumeScan:
    """Read every OCT B-scan of a posterior-pole volume and build thickness grids.

    RNFL thickness = RNFL - ILM; GCL thickness = GCL - RNFL (boundary names as
    Heidelberg writes them), both x axial µm/px, exactly as in xmlToThickness.R.
    """
    root = ET.parse(str(xml_path)).getroot()
    info = read_patient_info(xml_path)
    rnfl_rows, gcl_rows, ys, dirs = [], [], [], []
    xs = (np.inf, -np.inf)
    sx = 0.0
    for img in root.iter("Image"):
        if _text(img.find("ImageType/Type")) != "OCT":
            continue
        segs = _seglines(img)
        if not {"ILM", "RNFL", "GCL"} <= set(segs):
            continue
        um = _axial_um_per_px(img)
        rnfl_rows.append((segs["RNFL"] - segs["ILM"]) * um)
        gcl_rows.append((segs["GCL"] - segs["RNFL"]) * um)
        ctx = img.find("OphthalmicAcquisitionContext")
        start = ctx.find("Start/Coord")
        end = ctx.find("End/Coord")
        try:
            ys.append(float(_text(start.find("Y"))))
            x0, x1 = float(_text(start.find("X"))), float(_text(end.find("X")))
            xs = (min(xs[0], x0, x1), max(xs[1], x0, x1))
            dirs.append((x1 - x0, float(_text(end.find("Y"))) - float(_text(start.find("Y")))))
        except (AttributeError, ValueError):
            ys.append(np.nan)
        sx = float(_text(ctx.find("ScaleX")) or sx)
    if not rnfl_rows:
        raise ValueError(f"No segmented OCT B-scans found in {xml_path}")

    tilt = root.find(".//TiltAngle")
    tilt_deg = float(_text(tilt)) if tilt is not None and _text(tilt) else None
    loc_scale = None
    for img in root.iter("Image"):
        if _text(img.find("ImageType/Type")) == "LOCALIZER":
            try:
                loc_scale = float(_text(img.find("OphthalmicAcquisitionContext/ScaleX")))
            except ValueError:
                pass
            break
    center = root.find(".//ThicknessGrid/CenterPos/Coord")
    etdrs = None
    if center is not None:
        try:
            etdrs = (int(_text(center.find("X"))), int(_text(center.find("Y"))))
        except ValueError:
            etdrs = None
    return VolumeScan(
        rnfl_um=np.vstack(rnfl_rows),
        gcl_um=np.vstack(gcl_rows),
        tilt_angle_deg=tilt_deg,
        bscan_y_mm=np.array(ys),
        ascan_x_mm=xs,
        scale_x_mm=sx,
        etdrs_center_px=etdrs,
        info=info,
        localizer_scale_mm_per_px=loc_scale,
        bscan_dir_mm=tuple(np.mean(dirs, axis=0)) if dirs else (1.0, 0.0),
    )


def guess_export_kind(xml_path: str | Path) -> str:
    """Return 'circle', 'volume' or 'unknown' for an HEYEX XML export."""
    try:
        t = read_patient_info(xml_path).series_type
    except ET.ParseError:
        return "unknown"
    if t == "RadialCircles":
        return "circle"
    if t == "Volume":
        return "volume"
    return "unknown"
