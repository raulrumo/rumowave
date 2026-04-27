"""
Latency Analyzer — reads the most recent telemetry CSV from /logs and generates
a latency stability chart suitable for a portfolio or CV presentation.

Usage:
    python src/analyzer.py                    # latest CSV, saves PNG next to it
    python src/analyzer.py --csv logs/x.csv   # specific file
    python src/analyzer.py --show             # open window instead of saving
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import pandas as pd

_LOGS_DIR = Path(__file__).parent.parent / "logs"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _find_latest_csv() -> Path:
    csvs = sorted(_LOGS_DIR.glob("latency_*.csv"), key=lambda p: p.stat().st_mtime)
    if not csvs:
        raise FileNotFoundError(
            f"No latency CSV files found in {_LOGS_DIR}. "
            "Run the gateway and send some OSC messages first."
        )
    return csvs[-1]


def load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["latency_us"] = pd.to_numeric(df["latency_us"], errors="coerce")
    df = df.dropna(subset=["latency_us"])
    df["index"] = range(len(df))
    return df


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

def plot(df: pd.DataFrame, csv_path: Path, show: bool = False) -> Path | None:
    if df.empty:
        print("No data to plot — CSV is empty.")
        return None

    lat = df["latency_us"]
    n = len(lat)
    mean = lat.mean()
    p95  = lat.quantile(0.95)
    p99  = lat.quantile(0.99)
    lo   = lat.min()
    hi   = lat.max()

    # ---- layout -----------------------------------------------------------
    fig, axes = plt.subplots(
        2, 2,
        figsize=(14, 8),
        gridspec_kw={"height_ratios": [2, 1]},
    )
    fig.suptitle(
        "RumoWave — Latency Stability Report",
        fontsize=15, fontweight="bold", y=0.98,
    )
    fig.patch.set_facecolor("#0f1117")
    for ax in axes.flat:
        ax.set_facecolor("#1a1d27")
        ax.tick_params(colors="#c0c8e0")
        ax.xaxis.label.set_color("#c0c8e0")
        ax.yaxis.label.set_color("#c0c8e0")
        ax.title.set_color("#e0e8ff")
        for spine in ax.spines.values():
            spine.set_edgecolor("#2e3250")

    # ---- 1. Time-series scatter (top-left + top-right merged) --------------
    ax_ts = fig.add_subplot(2, 1, 1)  # will overlay the two top slots
    axes[0, 0].remove()
    axes[0, 1].remove()
    ax_ts.set_facecolor("#1a1d27")
    ax_ts.tick_params(colors="#c0c8e0")
    ax_ts.xaxis.label.set_color("#c0c8e0")
    ax_ts.yaxis.label.set_color("#c0c8e0")
    ax_ts.title.set_color("#e0e8ff")
    for spine in ax_ts.spines.values():
        spine.set_edgecolor("#2e3250")

    ax_ts.scatter(df["index"], lat, s=8, alpha=0.55, color="#4fc3f7", zorder=3, label="Sample")
    ax_ts.axhline(mean, color="#ffca28", lw=1.5, ls="--", label=f"Mean {mean:.0f} µs")
    ax_ts.axhline(p95,  color="#ff7043", lw=1.2, ls=":",  label=f"p95  {p95:.0f} µs")
    ax_ts.axhline(p99,  color="#ef5350", lw=1.2, ls="-.", label=f"p99  {p99:.0f} µs")

    # rolling average
    if n >= 5:
        roll = lat.rolling(window=min(20, n // 3 or 1), min_periods=1).mean()
        ax_ts.plot(df["index"], roll, color="#69f0ae", lw=1.8, label="Rolling avg")

    ax_ts.set_title("End-to-End Latency per Message (UDP receive → MIDI send)", pad=8)
    ax_ts.set_xlabel("Message index")
    ax_ts.set_ylabel("Latency (µs)")
    ax_ts.legend(loc="upper right", framealpha=0.3, labelcolor="#e0e8ff", fontsize=9)
    ax_ts.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f} µs"))
    ax_ts.set_position([0.06, 0.42, 0.90, 0.50])

    # ---- 2. Histogram (bottom-left) ----------------------------------------
    ax_hist = axes[1, 0]
    ax_hist.set_facecolor("#1a1d27")
    ax_hist.tick_params(colors="#c0c8e0")
    for spine in ax_hist.spines.values():
        spine.set_edgecolor("#2e3250")

    bins = min(60, max(10, n // 5))
    ax_hist.hist(lat, bins=bins, color="#4fc3f7", alpha=0.75, edgecolor="#1a1d27")
    ax_hist.axvline(mean, color="#ffca28", lw=1.5, ls="--")
    ax_hist.axvline(p99,  color="#ef5350", lw=1.2, ls="-.")
    ax_hist.set_title("Latency Distribution")
    ax_hist.set_xlabel("Latency (µs)")
    ax_hist.set_ylabel("Count")
    ax_hist.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax_hist.tick_params(colors="#c0c8e0")
    ax_hist.xaxis.label.set_color("#c0c8e0")
    ax_hist.yaxis.label.set_color("#c0c8e0")
    ax_hist.title.set_color("#e0e8ff")

    # ---- 3. Stats table (bottom-right) -------------------------------------
    ax_tbl = axes[1, 1]
    ax_tbl.set_facecolor("#1a1d27")
    for spine in ax_tbl.spines.values():
        spine.set_edgecolor("#2e3250")
    ax_tbl.axis("off")
    ax_tbl.title.set_color("#e0e8ff")
    ax_tbl.set_title("Summary Statistics")

    rows = [
        ["Messages",    f"{n}"],
        ["Min",         f"{lo:.1f} µs"],
        ["Mean",        f"{mean:.1f} µs"],
        ["p95",         f"{p95:.1f} µs"],
        ["p99",         f"{p99:.1f} µs"],
        ["Max",         f"{hi:.1f} µs"],
        ["Std dev",     f"{lat.std():.1f} µs"],
        ["Source",      csv_path.name],
    ]
    tbl = ax_tbl.table(
        cellText=rows,
        colLabels=["Metric", "Value"],
        loc="center",
        cellLoc="left",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_facecolor("#23263a" if r % 2 == 0 else "#1a1d27")
        cell.set_text_props(color="#e0e8ff")
        cell.set_edgecolor("#2e3250")
        if r == 0:
            cell.set_facecolor("#2e3250")
            cell.set_text_props(color="#ffffff", fontweight="bold")

    # ---- footer ------------------------------------------------------------
    fig.text(
        0.5, 0.01,
        "RumoWave  |  Python 3.11+ + Windows MIDI Services  |  github.com/raulrumo/midi-osc-gateway",
        ha="center", fontsize=8, color="#606880",
    )

    if show:
        plt.show()
        return None

    out_path = csv_path.with_suffix(".png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Chart saved: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RumoWave latency analyzer")
    parser.add_argument("--csv",  type=Path, default=None, help="Path to a specific CSV file")
    parser.add_argument("--show", action="store_true",     help="Show interactive window")
    args = parser.parse_args()

    csv_path = args.csv or _find_latest_csv()
    print(f"Loading: {csv_path}")

    df = load(csv_path)
    print(f"  {len(df)} samples loaded.")

    plot(df, csv_path, show=args.show)


if __name__ == "__main__":
    main()
