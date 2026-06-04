import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from iotdb.Session import Session

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"
DEVICE = "root.nab.d1"
UDF_NAME = "stan_detect"
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "NAB"
LABEL_JSON_CANDIDATES = [DATA_DIR / "combined_labels.json", DATA_DIR / "combined_windows.json"]


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate IoTDB UDF stan_detect on NAB.")
    p.add_argument("--host", default=IOTDB_HOST)
    p.add_argument("--port", type=int, default=IOTDB_PORT)
    p.add_argument("--user", default=IOTDB_USER)
    p.add_argument("--password", default=IOTDB_PASSWORD)
    p.add_argument("--device", default=DEVICE)
    p.add_argument("--udf", default=UDF_NAME)
    p.add_argument("--data-dir", type=Path, default=DATA_DIR)
    p.add_argument("--label-path", type=Path, default=None)
    p.add_argument("--categories", default="all")
    p.add_argument("--files", default="all")
    p.add_argument("--sensors", default="all")
    p.add_argument("--window", type=int, default=96)
    p.add_argument("--short-window", type=int, default=24)
    p.add_argument("--long-window", type=int, default=96)
    p.add_argument("--sensitivity", type=float, default=3.0)
    p.add_argument("--min-threshold", type=float, default=2.5)
    p.add_argument("--min-warmup", type=int, default=48)
    p.add_argument("--confirmation", type=int, default=1)
    p.add_argument("--cooldown", type=int, default=12)
    p.add_argument("--spike-ratio", type=float, default=0.08)
    p.add_argument("--max-alerts", type=int, default=0)
    p.add_argument("--min-score", type=float, default=0.0)
    p.add_argument("--global-sensitivity", type=float, default=3.0)
    p.add_argument("--top-fraction", type=float, default=0.01)
    p.add_argument("--seasonal-period", type=int, default=0)
    p.add_argument("--min-series-variability", type=float, default=1e-8)
    p.add_argument("--peak-ratio", type=float, default=0.88)
    p.add_argument("--tolerance", type=int, default=0)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--print-sql", action="store_true")
    p.add_argument("--strict-labels", action="store_true")
    p.add_argument("--point-label-mode", action="store_true")
    return p.parse_args()


def norm_csv(name):
    name = name.strip().replace("\\", "/")
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def parse_filter(value):
    return None if value.strip().lower() == "all" else {norm_csv(x) for x in value.split(",") if x.strip()}


def discover_files(data_dir, categories_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"NAB data directory not found: {data_dir}")
    cats = None if categories_arg.strip().lower() == "all" else {x.strip() for x in categories_arg.split(",") if x.strip()}
    filters = parse_filter(files_arg)
    result = []
    for cat_dir in sorted(x for x in data_dir.iterdir() if x.is_dir()):
        if cats is not None and cat_dir.name not in cats:
            continue
        for csv_path in sorted(cat_dir.glob("*.csv")):
            rel = csv_path.relative_to(data_dir).as_posix()
            names = {csv_path.name, csv_path.stem, rel, rel[:-4]}
            if filters is None or filters.intersection({norm_csv(x) for x in names}):
                result.append(csv_path)
    if not result:
        raise ValueError("No NAB csv files matched filters.")
    return result


def parse_sensors(value, count):
    if value.strip().lower() == "all":
        return list(range(count))
    indices = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        sensor = item if item.lower().startswith("s") else f"s{item}"
        index = int(sensor[1:])
        if index < 0 or index >= count:
            raise ValueError(f"Sensor {sensor} is out of range 0..{count - 1}")
        indices.append(index)
    if not indices:
        raise ValueError("No valid sensors specified.")
    return indices


def parse_dt(text):
    text = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(text)


def read_timestamps(csv_path):
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if "timestamp" not in reader.fieldnames or "value" not in reader.fieldnames:
            raise ValueError(f"Expected columns timestamp,value in {csv_path}")
        return [parse_dt(row["timestamp"]) for row in reader]


def resolve_label_path(path):
    if path is not None:
        if path.exists():
            return path
        raise FileNotFoundError(f"NAB label file not found: {path}")
    for candidate in LABEL_JSON_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "NAB label file not found. Expected combined_labels.json or combined_windows.json under dataset/NAB, "
        "or pass --label-path explicitly."
    )


def load_windows(path):
    path = resolve_label_path(path)
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def label_keys(csv_path, data_dir):
    rel = csv_path.relative_to(data_dir).as_posix()
    return [rel, f"data/{rel}", csv_path.name, csv_path.stem, rel[:-4], f"data/{rel[:-4]}"]


def windows_for_file(all_windows, csv_path, data_dir, strict):
    for key in label_keys(csv_path, data_dir):
        if key in all_windows:
            return all_windows[key], key
    if strict:
        raise KeyError(f"No anomaly windows found for {csv_path.relative_to(data_dir).as_posix()}")
    return [], None


def parse_windows(windows):
    if not windows:
        return []
    first = windows[0]
    if isinstance(first, (list, tuple)) and len(first) == 2:
        return [(parse_dt(start), parse_dt(end)) for start, end in windows]
    return [(parse_dt(point), parse_dt(point)) for point in windows]


def timestamp_index_to_datetime(timestamps, index):
    if index < 0 or index >= len(timestamps):
        return None
    return timestamps[index]


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


