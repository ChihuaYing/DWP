import argparse
import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from iotdb.Session import Session

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DATABASE = "root.yahoo"
UDF_NAME = "STAN_DETECT"
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "Yahoo_S5_Data"
OUTPUT_DIR = Path(__file__).resolve().parent / "Yahoo_Stan_V1_overlay_pics"

BENCHMARK_DIRS = {
    "A1Benchmark": "a1",
    "A2Benchmark": "a2",
    "A3Benchmark": "a3",
    "A4Benchmark": "a4",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run STAN_DETECT on Yahoo S5 in IoTDB and plot predictions over ground truth."
    )
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument("--udf", default=UDF_NAME)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--benchmarks", default="all", help="Benchmarks to plot, e.g. all or A1,A2,A3,A4.")
    parser.add_argument("--files", default="all", help="CSV file stems/names to plot, e.g. all or real_1,A4Benchmark-TS1.")
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--sensitivity", type=float, default=3.0)
    parser.add_argument("--min-threshold", type=float, default=3.0)
    parser.add_argument("--tolerance", type=int, default=0, help="Timestamp-index tolerance in sample points.")
    parser.add_argument("--dpi", type=int, default=140)
    parser.add_argument("--width", type=float, default=14.0)
    parser.add_argument("--height", type=float, default=5.5)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--print-sql", action="store_true")
    return parser.parse_args()


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def normalize_file_name(name):
    name = name.strip().replace("\\", "/")
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def parse_file_filter(value):
    if value.strip().lower() == "all":
        return None
    return {normalize_file_name(item) for item in value.split(",") if item.strip()}


def parse_benchmark_filter(value):
    if value.strip().lower() == "all":
        return None

    filters = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        item_upper = item.upper()
        if re.fullmatch(r"A[1-4]", item_upper):
            filters.add(f"{item_upper}Benchmark")
        elif item in BENCHMARK_DIRS:
            filters.add(item)
        else:
            raise ValueError(f"Unknown Yahoo S5 benchmark: {item}")
    return filters


def discover_yahoo_files(data_dir, benchmarks_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"Yahoo S5 data directory not found: {data_dir}")

    benchmark_filters = parse_benchmark_filter(benchmarks_arg)
    file_filters = parse_file_filter(files_arg)
    result = []

    for benchmark_dir_name, benchmark_key in BENCHMARK_DIRS.items():
        if benchmark_filters is not None and benchmark_dir_name not in benchmark_filters:
            continue

        benchmark_dir = data_dir / benchmark_dir_name
        if not benchmark_dir.exists():
            raise FileNotFoundError(f"Yahoo S5 benchmark directory not found: {benchmark_dir}")

        for csv_path in sorted(benchmark_dir.glob("*.csv"), key=lambda path: natural_sort_key(path.name)):
            if csv_path.stem.endswith("_all"):
                continue

            relative_name = csv_path.relative_to(data_dir).as_posix()
            accepted_names = {csv_path.name, csv_path.stem, relative_name, relative_name[:-4]}
            normalized_names = {normalize_file_name(name) for name in accepted_names}
            if file_filters is None or file_filters.intersection(normalized_names):
                result.append((benchmark_key, csv_path))

    if not result:
        raise ValueError("No Yahoo S5 csv files matched filters.")
    return result


def device_for_csv(database, benchmark_key, csv_path):
    name = csv_path.stem
    if benchmark_key in {"a3", "a4"}:
        match = re.fullmatch(r"A[34]Benchmark-TS(\d+)", name)
        if match:
            name = f"ts{match.group(1)}"

    safe_name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not safe_name or safe_name[0].isdigit():
        safe_name = f"s_{safe_name}"
    return f"{database}.{benchmark_key}.{safe_name}"


def read_yahoo_rows(csv_path, benchmark_key):
    rows = []

    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")

        columns = set(reader.fieldnames)
        timestamp_col = "timestamp" if "timestamp" in columns else "timestamps"
        if timestamp_col not in columns:
            raise ValueError(f"Expected timestamp/timestamps column in {csv_path}")
        if "value" not in columns:
            raise ValueError(f"Expected value column in {csv_path}")

        for row in reader:
            timestamp = int(float(row[timestamp_col]))
            value = float(row["value"])

            if benchmark_key in {"a1", "a2"}:
                anomaly = int(float(row.get("is_anomaly", row.get("label", 0)))) == 1
                changepoint = False
            elif benchmark_key == "a3":
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


