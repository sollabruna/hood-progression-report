"""Compose the Glaucoma Progression Report page.

Geometry is taken from ``Progression_Report_Template.psd`` (2250 x 1653 px);
every coordinate below is in that template space and multiplied by
``scale`` at render time so the page can be produced at higher resolution.
"""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import changemap, crnfl, hexml, hoodpdf

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TEMPLATE_W, TEMPLATE_H = 2250, 1653
MapMode = Literal["probability", "thickness"]

# ----------------------------------------------------------------- template
T = {
    "logo": (1837, 55, 2194, 147),
    "line1": (67, 135, 2184, 140),
    "line2": (78, 264, 2184, 269),
    "line3": (59, 1577, 2198, 1581),
    "title": (77, 81),
    "col_rnfl": (1276, 296),  # centre of "RNFL" label
    "col_gcl": (1820, 296),
    "view_label": (1548, 300),
    "hdr_rows": (160, 198, 236),
    "hdr_cols": (81, 876, 1476),
    "eye": (2149, 209),  # right edge, vertical centre
    "bscan1": (124, 336, 1018, 642),
    "bscan2": (124, 714, 1018, 1020),
    "diffplot": (42, 1040, 1066, 1579),
    "rnfl1": (1098, 333, 1456, 690),
    "gcl1": (1648, 336, 2003, 691),
    "rnfl2": (1098, 743, 1456, 1100),
    "gcl2": (1648, 743, 2003, 1100),
    "rnfl_chg": (1100, 1158, 1459, 1516),
    "gcl_chg": (1648, 1155, 2007, 1514),
    "chg_rect_pad": 3,
    "chg_region": (1032, 1114, 2244, 1572),  # bottom row: both change maps, labels and colour bars live here
    "chg_cb1": (1466, 1162, 1484, 1510),
    "chg_cb2": (2014, 1160, 2032, 1508),
    "prob_cb": (2038, 592, 2149, 908),
    "lbl_rnfl1": (1494, 510),  # centre of rotated label
    "lbl_gcl1": (2031, 511),
    "lbl_rnfl2": (1491, 925),
    "lbl_gcl2": (2031, 925),
    "lbl_rnfl_chg": (1540, 1336),
    "lbl_gcl_chg": (2088, 1334),
    "thick_cb_rnfl_x": 1462,  # left edge of per-row thickness colour bars
    "thick_cb_gcl_x": 2010,
    "footer_y": 1607,
    "footer_left": 59,
    "footer_right": 2183,
}
FONT_SIZES = {"title": 44, "header": 28, "eye": 68, "col": 42, "date": 40, "rot": 19, "footer": 21, "tick": 15}


