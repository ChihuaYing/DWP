import argparse
from pathlib import Path

import numpy as np

from iotdb.Session import Session

# ============================================
# 默认配置
# ============================================

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DEVICE = "root.msl.d1"
UDF_NAME = "stan_detect"

DATA_DIR = Path(__file__).resolve().parent / "dataset" / "MSL"
TEST_NPY_PATH = DATA_DIR / "MSL_test.npy"
LABEL_NPY_PATH = DATA_DIR / "MSL_test_label.npy"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate IoTDB UDF stan_detect on the whole MSL dataset."
    )
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--udf", default=UDF_NAME)
    parser.add_argument("--test-path", type=Path, default=TEST_NPY_PATH)
    parser.add_argument("--label-path", type=Path, default=LABEL_NPY_PATH)
    parser.add_argument(
        "--sensors",
        default="all",
        help="Sensors to evaluate, e.g. all, s0, s0,s3,s10, or 0,3,10.",
    )
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--sensitivity", type=float, default=3.0)
    parser.add_argument("--min-threshold", type=float, default=3.0)
    parser.add_argument(
        "--tolerance",
        type=int,
        default=0,
        help="A detected timestamp is correct if it is within +/- tolerance of a labeled anomaly.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Print top-k sensors ordered by F1.",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print every SQL query before executing it.",
    )
    return parser.parse_args()


def load_labels_and_num_cols(test_path, label_path):
    print("Loading MSL labels...")
    test_data = np.load(test_path, mmap_mode="r")
    labels = np.asarray(np.load(label_path)).squeeze()

    if test_data.ndim != 2:
        raise ValueError(f"Expected test data to be 2-D, got shape {test_data.shape}")
    if labels.ndim != 1:
        raise ValueError(f"Expected labels to be 1-D after squeeze, got shape {labels.shape}")
    if labels.shape[0] != test_data.shape[0]:
        raise ValueError(
            f"Data/label length mismatch: data rows={test_data.shape[0]}, labels={labels.shape[0]}"
        )

    print("test_data shape:", test_data.shape)
    print("labels shape:", labels.shape)
    return labels.astype(np.int32), test_data.shape[1]


def parse_sensor_argument(sensors_arg, num_cols):
    if sensors_arg.strip().lower() == "all":
        return [f"s{i}" for i in range(num_cols)]

    sensors = []
    for item in sensors_arg.split(","):
        item = item.strip()
        if not item:
            continue
        sensor = item if item.lower().startswith("s") else f"s{item}"
        index = int(sensor[1:])
        if index < 0 or index >= num_cols:
            raise ValueError(f"Sensor {sensor} is out of range 0..{num_cols - 1}")
        sensors.append(f"s{index}")

    if not sensors:
        raise ValueError("No valid sensors specified.")
    return sensors


def record_to_timestamp(record):
    for attr in ("timestamp", "time"):
        if hasattr(record, attr):
            value = getattr(record, attr)
            return int(value() if callable(value) else value)

    for method in ("get_timestamp", "getTimestamp", "get_time", "getTime"):
        if hasattr(record, method):
            return int(getattr(record, method)())

    text = str(record)
    first_token = text.replace(",", " ").split()[0]
    return int(first_token)


def record_to_score(record):
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
    if len(parts) >= 2:
        try:
            return float(parts[-1])
        except ValueError:
            return None
    return None


def close_dataset(dataset):
    for method in ("close_operation_handle", "closeOperationHandle", "close"):
        if hasattr(dataset, method):
            try:
                getattr(dataset, method)()
            except Exception:
                pass
            return


def query_udf_predictions(session, sql):
    dataset = session.execute_query_statement(sql)
    predictions = {}

    try:
        while dataset.has_next():
            record = dataset.next()
            timestamp = record_to_timestamp(record)
            score = record_to_score(record)
            predictions[timestamp] = score
    finally:
        close_dataset(dataset)

    return predictions


def build_udf_sql(args, sensor):
    return (
        f"SELECT {args.udf}({sensor}, "
        f"\"window\"=\"{args.window}\", "
        f"\"sensitivity\"=\"{args.sensitivity}\", "
        f"\"minThreshold\"=\"{args.min_threshold}\") "
        f"FROM {args.device}"
    )


