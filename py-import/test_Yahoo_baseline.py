import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

from iotdb.Session import Session

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DATABASE = "root.yahoo"
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "Yahoo_S5_Data"

BENCHMARK_DIRS = {
    "A1Benchmark": "a1",
    "A2Benchmark": "a2",
    "A3Benchmark": "a3",
    "A4Benchmark": "a4",
}

BASELINES = [
    {
        "name": "IQR",
        "sql": "IQR(value)",
        "mode": "sparse",
        "class": "org.apache.iotdb.library.anomaly.UDTFIQR",
    },
    {
        "name": "KSIGMA",
        "sql": "KSIGMA(value)",
        "mode": "sparse",
        "class": "org.apache.iotdb.library.anomaly.UDTFKSigma",
    },
    {
        "name": "TWOSIDEDFILTER",
        "sql": "TWOSIDEDFILTER(value)",
        "mode": "filtered",
        "class": "org.apache.iotdb.library.anomaly.UDTFTwoSidedFilter",
    },
    {
        "name": "OUTLIER",
        "sql": "OUTLIER(value, 'r'='5.0', 'k'='4', 'w'='10', 's'='5')",
        "mode": "sparse",
        "class": "org.apache.iotdb.library.anomaly.UDTFOutlier",
    },
]


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate IoTDB anomaly UDF baselines on Yahoo S5.")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=0,
        help="Timestamp-index tolerance in sample points.",
    )
    return parser.parse_args()


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def discover_yahoo_files(data_dir):
    if not data_dir.exists():
        raise FileNotFoundError(f"Yahoo S5 data directory not found: {data_dir}")

    result = []
    for benchmark_dir_name, benchmark_key in BENCHMARK_DIRS.items():
        benchmark_dir = data_dir / benchmark_dir_name
        if not benchmark_dir.exists():
            raise FileNotFoundError(f"Yahoo S5 benchmark directory not found: {benchmark_dir}")

        for csv_path in sorted(benchmark_dir.glob("*.csv"), key=lambda path: natural_sort_key(path.name)):
            if csv_path.stem.endswith("_all"):
                continue
            result.append((benchmark_key, csv_path))

    if not result:
        raise ValueError("No Yahoo S5 csv files found.")
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


def record_value(record):
    fields = getattr(record, "fields", None)
    if fields:
        field = fields[0]
        for attr in ("bool_value", "boolean_value"):
            if hasattr(field, attr):
                value = getattr(field, attr)
                if value is not None:
                    return bool(value() if callable(value) else value)
        for attr in ("double_value", "float_value", "long_value", "int_value", "string_value"):
            if hasattr(field, attr):
                value = getattr(field, attr)
                if value is not None:
                    return value() if callable(value) else value
        for method in ("get_bool_value", "getBooleanV", "get_double_value", "getDoubleV", "getFloatV", "getLongV", "getIntV"):
            if hasattr(field, method):
                try:
                    return getattr(field, method)()
                except Exception:
                    pass

    parts = str(record).replace(",", " ").split()
    return parts[-1] if len(parts) >= 2 else None


def close_dataset(dataset):
    for method in ("close_operation_handle", "closeOperationHandle", "close"):
        if hasattr(dataset, method):
            try:
                getattr(dataset, method)()
            except Exception:
                pass
            return


def close_session(session):
    try:
        session.close()
        print("Session closed.")
    except Exception as exc:
        print(f"Session close skipped/failed: {exc}")


def query_records(session, sql, debug_prefix=None):
    if debug_prefix:
        print(f"{debug_prefix} before execute_query_statement", flush=True)
    dataset = session.execute_query_statement(sql)
    if debug_prefix:
        print(f"{debug_prefix} after execute_query_statement", flush=True)
    rows = []
    try:
        while dataset.has_next():
            record = dataset.next()
            rows.append((record_time(record), record_value(record)))
            if debug_prefix and len(rows) % 1000 == 0:
                print(f"{debug_prefix} read {len(rows)} rows", flush=True)
    finally:
        close_dataset(dataset)
    if debug_prefix:
        print(f"{debug_prefix} after reading result rows={len(rows)}", flush=True)
    return rows


def predicted_timestamps(rows, all_timestamps, mode):
    row_times = {timestamp for timestamp, _ in rows}
    if mode == "filtered":
        return set(all_timestamps) - row_times
    if mode == "boolean":
        predicted = set()
        for timestamp, value in rows:
            if value is True or str(value).strip().lower() == "true":
                predicted.add(timestamp)
        return predicted
    return row_times


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


def print_metrics(prefix, metrics):
    print(
        f"{prefix} labels={metrics['label_count']}, detected={metrics['predicted_count']}, "
        f"P={metrics['precision']:.6f}, R={metrics['recall']:.6f}, F1={metrics['f1']:.6f}, "
        f"TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}"
    )