# ------------------------------------------------------------------- fonts
def _find_font(bold=False, italic=False) -> Path:
    name = {
        (False, False): ("Arial.ttf", "LiberationSans-Regular.ttf"),
        (True, False): ("Arial Bold.ttf", "LiberationSans-Bold.ttf"),
        (False, True): ("Arial Italic.ttf", "LiberationSans-Italic.ttf"),
        (True, True): ("Arial Bold Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    }[(bold, italic)]
    candidates = [
        Path("/Library/Fonts") / name[0],
        Path("/System/Library/Fonts/Supplemental") / name[0],
        Path.home() / "Library/Fonts" / name[0],
        Path("C:/Windows/Fonts") / name[0].replace("Arial Bold Italic", "arialbi").replace("Arial Bold", "arialbd").replace("Arial Italic", "ariali").replace("Arial", "arial"),
        ASSETS / "fonts" / name[1],
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("No usable font found (bundled Liberation Sans missing)")


def _font(size: float, bold=False, italic=False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_find_font(bold, italic)), int(round(size)))


# --------------------------------------------------------------- settings
@dataclass
class HeaderFields:
    patient: str = ""
    patient_id: str = ""
    diagnosis: str = ""
    dob: str = ""
    sex: str = ""
    exam1: str = ""
    exam2: str = ""
    eye: str = ""
    title: str = "Glaucoma Progression Report"


@dataclass
class ReportOptions:
    map_mode: MapMode = "probability"
    scale: float = 2.0
    bscan_crop_frac: tuple[float, float] = (0.04, 0.60)  # keep this vertical band of the B-scan
    ftd_angle: Optional[float] = None  # None -> -TiltAngle from the posterior-pole XML, else 0
    circle: str = "small"
    change_limits: dict = field(default_factory=lambda: dict(changemap.DEFAULT_LIMITS))
    change_smooth_mm: float = 0.10  # Gaussian smoothing of the change maps, in mm (0 = off)
    fill_missing: bool = True  # inpaint unsegmented A-scans / missing B-scans
    mask_disc: bool = True  # leave the optic disc grey (clean outline) instead of filling it
    draw_etdrs: bool = False
    show_change_colorbars: bool = True
    change_palette: str = changemap.DEFAULT_PALETTE  # "heidelberg" (green loss / black / red gain, as HEYEX) or "diverging"
    rotate_change_maps: bool = True  # rotate by the fovea-to-disc angle like the HEYEX maps
    rotation_sign: float = 1.0
    view: Optional[str] = None  # "field" or "retina"; default follows map_mode (probability -> field)
    change_frame: str = "tight"
    clip_to_frame: bool = True  # clip the change map to the same square frame as the HEYEX maps (else the panel grows)

    @property
    def view_name(self) -> str:
        return self.view or ("field" if self.map_mode == "probability" else "retina")
    footer_left: str = "Research Beta"
    footer_right: str = ""  # default: "Progression Report v<version>"


@dataclass
class ReportInputs:
    hood_baseline: str | Path
    hood_followup: str | Path
    circle_baseline: Optional[str | Path] = None
    circle_followup: Optional[str | Path] = None
    volume_baseline: Optional[str | Path] = None
    volume_followup: Optional[str | Path] = None
    diffplot_image: Optional[str | Path] = None  # pre-made cpRNFL plot (fallback)
    rnfl_change_image: Optional[str | Path] = None  # pre-made change maps (fallback)
    gcl_change_image: Optional[str | Path] = None


# ----------------------------------------------------------------- helpers
def _fit(img: Image.Image, box_wh, resample=Image.LANCZOS) -> Image.Image:
    """Scale ``img`` to fill ``box_wh`` exactly (template images were stretched slightly too)."""
    return img.resize((max(1, int(box_wh[0])), max(1, int(box_wh[1]))), resample)


def _rotated_text(text: str, font, fill="black") -> Image.Image:
    """Render text reading top-to-bottom (rotated 90° clockwise) with transparent background."""
    l, t, r, b = font.getbbox(text)
    im = Image.new("RGBA", (r - l + 4, b - t + 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((2 - l, 2 - t), text, font=font, fill=fill)
    return im.rotate(-90, expand=True)


def default_header(
    hood1: hoodpdf.HoodReport,
    hood2: hoodpdf.HoodReport,
    xml_info: Optional[hexml.PatientInfo] = None,
    xml_info2: Optional[hexml.PatientInfo] = None,
) -> HeaderFields:
    """Build header text from the XML (preferred, de-identified) with PDF fallbacks."""
    h1, h2 = hood1.header, hood2.header

    def fmt(d: Optional[dt.date], fallback: str) -> str:
        if d:
            return d.strftime("%m/%d/%Y")
        p = hoodpdf.parse_heidelberg_date(fallback)
        return p.strftime("%m/%d/%Y") if p else fallback

    pid = (xml_info.patient_id if xml_info else "") or h1.patient_id
    return HeaderFields(
        patient=pid,
        patient_id=pid,
        diagnosis="" if h1.diagnosis in ("", "---") else h1.diagnosis,
        dob=fmt(xml_info.dob if xml_info else None, h1.dob),
        sex=(xml_info.sex if xml_info else "") or h1.sex,
        exam1=fmt(xml_info.exam_date if xml_info else None, h1.exam),
        exam2=fmt(xml_info2.exam_date if xml_info2 else None, h2.exam),
        eye=(xml_info.eye if xml_info else "") or h1.eye,
    )


# -------------------------------------------------------------------- main
class ProgressionReport:
    def __init__(self, inputs: ReportInputs, options: Optional[ReportOptions] = None, header: Optional[HeaderFields] = None):
        self.inputs = inputs
        self.opt = options or ReportOptions()
        self.hood1 = hoodpdf.HoodReport(inputs.hood_baseline)
        self.hood2 = hoodpdf.HoodReport(inputs.hood_followup)
        self.circle1 = hexml.read_circle_scan(inputs.circle_baseline, self.opt.circle) if inputs.circle_baseline else None
        self.circle2 = hexml.read_circle_scan(inputs.circle_followup, self.opt.circle) if inputs.circle_followup else None
        self.vol1 = hexml.read_volume_scan(inputs.volume_baseline) if inputs.volume_baseline else None
        self.vol2 = hexml.read_volume_scan(inputs.volume_followup) if inputs.volume_followup else None
        info1 = (self.circle1.info if self.circle1 else None) or (self.vol1.info if self.vol1 else None)
        info2 = (self.circle2.info if self.circle2 else None) or (self.vol2.info if self.vol2 else None)
        self.header = header or default_header(self.hood1, self.hood2, info1, info2)
        self.warnings: list[str] = []
        self._check_consistency()

    # ------------------------------------------------------------- checks
    def _check_consistency(self):
        h1, h2 = self.hood1.header, self.hood2.header
        if h1.eye and h2.eye and h1.eye != h2.eye:
            self.warnings.append(f"Hood reports are for different eyes ({h1.eye} vs {h2.eye}).")
        if h1.patient_id and h2.patient_id and h1.patient_id != h2.patient_id:
            self.warnings.append(f"Hood reports have different patient IDs ({h1.patient_id} vs {h2.patient_id}).")
        for name, a, b in (("circle", self.circle1, self.circle2), ("volume", self.vol1, self.vol2)):
            if a and b:
                if a.info.laterality != b.info.laterality:
                    self.warnings.append(f"{name} XMLs are for different eyes.")
                if a.info.patient_id != b.info.patient_id:
                    self.warnings.append(f"{name} XMLs have different patient IDs.")
        d1, d2 = hoodpdf.parse_heidelberg_date(h1.exam), hoodpdf.parse_heidelberg_date(h2.exam)
        if d1 and d2 and d2 < d1:
            self.warnings.append("Follow-up Hood report is dated before the baseline report.")
        if self.circle1 and d1 and self.circle1.info.exam_date and self.circle1.info.exam_date != d1:
            self.warnings.append("Baseline circle XML date differs from the baseline Hood report date.")
        if self.circle2 and d2 and self.circle2.info.exam_date and self.circle2.info.exam_date != d2:
            self.warnings.append("Follow-up circle XML date differs from the follow-up Hood report date.")

    # --------------------------------------------------------- components
    def ftd_angle(self) -> float:
        if self.opt.ftd_angle is not None:
            return self.opt.ftd_angle
        for v in (self.vol2, self.vol1):
            if v and v.tilt_angle_deg is not None:
                # R convention: -TiltAngle for OD (= -7.7 for the test eye); mirrored for OS
                return -v.tilt_angle_deg if v.info.laterality.upper() != "L" else v.tilt_angle_deg
        return 0.0

    def bscan(self, hood: hoodpdf.HoodReport) -> Image.Image:
        im = hood.crop("bscan")
        f0, f1 = self.opt.bscan_crop_frac
        return im.crop((0, int(im.height * f0), im.width, int(im.height * f1)))

    def diffplot(self) -> Optional[Image.Image]:
        if self.circle1 and self.circle2:
            buf = io.BytesIO()
            fig = crnfl.plot_crnfl_comparison(self.circle1.rnfl_um, self.circle2.rnfl_um, ftd_angle=self.ftd_angle(), dpi=200)
            fig.savefig(buf, format="png", dpi=200, facecolor="white")
            import matplotlib.pyplot as plt

            plt.close(fig)
            buf.seek(0)
            return Image.open(buf).convert("RGB")
        if self.inputs.diffplot_image:
            return Image.open(self.inputs.diffplot_image).convert("RGB")
        return None

    def scan_height_mm(self) -> Optional[float]:
        """Height (mm) of the posterior-pole scan, which HEYEX shows as the full height of its thickness maps."""
        v = self.vol2 or self.vol1
        if not v:
            return None
        ys = v.bscan_y_mm[~np.isnan(v.bscan_y_mm)]
        return float(ys.max() - ys.min()) if ys.size > 1 else None

    def _overlay_geometry(self, region: str):
        """Scale and placement of the scanned area as HEYEX draws it in the given Hood map.

        The overlay is the rotated scan rectangle, centred horizontally in the IR
        square and clipped by it (the rotated scan is wider than the 30° frame).
        Its visible height is measured on the follow-up map and corresponds to the
        rotated bounding box of the scan; the vertical centre comes from the
        overlay's top/bottom.  Returns (px_per_mm, cx, cy, (slot_w, slot_h)) in
        template px, where (cx, cy) is the scan centre inside the slot.
        """
        crop = self.hood2.crop(region)
        fl, ft, fr, fb = hoodpdf.detect_ir_frame(crop)
        sl, st, sr, sb = T["rnfl1"]
        slot_w, slot_h = sr - sl, sb - st
        kx, ky = slot_w / (fr - fl), slot_h / (fb - ft)
        v = self.vol2 or self.vol1
        w_mm, h_mm = v.width_mm, v.height_mm
        tilt = v.rotation_deg if self.opt.rotate_change_maps else 0.0
        a = np.deg2rad(abs(tilt))
        bbox_h_mm = w_mm * np.sin(a) + h_mm * np.cos(a)
        bb = hoodpdf.detect_scan_overlay(crop)
        edge = 3
        if bb is None or (bb[1] <= ft + edge and bb[3] >= fb - edge):
            ppm = slot_h / bbox_h_mm
            cy = slot_h / 2
            self.warnings.append("Could not measure the scan overlay in the Hood map; change-map scale assumed from frame height.")
        else:
            ppm = (bb[3] - bb[1]) * ky / bbox_h_mm
            cy = ((bb[1] + bb[3]) / 2 - ft) * ky
        cx = slot_w / 2
        return ppm, cx, cy, (slot_w, slot_h)

    def _change_canvas(self, layer: str, region: str):
        """Slot-sized grey canvas with the change map at the HEYEX overlay's scale and position.

        With ``clip_to_frame`` (default) the scan is clipped to the frame exactly as
        HEYEX does, so the panel matches the maps above one-to-one.  Otherwise the
        canvas grows so nothing is cropped.  Returns (canvas, (dx, dy)) where
        (dx, dy) is the offset of the slot's top-left inside the canvas.
        """
        s = self.opt.scale
        ppm, cx, cy, (slot_w, slot_h) = self._overlay_geometry(region)
        cm = self.change_map(layer, px_per_mm=ppm * s)  # rotated scan, tight bounding box
        w, h = cm.width / s, cm.height / s
        left, top = cx - w / 2, cy - h / 2
        if self.opt.clip_to_frame:
            x0, y0, x1, y1 = 0.0, 0.0, float(slot_w), float(slot_h)
        else:
            x0, y0 = min(0.0, left), min(0.0, top)
            x1, y1 = max(float(slot_w), left + w), max(float(slot_h), top + h)
        canvas = Image.new("RGB", (int(round((x1 - x0) * s)), int(round((y1 - y0) * s))), changemap.BACKGROUND)
        canvas.paste(cm, (int(round((left - x0) * s)), int(round((top - y0) * s))))
        return canvas, (-x0, -y0)

    def change_map(self, layer: str, px_per_mm: Optional[float] = None) -> Optional[Image.Image]:
        if self.vol1 and self.vol2:
            return changemap.change_map_from_volumes(
                self.vol1, self.vol2, layer, limit=self.opt.change_limits.get(layer), px_per_mm=px_per_mm,
                smooth_sigma_mm=self.opt.change_smooth_mm, fill_missing=self.opt.fill_missing,
                mask_disc=self.opt.mask_disc, draw_etdrs=self.opt.draw_etdrs,
                rotate_to_fodi=self.opt.rotate_change_maps, rotation_sign=self.opt.rotation_sign,
                palette=self.opt.change_palette, field_view=self.opt.view_name == "field",
                frame=self.opt.change_frame,
            )
        p = self.inputs.rnfl_change_image if layer == "rnfl" else self.inputs.gcl_change_image
        return Image.open(p).convert("RGB") if p else None

    # -------------------------------------------------------------- render
    def render(self) -> Image.Image:
        s = self.opt.scale
        S = lambda v: int(round(v * s))  # noqa: E731
        page = Image.new("RGB", (S(TEMPLATE_W), S(TEMPLATE_H)), "white")
        d = ImageDraw.Draw(page)
        F = {k: _font(v * s, bold=k in ("title", "eye", "col", "date"), italic=k == "footer") for k, v in FONT_SIZES.items()}
        F["hdr_bold"] = _font(FONT_SIZES["header"] * s, bold=True)

        def paste(img: Image.Image, box):
            l, t, r, b = box
            page.paste(_fit(img, (S(r) - S(l), S(b) - S(t))), (S(l), S(t)))

        def rect(box, width=5, color="black"):
            l, t, r, b = box
            d.rectangle([S(l), S(t), S(r), S(b)], outline=color, width=max(1, S(width)))

        def hline(box, color="black"):
            l, t, r, b = box
            d.rectangle([S(l), S(t), S(r), S(b) - 1], fill=color)

        # --- chrome -------------------------------------------------------
        logo = Image.open(ASSETS / "Logo.png").convert("RGBA")
        lb = T["logo"]
        logo = logo.resize((S(lb[2]) - S(lb[0]), S(lb[3]) - S(lb[1])), Image.LANCZOS)
        page.paste(logo, (S(lb[0]), S(lb[1])), logo)
        d.text((S(T["title"][0]), S(T["title"][1])), self.header.title, font=F["title"], fill="black")
        d.text((S(T["col_rnfl"][0]), S(T["col_rnfl"][1])), "RNFL", font=F["col"], fill="black", anchor="mm")
        d.text((S(T["col_gcl"][0]), S(T["col_gcl"][1])), "GCL", font=F["col"], fill="black", anchor="mm")
        d.text((S(T["view_label"][0]), S(T["view_label"][1])), f"{self.opt.view_name.capitalize()} view",
               font=F["header"], fill="black", anchor="mm")
        d.text((S(T["eye"][0]), S(T["eye"][1])), self.header.eye, font=F["eye"], fill="black", anchor="rm")

        # --- header fields --------------------------------------------------
        h = self.header
        rows, cols = T["hdr_rows"], T["hdr_cols"]
        cells = {
            (0, 0): ("Patient: ", h.patient), (1, 0): ("Patient ID: ", h.patient_id), (2, 0): ("Diagnosis: ", h.diagnosis),
            (0, 1): ("DOB: ", h.dob), (1, 1): ("Exam 1: ", h.exam1), (2, 1): ("Exam 2: ", h.exam2),
            (0, 2): ("Sex: ", h.sex),
        }
        for (ri, ci), (label, value) in cells.items():
            x, y = S(cols[ci]), S(rows[ri])
            d.text((x, y), label, font=F["hdr_bold"], fill="black")
            x += d.textlength(label, font=F["hdr_bold"])
            d.text((x, y), value or "", font=F["header"], fill="black")

        # --- B-scans with date overlays -------------------------------------
        for key, hood, date in (("bscan1", self.hood1, h.exam1), ("bscan2", self.hood2, h.exam2)):
            paste(self.bscan(hood), T[key])
            l, t, r, b = T[key]
            d.text((S(r) - S(18), S(b) - S(10)), date, font=F["date"], fill="white", anchor="rd")

        # --- maps from the Hood reports --------------------------------------
        mode = self.opt.map_mode
        reg_rnfl, reg_gcl = ("rnfl_prob", "gcl_prob") if mode == "probability" else ("rnfl_thick", "gcl_thick")

        def paste_map(hood, region, box):
            """Paste a map crop so that its IR fundus square fills ``box`` exactly."""
            img = hood.crop(region)
            fl, ft, fr, fb = hoodpdf.detect_ir_frame(img)
            l, t, r, b = box
            kx, ky = (S(r) - S(l)) / (fr - fl), (S(b) - S(t)) / (fb - ft)
            full = img.resize((max(1, int(round(img.width * kx))), max(1, int(round(img.height * ky)))), Image.LANCZOS)
            page.paste(full, (int(round(S(l) - fl * kx)), int(round(S(t) - ft * ky))))

        paste_map(self.hood1, reg_rnfl, T["rnfl1"])
        paste_map(self.hood1, reg_gcl, T["gcl1"])
        paste_map(self.hood2, reg_rnfl, T["rnfl2"])
        paste_map(self.hood2, reg_gcl, T["gcl2"])

        if mode == "probability":
            for key, text in (("lbl_rnfl1", "RNFL thickness deviation [%]"), ("lbl_gcl1", "GCL thickness deviation [%]"),
                              ("lbl_rnfl2", "RNFL thickness deviation [%]"), ("lbl_gcl2", "GCL thickness deviation [%]")):
                im = _rotated_text(text, F["rot"])
                cx, cy = T[key]
                page.paste(im, (S(cx) - im.width // 2, S(cy) - im.height // 2), im)
            cb = self.hood1.crop("prob_cb")
            l, t, r, b = T["prob_cb"]
            hgt = 240
            wid = hgt * cb.width / cb.height
            x = l + (r - l - wid) / 2
            paste(cb, (x, t + 30, x + wid, t + 30 + hgt))
        else:
            # per-row colour bars cropped from the same report (they carry the µm label)
            for row_key, cb_key, x in (("rnfl1", "rnfl_thick_cb", T["thick_cb_rnfl_x"]), ("gcl1", "gcl_thick_cb", T["thick_cb_gcl_x"]),
                                       ("rnfl2", "rnfl_thick_cb", T["thick_cb_rnfl_x"]), ("gcl2", "gcl_thick_cb", T["thick_cb_gcl_x"])):
                hood = self.hood1 if row_key.endswith("1") else self.hood2
                cb = hood.crop(cb_key)
                l, t, r, b = T[row_key]
                hgt = b - t
                wid = hgt * cb.width / cb.height
                paste(cb, (x, t, x + wid, b))

        # --- change maps: same px/mm and position as the HEYEX maps above them ---
        rx0, ry0, rx1, ry1 = T["chg_region"]
        pad = T["chg_rect_pad"]
        layers = (("rnfl", "RNFL thickness change [µm]", "rnfl_chg"), ("gcl", "GCL thickness change [µm]", "gcl_chg"))
        if self.vol1 and self.vol2:
            canvases = {k: self._change_canvas(k, reg_rnfl if k == "rnfl" else reg_gcl) for k, _, _ in layers}
            title_h, bar_w, tick_w, gap = 26, 18, 36, 8
            y_map = ry0 + title_h + pad
            # preferred: same columns as the maps above; fall back to centring if the row overflows
            xs = []
            for layer, _, slot_key in layers:
                cv, off = canvases[layer]
                xs.append(T[slot_key][0] - off[0])
            rn_w = canvases["rnfl"][0].width / s
            if xs[0] + rn_w + pad + gap + bar_w + gap + tick_w > xs[1] - pad or xs[0] < rx0:
                widths = [canvases[k][0].width / s for k, _, _ in layers]
                group = [w + gap + bar_w + gap + tick_w for w in widths]
                x = rx0 + ((rx1 - rx0) - (sum(group) + 22)) / 2
                xs = [x, x + group[0] + 22]
            for (layer, text, _), x in zip(layers, xs):
                cv, _ = canvases[layer]
                w, h = cv.width / s, cv.height / s
                page.paste(cv, (S(x), S(y_map)))
                rect((x - pad, y_map - pad, x + w + pad, y_map + h + pad), width=3, color=(0, 220, 230))
                d.text((S(x + w / 2), S(ry0 + title_h / 2)), text, font=F["rot"], fill="black", anchor="mm")
                if self.opt.show_change_colorbars:
                    bar = changemap.change_colorbar_labeled(layer, S(bar_w), S(h), F["tick"],
                                                            limit=self.opt.change_limits.get(layer), palette=self.opt.change_palette)
                    page.paste(bar, (S(x + w + pad + gap), S(y_map) - F["tick"].size // 2), bar)
        else:
            for i, (layer, text, _) in enumerate(layers):
                l, t = rx0 + 60 + i * 560, ry0 + 40
                rect((l, t, l + 360, t + 360), width=2, color=(180, 180, 180))
                d.text((S(l + 180), S(t + 180)), "no posterior-pole XML", font=F["rot"], fill="gray", anchor="mm")

        # --- cpRNFL plot ------------------------------------------------------------
        dp = self.diffplot()
        l, t, r, b = T["diffplot"]
        if dp is not None:
            # scale so the plot axes span exactly the B-scan width, and align them to the B-scan edges
            bl, _, br, _ = T["bscan1"]
            frac = crnfl.AXES_RIGHT - crnfl.AXES_LEFT
            w = (br - bl) / frac
            k = min(S(w) / dp.width, (S(b) - S(t)) / dp.height)
            x = bl - crnfl.AXES_LEFT * (dp.width * k / s)
            page.paste(dp.resize((int(dp.width * k), int(dp.height * k)), Image.LANCZOS), (S(x), S(t)))
        else:
            d.text(((S(l) + S(r)) // 2, (S(t) + S(b)) // 2), "no circle XML", font=F["rot"], fill="gray", anchor="mm")

        # --- rules (drawn last so pasted images never cover them) ---------------
        for k in ("line1", "line2", "line3"):
            hline(T[k])

        # --- footer -------------------------------------------------------------
        d.text((S(T["footer_left"]), S(T["footer_y"])), self.opt.footer_left, font=F["footer"], fill="black", anchor="lm")
        from . import __version__
        right = self.opt.footer_right or f"Progression Report v{__version__}"
        d.text((S(T["footer_right"]), S(T["footer_y"])), right, font=F["footer"], fill="black", anchor="rm")
        return page

    # -------------------------------------------------------------- export
    def save(self, out_path: str | Path, page: Optional[Image.Image] = None) -> Path:
        out_path = Path(out_path)
        page = page or self.render()
        if out_path.suffix.lower() == ".pdf":
            page.save(out_path, "PDF", resolution=TEMPLATE_W * self.opt.scale / 11.0)  # 11 in wide (letter landscape)
        else:
            page.save(out_path, dpi=(int(TEMPLATE_W * self.opt.scale / 11.0),) * 2)
        return out_path

    def suggested_filename(self, ext: str = "png") -> str:
        h = self.header
        pid = h.patient_id or "patient"
        e1 = h.exam1.replace("/", "") or "exam1"
        e2 = h.exam2.replace("/", "") or "exam2"
        mode = "prob" if self.opt.map_mode == "probability" else "thick"
        return f"{pid}_{h.eye or 'eye'}_{e1}_to_{e2}_{mode}.{ext}"
