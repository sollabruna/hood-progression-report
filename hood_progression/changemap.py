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


def inpaint_smooth(grid: np.ndarray, sigma_cols: float = 12.0) -> np.ndarray:
    """Fill NaNs with a smooth estimate from the surrounding valid data.

    Normalised Gaussian convolution of the valid values, with the kernel widened
    until every gap is covered (B-scan spacing ~0.125 mm vs A-scan ~0.012 mm, so
    the kernel is anisotropic).  Valid points keep their original values.
    """
    g = grid.copy()
    nan = np.isnan(g)
    if not nan.any() or nan.all():
        return g
    valid = (~nan).astype(float)
    vals = np.where(nan, 0.0, g)
    out = g.copy()
    todo = nan.copy()
    sig = sigma_cols
    for _ in range(8):
        num = ndimage.gaussian_filter(vals, sigma=(sig / 10.5, sig), mode="nearest")
        den = ndimage.gaussian_filter(valid, sigma=(sig / 10.5, sig), mode="nearest")
        ok = todo & (den > 0.02)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[ok] = (num / den)[ok]
        todo &= ~ok
        if not todo.any():
            break
        sig *= 2
    if todo.any():  # anything still open: nearest valid value
        idx = ndimage.distance_transform_edt(todo, sampling=(10.5, 1.0), return_distances=False, return_indices=True)
        out[todo] = np.where(np.isnan(out[tuple(idx)]), 0.0, out[tuple(idx)])[todo]
    return out


def _edge_runs(nan: np.ndarray):
    """Per B-scan length of the NaN run at the left and right end (n if the row is empty)."""
    n = nan.shape[1]
    L, R = np.zeros(nan.shape[0], int), np.zeros(nan.shape[0], int)
    for r in range(nan.shape[0]):
        ok = np.flatnonzero(~nan[r])
        if ok.size == 0:
            L[r] = R[r] = n
        else:
            L[r], R[r] = ok[0], n - 1 - ok[-1]
    return L, R


def _margin_fit(run: np.ndarray, tol: int = 12, max_run: int = 100) -> np.ndarray:
    """Expected margin width per row: robust straight-line fit of the edge-run lengths.

    The unsegmented margin at a B-scan end widens linearly along the stack when
    the pattern is rotated; the disc and failed B-scans stick out beyond it.
    """
    rows = np.arange(run.size)
    keep = run < max_run
    if keep.sum() < 5:
        return np.full(run.size, tol)
    for _ in range(3):
        a, b = np.polyfit(rows[keep], run[keep], 1)
        fit = a * rows + b
        keep = (run < max_run) & (np.abs(run - fit) <= 15)
        if keep.sum() < 5:
            break
    return np.clip(fit, 0, None) + tol


def _edge_margin_runs(nan: np.ndarray) -> np.ndarray:
    """Mask of the unsegmented margins at the B-scan ends (fitted width per row)."""
    n = nan.shape[1]
    L, R = _edge_runs(nan)
    mL, mR = _margin_fit(L), _margin_fit(R)
    cols = np.arange(n)
    m = (cols[None, :] < mL[:, None]) | (cols[None, :] >= (n - mR)[:, None])
    return m & nan


def disc_mask(grids: list[np.ndarray], min_area_frac: float = 0.002, fovea_row: Optional[float] = None,
              nasal_side: Optional[str] = None) -> np.ndarray:
    """Clean mask of the optic disc: convex hull of the unsegmented disc blob.

    The unsegmented margins at the B-scan ends are removed first (their width
    is fitted per scan, since it slopes along the rotated stack).  Among the
    remaining unsegmented blobs the disc is the one on the nasal side with
    disc-like proportions (not a wide band of failed B-scans) lying closest to
    the fovea's height; everything else is left to the inpainting.  The convex
    hull is taken in physical (mm-equal) coordinates for a smooth outline, and
    extended to the scan edge when the disc runs off it, as in the HEYEX maps.
    """
    from PIL import Image, ImageDraw
    from scipy.spatial import ConvexHull

    nan = np.zeros(grids[0].shape, bool)
    for g in grids:
        nan |= np.isnan(g)
    nb, na = nan.shape
    cand = nan & ~_edge_margin_runs(nan)
    cand = ndimage.binary_closing(cand, structure=np.ones((3, 15)), border_value=0)
    lab, n = ndimage.label(cand)
    mask = np.zeros_like(nan)
    if not n:
        return mask
    best, best_score = None, None
    for k in range(1, n + 1):
        ys, xs = np.nonzero(lab == k)
        size = ys.size
        if size < min_area_frac * nan.size:
            continue
        h_rows, w_cols = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        if h_rows < 3 or w_cols < 15 or w_cols > 260:  # too thin, or a wide band of failed B-scans
            continue
        cx, cy = xs.mean(), ys.mean()
        if nasal_side == "right" and cx < na / 2 or nasal_side == "left" and cx > na / 2:
            continue  # the disc is on the nasal side
        dist = abs(cy - fovea_row) if fovea_row is not None else abs(cy - nb / 2)
        score = (dist, -size)
        if best_score is None or score < best_score:
            best, best_score = k, score
    if best is None:
        return mask
    ys, xs = np.nonzero(lab == best)
    pts = np.column_stack([xs, ys * 10.5])  # mm-equal units (B-scan step ~ 10.5 A-scans)
    try:
        hull = ConvexHull(pts)
        poly = [(float(pts[i, 0]), float(pts[i, 1])) for i in hull.vertices]
    except Exception:  # noqa: BLE001  (degenerate blob)
        return lab == best
    im = Image.new("L", (na, int(round(nb * 10.5))), 0)
    ImageDraw.Draw(im).polygon(poly, fill=255)
    hi = np.asarray(im) > 0
    rows = np.clip(np.round(np.arange(nb) * 10.5 + 5).astype(int), 0, hi.shape[0] - 1)
    mask = hi[rows]
    reach = 80
    for r in np.flatnonzero(mask.any(axis=1)):
        cols = np.flatnonzero(mask[r])
        if cols.max() >= na - reach:
            mask[r, cols.max():] = True
        if cols.min() <= reach:
            mask[r, : cols.min()] = True
    return mask