def evaluate_baseline(session, baseline, yahoo_files, tolerance):
    metrics = []
    errors = []
    benchmark_metrics = defaultdict(list)

    print(f"\n========== {baseline['name']} ==========")
    for index, (benchmark_key, csv_path) in enumerate(yahoo_files, start=1):
        device = device_for_csv(DATABASE, benchmark_key, csv_path)
        relative_name = csv_path.relative_to(DATA_DIR).as_posix()
        timestamps, truth = read_yahoo_labels(csv_path, benchmark_key)
        sql = f"SELECT {baseline['sql']} FROM {device}"
        try:
            rows = query_records(session, sql)
            predicted = predicted_timestamps(rows, timestamps, baseline["mode"])
            item_metrics = evaluate_predictions(predicted, timestamps, truth, tolerance)
            metrics.append(item_metrics)
            benchmark_metrics[benchmark_key].append(item_metrics)
        except Exception as exc:
            errors.append((relative_name, str(exc)))

        if index % 50 == 0:
            print(f"{baseline['name']}: processed {index}/{len(yahoo_files)}", flush=True)

    if metrics:
        for benchmark_key in sorted(benchmark_metrics):
            print_metrics(f"{baseline['name']} {benchmark_key.upper()}", add_metrics(benchmark_metrics[benchmark_key]))
        overall = add_metrics(metrics)
        print_metrics(f"{baseline['name']} OVERALL", overall)
    else:
        overall = None
        print(f"{baseline['name']} OVERALL failed: no series produced metrics")

    if errors:
        print(f"{baseline['name']} errors: {len(errors)}")
        for relative_name, error in errors[:5]:
            print(f"  {relative_name}: {error}")
        if len(errors) > 5:
            print(f"  ... {len(errors) - 5} more")

    return overall, errors


def ensure_registered(session):
    print("Registering IoTDB anomaly UDFs if needed...")
    for baseline in BASELINES:
        try:
            session.execute_non_query_statement(
                f"CREATE FUNCTION {baseline['name']} AS '{baseline['class']}'"
            )
            print(f"Registered {baseline['name']} -> {baseline['class']}")
        except Exception as exc:
            message = str(exc)
            if "already" in message.lower() or "exist" in message.lower():
                print(f"{baseline['name']} already registered")
            else:
                print(f"{baseline['name']} registration skipped/failed: {exc}")


def main():
    args = parse_args()
    yahoo_files = discover_yahoo_files(DATA_DIR)
    print(f"Discovered {len(yahoo_files)} Yahoo S5 csv files.")
    print("Testing IoTDB anomaly UDF baselines with documented default calls.")
    print(f"tolerance: {args.tolerance}")
    files_by_benchmark = {
        benchmark_key: [
            (file_benchmark_key, csv_path)
            for file_benchmark_key, csv_path in yahoo_files
            if file_benchmark_key == benchmark_key
        ]
        for benchmark_key in BENCHMARK_DIRS.values()
    }

    session = Session(IOTDB_HOST, IOTDB_PORT, IOTDB_USER, IOTDB_PASSWORD)
    try:
        session.open(False)
        print("Connected to IoTDB.")
        ensure_registered(session)
    finally:
        close_session(session)

    for benchmark_key in BENCHMARK_DIRS.values():
        benchmark_files = files_by_benchmark[benchmark_key]
        print(f"\n========== {benchmark_key.upper()} Baseline Evaluation ==========")
        print(f"Testing {len(benchmark_files)} Yahoo S5 {benchmark_key.upper()} csv files.")

        results = {}
        for baseline in BASELINES:
            session = Session(IOTDB_HOST, IOTDB_PORT, IOTDB_USER, IOTDB_PASSWORD)
            try:
                session.open(False)
                overall, errors = evaluate_baseline(session, baseline, benchmark_files, args.tolerance)
                results[baseline["name"]] = {"metrics": overall, "errors": errors}
            except Exception as exc:
                print(f"{baseline['name']} failed before/while evaluating: {exc}")
                results[baseline["name"]] = {"metrics": None, "errors": [("__baseline__", str(exc))]}
            finally:
                close_session(session)

        print(f"\n========== {benchmark_key.upper()} Baseline F1 Summary ==========")
        for baseline in BASELINES:
            name = baseline["name"]
            metrics = results[name]["metrics"]
            errors = results[name]["errors"]
            if metrics is None:
                print(f"{name}: F1=N/A, errors={len(errors)}")
            else:
                print(
                    f"{name}: F1={metrics['f1']:.6f}, P={metrics['precision']:.6f}, "
                    f"R={metrics['recall']:.6f}, detected={metrics['predicted_count']}, errors={len(errors)}"
                )


if __name__ == "__main__":
    main()
