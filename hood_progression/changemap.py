"""RNFL / GCL thickness change maps from two posterior-pole volume exports.

Port of ``generate_rnfl_change_map.R`` / ``generate_gcl_change_map.R`` with the
Heidelberg-style palette used in the lab's reports: green = thinning,
black = no change, red = thickening, saturating to white beyond the limits
(the R palette ``c("white", "#00ff00", "black", "#ff0000", "white")``).

Orientation: B-scan 1 of a Spectralis posterior-pole volume is the
inferior-most line, so the grid is flipped vertically to put superior at the
top; A-scans run left-to-right as on the fundus image.  Nothing is mirrored
for left eyes (same as the Heidelberg report and the original R code).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .hexml import VolumeScan

PALETTES = {
    # red = thinning (loss), white = no change, blue = thickening
    "diverging": [(165, 0, 38), (215, 48, 39), (244, 109, 67), (253, 174, 97), (254, 224, 144), (255, 255, 255),
                  (224, 243, 248), (171, 217, 233), (116, 173, 209), (69, 117, 180), (49, 54, 149)],
    # Heidelberg-style: green = thinning, black = none, red = thickening, white beyond limits
    "heidelberg": [(255, 255, 255), (0, 255, 0), (0, 0, 0), (255, 0, 0), (255, 255, 255)],
}
DEFAULT_PALETTE = "heidelberg"
DEFAULT_LIMITS = {"rnfl": 40.0, "gcl": 30.0}
BACKGROUND = (205, 205, 205)  # outside the scanned area / unsegmented


def _colormap(values: np.ndarray, limit: float, palette: str = DEFAULT_PALETTE, nan_rgb=BACKGROUND) -> np.ndarray:
    """Map values in [-limit, limit] onto the palette; NaN -> nan_rgb."""
    pal = np.array(PALETTES[palette], dtype=float)
    t = np.clip((values + limit) / (2 * limit), 0, 1)  # 0..1
    stops = np.linspace(0, 1, len(pal))
    rgb = np.stack([np.interp(t, stops, pal[:, c]) for c in range(3)], axis=-1)
    rgb[np.isnan(values)] = nan_rgb
    return rgb.astype(np.uint8)


def inpaint_nearest(grid: np.ndarray) -> np.ndarray:
    """Fill every NaN with the value of the nearest valid grid point (in B-scan/A-scan units,
    scaled so that one B-scan step counts as ~12 A-scans, i.e. equal distance in mm)."""
    g = grid.copy()
    nan = np.isnan(g)
    if not nan.any():
        return g
    if nan.all():
        return g
    # anisotropic sampling: 61 rows over 7.5 mm vs 768 cols over 9.0 mm
    idx = ndimage.distance_transform_edt(nan, sampling=(10.5, 1.0), return_distances=False, return_indices=True)
    return g[tuple(idx)]


def disc_mask(grids: list[np.ndarray], min_area_frac: float = 0.004) -> np.ndarray:
    """Mask of the optic-disc gap: the large unsegmented blob(s) in either volume.

    The disc is the only *large* unsegmented region; small holes and the thin
    unsegmented margins at the B-scan ends are inpainted instead.
    """
    nan = np.zeros(grids[0].shape, bool)
    for g in grids:
        nan |= np.isnan(g)
    closed = ndimage.binary_closing(nan, structure=np.ones((3, 25)), border_value=0)
    lab, n = ndimage.label(closed)
    mask = np.zeros_like(nan)
    if n:
        sizes = ndimage.sum(closed, lab, range(1, n + 1))
        keep = [i + 1 for i, sz in enumerate(sizes) if sz >= min_area_frac * nan.size]
        # ignore thin strips hugging the B-scan ends (missing segmentation at the margins)
        for k in list(keep):
            ys, xs = np.nonzero(lab == k)
            if xs.max() - xs.min() < 20 and (xs.min() <= 2 or xs.max() >= nan.shape[1] - 3):
                keep.remove(k)
        mask = np.isin(lab, keep)
        mask = ndimage.binary_dilation(mask, structure=np.ones((3, 9)))
    return mask


def thickness_change_grid(baseline: VolumeScan, followup: VolumeScan, layer: str,
                          fill_missing: bool = True, mask_disc: bool = True) -> np.ndarray:
    """Follow-up minus baseline thickness (µm), shape (n_bscans, n_ascans).

    Missing values (unsegmented A-scans, missing B-scans) are inpainted from the
    nearest valid data so the map has no holes; the optic-disc gap is kept as
    NaN when ``mask_disc`` is set (HEYEX leaves it grey too).
    """
    key = {"rnfl": "rnfl_um", "gcl": "gcl_um"}[layer]
    b, f = getattr(baseline, key), getattr(followup, key)
    if b.shape != f.shape:
        raise ValueError(f"Volume shapes differ: baseline {b.shape} vs follow-up {f.shape}")
    disc = disc_mask([b, f]) if mask_disc else np.zeros(b.shape, bool)
    if fill_missing:
        b, f = inpaint_nearest(b), inpaint_nearest(f)
    change = f - b
    change[disc] = np.nan
    return change


def _localizer_scale(vol: VolumeScan) -> float:
    return vol.localizer_scale_mm_per_px or 0.0117


def change_map_from_volumes(
    baseline: VolumeScan,
    followup: VolumeScan,
    layer: str,
    size: int = 768,
    limit: Optional[float] = None,
    smooth_sigma_mm: float = 0.15,
    fill_missing: bool = True,
    mask_disc: bool = True,
    draw_etdrs: bool = False,
    rotate_to_fodi: bool = True,
    rotation_sign: float = 1.0,
    palette: str = DEFAULT_PALETTE,
    field_mm: Optional[float] = None,
    field_view: bool = False,
    frame: str = "tight",
    px_per_mm: Optional[float] = None,
    out_path: Optional[str | Path] = None,
) -> Image.Image:
    """Render the change map in the same frame as the Hood report's thickness maps.

    HEYEX's "Retina view" square shows the scan's full height (25°) centred on
    the scan, with the width cropped (that is why the disc is clipped at the
    edge).  We reproduce that: ``field_mm`` defaults to the scanned height, the
    frame is centred on the scanned rectangle, and the rectangle is rotated
    about its centre by the fovea-to-disc tilt angle so it lines up with the
    HEYEX maps above it.  ``frame="tight"`` (default) returns just the rotated
    rectangle's bounding box (nothing cropped, no square padding), ``"full"`` a
    square containing it, and ``"heyex"`` the HEYEX crop.  ``px_per_mm`` fixes
    the scale explicitly (e.g. to match the thickness maps); otherwise it is
    derived from ``size``.  Set ``rotation_sign = -1`` if a report's
    rotation goes the other way.  ``field_view=True`` flips the result vertically
    (inferior retina at the top) to match the Hood report's "Field view"
    deviation maps.
    """
    change = thickness_change_grid(baseline, followup, layer, fill_missing=fill_missing, mask_disc=mask_disc)
    limit = limit or DEFAULT_LIMITS[layer]
    vol = followup
    loc = _localizer_scale(vol)
    x0, x1 = vol.ascan_x_mm
    ys = vol.bscan_y_mm[~np.isnan(vol.bscan_y_mm)]
    width_mm, height_mm = vol.width_mm, vol.height_mm
    tilt = vol.rotation_deg if rotate_to_fodi else 0.0
    a = np.deg2rad(abs(tilt))
    bbox_w_mm = width_mm * np.cos(a) + height_mm * np.sin(a)
    bbox_h_mm = width_mm * np.sin(a) + height_mm * np.cos(a)
    if frame == "heyex":
        fw_mm = fh_mm = field_mm or height_mm
    elif frame == "full":
        fw_mm = fh_mm = field_mm or max(bbox_w_mm, bbox_h_mm) * 1.02
    else:  # tight
        fw_mm, fh_mm = bbox_w_mm, bbox_h_mm
    if px_per_mm is None:
        px_per_mm = size / max(fw_mm, fh_mm)
    canvas_w, canvas_h = int(round(fw_mm * px_per_mm)), int(round(fh_mm * px_per_mm))
    cx_mm = (min(x0, x1) + max(x0, x1)) / 2 if np.isfinite(x0 + x1) else width_mm / 2
    cy_mm = (ys.min() + ys.max()) / 2 if ys.size > 1 else height_mm / 2
    origin = (cx_mm - fw_mm / 2, cy_mm - fh_mm / 2)  # top-left of the frame, in SLO mm

    # --- rasterise the scanned rectangle at its physical size -------------
    w, h = max(2, int(round(width_mm * px_per_mm))), max(2, int(round(height_mm * px_per_mm)))
    grid = np.flipud(change) if vol.first_bscan_is_inferior else change  # superior at top
    if not vol.ascans_left_to_right:
        grid = np.fliplr(grid)
    mask = np.isnan(grid)
    zoom = (h / grid.shape[0], w / grid.shape[1])
    up = ndimage.zoom(np.where(mask, 0.0, grid), zoom, order=1)
    up_mask = ndimage.zoom(mask.astype(float), zoom, order=1) > 0.5
    sigma_px = smooth_sigma_mm * px_per_mm
    if sigma_px > 0.3:
        # normalised convolution: smooth valid data only, so the disc gap does not bleed in
        weight = ndimage.gaussian_filter((~up_mask).astype(float), sigma_px)
        up = ndimage.gaussian_filter(np.where(up_mask, 0.0, up), sigma_px)
        with np.errstate(invalid="ignore", divide="ignore"):
            up = np.where(weight > 1e-3, up / weight, 0.0)
    up[up_mask] = np.nan
    rgb = _colormap(up, limit, palette)
    alpha = np.where(up_mask, 0, 255).astype(np.uint8)
    rect = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")

    # --- rotate about the rectangle centre by the FoDi tilt angle ----------
    if tilt:
        rect = rect.rotate(rotation_sign * tilt, resample=Image.BILINEAR, expand=True)

    # --- place in the SLO frame ---------------------------------------------
    canvas = Image.new("RGB", (canvas_w, canvas_h), BACKGROUND)
    cx, cy = canvas_w / 2, canvas_h / 2
    canvas.paste(rect, (int(round(cx - rect.width / 2)), int(round(cy - rect.height / 2))), rect)

    # --- ETDRS 1/3/6 mm grid at the HEYEX fovea position ----------------------
    if draw_etdrs and vol.etdrs_center_px is not None:
        ex = (vol.etdrs_center_px[0] * loc - origin[0]) * px_per_mm
        ey = (vol.etdrs_center_px[1] * loc - origin[1]) * px_per_mm
        d = ImageDraw.Draw(canvas)
        col = (60, 60, 60)
        for dia in (1.0, 3.0, 6.0):
            r = dia / 2 * px_per_mm
            d.ellipse([ex - r, ey - r, ex + r, ey + r], outline=col, width=2)
        r0, r = 0.5 * px_per_mm, 3.0 * px_per_mm
        for ang in (45, 135, 225, 315):
            a = np.deg2rad(ang)
            d.line([(ex + r0 * np.cos(a), ey + r0 * np.sin(a)), (ex + r * np.cos(a), ey + r * np.sin(a))], fill=col, width=2)
    if field_view:
        canvas = canvas.transpose(Image.FLIP_TOP_BOTTOM)
    if out_path:
        canvas.save(out_path)
    return canvas


def change_colorbar(layer: str, width: int = 20, height: int = 360, limit: Optional[float] = None,
                    palette: str = DEFAULT_PALETTE) -> Image.Image:
    """Vertical colour bar matching the change map palette (thickening at top)."""
    limit = limit or DEFAULT_LIMITS[layer]
    vals = np.linspace(limit, -limit, height)[:, None].repeat(width, axis=1)
    return Image.fromarray(_colormap(vals, limit, palette))


def change_colorbar_labeled(layer: str, bar_width: int, height: int, font, limit: Optional[float] = None,
                            palette: str = DEFAULT_PALETTE, n_ticks: int = 5) -> Image.Image:
    """Colour bar with a thin outline and tick labels (µm) to its right, transparent background."""
    limit = limit or DEFAULT_LIMITS[layer]
    bar = change_colorbar(layer, bar_width, height, limit, palette).convert("RGBA")
    labels = [f"{v:+.0f}" if v else "0" for v in np.linspace(limit, -limit, n_ticks)]
    lw = max(font.getbbox(t)[2] for t in labels)
    pad = int(font.size * 0.3)
    im = Image.new("RGBA", (bar_width + pad + lw + 2, height + font.size), (0, 0, 0, 0))
    top = font.size // 2
    im.paste(bar, (0, top))
    d = ImageDraw.Draw(im)
    d.rectangle([0, top, bar_width - 1, top + height - 1], outline=(90, 90, 90), width=1)
    for t, y in zip(labels, np.linspace(0, height - 1, n_ticks)):
        y = int(y) + top
        d.line([(bar_width, y), (bar_width + pad // 2, y)], fill=(60, 60, 60), width=1)
        d.text((bar_width + pad, y), t, font=font, fill=(0, 0, 0), anchor="lm")
    return im
