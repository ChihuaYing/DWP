import argparse
import csv
import re
from collections import defaultdict
from copy import copy
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
SWEEP_OUTPUT_DIR = Path(__file__).resolve().parent / "Yahoo_Stan_V1_superpara_graph"

BENCHMARK_DIRS = {
    "A1Benchmark": "a1",
    "A2Benchmark": "a2",
    "A3Benchmark": "a3",
    "A4Benchmark": "a4",
}


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate STAN_DETECT on Yahoo S5 in IoTDB.")
    p.add_argument("--host", default=IOTDB_HOST)
    p.add_argument("--port", type=int, default=IOTDB_PORT)
    p.add_argument("--user", default=IOTDB_USER)
    p.add_argument("--password", default=IOTDB_PASSWORD)
    p.add_argument("--database", default=DATABASE)
    p.add_argument("--udf", default=UDF_NAME)
    p.add_argument("--data-dir", type=Path, default=DATA_DIR)
    p.add_argument("--benchmarks", default="all", help="Benchmarks to test, e.g. all or A1,A2,A3,A4.")
    p.add_argument("--files", default="all", help="CSV file stems/names to test, e.g. all or real_1,A4Benchmark-TS1.")
    p.add_argument("--window", type=int, default=100)
    p.add_argument("--sensitivity", type=float, default=3.0)
    p.add_argument("--min-threshold", type=float, default=3.0)
    p.add_argument("--tolerance", type=int, default=0, help="Timestamp-index tolerance in sample points.")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--print-sql", action="store_true")
    p.add_argument("--print-files", action="store_true", help="Print one metrics line for every Yahoo csv file.")
    p.add_argument(
        "--sweep-sensitivity",
        action="store_true",
        help="Test sensitivity values from --sensitivity-start to --sensitivity-end and plot metrics.",
    )
    p.add_argument("--sensitivity-start", type=float, default=1.5)
    p.add_argument("--sensitivity-end", type=float, default=8.0)
    p.add_argument("--sensitivity-step", type=float, default=0.25)
    p.add_argument("--sweep-output-dir", type=Path, default=SWEEP_OUTPUT_DIR)
    return p.parse_args()


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


def read_yahoo_labels(csv_path, benchmark_key):
    timestamps = []
    labels = []

    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")

        columns = set(reader.fieldnames)
        timestamp_col = "timestamp" if "timestamp" in columns else "timestamps"
        if timestamp_col not in columns:
            raise ValueError(f"Expected timestamp/timestamps column in {csv_path}")

        if benchmark_key in {"a1", "a2"}:
            label_cols = ["is_anomaly"] if "is_anomaly" in columns else ["label"]
        elif benchmark_key == "a3":
            label_cols = ["anomaly"]
        else:
            label_cols = ["anomaly", "changepoint"]

        missing = [col for col in label_cols if col not in columns]
        if missing:
            raise ValueError(f"Missing label column(s) {missing} in {csv_path}")

        for row in reader:
            timestamps.append(int(float(row[timestamp_col])))
            labels.append(any(int(float(row[col])) == 1 for col in label_cols))

    truth = {timestamp for timestamp, label in zip(timestamps, labels) if label}
    return timestamps, truth


def record_time(record):
    for attr in ("timestamp", "time"):
        if hasattr(record, attr):
            value = getattr(record, attr)
            return int(value() if callable(value) else value)
    for method in ("get_timestamp", "getTimestamp", "get_time", "getTime"):
        if hasattr(record, method):
            return int(getattr(record, method)())
    return int(str(record).replace(",", " ").split()[0])


def record_score(record):
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


def query_predictions(session, sql):
    dataset = session.execute_query_statement(sql)
    predictions = {}
    try:
        while dataset.has_next():
            record = dataset.next()
            predictions[record_time(record)] = record_score(record)
    finally:
        close_dataset(dataset)
    return predictions


def build_sql(args, device):
    return (
        f"SELECT {args.udf}(value, "
        f"'window'='{args.window}', "
        f"'sensitivity'='{args.sensitivity}', "
        f"'minThreshold'='{args.min_threshold}') "
        f"FROM {device}"
    )


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


def print_metrics(prefix, metrics):
    print(
        f"{prefix} labels={metrics['label_count']}, detected={metrics['predicted_count']}, "
        f"P={metrics['precision']:.4f}, R={metrics['recall']:.4f}, F1={metrics['f1']:.4f}, "
        f"TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}"
    )