def timestamp_from_record(record):
    for attr in ("timestamp", "time"):
        if hasattr(record, attr):
            value = getattr(record, attr)
            return int(value() if callable(value) else value)
    for method in ("get_timestamp", "getTimestamp", "get_time", "getTime"):
        if hasattr(record, method):
            return int(getattr(record, method)())
    return int(str(record).replace(",", " ").split()[0])


def score_from_record(record):
    fields = getattr(record, "fields", None)
    if fields:
        field = fields[0]
        for attr in ("double_value", "float_value", "long_value", "int_value"):
            if hasattr(field, attr):
                value = getattr(field, attr)
                if value is not None:
                    return float(value() if callable(value) else value)
        for method in ("get_double_value", "getDoubleV", "getFloatV", "getLongV", "getIntV"):
            if hasattr(field, method):
                try:
                    return float(getattr(field, method)())
                except Exception:
                    pass
    parts = str(record).replace(",", " ").split()
    try:
        return float(parts[-1]) if len(parts) >= 2 else None
    except ValueError:
        return None


def close_dataset(dataset):
    for method in ("close_operation_handle", "closeOperationHandle", "close"):
        if hasattr(dataset, method):
            try:
                getattr(dataset, method)()
            except Exception:
                pass
            return


def build_sql(args, device):
    return (
        f"SELECT {args.udf}(value, "
        f"'window'='{args.window}', "
        f"'sensitivity'='{args.sensitivity}', "
        f"'minThreshold'='{args.min_threshold}') "
        f"FROM {device}"
    )


def query_predictions(session, sql):
    dataset = session.execute_query_statement(sql)
    predictions = {}
    try:
        while dataset.has_next():
            record = dataset.next()
            predictions[timestamp_from_record(record)] = score_from_record(record)
    finally:
        close_dataset(dataset)
    return predictions


def expand_with_tolerance(timestamps, truth, tolerance):
    if tolerance <= 0:
        return set(truth)

    index_by_timestamp = {timestamp: index for index, timestamp in enumerate(timestamps)}
    expanded = set()
    for timestamp in truth:
        index = index_by_timestamp.get(timestamp)
        if index is None:
            continue
        left = max(0, index - tolerance)
        right = min(len(timestamps), index + tolerance + 1)
        expanded.update(timestamps[left:right])
    return expanded


def count_unmatched_truth(predicted, timestamps, truth, tolerance):
    timestamp_to_index = {timestamp: index for index, timestamp in enumerate(timestamps)}
    predicted_indices = {timestamp_to_index[timestamp] for timestamp in predicted if timestamp in timestamp_to_index}
    unmatched = 0
    for timestamp in truth:
        index = timestamp_to_index.get(timestamp)
        if index is None:
            unmatched += 1
            continue
        if not any((index + offset) in predicted_indices for offset in range(-tolerance, tolerance + 1)):
            unmatched += 1
    return unmatched


def evaluate_predictions(predicted, timestamps, truth, tolerance):
    valid_timestamps = set(timestamps)
    predicted = {timestamp for timestamp in predicted if timestamp in valid_timestamps}
    truth_for_matching = expand_with_tolerance(timestamps, truth, tolerance)

    tp = len(predicted & truth_for_matching)
    fp = len(predicted - truth_for_matching)
    fn = len(truth - predicted) if tolerance <= 0 else count_unmatched_truth(predicted, timestamps, truth, tolerance)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "label_count": len(truth),
        "predicted_count": len(predicted),
    }


def add_metrics(metrics):
    tp = sum(item["tp"] for item in metrics)
    fp = sum(item["fp"] for item in metrics)
    fn = sum(item["fn"] for item in metrics)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "label_count": sum(item["label_count"] for item in metrics),
        "predicted_count": sum(item["predicted_count"] for item in metrics),
    }


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def plot_overlay(rows, predicted, benchmark_key, relative_name, args):
    timestamps = [row["timestamp"] for row in rows]
    values = [row["value"] for row in rows]
    anomaly_rows = [row for row in rows if row["anomaly"]]
    changepoint_rows = [row for row in rows if row["changepoint"]]
    predicted_rows = [row for row in rows if row["timestamp"] in predicted]

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
            label="ground truth anomaly",
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

    if predicted_rows:
        ax.scatter(
            [row["timestamp"] for row in predicted_rows],
            [row["value"] for row in predicted_rows],
            s=70,
            facecolors="none",
            edgecolors="#1f77b4",
            marker="o",
            linewidths=1.8,
            label="predicted anomaly",
            zorder=6,
        )

    ax.set_title(f"{relative_name} | gt={len(anomaly_rows) + len(changepoint_rows)} | pred={len(predicted_rows)}")
    ax.set_xlabel("timestamp")
    ax.set_ylabel("value")
    ax.grid(True, color="#dddddd", linewidth=0.7, alpha=0.7)
    ax.legend(loc="best")
    fig.tight_layout()

    out_path = args.output_dir / benchmark_key / f"{safe_name(relative_name)}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi)
    plt.close(fig)
    return out_path


