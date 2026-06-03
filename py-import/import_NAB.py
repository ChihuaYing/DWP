import argparse
import csv
import math
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

DATABASE = "root.nab"
DEVICE = "root.nab.d1"

DATA_DIR = Path(__file__).resolve().parent / "dataset" / "NAB"
LABEL_JSON_PATH = DATA_DIR / "combined_windows.json"

BATCH_SIZE = 1000


def parse_args():
    parser = argparse.ArgumentParser(description="Import NAB csv data into IoTDB.")
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--label-path", type=Path, default=LABEL_JSON_PATH)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--categories",
        default="all",
        help="NAB subdirectories to import, e.g. all or artificialWithAnomaly,realAWSCloudwatch.",
    )
    parser.add_argument(
        "--files",
        default="all",
        help="CSV file stems/names to import, e.g. all or art_daily_flatmiddle.csv,nyc_taxi.",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Drop the target database before importing. Use with care.",
    )
    parser.add_argument(
        "--list-series",
        action="store_true",
        help="Print the stable mapping from IoTDB measurements to NAB csv files.",
    )
    return parser.parse_args()


def normalize_csv_name(name):
    name = name.strip().replace("\\", "/")
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def parse_filter_argument(value):
    if value.strip().lower() == "all":
        return None
    return {normalize_csv_name(item) for item in value.split(",") if item.strip()}


def discover_nab_csv_files(data_dir, categories_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"NAB data directory not found: {data_dir}")

    categories = None
    if categories_arg.strip().lower() != "all":
        categories = {item.strip() for item in categories_arg.split(",") if item.strip()}

    file_filters = parse_filter_argument(files_arg)
    csv_files = []
    for category_dir in sorted(path for path in data_dir.iterdir() if path.is_dir()):
        if categories is not None and category_dir.name not in categories:
            continue
        for csv_path in sorted(category_dir.glob("*.csv")):
            relative_name = csv_path.relative_to(data_dir).as_posix()
            accepted_names = {csv_path.name, csv_path.stem, relative_name, relative_name[:-4]}
            if file_filters is None or file_filters.intersection(
                {normalize_csv_name(name) for name in accepted_names}
            ):
                csv_files.append(csv_path)

    if not csv_files:
        raise ValueError("No NAB csv files matched the given --categories/--files filters.")
    return csv_files


def read_nab_values(csv_path):
    values = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if "timestamp" not in reader.fieldnames or "value" not in reader.fieldnames:
            raise ValueError(f"Expected columns timestamp,value in {csv_path}")
        for row in reader:
            value = float(row["value"])
            values.append(value if math.isfinite(value) else None)
    return values


def execute_ignore_error(session, sql, message=None):
    try:
        session.execute_non_query_statement(sql)
        if message:
            print(message)
    except Exception as exc:
        if message:
            print(f"{message}: skipped ({exc})")


def create_schema(session, database, device, num_series):
    execute_ignore_error(session, f"CREATE DATABASE {database}", f"Database {database} ready")

    print("Creating timeseries if needed...")
    for index in range(num_series):
        execute_ignore_error(
            session,
            f"CREATE TIMESERIES {device}.s{index} "
            "WITH DATATYPE=DOUBLE, ENCODING=GORILLA",
        )
    print("Timeseries ready.")


def insert_one_series(session, device, sensor, values, batch_size):
    measurements = [sensor]
    data_types = [TSDataType.DOUBLE]
    timestamps = []
    rows = []
    inserted = 0

    for timestamp, value in enumerate(values):
        timestamps.append(timestamp)
        rows.append([value])

        if len(timestamps) >= batch_size:
            session.insert_tablet(Tablet(device, measurements, data_types, rows, timestamps))
            inserted += len(timestamps)
            timestamps = []
            rows = []

    if timestamps:
        session.insert_tablet(Tablet(device, measurements, data_types, rows, timestamps))
        inserted += len(timestamps)

    return inserted


def import_nab_data(session, args, csv_files):
    if args.force_recreate:
        print(f"Dropping database {args.database}...")
        execute_ignore_error(session, f"DROP DATABASE {args.database}")

    if args.label_path.exists():
        print(f"NAB label file found: {args.label_path}")
    else:
        print(
            f"Warning: NAB label file not found at {args.label_path}. "
            "Import will continue, but test_NAB_stan_detect.py needs combined_windows.json."
        )

    create_schema(session, args.database, args.device, len(csv_files))

    print("\n========== NAB Series Mapping ==========")
    for index, csv_path in enumerate(csv_files):
        relative_name = csv_path.relative_to(args.data_dir).as_posix()
        print(f"s{index}: {relative_name}")

    print("\nStart inserting NAB data...")
    total_points = 0
    for index, csv_path in enumerate(csv_files):
        values = read_nab_values(csv_path)
        inserted = insert_one_series(session, args.device, f"s{index}", values, args.batch_size)
        total_points += inserted
        relative_name = csv_path.relative_to(args.data_dir).as_posix()
        print(f"Inserted {inserted} points into s{index} ({relative_name})")

    print(f"NAB import finished. series={len(csv_files)}, total_points={total_points}")


def main():
    args = parse_args()
    csv_files = discover_nab_csv_files(args.data_dir, args.categories, args.files)
    print(f"Discovered {len(csv_files)} NAB csv files.")

    if args.list_series:
        for index, csv_path in enumerate(csv_files):
            print(f"s{index}: {csv_path.relative_to(args.data_dir).as_posix()}")

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")

    try:
        import_nab_data(session, args, csv_files)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