def _resample_to(src: VolumeScan, grid: np.ndarray, dst: VolumeScan) -> np.ndarray:
    """Resample ``grid`` (rows = src B-scans) onto dst's B-scan positions and A-scan count.

    Used when the two visits were acquired with different scan patterns (e.g.
    61 vs 49 lines) or widths.  Rows are interpolated by fundus Y position
    (nearest valid neighbours, so missing rows do not poison the result);
    columns by linear interpolation along the B-scan.
    """
    sy, dy = src.bscan_y_mm, dst.bscan_y_mm
    ok = ~np.isnan(sy)
    out_rows = np.full((len(dy), grid.shape[1]), np.nan)
    if ok.sum() >= 2:
        order = np.argsort(sy[ok])
        ys_sorted, rows_sorted = sy[ok][order], grid[ok][order]
        for i, y in enumerate(dy):
            if np.isnan(y):
                continue
            j = np.searchsorted(ys_sorted, y)
            j0, j1 = max(j - 1, 0), min(j, len(ys_sorted) - 1)
            if j0 == j1 or ys_sorted[j1] == ys_sorted[j0]:
                out_rows[i] = rows_sorted[j0]
                continue
            t = (y - ys_sorted[j0]) / (ys_sorted[j1] - ys_sorted[j0])
            a, c = rows_sorted[j0], rows_sorted[j1]
            mix = (1 - t) * a + t * c
            out_rows[i] = np.where(np.isnan(mix), np.where(np.isnan(a), c, a), mix)
    else:
        out_rows[: min(len(dy), grid.shape[0])] = grid[: min(len(dy), grid.shape[0])]
    nd = dst.rnfl_um.shape[1]
    if out_rows.shape[1] != nd:
        xs = np.linspace(0, 1, out_rows.shape[1])
        xd = np.linspace(0, 1, nd)
        out_rows = np.vstack([np.interp(xd, xs, r) for r in out_rows])
    return out_rows


def _fovea_row(vol: VolumeScan) -> Optional[float]:
    """Grid row (B-scan index) at the fovea, from HEYEX's ETDRS centre, if available."""
    if vol.etdrs_center_px is None or not vol.localizer_scale_mm_per_px:
        return None
    y_mm = vol.etdrs_center_px[1] * vol.localizer_scale_mm_per_px
    ys = vol.bscan_y_mm
    ok = ~np.isnan(ys)
    if ok.sum() < 2:
        return None
    return float(np.interp(y_mm, ys[ok][::-1], np.flatnonzero(ok)[::-1]) if ys[ok][0] > ys[ok][-1] else np.interp(y_mm, ys[ok], np.flatnonzero(ok)))


def thickness_change_grid(baseline: VolumeScan, followup: VolumeScan, layer: str,
                          fill_missing: bool = True, mask_disc: bool = True) -> np.ndarray:
    """Follow-up minus baseline thickness (µm), shape (n_bscans, n_ascans).

    Missing values (unsegmented A-scans, missing B-scans) are inpainted from the
    nearest valid data so the map has no holes; the optic-disc gap is kept as
    NaN when ``mask_disc`` is set (HEYEX leaves it grey too).
    """
    key = {"rnfl": "rnfl_um", "gcl": "gcl_um"}[layer]
    b, f = getattr(baseline, key), getattr(followup, key)
    b_rnfl = baseline.rnfl_um
    if b.shape != f.shape:  # different scan pattern: bring the baseline onto the follow-up's grid
        b = _resample_to(baseline, b, followup)
        b_rnfl = _resample_to(baseline, baseline.rnfl_um, followup)
    # the disc is defined on the RNFL data (where it is cleanest) so both maps share one outline
    nasal = None
    lat = (followup.info.laterality or "").upper()
    if lat in ("R", "L"):
        nasal = ("right" if lat == "R" else "left") if followup.ascans_left_to_right else ("left" if lat == "R" else "right")
    disc = disc_mask([b_rnfl, followup.rnfl_um], fovea_row=_fovea_row(followup), nasal_side=nasal) if mask_disc else np.zeros(b.shape, bool)
    if fill_missing:
        b, f = inpaint_smooth(b), inpaint_smooth(f)
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
    smooth_sigma_mm: float = 0.10,
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
        rgb_im = Image.fromarray(rgb).rotate(rotation_sign * tilt, resample=Image.BILINEAR, expand=True, fillcolor=BACKGROUND)
        a_im = Image.fromarray(alpha).rotate(rotation_sign * tilt, resample=Image.BILINEAR, expand=True, fillcolor=0)
        rect = rgb_im.convert("RGBA")
        rect.putalpha(a_im.point(lambda v: 255 if v >= 128 else 0))

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
