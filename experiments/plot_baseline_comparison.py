import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


OUTPUT_DIR = Path(__file__).resolve().parent / "media"

RESULTS = {
    "A1": [
        {"algorithm": "IQR", "precision": 0.276666, "recall": 0.691176, "f1": 0.395157, "detected": 4247, "tp": 1175, "fp": 3072, "fn": 525},
        {"algorithm": "KSIGMA", "precision": 0.695035, "recall": 0.391218, "f1": 0.500639, "detected": 846, "tp": 588, "fp": 258, "fn": 915},
        {"algorithm": "TWOSIDEDFILTER", "precision": 0.048113, "recall": 0.145625, "f1": 0.072329, "detected": 4531, "tp": 523, "fp": 4008, "fn": 969},
        {"algorithm": "OUTLIER", "precision": 0.049770, "recall": 0.254159, "f1": 0.083239, "detected": 8901, "tp": 914, "fp": 7987, "fn": 578},
        {"algorithm": "DDAD", "precision": 0.577042, "recall": 0.363281, "f1": 0.445865, "detected": 967, "tp": 558, "fp": 409, "fn": 978},
    ],
    "A2": [
        {"algorithm": "IQR", "precision": 0.994764, "recall": 0.407725, "f1": 0.578387, "detected": 191, "tp": 190, "fp": 1, "fn": 276},
        {"algorithm": "KSIGMA", "precision": 0.926984, "recall": 0.626609, "f1": 0.747759, "detected": 315, "tp": 293, "fp": 22, "fn": 173},
        {"algorithm": "TWOSIDEDFILTER", "precision": 0.011847, "recall": 0.987832, "f1": 0.023413, "detected": 75377, "tp": 1390, "fp": 73987, "fn": 17},
        {"algorithm": "OUTLIER", "precision": 0.992481, "recall": 0.860870, "f1": 0.922002, "detected": 399, "tp": 396, "fp": 3, "fn": 64},
        {"algorithm": "DDAD", "precision": 0.973545, "recall": 0.814159, "f1": 0.886747, "detected": 378, "tp": 368, "fp": 10, "fn": 84},
    ],
    "A3": [
        {"algorithm": "IQR", "precision": 0.992063, "recall": 0.132696, "f1": 0.234082, "detected": 126, "tp": 125, "fp": 1, "fn": 817},
        {"algorithm": "KSIGMA", "precision": 0.675325, "recall": 0.220573, "f1": 0.332534, "detected": 308, "tp": 208, "fp": 100, "fn": 734},
        {"algorithm": "TWOSIDEDFILTER", "precision": 0.214454, "recall": 0.431149, "f1": 0.286435, "detected": 2117, "tp": 852, "fp": 1265, "fn": 892},
        {"algorithm": "OUTLIER", "precision": 0.163446, "recall": 0.462894, "f1": 0.241588, "detected": 3053, "tp": 1008, "fp": 2045, "fn": 736},
        {"algorithm": "DDAD", "precision": 0.888699, "recall": 0.548626, "f1": 0.678431, "detected": 584, "tp": 519, "fp": 65, "fn": 427},
    ],
    "A4": [
        {"algorithm": "IQR", "precision": 0.085962, "recall": 0.200000, "f1": 0.120243, "detected": 2536, "tp": 218, "fp": 2318, "fn": 872},
        {"algorithm": "KSIGMA", "precision": 0.134902, "recall": 0.217794, "f1": 0.166608, "detected": 1742, "tp": 260, "fp": 1482, "fn": 930},
        {"algorithm": "TWOSIDEDFILTER", "precision": 0.170262, "recall": 0.501718, "f1": 0.254245, "detected": 3430, "tp": 1722, "fp": 1708, "fn": 1018},
        {"algorithm": "OUTLIER", "precision": 0.175676, "recall": 0.399658, "f1": 0.244068, "detected": 2664, "tp": 1074, "fp": 1590, "fn": 1607},
        {"algorithm": "DDAD", "precision": 0.560624, "recall": 0.440982, "f1": 0.493658, "detected": 833, "tp": 467, "fp": 366, "fn": 592},
    ],
}

