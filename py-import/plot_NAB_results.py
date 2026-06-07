import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


NAB_DIR = Path(__file__).resolve().parent / "dataset" / "NAB"
DATA_DIR = NAB_DIR / "data"
RESULTS_ROOT = NAB_DIR / "results"
LABELS_PATH = NAB_DIR / "labels" / "combined_labels.json"
WINDOWS_PATH = NAB_DIR / "labels" / "combined_windows.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "STANNABV3-pics"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot NAB series with detector predictions and ground-truth labels."
    )
    parser.add_argument("--detector-name", required=True)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--labels-path", type=Path, default=LABELS_PATH)
    parser.add_argument("--windows-path", type=Path, default=WINDOWS_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--categories", default="all")
    parser.add_argument("--files", default="all")
    parser.add_argument("--dpi", type=int, default=140)
    parser.add_argument("--width", type=float, default=14.0)
    parser.add_argument("--height", type=float, default=5.5)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output.",
    )
    parser.add_argument(
        "--no-windows",
        action="store_true",
        help="Do not draw ground-truth anomaly windows.",
    )
    parser.add_argument(
        "--no-labels",
        action="store_true",
        help="Do not draw point labels from combined_labels.json.",
    )
    return parser.parse_args()


def parse_dt(text):
    text = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(text)


def norm_csv(name):
    name = name.strip().replace("\\", "/")
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def parse_filter(value):
    if value.strip().lower() == "all":
        return None
    return {norm_csv(item) for item in value.split(",") if item.strip()}


def discover_files(data_dir, categories_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"NAB data directory not found: {data_dir}")

    categories = None
    if categories_arg.strip().lower() != "all":
        categories = {item.strip() for item in categories_arg.split(",") if item.strip()}

    file_filters = parse_filter(files_arg)
    csv_files = []
    for category_dir in sorted(path for path in data_dir.iterdir() if path.is_dir()):
        if categories is not None and category_dir.name not in categories:
            continue
        for csv_path in sorted(category_dir.glob("*.csv")):
            rel = csv_path.relative_to(data_dir).as_posix()
            names = {csv_path.name, csv_path.stem, rel, rel[:-4]}
            if file_filters is None or file_filters.intersection({norm_csv(name) for name in names}):
                csv_files.append(csv_path)

    if not csv_files:
        raise ValueError("No NAB csv files matched filters.")
    return csv_files