def build_sql(args, sensor):
    return (
        f"SELECT {args.udf}({sensor}, "
        f"\"window\"=\"{args.window}\", "
        f"\"shortWindow\"=\"{args.short_window}\", "
        f"\"longWindow\"=\"{args.long_window}\", "
        f"\"sensitivity\"=\"{args.sensitivity}\", "
        f"\"minThreshold\"=\"{args.min_threshold}\", "
        f"\"minWarmup\"=\"{args.min_warmup}\", "
        f"\"confirmation\"=\"{args.confirmation}\", "
        f"\"cooldown\"=\"{args.cooldown}\", "
        f"\"spikeRatio\"=\"{args.spike_ratio}\", "
        f"\"maxAlerts\"=\"{args.max_alerts}\", "
        f"\"minScore\"=\"{args.min_score}\", "
        f"\"globalSensitivity\"=\"{args.global_sensitivity}\", "
        f"\"topFraction\"=\"{args.top_fraction}\", "
        f"\"seasonalPeriod\"=\"{args.seasonal_period}\", "
        f"\"minSeriesVariability\"=\"{args.min_series_variability}\", "
        f"\"peakRatio\"=\"{args.peak_ratio}\") "
        f"FROM {args.device}"
    )


def point_indices_from_windows(timestamps, windows, tolerance):
    parsed_windows = parse_windows(windows)
    step = infer_step(timestamps)
    truth = set()
    for idx, ts in enumerate(timestamps):
        for start, end in parsed_windows:
            left = start
            right = end
            if tolerance > 0:
                left = start - step * tolerance
                right = end + step * tolerance
            if left <= ts <= right:
                truth.add(idx)
                break
    return truth


def evaluate_point_predictions(predicted, timestamps, windows, tolerance):
    truth = point_indices_from_windows(timestamps, windows, tolerance)
    predicted = {int(i) for i in predicted if 0 <= int(i) < len(timestamps)}
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "label_count": len(truth), "predicted_count": len(predicted)}


def infer_step(timestamps):
    if len(timestamps) < 2:
        from datetime import timedelta
        return timedelta(0)
    return timestamps[1] - timestamps[0]


def add_metrics(metrics):
    tp, fp, fn = sum(x["tp"] for x in metrics), sum(x["fp"] for x in metrics), sum(x["fn"] for x in metrics)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "label_count": sum(x["label_count"] for x in metrics), "predicted_count": sum(x["predicted_count"] for x in metrics)}


def print_metrics(prefix, m):
    print(f"{prefix} detected={m['predicted_count']}, P={m['precision']:.4f}, R={m['recall']:.4f}, F1={m['f1']:.4f}, TP={m['tp']}, FP={m['fp']}, FN={m['fn']}")


def run(session, args, csv_files, all_windows):
    results = []
    print("\n========== Evaluation Config ==========")
    print(f"device: {args.device}\nudf: {args.udf}\nwindow: {args.window}\nshortWindow: {args.short_window}\nlongWindow: {args.long_window}\nsensitivity: {args.sensitivity}\nminThreshold: {args.min_threshold}\nminWarmup: {args.min_warmup}\nconfirmation: {args.confirmation}\ncooldown: {args.cooldown}\nspikeRatio: {args.spike_ratio}\nmaxAlerts: {args.max_alerts}\nminScore: {args.min_score}\nglobalSensitivity: {args.global_sensitivity}\ntopFraction: {args.top_fraction}\nseasonalPeriod: {args.seasonal_period}\nminSeriesVariability: {args.min_series_variability}\npeakRatio: {args.peak_ratio}\ntolerance: {args.tolerance}")
    print("\n========== Per NAB File Result ==========")
    for index in parse_sensors(args.sensors, len(csv_files)):
        sensor, csv_path = f"s{index}", csv_files[index]
        rel = csv_path.relative_to(args.data_dir).as_posix()
        windows, label_key = windows_for_file(all_windows, csv_path, args.data_dir, args.strict_labels)
        timestamps = read_timestamps(csv_path)
        sql = build_sql(args, sensor)
        if args.print_sql:
            print(sql)
        predicted = set(query_predictions(session, sql))
        metrics = evaluate_point_predictions(predicted, timestamps, windows, args.tolerance)
        results.append((sensor, rel, label_key, metrics))
        print_metrics(f"{sensor} {rel}", metrics)
    overall = add_metrics([x[3] for x in results])
    print("\n========== Overall Result On NAB ==========")
    print(f"tested_series: {len(results)}\nlabel_points: {overall['label_count']}\ndetected_points: {overall['predicted_count']}\nTP: {overall['tp']}\nFP: {overall['fp']}\nFN: {overall['fn']}\nprecision: {overall['precision']:.6f}\nrecall: {overall['recall']:.6f}\nf1: {overall['f1']:.6f}")
    print(f"\n========== Top {min(args.top_k, len(results))} NAB Files By F1 ==========")
    for sensor, rel, _, metrics in sorted(results, key=lambda x: (x[3]["f1"], x[3]["recall"], x[3]["precision"]), reverse=True)[: args.top_k]:
        print_metrics(f"{sensor} {rel}", metrics)
    missing = [rel for _, rel, label_key, _ in results if label_key is None]
    if missing:
        print("\nFiles without label windows were treated as normal:")
        for rel in missing:
            print(rel)
    return overall


def main():
    args = parse_args()
    csv_files = discover_files(args.data_dir, args.categories, args.files)
    all_windows = load_windows(args.label_path)
    print(f"Discovered {len(csv_files)} NAB csv files.")
    print(f"Loaded label windows for {len(all_windows)} files.")
    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")
    try:
        run(session, args, csv_files, all_windows)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