METRIC_COLORS = {
    "precision": "#3b6ea8",
    "recall": "#d88331",
    "f1": "#b5a6d6",
}

COUNT_COLORS = {
    "tp": "#3f8f5f",
    "fp": "#b9473f",
    "fn": "#8e6bb8",
    "detected": "#577590",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Plot DDAD and baseline performance on Yahoo S5.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--counts", action="store_true", help="Also plot TP/FP/FN/detected count charts.")
    return parser.parse_args()


def autolabel(axis, bars, fmt="{:.2f}", fontsize=8):
    for bar in bars:
        height = bar.get_height()
        axis.annotate(
            fmt.format(height),
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=fontsize,
        )


def draw_metric_chart(benchmark, rows, output_dir, dpi):
    algorithms = [row["algorithm"] for row in rows]
    metrics = ["precision", "recall", "f1"]
    x = np.arange(len(algorithms))
    width = 0.24

    fig, ax = plt.subplots(figsize=(11, 5.8))
    for offset, metric in enumerate(metrics):
        values = [row[metric] for row in rows]
        bars = ax.bar(
            x + (offset - 1) * width,
            values,
            width,
            label=metric.capitalize(),
            color=METRIC_COLORS[metric],
        )
        autolabel(ax, bars)

    ax.set_title(f"Yahoo S5 {benchmark}: DDAD vs Baselines")
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.08)
    ax.set_xticks(x)
    ax.set_xticklabels(algorithms)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7, alpha=0.8)
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.10))
    fig.tight_layout()

    output_path = output_dir / f"DDAD_baseline_{benchmark}_metrics.png"
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    return output_path


def draw_count_chart(benchmark, rows, output_dir, dpi):
    algorithms = [row["algorithm"] for row in rows]
    metrics = ["tp", "fp", "fn", "detected"]
    x = np.arange(len(algorithms))
    width = 0.2

    fig, ax = plt.subplots(figsize=(11, 5.8))
    for offset, metric in enumerate(metrics):
        values = [row[metric] for row in rows]
        ax.bar(
            x + (offset - 1.5) * width,
            values,
            width,
            label=metric.upper() if metric != "detected" else "Detected",
            color=COUNT_COLORS[metric],
        )

    ax.set_title(f"Yahoo S5 {benchmark}: Detection Counts")
    ax.set_ylabel("Count, log scale")
    ax.set_yscale("symlog", linthresh=1)
    ax.set_xticks(x)
    ax.set_xticklabels(algorithms)
    ax.grid(axis="y", color="#dddddd", linewidth=0.7, alpha=0.8)
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.10))
    fig.tight_layout()

    output_path = output_dir / f"DDAD_baseline_{benchmark}_counts.png"
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    return output_path


def draw_summary_chart(output_dir, dpi):
    benchmarks = list(RESULTS)
    algorithms = [row["algorithm"] for row in RESULTS["A1"]]
    x = np.arange(len(algorithms))
    width = 0.68

    fig, axes = plt.subplots(2, 2, figsize=(14, 8.5), sharey=True)
    for ax, benchmark in zip(axes.ravel(), benchmarks):
        values = [row["f1"] for row in RESULTS[benchmark]]
        bars = ax.bar(x, values, width, color="#b5a6d6")
        autolabel(ax, bars)
        ax.set_title(benchmark)
        ax.set_ylim(0, 1.0)
        ax.set_xticks(x)
        ax.set_xticklabels(algorithms, rotation=20, ha="right")
        ax.grid(axis="y", color="#dddddd", linewidth=0.7, alpha=0.8)

    fig.suptitle("Yahoo S5 F1 Comparison: DDAD vs Baselines")
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    output_path = output_dir / "DDAD_baseline_f1_summary.png"
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    return output_path


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    output_paths = []
    for benchmark, rows in RESULTS.items():
        output_paths.append(draw_metric_chart(benchmark, rows, args.output_dir, args.dpi))
        if args.counts:
            output_paths.append(draw_count_chart(benchmark, rows, args.output_dir, args.dpi))
    output_paths.append(draw_summary_chart(args.output_dir, args.dpi))

    print("Generated charts for DDAD:")
    for output_path in output_paths:
        print(output_path)


if __name__ == "__main__":
    main()