def sensitivity_values(start, end, step):
    if step <= 0:
        raise ValueError("--sensitivity-step must be positive")
    values = []
    current = start
    while current <= end + 1e-9:
        values.append(round(current, 10))
        current += step
    return values


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def plot_sensitivity_sweep(rows, args, best_row):
    args.sweep_output_dir.mkdir(parents=True, exist_ok=True)
    sensitivities = [row["sensitivity"] for row in rows]

    fig, axes = plt.subplots(3, 1, figsize=(10.5, 11.0), sharex=True)
    metrics = [
        ("f1", "F1-score", "#2f4858"),
        ("precision", "Precision", "#1b7837"),
        ("recall", "Recall", "#b2182b"),
    ]

    for axis, (key, title, color) in zip(axes, metrics):
        values = [row[key] for row in rows]
        axis.plot(sensitivities, values, marker="o", linewidth=1.4, color=color)
        axis.axvline(best_row["sensitivity"], color="#666666", linestyle="--", linewidth=1.0)
        axis.scatter([best_row["sensitivity"]], [best_row[key]], color="#000000", s=36, zorder=5)
        axis.set_ylabel(title)
        axis.grid(True, color="#dddddd", linewidth=0.7, alpha=0.8)
        axis.set_ylim(bottom=0.0)

    axes[-1].set_xlabel("sensitivity")
    title = f"STAN V1 Yahoo S5 sensitivity sweep | benchmarks={args.benchmarks} | window={args.window}"
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = (
        f"stan_v1_sensitivity_sweep_{safe_name(args.benchmarks)}_"
        f"w{args.window}_m{args.min_threshold}_{timestamp}.png"
    )
    output_path = args.sweep_output_dir / file_name
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def run_sensitivity_sweep(session, args, yahoo_files):
    values = sensitivity_values(args.sensitivity_start, args.sensitivity_end, args.sensitivity_step)
    print("\n========== STAN V1 Sensitivity Sweep ==========")
    print(f"benchmarks: {args.benchmarks}")
    print(f"sensitivities: {values}")
    print(f"window: {args.window}")
    print(f"min_threshold: {args.min_threshold}")
    print(f"series: {len(yahoo_files)}")

    rows = []
    for sensitivity in values:
        sweep_args = copy(args)
        sweep_args.sensitivity = sensitivity
        sweep_args.print_files = False
        sweep_args.print_sql = False
        overall = run(session, sweep_args, yahoo_files, quiet=True)
        row = {
            "sensitivity": sensitivity,
            "precision": overall["precision"],
            "recall": overall["recall"],
            "f1": overall["f1"],
            "detected": overall["predicted_count"],
            "tp": overall["tp"],
            "fp": overall["fp"],
            "fn": overall["fn"],
        }
        rows.append(row)
        print(
            f"sensitivity={sensitivity:g}, F1={row['f1']:.6f}, "
            f"P={row['precision']:.6f}, R={row['recall']:.6f}, "
            f"detected={row['detected']}, TP={row['tp']}, FP={row['fp']}, FN={row['fn']}"
        )

    best_row = max(rows, key=lambda row: (row["f1"], row["precision"], row["recall"]))
    output_path = plot_sensitivity_sweep(rows, args, best_row)

    print("\n========== Best Sensitivity ==========")
    print(f"best_sensitivity: {best_row['sensitivity']:g}")
    print(f"best_f1: {best_row['f1']:.6f}")
    print(f"precision: {best_row['precision']:.6f}")
    print(f"recall: {best_row['recall']:.6f}")
    print(f"detected_points: {best_row['detected']}")
    print(f"TP: {best_row['tp']}")
    print(f"FP: {best_row['fp']}")
    print(f"FN: {best_row['fn']}")
    print(f"plot: {output_path}")
    return best_row


def run(session, args, yahoo_files, quiet=False):
    results = []
    benchmark_metrics = defaultdict(list)

    if not quiet:
        print("\n========== Evaluation Config ==========")
        print(
            f"database: {args.database}\nudf: {args.udf}\nwindow: {args.window}\n"
            f"sensitivity: {args.sensitivity}\nmin_threshold: {args.min_threshold}\ntolerance: {args.tolerance}"
        )
        print("\n========== Per Yahoo S5 File Result ==========")
        if not args.print_files:
            print("Per-file output is disabled. Use --print-files to show every csv file.")

    for benchmark_key, csv_path in yahoo_files:
        device = device_for_csv(args.database, benchmark_key, csv_path)
        relative_name = csv_path.relative_to(args.data_dir).as_posix()
        timestamps, truth = read_yahoo_labels(csv_path, benchmark_key)
        sql = build_sql(args, device)

        if args.print_sql and not quiet:
            print(sql)

        predicted = set(query_predictions(session, sql))
        metrics = evaluate_predictions(predicted, timestamps, truth, args.tolerance)
        results.append((benchmark_key, relative_name, metrics))
        benchmark_metrics[benchmark_key].append(metrics)
        if args.print_files and not quiet:
            print_metrics(f"{benchmark_key.upper()} {relative_name}", metrics)

    if not quiet:
        print("\n========== Result By Benchmark ==========")
        for benchmark_key in sorted(benchmark_metrics):
            print_metrics(benchmark_key.upper(), add_metrics(benchmark_metrics[benchmark_key]))

    overall = add_metrics([item[2] for item in results])
    if not quiet:
        print("\n========== Overall Result On Yahoo S5 ==========")
        print(f"tested_series: {len(results)}")
        print(f"label_points: {overall['label_count']}")
        print(f"detected_points: {overall['predicted_count']}")
        print(f"TP: {overall['tp']}")
        print(f"FP: {overall['fp']}")
        print(f"FN: {overall['fn']}")
        print(f"precision: {overall['precision']:.6f}")
        print(f"recall: {overall['recall']:.6f}")
        print(f"f1: {overall['f1']:.6f}")

        print(f"\n========== Top {min(args.top_k, len(results))} Yahoo S5 Files By F1 ==========")
        for benchmark_key, relative_name, metrics in sorted(
            results,
            key=lambda item: (item[2]["f1"], item[2]["recall"], item[2]["precision"]),
            reverse=True,
        )[: args.top_k]:
            print_metrics(f"{benchmark_key.upper()} {relative_name}", metrics)

    return overall


def main():
    args = parse_args()
    yahoo_files = discover_yahoo_files(args.data_dir, args.benchmarks, args.files)
    print(f"Discovered {len(yahoo_files)} Yahoo S5 csv files.")

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")

    try:
        if args.sweep_sensitivity:
            run_sensitivity_sweep(session, args, yahoo_files)
        else:
            run(session, args, yahoo_files)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
