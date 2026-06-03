import argparse
from itertools import product
from pathlib import Path
from iotdb.Session import Session

from test_MSL_stan_detect import (
    DEVICE,
    IOTDB_HOST,
    IOTDB_PASSWORD,
    IOTDB_PORT,
    IOTDB_USER,
    LABEL_NPY_PATH,
    TEST_NPY_PATH,
    UDF_NAME,
    evaluate_predictions,
    load_labels_and_num_cols,
    parse_sensor_argument,
    query_udf_predictions,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Grid-search stan_detect parameters on the whole MSL dataset."
    )
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--udf", default=UDF_NAME)
    parser.add_argument("--test-path", type=Path, default=TEST_NPY_PATH)
    parser.add_argument("--label-path", type=Path, default=LABEL_NPY_PATH)
    parser.add_argument("--sensors", default="all")
    parser.add_argument("--windows", default="50,100,200,300")
    parser.add_argument("--sensitivities", default="3,4,5,6,8,10")
    parser.add_argument("--min-thresholds", default="3,4,5,6,8,10,12,15")
    parser.add_argument("--tolerance", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--target",
        choices=("f1", "precision", "recall"),
        default="f1",
        help="Main metric used to rank parameter groups.",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print SQL before executing each UDF query.",
    )
    return parser.parse_args()


def parse_int_list(text):
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def parse_float_list(text):
    return [float(item.strip()) for item in text.split(",") if item.strip()]


def build_udf_sql(args, sensor, window, sensitivity, min_threshold):
    return (
        f"SELECT {args.udf}({sensor}, "
        f"\"window\"=\"{window}\", "
        f"\"sensitivity\"=\"{sensitivity}\", "
        f"\"minThreshold\"=\"{min_threshold}\") "
        f"FROM {args.device}"
    )


def run_one_group(session, args, labels, sensors, window, sensitivity, min_threshold):
    union_predictions = {}

    for sensor in sensors:
        sql = build_udf_sql(args, sensor, window, sensitivity, min_threshold)
        if args.print_sql:
            print(sql)

        predictions = query_udf_predictions(session, sql)
        for timestamp, score in predictions.items():
            old_score = union_predictions.get(timestamp)
            if old_score is None or (score is not None and score > old_score):
                union_predictions[timestamp] = score

    metrics = evaluate_predictions(union_predictions.keys(), labels, args.tolerance)
    return {
        "window": window,
        "sensitivity": sensitivity,
        "min_threshold": min_threshold,
        **metrics,
    }


def ranking_key(item, target):
    if target == "precision":
        return (item["precision"], item["f1"], item["recall"], -item["fp"])
    if target == "recall":
        return (item["recall"], item["f1"], item["precision"], -item["fp"])
    return (item["f1"], item["precision"], item["recall"], -item["fp"])


def print_result(prefix, item):
    print(
        f"{prefix} window={item['window']}, "
        f"sensitivity={item['sensitivity']}, "
        f"minThreshold={item['min_threshold']} -> "
        f"detected={item['predicted_count']}, "
        f"P={item['precision']:.6f}, R={item['recall']:.6f}, F1={item['f1']:.6f}, "
        f"TP={item['tp']}, FP={item['fp']}, FN={item['fn']}"
    )


def main():
    args = parse_args()
    labels, num_cols = load_labels_and_num_cols(args.test_path, args.label_path)
    sensors = parse_sensor_argument(args.sensors, num_cols)

    windows = parse_int_list(args.windows)
    sensitivities = parse_float_list(args.sensitivities)
    min_thresholds = parse_float_list(args.min_thresholds)
    groups = list(product(windows, sensitivities, min_thresholds))

    print("\n========== STAN Parameter Grid Search ==========")
    print(f"device: {args.device}")
    print(f"udf: {args.udf}")
    print(f"sensors: {len(sensors)}")
    print(f"windows: {windows}")
    print(f"sensitivities: {sensitivities}")
    print(f"minThresholds: {min_thresholds}")
    print(f"tolerance: {args.tolerance}")
    print(f"target: {args.target}")
    print(f"total groups: {len(groups)}")

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")

    results = []
    try:
        for index, (window, sensitivity, min_threshold) in enumerate(groups, start=1):
            item = run_one_group(
                session,
                args,
                labels,
                sensors,
                window,
                sensitivity,
                min_threshold,
            )
            results.append(item)
            print_result(f"[{index}/{len(groups)}]", item)
    finally:
        session.close()
        print("Session closed.")

    ranked = sorted(results, key=lambda item: ranking_key(item, args.target), reverse=True)

    print(f"\n========== Best {min(args.top_k, len(ranked))} Parameter Groups ==========")
    for rank, item in enumerate(ranked[: args.top_k], start=1):
        print_result(f"#{rank}", item)

    best = ranked[0]
    print("\n========== Recommended Test Command ==========")
    print(
        "python test_MSL_stan_detect.py "
        f"--window {best['window']} "
        f"--sensitivity {best['sensitivity']} "
        f"--min-threshold {best['min_threshold']} "
        f"--tolerance {args.tolerance} "
        f"--sensors {args.sensors}"
    )


if __name__ == "__main__":
    main()
