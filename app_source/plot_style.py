"""Shared publication-style tokens for report and app figures."""

from __future__ import annotations

import matplotlib as mpl


INK = "#172033"
MUTED = "#667085"
GRID = "#E4EAF0"
LIGHT = "#F6F8FA"
ACCENT = "#0F766E"
OTHER = "#8A99A8"

# Okabe-Ito inspired, color-vision-deficiency-safe categorical palette.
CVD_PALETTE = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#7A6F5D",
    "#8A99A8",
)


PUBLICATION_RC = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7.5,
    "axes.labelsize": 7.5,
    "axes.titlesize": 10,
    "axes.titleweight": "bold",
    "axes.labelcolor": INK,
    "axes.edgecolor": "#AAB4C0",
    "axes.linewidth": 0.55,
    "xtick.labelsize": 6.8,
    "ytick.labelsize": 6.8,
    "xtick.color": "#475467",
    "ytick.color": INK,
    "xtick.major.width": 0.55,
    "ytick.major.width": 0.55,
    "legend.fontsize": 6.5,
    "legend.title_fontsize": 6.5,
    "lines.linewidth": 1.0,
    "patch.linewidth": 0.55,
    "savefig.dpi": 600,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.06,
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
}


def publication_context():
    """Return an rc_context shared by all Word/report figures."""
    return mpl.rc_context(PUBLICATION_RC)


def style_axis(axis, *, grid_axis: str = "x", hide_left: bool = True) -> None:
    """Apply restrained report styling to one Matplotlib axis."""
    axis.set_axisbelow(True)
    axis.grid(axis=grid_axis, color=GRID, linewidth=0.55, alpha=0.9)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    if hide_left:
        axis.spines["left"].set_visible(False)
        axis.tick_params(axis="y", length=0)


def add_axis_title(axis, title: str, subtitle: str = "") -> None:
    """Create a compact two-level title that remains legible in Word."""
    axis.set_title(title, loc="left", color=INK, pad=18 if subtitle else 9)
    if subtitle:
        axis.text(
            0,
            1.012,
            subtitle,
            transform=axis.transAxes,
            ha="left",
            va="bottom",
            fontsize=6.7,
            color=MUTED,
        )


__all__ = [
    "ACCENT",
    "CVD_PALETTE",
    "GRID",
    "INK",
    "LIGHT",
    "MUTED",
    "OTHER",
    "PUBLICATION_RC",
    "add_axis_title",
    "publication_context",
    "style_axis",
]
