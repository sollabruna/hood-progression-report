"""Read a Heidelberg *Hood Glaucoma Report* PDF and crop its components.

The report is a single letter-landscape page printed from HEYEX ("Microsoft:
Print To PDF").  Crop boxes are given in *fractions of the page* so they are
independent of the rendering resolution, and every crop is refined to the
bounding box of non-white pixels inside its search box, which absorbs small
layout shifts between software versions.

Regions:
    bscan          the ONH B-scan (3.5 mm circle) with ILM/RNFL overlay
    rnfl_thick     RNFL thickness map ("Retina view", top)
    rnfl_thick_cb  its colour bar + "RNFL thickness [µm]" label
    gcl_thick      GCL thickness map ("Retina view", bottom)
    gcl_thick_cb   its colour bar + label
    rnfl_prob      RNFL thickness deviation (probability) map ("Field view", top, 24-2 overlay)
    gcl_prob       GCL thickness deviation map ("Field view", bottom, 10-2 overlay)
    prob_cb        the shared [%] probability colour bar
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

# Fractions of (width, height) measured on a 3300 x 2550 px (300 dpi) render.
# Order: left, top, right, bottom.
_W, _H = 3300.0, 2550.0
REGIONS_PX = {
    "bscan": (210, 395, 1205, 1015),
    "rnfl_thick": (1460, 585, 2004, 1140),
    "rnfl_thick_cb": (2008, 595, 2135, 1135),
    "gcl_thick": (1460, 1495, 2004, 2050),
    "gcl_thick_cb": (2008, 1505, 2135, 2045),
    "rnfl_prob": (2360, 580, 2940, 1150),
    "gcl_prob": (2360, 1485, 2940, 2055),
    "prob_cb": (3050, 1150, 3150, 1440),
}
REGIONS = {k: (l / _W, t / _H, r / _W, b / _H) for k, (l, t, r, b) in REGIONS_PX.items()}


@dataclass
class HoodHeader:
    patient: str = ""
    patient_id: str = ""
    diagnosis: str = ""
    dob: str = ""
    exam: str = ""
    comment: str = ""
    sex: str = ""
    eye: str = ""
    software_version: str = ""
    raw_text: str = field(default="", repr=False)

    def exam_date(self) -> Optional[dt.date]:
        return parse_heidelberg_date(self.exam)

    def dob_date(self) -> Optional[dt.date]:
        return parse_heidelberg_date(self.dob)


def parse_heidelberg_date(s: str) -> Optional[dt.date]:
    """Parse 'Apr/26/2023', '26.04.2023', '4/26/2023' or '2023-04-26'."""
    s = (s or "").strip()
    for fmt in ("%b/%d/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%b/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class HoodReport:
    """One Hood Glaucoma Report page."""

    def __init__(self, pdf_path: str | Path, dpi: int = 300, page_index: int = 0):
        self.path = Path(pdf_path)
        self.dpi = dpi
        doc = pdfium.PdfDocument(str(self.path))
        try:
            page = doc[page_index]
            self.page_size_pt = page.get_size()
            self.image: Image.Image = page.render(scale=dpi / 72.0).to_pil().convert("RGB")
            self.text: str = page.get_textpage().get_text_range()
        finally:
            doc.close()
        self.header = parse_header(self.text)

    # ------------------------------------------------------------------ crops
    def box_px(self, region: str) -> tuple[int, int, int, int]:
        l, t, r, b = REGIONS[region]
        W, H = self.image.size
        return int(l * W), int(t * H), int(r * W), int(b * H)

    def crop(self, region: str, refine: bool = True, white_threshold: int = 235, pad: int = 0) -> Image.Image:
        box = self.box_px(region)
        if refine:
            box = refine_to_content(self.image, box, white_threshold)
        if pad:
            box = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        return self.image.crop(box)

    def crops(self, regions=None, **kw) -> dict[str, Image.Image]:
        return {r: self.crop(r, **kw) for r in (regions or REGIONS)}


def refine_to_content(img: Image.Image, box, white_threshold: int = 235):
    """Shrink ``box`` to the bounding box of non-white pixels inside it."""
    a = np.asarray(img.crop(box).convert("L"))
    mask = a < white_threshold
    ys = np.flatnonzero(mask.any(axis=1))
    xs = np.flatnonzero(mask.any(axis=0))
    if ys.size == 0 or xs.size == 0:
        return box
    return (box[0] + int(xs[0]), box[1] + int(ys[0]), box[0] + int(xs[-1]) + 1, box[1] + int(ys[-1]) + 1)


_LABELS = ("Patient ID", "Patient", "Diagnosis", "DOB", "Exam.", "Exam", "Comment", "Sex")


def parse_header(text: str) -> HoodHeader:
    """Pull the header fields out of the page text (robust to column order)."""
    h = HoodHeader(raw_text=text)
    # normalise whitespace but keep line breaks for the eye token
    flat = re.sub(r"[ \t]+", " ", text.replace("\r", ""))

    def grab(label: str) -> str:
        m = re.search(rf"{re.escape(label)}\s*:\s*(.*?)(?=\s(?:{'|'.join(map(re.escape, _LABELS))})\s*:|\n|$)", flat)
        return m.group(1).strip() if m else ""

    h.patient = grab("Patient")
    h.patient_id = grab("Patient ID")
    h.diagnosis = grab("Diagnosis")
    h.dob = grab("DOB")
    h.exam = re.sub(r"\b(OD|OS)\b", "", grab("Exam.") or grab("Exam")).strip()
    h.comment = grab("Comment")
    h.sex = grab("Sex")
    m = re.search(r"\b(OD|OS)\b", flat)
    h.eye = m.group(1) if m else ""
    m = re.search(r"Software Version:\s*([\d.]+)", flat)
    h.software_version = m.group(1) if m else ""
    # The "Patient:" line is "LastName, FirstName"; Sex may have swallowed the eye token
    h.sex = re.sub(r"\b(OD|OS)\b", "", h.sex).strip()
    return h


def detect_scan_overlay(img: Image.Image, sat_threshold: float = 0.2, val_threshold: float = 0.25):
    """Bounding box (l, t, r, b) of the coloured thickness/deviation overlay in a map crop.

    The fundus (SLO) background is grey; the overlay is saturated colour.  The
    largest blob of saturated pixels is the scanned rectangle (rotated by the
    fovea-disc angle, possibly clipped at the disc).  Returns None if nothing is found.
    """
    from scipy import ndimage

    hsv = np.asarray(img.convert("HSV")).astype(float) / 255.0
    mask = (hsv[..., 1] > sat_threshold) & (hsv[..., 2] > val_threshold)
    mask = ndimage.binary_opening(mask, iterations=2)
    lab, n = ndimage.label(mask)
    if n == 0:
        return None
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = np.isin(lab, [i + 1 for i, sz in enumerate(sizes) if sz > 0.02 * sizes.max()])
    ys = np.flatnonzero(keep.any(axis=1))
    xs = np.flatnonzero(keep.any(axis=0))
    return (int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1)


def detect_ir_frame(img: Image.Image):
    """Bounding box (l, t, r, b) of the IR (SLO) fundus square inside a map crop.

    The square is grey fundus plus the coloured overlay; the 24-2/10-2 dots and
    frame lines around it are pure black and the page is white, so they are
    excluded and the largest remaining blob is the square.
    """
    from scipy import ndimage

    rgb = img.convert("RGB")
    L = np.asarray(rgb.convert("L")).astype(int)
    hsv = np.asarray(rgb.convert("HSV")).astype(float) / 255.0
    mask = ((L > 45) & (L < 238)) | ((hsv[..., 1] > 0.28) & (hsv[..., 2] > 0.25))
    mask = ndimage.binary_opening(mask, iterations=2)
    lab, n = ndimage.label(mask)
    if n == 0:
        return (0, 0, img.width, img.height)
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = lab == (int(np.argmax(sizes)) + 1)
    ys = np.flatnonzero(keep.any(axis=1))
    xs = np.flatnonzero(keep.any(axis=0))
    return (int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1)
