import argparse
import csv
import math
import re
from collections import defaultdict
from pathlib import Path

from iotdb.Session import Session

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DATABASE = "root.yahoo"
DATA_DIR = Path(__file__).resolve().parents[1] / "dataset" / "Yahoo_S5_Data"

BENCHMARK_DIRS = {
    # "A1Benchmark": "a1",
    "A2Benchmark": "a2",
    # "A3Benchmark": "a3",
    # "A4Benchmark": "a4",
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

PARAM_GRIDS = {
    "IQR": [
        {"compute": "batch"},
        {"compute": "stream", "q1_percentile": 10, "q3_percentile": 90},
        {"compute": "stream", "q1_percentile": 15, "q3_percentile": 85},
        {"compute": "stream", "q1_percentile": 20, "q3_percentile": 80},
        {"compute": "stream", "q1_percentile": 25, "q3_percentile": 75},
    ],
    "KSIGMA": [
        {"k": 1.5, "window": 10000},
        {"k": 2.0, "window": 10000},
        {"k": 2.5, "window": 10000},
        {"k": 3.0, "window": 10000},
        {"k": 3.5, "window": 10000},
        {"k": 4.0, "window": 10000},
    ],
    "TWOSIDEDFILTER": [
        {"len": 5, "threshold": 0.6},
        {"len": 5, "threshold": 0.7},
        {"len": 5, "threshold": 0.8},
        {"len": 5, "threshold": 0.85},
        {"len": 5, "threshold": 0.9},
        {"len": 7, "threshold": 0.6},
        {"len": 7, "threshold": 0.7},
        {"len": 7, "threshold": 0.8},
        {"len": 7, "threshold": 0.85},
        {"len": 7, "threshold": 0.9},
    ],
    "OUTLIER": [
        {"r": 5.0, "k": 4, "w": 10, "s": 5},
        {"r": 100.0, "k": 4, "w": 10, "s": 5},
        {"r": 1000.0, "k": 4, "w": 10, "s": 5},
        {"r": 2000.0, "k": 4, "w": 10, "s": 5},
        {"r": 3000.0, "k": 4, "w": 10, "s": 5},
        {"r": 5000.0, "k": 4, "w": 10, "s": 5},
    ],
}


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


def query_series_values(session, device):
    rows = query_records(session, f"SELECT value FROM {device}")
    values = []
    for _, value in rows:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def percentile(sorted_values, p):
    if not sorted_values:
        return 0.0
    if p <= 0:
        return sorted_values[0]
    if p >= 100:
        return sorted_values[-1]
    position = (len(sorted_values) - 1) * (p / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def build_sql(baseline_name, params):
    if baseline_name == "IQR":
        if params.get("compute") == "stream":
            return "IQR(value, 'compute'='stream', 'q1'='{q1}', 'q3'='{q3}')".format(**params)
        return "IQR(value)"
    if baseline_name == "KSIGMA":
        return "KSIGMA(value, 'k'='{k}', 'window'='{window}')".format(**params)
    if baseline_name == "TWOSIDEDFILTER":
        return "TWOSIDEDFILTER(value, 'len'='{len}', 'threshold'='{threshold}')".format(**params)
    if baseline_name == "OUTLIER":
        return "OUTLIER(value, 'r'='{r}', 'k'='{k}', 'w'='{w}', 's'='{s}')".format(**params)
    raise ValueError(f"Unsupported baseline: {baseline_name}")


def normalize_params(baseline_name, params, values=None):
    if baseline_name == "IQR" and params.get("compute") == "stream":
        sorted_values = sorted(values or [])
        q1 = percentile(sorted_values, params["q1_percentile"])
        q3 = percentile(sorted_values, params["q3_percentile"])
        if q3 <= q1:
            return None
        return {
            "compute": "stream",
            "q1": f"{q1:.12f}",
            "q3": f"{q3:.12f}",
            "label": f"compute=stream,q1_p={params['q1_percentile']},q3_p={params['q3_percentile']}",
        }
    if baseline_name == "IQR":
        return {"compute": "batch", "label": "compute=batch"}
    if baseline_name == "KSIGMA":
        return {**params, "label": f"k={params['k']},window={params['window']}"}
    if baseline_name == "TWOSIDEDFILTER":
        return {**params, "label": f"len={params['len']},threshold={params['threshold']}"}
    if baseline_name == "OUTLIER":
        return {**params, "label": f"r={params['r']},k={params['k']},w={params['w']},s={params['s']}"}
    return None


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


def evaluate_baseline(session, baseline, benchmark_files, tolerance):
    metrics = []
    errors = []
    benchmark_metrics = defaultdict(list)

    print(f"\n========== {baseline['name']} ==========")
    for index, (benchmark_key, csv_path) in enumerate(benchmark_files, start=1):
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
            print(f"{baseline['name']}: processed {index}/{len(benchmark_files)}", flush=True)

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


def tune_baseline(session, baseline, benchmark_files, tolerance):
    baseline_name = baseline["name"]
    candidates = PARAM_GRIDS[baseline_name]
    best = None

    print(f"\n---------- Tuning {baseline_name} ----------")
    for candidate in candidates:
        candidate_metrics = []
        candidate_errors = []
        candidate_label = None

        try:
            if baseline_name == "IQR" and candidate.get("compute") == "stream":
                values = []
                for benchmark_key, csv_path in benchmark_files:
                    device = device_for_csv(DATABASE, benchmark_key, csv_path)
                    values.extend(query_series_values(session, device))
                normalized = normalize_params(baseline_name, candidate, values)
            else:
                normalized = normalize_params(baseline_name, candidate)

            if normalized is None:
                continue
            candidate_label = normalized.pop("label")
            sql = build_sql(baseline_name, normalized)

            for benchmark_key, csv_path in benchmark_files:
                device = device_for_csv(DATABASE, benchmark_key, csv_path)
                timestamps, truth = read_yahoo_labels(csv_path, benchmark_key)
                rows = query_records(session, f"SELECT {sql} FROM {device}")
                predicted = predicted_timestamps(rows, timestamps, baseline["mode"])
                candidate_metrics.append(evaluate_predictions(predicted, timestamps, truth, tolerance))
        except Exception as exc:
            candidate_errors.append((candidate_label or str(candidate), str(exc)))
            print(f"{baseline_name} candidate failed: {candidate_label or candidate} -> {exc}")
            continue

        overall = add_metrics(candidate_metrics) if candidate_metrics else None
        if overall is None:
            continue
        print(
            f"{baseline_name} candidate {candidate_label}: "
            f"F1={overall['f1']:.6f}, P={overall['precision']:.6f}, R={overall['recall']:.6f}, "
            f"detected={overall['predicted_count']}"
        )
        if best is None or overall["f1"] > best["metrics"]["f1"]:
            best = {
                "candidate": candidate_label,
                "sql": sql,
                "metrics": overall,
                "errors": candidate_errors,
            }

    return best


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
    print("Testing IoTDB anomaly UDF baselines with per-benchmark tuning.")
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

    summary = {}
    for benchmark_key in BENCHMARK_DIRS.values():
        benchmark_files = files_by_benchmark[benchmark_key]
        print(f"\n========== {benchmark_key.upper()} Baseline Evaluation ==========")
        print(f"Tuning on {len(benchmark_files)} Yahoo S5 {benchmark_key.upper()} csv files.")

        results = {}
        for baseline in BASELINES:
            session = Session(IOTDB_HOST, IOTDB_PORT, IOTDB_USER, IOTDB_PASSWORD)
            try:
                session.open(False)
                best = tune_baseline(session, baseline, benchmark_files, args.tolerance)
                results[baseline["name"]] = best
            except Exception as exc:
                print(f"{baseline['name']} failed before/while tuning: {exc}")
                results[baseline["name"]] = None
            finally:
                close_session(session)

        print(f"\n========== {benchmark_key.upper()} Baseline F1 Summary ==========")
        summary[benchmark_key] = results
        for baseline in BASELINES:
            name = baseline["name"]
            best = results[name]
            if best is None:
                print(f"{name}: F1=N/A")
            else:
                metrics = best["metrics"]
                print(
                    f"{name}: best={best['candidate']}, F1={metrics['f1']:.6f}, P={metrics['precision']:.6f}, "
                    f"R={metrics['recall']:.6f}, detected={metrics['predicted_count']}"
                )


if __name__ == "__main__":
    main()
