import argparse
import csv
import math
import re
from pathlib import Path

from iotdb.Session import Session
from iotdb.utils.IoTDBConstants import TSDataType
from iotdb.utils.Tablet import Tablet

# ============================================
# 默认配置
# ============================================

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DATABASE = "root.yahoo"
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "Yahoo_S5_Data"

BATCH_SIZE = 1000

BENCHMARK_DIRS = {
    "A1Benchmark": "a1",
    "A2Benchmark": "a2",
    "A3Benchmark": "a3",
    "A4Benchmark": "a4",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Import all Yahoo S5 benchmark csv data into IoTDB.")
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--benchmarks",
        default="all",
        help="Benchmarks to import, e.g. all or A1,A2,A3,A4.",
    )
    parser.add_argument(
        "--files",
        default="all",
        help="CSV file stems/names to import, e.g. all or real_1,A3Benchmark-TS1.csv.",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Drop the target database before importing. Use with care.",
    )
    parser.add_argument(
        "--list-series",
        action="store_true",
        help="Print the stable mapping from Yahoo S5 csv files to IoTDB devices.",
    )
    return parser.parse_args()


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


def discover_yahoo_s5_csv_files(data_dir, benchmarks_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"Yahoo S5 data directory not found: {data_dir}")

    benchmark_filters = parse_benchmark_filter(benchmarks_arg)
    file_filters = parse_file_filter(files_arg)

    csv_files = []
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
                csv_files.append((benchmark_key, csv_path))

    if not csv_files:
        raise ValueError("No Yahoo S5 csv files matched the given --benchmarks/--files filters.")
    return csv_files


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


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


def execute_ignore_error(session, sql, message=None):
    try:
        session.execute_non_query_statement(sql)
        if message:
            print(message)
    except Exception as exc:
        if message:
            print(f"{message}: skipped ({exc})")


def create_schema(session, database, device, include_label):
    execute_ignore_error(session, f"CREATE DATABASE {database}")
    execute_ignore_error(
        session,
        f"CREATE TIMESERIES {device}.value WITH DATATYPE=DOUBLE, ENCODING=GORILLA",
    )

    if include_label:
        execute_ignore_error(
            session,
            f"CREATE TIMESERIES {device}.label WITH DATATYPE=INT32, ENCODING=RLE",
        )


def read_yahoo_rows(csv_path, include_label):
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")

        columns = set(reader.fieldnames)
        timestamp_col = "timestamp" if "timestamp" in columns else "timestamps"
        if timestamp_col not in columns or "value" not in columns:
            raise ValueError(f"Expected timestamp/timestamps and value columns in {csv_path}")

        label_col = None
        if include_label:
            if "is_anomaly" in columns:
                label_col = "is_anomaly"
            elif "label" in columns:
                label_col = "label"
            elif "anomaly" in columns:
                label_col = "anomaly"
            else:
                raise ValueError(f"Expected anomaly label column in {csv_path}")

        for row in reader:
            timestamp = int(float(row[timestamp_col]))
            value = float(row["value"])
            value = value if math.isfinite(value) else None

            if include_label:
                yield timestamp, [value, int(float(row[label_col]))]
            else:
                yield timestamp, [value]


def insert_csv(session, csv_path, device, include_label, batch_size):
    measurements = ["value", "label"] if include_label else ["value"]
    data_types = [TSDataType.DOUBLE, TSDataType.INT32] if include_label else [TSDataType.DOUBLE]

    timestamps = []
    rows = []
    inserted = 0

    for timestamp, values in read_yahoo_rows(csv_path, include_label):
        timestamps.append(timestamp)
        rows.append(values)

        if len(timestamps) >= batch_size:
            session.insert_tablet(Tablet(device, measurements, data_types, rows, timestamps))
            inserted += len(timestamps)
            timestamps = []
            rows = []

    if timestamps:
        session.insert_tablet(Tablet(device, measurements, data_types, rows, timestamps))
        inserted += len(timestamps)

    return inserted


def import_yahoo_s5_data(session, args, csv_files):
    if args.force_recreate:
        print(f"Dropping database {args.database}...")
        execute_ignore_error(session, f"DROP DATABASE {args.database}")

    print("\n========== Yahoo S5 Series Mapping ==========")
    mappings = []
    for benchmark_key, csv_path in csv_files:
        device = device_for_csv(args.database, benchmark_key, csv_path)
        relative_name = csv_path.relative_to(args.data_dir).as_posix()
        include_label = benchmark_key in {"a1", "a2"}
        mappings.append((benchmark_key, csv_path, device, include_label))
        measurements = "value,label" if include_label else "value"
        print(f"{relative_name} -> {device} ({measurements})")

    print("\nCreating timeseries if needed...")
    for _, _, device, include_label in mappings:
        create_schema(session, args.database, device, include_label)
    print("Timeseries ready.")

    print("\nStart inserting Yahoo S5 data...")
    total_points = 0
    for _, csv_path, device, include_label in mappings:
        inserted = insert_csv(session, csv_path, device, include_label, args.batch_size)
        total_points += inserted
        relative_name = csv_path.relative_to(args.data_dir).as_posix()
        print(f"Inserted {inserted} points into {device} ({relative_name})")

    print(f"Yahoo S5 import finished. series={len(mappings)}, total_points={total_points}")


def main():
    args = parse_args()
    csv_files = discover_yahoo_s5_csv_files(args.data_dir, args.benchmarks, args.files)
    print(f"Discovered {len(csv_files)} Yahoo S5 csv files.")

    if args.list_series:
        for benchmark_key, csv_path in csv_files:
            device = device_for_csv(args.database, benchmark_key, csv_path)
            print(f"{csv_path.relative_to(args.data_dir).as_posix()} -> {device}")
        return

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")

    try:
        import_yahoo_s5_data(session, args, csv_files)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