def main():
    args = parse_args()
    yahoo_files = discover_yahoo_files(args.data_dir, args.benchmarks, args.files)

    if not args.quiet:
        print("========== Yahoo S5 Overlay Config ==========")
        print(f"database: {args.database}")
        print(f"udf: {args.udf}")
        print(f"window: {args.window}")
        print(f"sensitivity: {args.sensitivity}")
        print(f"min_threshold: {args.min_threshold}")
        print(f"tolerance: {args.tolerance}")
        print(f"series: {len(yahoo_files)}")
        print(f"output_dir: {args.output_dir}")
        print("\n========== Plotting ==========")

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    if not args.quiet:
        print("Connected to IoTDB.")

    plot_rows = []
    benchmark_metrics = defaultdict(list)

    try:
        for index, (benchmark_key, csv_path) in enumerate(yahoo_files, start=1):
            device = device_for_csv(args.database, benchmark_key, csv_path)
            relative_name = csv_path.relative_to(args.data_dir).as_posix()
            rows = read_yahoo_rows(csv_path, benchmark_key)
            timestamps = [row["timestamp"] for row in rows]
            truth = {row["timestamp"] for row in rows if row["label"]}
            sql = build_sql(args, device)

            if args.print_sql and not args.quiet:
                print(sql)

            predicted = set(query_predictions(session, sql))
            metrics = evaluate_predictions(predicted, timestamps, truth, args.tolerance)
            benchmark_metrics[benchmark_key].append(metrics)

            out_path = plot_overlay(rows, predicted, benchmark_key, relative_name, args)
            plot_rows.append(
                {
                    "benchmark": benchmark_key,
                    "relative_path": relative_name,
                    "output_path": str(out_path),
                    "points": len(rows),
                    "ground_truth": len(truth),
                    "predicted": len(predicted),
                    "precision": metrics["precision"],
                    "recall": metrics["recall"],
                    "f1": metrics["f1"],
                }
            )

            if not args.quiet:
                print(
                    f"[{index}/{len(yahoo_files)}] {relative_name} pred={len(predicted)} gt={len(truth)} "
                    f"P={metrics['precision']:.4f} R={metrics['recall']:.4f} F1={metrics['f1']:.4f} -> {out_path}"
                )

        overall = add_metrics([add_metrics([m]) for m in []]) if False else add_metrics([item for group in benchmark_metrics.values() for item in group])

        if not args.quiet:
            print("\n========== Result By Benchmark ==========")
            for benchmark_key in sorted(benchmark_metrics):
                m = add_metrics(benchmark_metrics[benchmark_key])
                print(
                    f"{benchmark_key.upper()} labels={m['label_count']}, detected={m['predicted_count']}, "
                    f"P={m['precision']:.4f}, R={m['recall']:.4f}, F1={m['f1']:.4f}, TP={m['tp']}, FP={m['fp']}, FN={m['fn']}"
                )

            print("\n========== Overall Result ==========")
            print(f"series: {len(plot_rows)}")
            print(f"label_points: {overall['label_count']}")
            print(f"detected_points: {overall['predicted_count']}")
            print(f"TP: {overall['tp']}")
            print(f"FP: {overall['fp']}")
            print(f"FN: {overall['fn']}")
            print(f"precision: {overall['precision']:.6f}")
            print(f"recall: {overall['recall']:.6f}")
            print(f"f1: {overall['f1']:.6f}")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = args.output_dir / "overlay_manifest.txt"
        with manifest_path.open("w", encoding="utf-8") as fp:
            fp.write(f"created_at={datetime.now().isoformat(timespec='seconds')}\n")
            for row in plot_rows:
                fp.write(
                    f"{row['benchmark']}\t{row['relative_path']}\t{row['output_path']}\t"
                    f"pred={row['predicted']}\tgt={row['ground_truth']}\tF1={row['f1']:.6f}\n"
                )

        if not args.quiet:
            print(f"\nmanifest: {manifest_path}")

    finally:
        session.close()
        if not args.quiet:
            print("Session closed.")


if __name__ == "__main__":
    main()
