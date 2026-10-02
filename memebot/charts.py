"""Balkendiagramme: Risikofaktoren und Halte-Empfehlung (lang vs. kurz) als PNG."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False  # "$SYMBOL" nicht als Formel rendern
import matplotlib.pyplot as plt  # noqa: E402

from .scoring import Analysis, risk_level  # noqa: E402
from .utils import format_usd  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
STATUS = {"niedrig": "#0ca30c", "mittel": "#fab219", "hoch": "#ec835a", "sehr hoch": "#d03b3b"}
STATUS_ICON = {"niedrig": "✓", "mittel": "!", "hoch": "!!", "sehr hoch": "✕"}
HORIZON_ACTIVE = "#2a78d6"
HORIZON_MUTED = "#c3c2b7"


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, 118)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, length=0, labelsize=9)


def render_analysis_chart(analysis: Analysis, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    m = analysis.market
    fig, (ax_risk, ax_hold) = plt.subplots(
        1, 2, figsize=(11, 4.8), gridspec_kw={"width_ratios": [1.6, 1]}, facecolor=SURFACE
    )

    # 1) Risiko-Balkendiagramm: Gesamt oben, darunter die Einzelfaktoren
    rows = [("GESAMT-RISIKO", analysis.risk)] + [(f.name, f.value) for f in analysis.risk_factors]
    labels = [r[0] for r in rows][::-1]
    values = [r[1] for r in rows][::-1]
    levels = [risk_level(v) for v in values]
    ax_risk.barh(labels, values, height=0.55, color=[STATUS[lv] for lv in levels])
    for i, (value, level) in enumerate(zip(values, levels)):
        ax_risk.text(value + 1.5, i, f"{value:.0f}  {STATUS_ICON[level]} {level}", va="center",
                     fontsize=9, color=TEXT)
    _style(ax_risk)
    ax_risk.get_yticklabels()[-1].set_fontweight("bold")
    ax_risk.set_title("Risikofaktor (0 = sicher, 100 = extrem riskant)", loc="left", fontsize=10.5,
                      color=TEXT)

    # 2) Halte-Empfehlung: Lang vs. Kurz
    hold_labels = ["Kurz halten\n(Flip / Scalp)", "Lang halten\n(Hold)"]
    hold_values = [analysis.short_score, analysis.long_score]
    winner = 1 if analysis.horizon == "LANG" else 0
    colors = [HORIZON_ACTIVE if i == winner else HORIZON_MUTED for i in range(2)]
    ax_hold.barh(hold_labels, hold_values, height=0.32, color=colors)
    ax_hold.set_ylim(-1.1, 2.1)
    for i, value in enumerate(hold_values):
        suffix = "  ← Empfehlung" if i == winner else ""
        ax_hold.text(value + 1.5, i, f"{value:.0f}{suffix}", va="center", fontsize=9, color=TEXT,
                     fontweight="bold" if i == winner else "normal")
    _style(ax_hold)
    ax_hold.set_title("Halte-Dauer (Eignung 0–100)", loc="left", fontsize=10.5, color=TEXT)

    cluster_txt = (
        f"{analysis.clusters.connected_wallets} verbundene Wallets"
        if analysis.clusters.connected_wallets is not None
        else "Bubblemaps: keine Clusterdaten"
    )
    fig.suptitle(
        f"${m.symbol} – Risiko {analysis.risk:.0f}/100 ({analysis.risk_level}) · "
        f"Empfehlung: {analysis.horizon} halten",
        x=0.01, ha="left", fontsize=13, fontweight="bold", color=TEXT,
    )
    fig.text(
        0.01, 0.885,
        f"MC {format_usd(m.market_cap)} · Liq {format_usd(m.liquidity_usd)} · Score {analysis.opportunity:.0f} · {cluster_txt}",
        fontsize=9, color=TEXT_SECONDARY,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    path = os.path.join(out_dir, f"{m.symbol}_{m.address[:8]}.png".replace("/", "_"))
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return path