def timestamps_with_tolerance(timestamps, start, end, tolerance):
    expanded = set()
    for timestamp in timestamps:
        left = max(start, timestamp - tolerance)
        right = min(end, timestamp + tolerance)
        expanded.update(range(left, right + 1))
    return expanded


def evaluate_predictions(predicted_timestamps, labels, tolerance):
    label_timestamps = {index for index, value in enumerate(labels) if int(value) != 0}
    predicted_timestamps = set(predicted_timestamps)

    if tolerance > 0:
        label_windows = timestamps_with_tolerance(label_timestamps, 0, len(labels) - 1, tolerance)
        true_positive_predictions = {ts for ts in predicted_timestamps if ts in label_windows}
        matched_labels = set()
        for timestamp in true_positive_predictions:
            for label_timestamp in range(
                max(0, timestamp - tolerance),
                min(len(labels) - 1, timestamp + tolerance) + 1,
            ):
                if label_timestamp in label_timestamps:
                    matched_labels.add(label_timestamp)

        tp = len(true_positive_predictions)
        fp = len(predicted_timestamps - true_positive_predictions)
        fn = len(label_timestamps - matched_labels)
    else:
        tp = len(predicted_timestamps & label_timestamps)
        fp = len(predicted_timestamps - label_timestamps)
        fn = len(label_timestamps - predicted_timestamps)

    precision = tp / (tp + fp) if tp + fp > 0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "label_count": len(label_timestamps),
        "predicted_count": len(predicted_timestamps),
    }


def print_metrics(prefix, metrics):
    print(
        f"{prefix} detected={metrics['predicted_count']}, "
        f"P={metrics['precision']:.4f}, R={metrics['recall']:.4f}, F1={metrics['f1']:.4f}, "
        f"TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}"
    )


def run_evaluation(session, args, labels, num_cols):
    sensors = parse_sensor_argument(args.sensors, num_cols)
    union_predictions = {}
    per_sensor_results = []

    print("\n========== Evaluation Config ==========")
    print(f"device: {args.device}")
    print(f"udf: {args.udf}")
    print(f"sensors: {len(sensors)}")
    print(f"window: {args.window}")
    print(f"sensitivity: {args.sensitivity}")
    print(f"minThreshold: {args.min_threshold}")
    print(f"tolerance: {args.tolerance}")

    print("\n========== Per Sensor Result ==========")
    for sensor in sensors:
        sql = build_udf_sql(args, sensor)
        if args.print_sql:
            print(sql)

        predictions = query_udf_predictions(session, sql)
        for timestamp, score in predictions.items():
            old_score = union_predictions.get(timestamp)
            if old_score is None or (score is not None and score > old_score):
                union_predictions[timestamp] = score

        metrics = evaluate_predictions(predictions.keys(), labels, args.tolerance)
        per_sensor_results.append((sensor, metrics))
        print_metrics(sensor, metrics)

    overall = evaluate_predictions(union_predictions.keys(), labels, args.tolerance)

    print("\n========== Overall Result On MSL ==========")
    print(f"tested_sensors: {len(sensors)}")
    print(f"label_timestamps: {overall['label_count']}")
    print(f"detected_timestamps_union: {overall['predicted_count']}")
    print(f"TP: {overall['tp']}")
    print(f"FP: {overall['fp']}")
    print(f"FN: {overall['fn']}")
    print(f"precision: {overall['precision']:.6f}")
    print(f"recall: {overall['recall']:.6f}")
    print(f"f1: {overall['f1']:.6f}")

    print(f"\n========== Top {min(args.top_k, len(per_sensor_results))} Sensors By F1 ==========")
    ranked = sorted(
        per_sensor_results,
        key=lambda item: (
            item[1]["f1"],
            item[1]["recall"],
            item[1]["precision"],
        ),
        reverse=True,
    )
    for sensor, metrics in ranked[: args.top_k]:
        print_metrics(sensor, metrics)

    return overall


def main():
    args = parse_args()
    labels, num_cols = load_labels_and_num_cols(args.test_path, args.label_path)

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")

    try:
        run_evaluation(session, args, labels, num_cols)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
