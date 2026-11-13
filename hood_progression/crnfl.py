"""cpRNFL baseline/follow-up comparison and difference plot.

Port of ``circle_scan.R`` (``change_map`` / ``plot_crnfl_diff``).  Angular
position is 0° at the temporal start point of the circle scan, increasing
through superior to nasal (+180°), and negative through inferior.  The plot
x-axis is reversed so that it reads N - NS - TS - T - TI - NI - N from left
to right, like the Hood report's NSTIN plot.

The "ftd angle" (fovea-to-disc tilt) shifts the angular axis; the R script
used -7.7° for a scan whose posterior-pole export reports TiltAngle = 7.7, so
by default we use ``-TiltAngle``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
# Figure margins (fractions); the report uses these to align the axes with the B-scans above
AXES_LEFT, AXES_RIGHT = 0.075, 0.985
FIGSIZE = (10.0, 5.3)
SECTOR_LINES = (135, 90, 45, -45, -90, -135)
SECTOR_LABELS = ((170, "N"), (115, "NS"), (70, "TS"), (0, "T"), (-70, "TI"), (-115, "NI"), (-170, "N"))


def angular_axis(n: int = 768, ftd_angle: float = 0.0) -> np.ndarray:
    """Degrees for each A-scan, as in the R code: seq(0, 360, len=n) + ftd, wrapped to (-180, 180]."""
    deg = np.linspace(0.0, 360.0, n) + ftd_angle
    deg = np.where(deg > 180, deg - 360, deg)
    return deg


def load_short_term_variability(path: Optional[str | Path] = None) -> dict[str, np.ndarray]:
    """Short-term test-retest limits of the cpRNFL difference (768 points)."""
    path = Path(path) if path else ASSETS / "short_term_var_crnfl.csv"
    data = np.genfromtxt(path, delimiter=",", names=True)
    return {k: np.asarray(data[k], dtype=float) for k in data.dtype.names}


def _smooth(y: np.ndarray, window: int = 77) -> np.ndarray:
    """Cheap stand-in for R's loess(span=0.1): centred moving average, wrap-around."""
    k = np.ones(window) / window
    pad = window // 2
    yp = np.concatenate([y[-pad:], y, y[:pad]])
    return np.convolve(yp, k, mode="valid")


def sector_means(rnfl: np.ndarray, deg: np.ndarray) -> dict[str, float]:
    """Garway-Heath style sector means (G, T, TS, NS, N, NI, TI)."""
    return {
        "G": float(np.nanmean(rnfl)),
        "T": float(np.nanmean(rnfl[(deg >= -45) & (deg <= 45)])),
        "TS": float(np.nanmean(rnfl[(deg > 45) & (deg <= 90)])),
        "NS": float(np.nanmean(rnfl[(deg > 90) & (deg <= 135)])),
        "N": float(np.nanmean(rnfl[(deg > 135) | (deg < -135)])),
        "NI": float(np.nanmean(rnfl[(deg >= -135) & (deg < -90)])),
        "TI": float(np.nanmean(rnfl[(deg < -45) & (deg >= -90)])),
    }


def plot_crnfl_comparison(
    baseline_um: np.ndarray,
    followup_um: np.ndarray,
    ftd_angle: float = 0.0,
    out_path: Optional[str | Path] = None,
    dpi: int = 300,
    figsize=FIGSIZE,
    threshold_csv: Optional[str | Path] = None,
    max_diff: float = 50.0,
):
    """Render the stacked comparison + difference plot and return the PNG path (or Figure)."""
    n = len(baseline_um)
    deg = angular_axis(n, ftd_angle)
    diff = followup_um - baseline_um
    var = load_short_term_variability(threshold_csv)
    lower, upper = var["lower_5"][:n], var["upper_95"][:n]

    # Plot in the order of increasing degrees so lines don't jump at the wrap point
    order = np.argsort(deg)
    d, b, f, df = deg[order], baseline_um[order], followup_um[order], diff[order]
    lo, up = _smooth(lower[order]), _smooth(upper[order])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=figsize, dpi=dpi, gridspec_kw={"hspace": 0.35})
    for ax in (ax1, ax2):
        ax.set_xlim(180, -180)
        ax.set_xticks(np.arange(-180, 181, 45))
        for xv in SECTOR_LINES:
            ax.axvline(xv, ls=(0, (5, 5)), color="black", alpha=0.4, lw=0.8)
        ax.axhline(0, color="black", lw=0.8)
        ax.spines[["top", "right"]].set_visible(True)
        ax.tick_params(labelsize=9)

    # --- comparison -------------------------------------------------------
    ax1.plot(d, b, color="#b0b0b0", lw=1.5, label="Baseline")
    ax1.plot(d, f, color="black", lw=1.5, label="Follow-Up")
    ax1.set_ylim(0, 320)
    ax1.set_yticks(np.arange(0, 301, 50))
    ax1.set_ylabel("cRNFL Thickness", fontsize=10)
    ax1.set_xlabel("degrees", fontsize=10)
    for x, lab in SECTOR_LABELS:
        ax1.text(x, 300, lab, ha="center", va="center", fontsize=10)

    # --- difference, coloured by threshold -------------------------------
    below = df < lower[order]
    pts = np.column_stack([d, df]).reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    cols = np.where(below[:-1] | below[1:], "red", "#339999")
    ax2.add_collection(LineCollection(segs, colors=cols, lw=1.5))
    ax2.plot(d, lo, color="#999999", ls=(0, (5, 5)), lw=0.8)
    ax2.plot(d, up, color="#999999", ls=(0, (5, 5)), lw=0.8)
    ax2.set_ylim(-max_diff, max_diff)
    ax2.set_yticks(np.arange(-max_diff, max_diff + 1, 25))
    ax2.set_ylabel("cRNFL Thickness", fontsize=10)
    ax2.set_xlabel("Degrees", fontsize=10)
    for x, lab in SECTOR_LABELS:
        ax2.text(x, -max_diff + 3, lab, ha="center", va="bottom", fontsize=10)

    fig.subplots_adjust(left=AXES_LEFT, right=AXES_RIGHT, top=0.98, bottom=0.09)
    if out_path is None:
        return fig
    fig.savefig(out_path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return Path(out_path)
