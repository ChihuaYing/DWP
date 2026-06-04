import argparse
import csv
import math
from pathlib import Path

from iotdb.Session import Session


IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DEVICE = "root.nab.d1"
UDF_NAME = "STAN_DETECT_NAB_V2"
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "NAB" / "data"
RESULTS_ROOT = Path(__file__).resolve().parent / "dataset" / "NAB" / "results"
DETECTOR_NAME = "iotdbStanNABV2"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export IoTDB UDF predictions as NAB-compatible results CSV files."
    )
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--udf", default=UDF_NAME)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--detector-name", default=DETECTOR_NAME)
    parser.add_argument("--categories", default="all")
    parser.add_argument("--files", default="all")
    parser.add_argument("--sensors", default="all")
    parser.add_argument(
        "--udf-param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "UDF parameter to pass inside the SQL function call. "
            "Can be repeated, e.g. --udf-param shortWindow=96 --udf-param minScore=5.0."
        ),
    )
    parser.add_argument("--window", type=int, default=None, help="Deprecated shorthand for --udf-param window=...")
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=None,
        help="Deprecated shorthand for --udf-param sensitivity=...",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Deprecated shorthand for --udf-param threshold=...",
    )
    parser.add_argument(
        "--score-mode",
        choices=("binary", "raw", "logistic", "clipped"),
        default="binary",
        help=(
            "How to write UDF outputs into NAB anomaly_score. "
            "binary writes 1.0 for any UDF detection; raw writes the UDF score; "
            "logistic maps score to score/(1+score); clipped clamps to [0,1]."
        ),
    )
    parser.add_argument(
        "--clean-detector-dir",
        action="store_true",
        help="Delete old CSV files under results/<detector-name> before exporting.",
    )
    parser.add_argument("--print-sql", action="store_true")
    return parser.parse_args()


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


def read_nab_rows(csv_path):
    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if "timestamp" not in reader.fieldnames or "value" not in reader.fieldnames:
            raise ValueError(f"Expected columns timestamp,value in {csv_path}")
        for row in reader:
            rows.append((row["timestamp"], row["value"]))
    return rows


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
    return float(parts[-1]) if len(parts) >= 2 else 1.0


def close_dataset(dataset):
    for method in ("close_operation_handle", "closeOperationHandle", "close"):
        if hasattr(dataset, method):
            try:
                getattr(dataset, method)()
            except Exception:
                pass
            return


def sql_quote(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def parse_udf_params(args):
    params = []

    for raw in args.udf_param:
        if "=" not in raw:
            raise ValueError(f"Invalid --udf-param {raw!r}; expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid --udf-param {raw!r}; key is empty.")
        params.append((key, value))

    if args.window is not None:
        params.append(("window", args.window))
    if args.sensitivity is not None:
        params.append(("sensitivity", args.sensitivity))
    if args.threshold is not None:
        params.append(("threshold", args.threshold))

    return params


def build_sql(args, sensor, udf_params):
    param_sql = "".join(
        f", '{sql_quote(key)}'='{sql_quote(value)}'" for key, value in udf_params
    )
    return f"SELECT {args.udf}({sensor}{param_sql}) AS anomaly_score FROM {args.device}"


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


def format_score(score, mode):
    if score is None:
        return 0.0
    if mode == "binary":
        return 1.0
    if mode == "raw":
        return float(score)
    if mode == "logistic":
        score = max(0.0, float(score))
        return score / (1.0 + score)
    if mode == "clipped":
        return min(1.0, max(0.0, float(score)))
    raise ValueError(f"Unsupported score mode: {mode}")


def clean_detector_dir(detector_dir):
    if not detector_dir.exists():
        return
    for csv_path in detector_dir.rglob("*.csv"):
        csv_path.unlink()


def write_results_file(out_path, rows, predictions, score_mode):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["timestamp", "value", "anomaly_score"])
        for index, (timestamp, value) in enumerate(rows):
            writer.writerow([timestamp, value, format_score(predictions.get(index), score_mode)])


def export_results(session, args, csv_files):
    detector_dir = args.results_root / args.detector_name
    if args.clean_detector_dir:
        clean_detector_dir(detector_dir)

    udf_params = parse_udf_params(args)
    sensor_indices = parse_sensors(args.sensors, len(csv_files))
    exported = 0
    total_detections = 0

    print("\n========== NAB IoTDB UDF Export Config ==========")
    print(f"data_dir: {args.data_dir}")
    print(f"results_dir: {detector_dir}")
    print(f"device: {args.device}")
    print(f"udf: {args.udf}")
    print("udf_params: " + (", ".join(f"{key}={value}" for key, value in udf_params) or "<none>"))
    print(f"score_mode: {args.score_mode}")

    print("\n========== Exported Files ==========")
    for index in sensor_indices:
        sensor = f"s{index}"
        csv_path = csv_files[index]
        rel = csv_path.relative_to(args.data_dir)
        rel_posix = rel.as_posix()
        rows = read_nab_rows(csv_path)
        sql = build_sql(args, sensor, udf_params)
        if args.print_sql:
            print(sql)
        predictions = query_predictions(session, sql)

        out_name = f"{args.detector_name}_{csv_path.name}"
        out_path = detector_dir / rel.parent / out_name
        write_results_file(out_path, rows, predictions, args.score_mode)

        exported += 1
        total_detections += len(predictions)
        print(f"{sensor} {rel_posix}: rows={len(rows)}, detections={len(predictions)}, output={out_path}")

    print("\n========== Export Summary ==========")
    print(f"exported_files: {exported}")
    print(f"total_detections: {total_detections}")
    print(f"detector_name: {args.detector_name}")


def main():
    args = parse_args()
    if "_" in args.detector_name:
        raise ValueError(
            "NAB normalize() parses detector names poorly when they contain underscores. "
            "Use a detector name without underscores, e.g. iotdbStanNABV2."
        )

    csv_files = discover_files(args.data_dir, args.categories, args.files)
    print(f"Discovered {len(csv_files)} NAB csv files.")

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")
    try:
        export_results(session, args, csv_files)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
