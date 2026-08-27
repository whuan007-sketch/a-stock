from __future__ import annotations

from pathlib import Path

import pandas as pd

from a_stock.pipeline import PipelineResult


def _plot_candidate(result: PipelineResult, row: dict, output_dir: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError as exc:
        raise RuntimeError("生成技术图需要 matplotlib") from exc

    code = str(row["code"])
    generated: list[Path] = []
    daily = result.daily.daily_frames.get(code)
    if daily is not None and not daily.empty:
        frame = daily.tail(60).copy()
        dates = pd.to_datetime(frame["date"])
        figure, (price_axis, volume_axis) = plt.subplots(
            2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
        )
        x_values = mdates.date2num(dates)
        for x_value, (_, bar) in zip(x_values, frame.iterrows()):
            color = "#d62728" if float(bar["close"]) >= float(bar["open"]) else "#2ca02c"
            price_axis.vlines(x_value, float(bar["low"]), float(bar["high"]), color=color, linewidth=0.8)
            lower = min(float(bar["open"]), float(bar["close"]))
            height = max(abs(float(bar["close"]) - float(bar["open"])), 0.001)
            price_axis.add_patch(Rectangle((x_value - 0.3, lower), 0.6, height, color=color, alpha=0.75))
        for period, color in ((5, "#ff7f0e"), (10, "#1f77b4"), (20, "#9467bd")):
            price_axis.plot(dates, frame["close"].rolling(period).mean(), label=f"MA{period}", color=color)
        if pd.notna(row.get("support_price")):
            price_axis.axhline(float(row["support_price"]), color="#2ca02c", linestyle="--", label="Support")
        if pd.notna(row.get("resistance_price")):
            price_axis.axhline(float(row["resistance_price"]), color="#d62728", linestyle="--", label="Resistance")
        price_axis.set_title(f"{code} {row.get('name', '')} Daily / MA / Support-Resistance")
        price_axis.legend(loc="best")
        volume_axis.bar(dates, frame["volume_lot"], color="#7f7f7f", width=0.7)
        volume_axis.set_ylabel("Volume (lot)")
        volume_axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        figure.tight_layout()
        path = output_dir / f"{code}_daily.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        generated.append(path)

    minutes = result.intraday.minute_frames.get(code)
    aligned = result.relative.aligned_frames.get(code)
    if minutes is not None and not minutes.empty:
        valid = minutes.loc[minutes["cumulative_volume_lot"] > 0].copy()
        valid["vwap"] = valid["cumulative_amount_cny"] / (valid["cumulative_volume_lot"] * 100.0)
        figure, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
        labels = [item.strftime("%H:%M") for item in valid["time"]]
        axes[0].plot(labels, valid["price"], label="Price", color="#1f77b4")
        axes[0].plot(labels, valid["vwap"], label="VWAP", color="#ff7f0e")
        axes[0].legend(loc="best")
        axes[0].set_title(f"{code} Intraday / VWAP")
        if aligned is not None and not aligned.empty:
            aligned_labels = [item.strftime("%H:%M") for item in aligned["time"]]
            axes[1].plot(aligned_labels, aligned["stock_return_pct"], label="Stock %")
            axes[1].plot(aligned_labels, aligned["index_return_pct"], label="Index %")
            axes[1].plot(aligned_labels, aligned["excess_return_pct"], label="Excess %", linestyle="--")
            axes[1].legend(loc="best")
        step = max(1, len(labels) // 8)
        axes[1].set_xticks(range(0, len(labels), step))
        axes[1].set_xticklabels(labels[::step], rotation=30)
        figure.tight_layout()
        path = output_dir / f"{code}_intraday.png"
        figure.savefig(path, dpi=150)
        plt.close(figure)
        generated.append(path)
    return generated


def generate_candidate_charts(result: PipelineResult, output_dir: str | Path) -> list[Path]:
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for row in result.final_candidates.to_dict("records"):
        generated.extend(_plot_candidate(result, row, directory))
    return generated
