import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "py-import" / "dataset" / "Yahoo_S5_Data"
OUTPUT_DIR = Path(__file__).resolve().parent / "Yahoo_S5-pics"

BENCHMARK_DIRS = {
    "A1Benchmark": "A1",
    "A2Benchmark": "A2",
    "A3Benchmark": "A3",
    "A4Benchmark": "A4",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Plot Yahoo S5 values with ground-truth labels.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--series-per-benchmark", type=int, default=10)
    parser.add_argument("--dpi", type=int, default=140)
    parser.add_argument("--width", type=float, default=14.0)
    parser.add_argument("--height", type=float, default=5.5)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def discover_yahoo_files(data_dir, series_per_benchmark):
    if not data_dir.exists():
        raise FileNotFoundError(f"Yahoo S5 data directory not found: {data_dir}")

    csv_files = []
    for benchmark_dir_name, benchmark_label in BENCHMARK_DIRS.items():
        benchmark_dir = data_dir / benchmark_dir_name
        if not benchmark_dir.exists():
            raise FileNotFoundError(f"Yahoo S5 benchmark directory not found: {benchmark_dir}")

        files = [
            path
            for path in sorted(benchmark_dir.glob("*.csv"), key=lambda item: natural_sort_key(item.name))
            if not path.stem.endswith("_all")
        ]
        for csv_path in files[:series_per_benchmark]:
            csv_files.append((benchmark_label, csv_path))

    if not csv_files:
        raise ValueError("No Yahoo S5 csv files found.")
    return csv_files


def timestamp_column(fieldnames):
    if "timestamp" in fieldnames:
        return "timestamp"
    if "timestamps" in fieldnames:
        return "timestamps"
    raise ValueError("Expected timestamp or timestamps column")


def read_yahoo_rows(csv_path, benchmark_label):
    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = set(reader.fieldnames or [])
        timestamp_col = timestamp_column(fieldnames)
        if "value" not in fieldnames:
            raise ValueError(f"Expected value column in {csv_path}")

        for row in reader:
            timestamp = int(float(row[timestamp_col]))
            value = float(row["value"])

            if benchmark_label in {"A1", "A2"}:
                anomaly = int(float(row.get("is_anomaly", row.get("label", 0)))) == 1
                changepoint = False
            elif benchmark_label == "A3":
                anomaly = int(float(row.get("anomaly", 0))) == 1
                changepoint = False
            else:
                anomaly = int(float(row.get("anomaly", 0))) == 1
                changepoint = int(float(row.get("changepoint", 0))) == 1

            rows.append(
                {
                    "timestamp": timestamp,
                    "value": value,
                    "anomaly": anomaly,
                    "changepoint": changepoint,
                    "label": anomaly or changepoint,
                }
            )
    return rows


def safe_stem(relative_path):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", relative_path.replace("/", "__"))


def draw_one(benchmark_label, csv_path, data_dir, output_dir, args):
    rows = read_yahoo_rows(csv_path, benchmark_label)
    timestamps = [row["timestamp"] for row in rows]
    values = [row["value"] for row in rows]
    anomaly_rows = [row for row in rows if row["anomaly"]]
    changepoint_rows = [row for row in rows if row["changepoint"]]
    label_rows = [row for row in rows if row["label"]]
    relative_name = csv_path.relative_to(data_dir).as_posix()

    fig, ax = plt.subplots(figsize=(args.width, args.height))
    ax.plot(timestamps, values, color="#2f4858", linewidth=1.1, label="value")

    if anomaly_rows:
        ax.scatter(
            [row["timestamp"] for row in anomaly_rows],
            [row["value"] for row in anomaly_rows],
            s=56,
            color="#d62728",
            marker="x",
            linewidths=2.0,
            label="anomaly label",
            zorder=5,
        )

    if changepoint_rows:
        for index, row in enumerate(changepoint_rows):
            ax.axvline(
                row["timestamp"],
                color="#7b3294",
                alpha=0.6,
                linewidth=1.2,
                label="changepoint" if index == 0 else None,
            )

    ax.set_title(f"{relative_name} | labels={len(label_rows)}")
    ax.set_xlabel("timestamp")
    ax.set_ylabel("value")
    ax.grid(True, color="#dddddd", linewidth=0.7, alpha=0.7)
    ax.legend(loc="best")
    fig.tight_layout()

    out_path = output_dir / benchmark_label / f"{safe_stem(relative_name)}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)
    plt.close(fig)

    return {
        "benchmark": benchmark_label,
        "relative_path": relative_name,
        "output_path": str(out_path),
        "points": len(rows),
        "labels": len(label_rows),
        "anomalies": len(anomaly_rows),
        "changepoints": len(changepoint_rows),
    }


def write_manifest(output_dir, rows):
    manifest_path = output_dir / "plot_manifest.json"
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    data = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "plots": rows,
    }
    with tmp_path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    tmp_path.replace(manifest_path)


def main():
    args = parse_args()
    csv_files = discover_yahoo_files(args.data_dir, args.series_per_benchmark)
    plot_rows = []

    if not args.quiet:
        print("========== Yahoo S5 Plot Config ==========")
        print(f"data_dir: {args.data_dir}")
        print(f"output_dir: {args.output_dir}")
        print(f"series_per_benchmark: {args.series_per_benchmark}")
        print(f"plots: {len(csv_files)}")
        print("\n========== Plotting ==========")

    for index, (benchmark_label, csv_path) in enumerate(csv_files, start=1):
        row = draw_one(benchmark_label, csv_path, args.data_dir, args.output_dir, args)
        plot_rows.append(row)
        if not args.quiet:
            print(
                f"[{index}/{len(csv_files)}] {row['relative_path']} "
                f"points={row['points']} labels={row['labels']} -> {row['output_path']}"
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output_dir, plot_rows)

    if not args.quiet:
        print("\n========== Plot Summary ==========")
        print(f"plots: {len(plot_rows)}")
        print(f"output_dir: {args.output_dir}")
        print(f"manifest: {args.output_dir / 'plot_manifest.json'}")


if __name__ == "__main__":
    main()
