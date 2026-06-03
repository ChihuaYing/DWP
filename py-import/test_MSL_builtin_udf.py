import argparse
from pathlib import Path
import numpy as np
from iotdb.Session import Session

from test_MSL_stan_detect import (
    DEVICE, IOTDB_HOST, IOTDB_PORT, IOTDB_USER, IOTDB_PASSWORD,
    TEST_NPY_PATH, LABEL_NPY_PATH,
    load_labels_and_num_cols, parse_sensor_argument,
    record_to_timestamp, close_dataset,
    evaluate_predictions, print_metrics
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Test IoTDB built-in UDFs (IQR, LOF, TwoSidedFilter) on MSL."
    )
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--test-path", type=Path, default=TEST_NPY_PATH)
    parser.add_argument("--label-path", type=Path, default=LABEL_NPY_PATH)
    parser.add_argument("--udf", choices=["IQR", "LOF", "TwoSidedFilter"], required=True)
    parser.add_argument("--sensors", default="all")
    parser.add_argument("--tolerance", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--print-sql", action="store_true")
    
    parser.add_argument("--iqr-compute", default="batch", choices=["batch", "stream"])
    parser.add_argument("--lof-window", type=int, default=1000)
    parser.add_argument("--lof-k", type=int, default=5)
    parser.add_argument("--lof-threshold", type=float, default=1.5)
    parser.add_argument("--twosided-len", type=float, default=10)
    parser.add_argument("--twosided-threshold", type=float, default=0.5)
    parser.add_argument("--twosided-diff", type=float, default=0.05)
    return parser.parse_args()


def record_to_value(record, col=0):
    fields = getattr(record, "fields", None)
    if fields and len(fields) > col:
        field = fields[col]
        for attr in ("double_value", "float_value", "long_value", "int_value"):
            if hasattr(field, attr):
                val = getattr(field, attr)
                if val is not None:
                    return float(val() if callable(val) else val)
    try:
        parts = str(record).replace(",", " ").split()
        return float(parts[col + 1])
    except (ValueError, IndexError):
        return None


def query_iqr(session, args, sensor):
    sql = f'SELECT IQR({sensor}, "compute"="{args.iqr_compute}") FROM {args.device}'
    if args.print_sql:
        print(sql)
    dataset = session.execute_query_statement(sql)
    predictions = set()
    try:
        while dataset.has_next():
            predictions.add(record_to_timestamp(dataset.next()))
    finally:
        close_dataset(dataset)
    return predictions


def query_lof(session, args, sensor):
    sql = f'SELECT LOF({sensor}, "window"="{args.lof_window}", "k"="{args.lof_k}") FROM {args.device}'
    if args.print_sql:
        print(sql)
    dataset = session.execute_query_statement(sql)
    predictions = set()
    try:
        while dataset.has_next():
            rec = dataset.next()
            ts = record_to_timestamp(rec)
            score = record_to_value(rec, 0)
            if score is not None and score > args.lof_threshold:
                predictions.add(ts)
    finally:
        close_dataset(dataset)
    return predictions


def query_twosided(session, args, sensor):
    sql_orig = f'SELECT {sensor} FROM {args.device}'
    dataset = session.execute_query_statement(sql_orig)
    original = {}
    try:
        while dataset.has_next():
            rec = dataset.next()
            ts = record_to_timestamp(rec)
            val = record_to_value(rec, 0)
            if val is not None:
                original[ts] = val
    finally:
        close_dataset(dataset)
    
    sql = f'SELECT TwoSidedFilter({sensor}, "len"="{args.twosided_len}", "threshold"="{args.twosided_threshold}") FROM {args.device}'
    if args.print_sql:
        print(sql)
    dataset = session.execute_query_statement(sql)
    predictions = set()
    try:
        while dataset.has_next():
            rec = dataset.next()
            ts = record_to_timestamp(rec)
            repaired = record_to_value(rec, 0)
            if ts in original and repaired is not None:
                orig = original[ts]
                diff = abs(repaired - orig) / abs(orig) if orig != 0 else abs(repaired - orig)
                if diff > args.twosided_diff:
                    predictions.add(ts)
    finally:
        close_dataset(dataset)
    return predictions


def run_evaluation(session, args, labels, sensors):
    union_predictions = set()
    per_sensor_results = []
    
    print("\n========== Evaluation Config ==========")
    print(f"device: {args.device}")
    print(f"udf: {args.udf}")
    print(f"sensors: {len(sensors)}")
    print(f"tolerance: {args.tolerance}")
    
    if args.udf == "IQR":
        print(f"compute: {args.iqr_compute}")
    elif args.udf == "LOF":
        print(f"window: {args.lof_window}, k: {args.lof_k}, threshold: {args.lof_threshold}")
    elif args.udf == "TwoSidedFilter":
        print(f"len: {args.twosided_len}, threshold: {args.twosided_threshold}, diff: {args.twosided_diff}")
    
    print("\n========== Per Sensor Result ==========")
    for sensor in sensors:
        if args.udf == "IQR":
            predictions = query_iqr(session, args, sensor)
        elif args.udf == "LOF":
            predictions = query_lof(session, args, sensor)
        elif args.udf == "TwoSidedFilter":
            predictions = query_twosided(session, args, sensor)
        else:
            raise ValueError(f"Unsupported UDF: {args.udf}")
        
        union_predictions.update(predictions)
        metrics = evaluate_predictions(predictions, labels, args.tolerance)
        per_sensor_results.append((sensor, metrics))
        print_metrics(sensor, metrics)
    
    overall = evaluate_predictions(union_predictions, labels, args.tolerance)
    
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
    ranked = sorted(per_sensor_results, key=lambda x: (x[1]["f1"], x[1]["recall"]), reverse=True)
    for sensor, metrics in ranked[:args.top_k]:
        print_metrics(sensor, metrics)
    
    return overall


def main():
    args = parse_args()
    labels, num_cols = load_labels_and_num_cols(args.test_path, args.label_path)
    sensors = parse_sensor_argument(args.sensors, num_cols)
    
    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")
    
    try:
        run_evaluation(session, args, labels, sensors)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