def read_json_object(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def read_result_rows(path):
    rows = []
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        required = {"timestamp", "value", "anomaly_score"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"Expected columns timestamp,value,anomaly_score in {path}")
        for row in reader:
            score_text = row.get("anomaly_score", "0")
            try:
                score = float(score_text)
            except ValueError:
                score = 0.0
            rows.append(
                {
                    "timestamp": parse_dt(row["timestamp"]),
                    "value": float(row["value"]),
                    "anomaly_score": score,
                }
            )
    return rows


def label_keys(csv_path, data_dir):
    rel = csv_path.relative_to(data_dir).as_posix()
    return [rel, f"data/{rel}", csv_path.name, csv_path.stem, rel[:-4], f"data/{rel[:-4]}"]


def lookup_labels(label_data, csv_path, data_dir):
    for key in label_keys(csv_path, data_dir):
        if key in label_data:
            return label_data[key]
    return []


def lookup_windows(window_data, csv_path, data_dir):
    for key in label_keys(csv_path, data_dir):
        if key in window_data:
            return window_data[key]
    return []


def result_path_for(results_root, detector_name, csv_path, data_dir):
    rel = csv_path.relative_to(data_dir)
    return results_root / detector_name / rel.parent / f"{detector_name}_{csv_path.name}"


def safe_stem(relative_path):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", relative_path.replace("/", "__"))


def nearest_values_by_time(rows):
    return {row["timestamp"]: row["value"] for row in rows}


def values_for_label_points(rows, label_points):
    by_time = nearest_values_by_time(rows)
    values = []
    for point in label_points:
        dt = parse_dt(point)
        if dt in by_time:
            values.append((dt, by_time[dt]))
    return values


def draw_one(csv_path, data_dir, results_root, detector_name, labels, windows, output_dir, args):
    result_path = result_path_for(results_root, detector_name, csv_path, data_dir)
    if not result_path.exists():
        raise FileNotFoundError(f"NAB result file not found: {result_path}")

    rows = read_result_rows(result_path)
    timestamps = [row["timestamp"] for row in rows]
    values = [row["value"] for row in rows]
    predictions = [row for row in rows if row["anomaly_score"] > 0.0]
    label_points = lookup_labels(labels, csv_path, data_dir)
    label_values = values_for_label_points(rows, label_points)
    window_ranges = lookup_windows(windows, csv_path, data_dir)
    rel = csv_path.relative_to(data_dir).as_posix()

    fig, ax = plt.subplots(figsize=(args.width, args.height))
    ax.plot(timestamps, values, color="#2f4858", linewidth=1.1, label="value")

    if not args.no_windows:
        for index, window in enumerate(window_ranges):
            if isinstance(window, list) and len(window) == 2:
                start, end = parse_dt(window[0]), parse_dt(window[1])
                ax.axvspan(
                    start,
                    end,
                    color="#7fbf7b",
                    alpha=0.18,
                    label="label window" if index == 0 else None,
                )

    if predictions:
        ax.scatter(
            [row["timestamp"] for row in predictions],
            [row["value"] for row in predictions],
            s=30,
            color="#d62728",
            marker="o",
            linewidths=0,
            label="detected",
            zorder=4,
        )

    if not args.no_labels and label_values:
        ax.scatter(
            [item[0] for item in label_values],
            [item[1] for item in label_values],
            s=70,
            color="#1a9850",
            marker="x",
            linewidths=2.0,
            label="label point",
            zorder=5,
        )

    ax.set_title(f"{rel} | detections={len(predictions)} | labels={len(label_points)}")
    ax.set_xlabel("timestamp")
    ax.set_ylabel("value")
    ax.grid(True, color="#dddddd", linewidth=0.7, alpha=0.7)
    ax.legend(loc="best")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    fig.tight_layout()

    out_path = output_dir / csv_path.parent.name / f"{safe_stem(rel)}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)
    plt.close(fig)
    return {
        "relative_path": rel,
        "output_path": str(out_path),
        "detections": len(predictions),
        "labels": len(label_points),
        "windows": len(window_ranges),
    }


def write_manifest(output_dir, detector_name, rows):
    manifest_path = output_dir / "plot_manifest.json"
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    data = {
        "detector_name": detector_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "plots": rows,
    }
    with tmp_path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    tmp_path.replace(manifest_path)


def main():
    args = parse_args()
    csv_files = discover_files(args.data_dir, args.categories, args.files)
    labels = read_json_object(args.labels_path)
    windows = read_json_object(args.windows_path)
    plot_rows = []

    if not args.quiet:
        print("========== NAB Plot Config ==========")
        print(f"detector_name: {args.detector_name}")
        print(f"data_dir: {args.data_dir}")
        print(f"results_root: {args.results_root}")
        print(f"labels_path: {args.labels_path}")
        print(f"windows_path: {args.windows_path}")
        print(f"output_dir: {args.output_dir}")
        print(f"files: {len(csv_files)}")
        print("\n========== Plotting ==========")

    for index, csv_path in enumerate(csv_files, start=1):
        row = (
            draw_one(
                csv_path=csv_path,
                data_dir=args.data_dir,
                results_root=args.results_root,
                detector_name=args.detector_name,
                labels=labels,
                windows=windows,
                output_dir=args.output_dir,
                args=args,
            )
        )
        plot_rows.append(row)
        if not args.quiet:
            print(
                f"[{index}/{len(csv_files)}] {row['relative_path']} "
                f"detections={row['detections']} labels={row['labels']} -> {row['output_path']}"
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(args.output_dir, args.detector_name, plot_rows)
    if not args.quiet:
        print("\n========== Plot Summary ==========")
        print(f"plots: {len(plot_rows)}")
        print(f"output_dir: {args.output_dir}")
        print(f"manifest: {args.output_dir / 'plot_manifest.json'}")


if __name__ == "__main__":
    main()
