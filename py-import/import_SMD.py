import argparse
import math
from pathlib import Path

import numpy as np

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

DATABASE = "root.smd"
DEVICE = "root.smd.d1"

DATA_DIR = Path(__file__).resolve().parent / "dataset" / "SMD"
TEST_NPY_PATH = DATA_DIR / "SMD_test.npy"
LABEL_NPY_PATH = DATA_DIR / "SMD_test_label.npy"

BATCH_SIZE = 1000


def parse_args():
    parser = argparse.ArgumentParser(description="Import SMD test data into IoTDB.")
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--test-path", type=Path, default=TEST_NPY_PATH)
    parser.add_argument("--label-path", type=Path, default=LABEL_NPY_PATH)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Drop the target database before importing. Use with care.",
    )
    return parser.parse_args()


def load_smd_arrays(test_path, label_path):
    print("Loading npy files...")
    test_data = np.load(test_path)
    labels = np.load(label_path)

    if test_data.ndim != 2:
        raise ValueError(f"Expected test data to be 2-D, got shape {test_data.shape}")

    labels = np.asarray(labels).squeeze()
    if labels.ndim != 1:
        raise ValueError(f"Expected labels to be 1-D after squeeze, got shape {labels.shape}")
    if labels.shape[0] != test_data.shape[0]:
        raise ValueError(
            f"Data/label length mismatch: data rows={test_data.shape[0]}, labels={labels.shape[0]}"
        )

    print("test_data shape:", test_data.shape)
    print("labels shape:", labels.shape)
    return test_data, labels.astype(np.int32)


def execute_ignore_error(session, sql, message=None):
    try:
        session.execute_non_query_statement(sql)
        if message:
            print(message)
    except Exception as exc:
        if message:
            print(f"{message}: skipped ({exc})")


def create_schema(session, database, device, num_cols):
    execute_ignore_error(session, f"CREATE DATABASE {database}", f"Database {database} ready")

    print("Creating timeseries if needed...")
    for i in range(num_cols):
        execute_ignore_error(
            session,
            f"CREATE TIMESERIES {device}.s{i} "
            "WITH DATATYPE=DOUBLE, ENCODING=GORILLA",
        )

    execute_ignore_error(
        session,
        f"CREATE TIMESERIES {device}.label "
        "WITH DATATYPE=INT32, ENCODING=RLE",
    )
    print("Timeseries ready.")


def import_smd_data(session, args, test_data, labels):
    num_rows, num_cols = test_data.shape

    if args.force_recreate:
        print(f"Dropping database {args.database}...")
        execute_ignore_error(session, f"DROP DATABASE {args.database}")

    create_schema(session, args.database, args.device, num_cols)

    measurements = [f"s{i}" for i in range(num_cols)] + ["label"]
    data_types = [TSDataType.DOUBLE] * num_cols + [TSDataType.INT32]

    print("Start inserting SMD test data...")
    timestamps = []
    values = []
    inserted = 0

    for timestamp in range(num_rows):
        timestamps.append(timestamp)
        row = []
        for value in test_data[timestamp]:
            value = float(value)
            row.append(value if math.isfinite(value) else None)
        row.append(int(labels[timestamp]))
        values.append(row)

        if len(timestamps) >= args.batch_size:
            session.insert_tablet(
                Tablet(args.device, measurements, data_types, values, timestamps)
            )
            inserted += len(timestamps)
            print(f"Inserted {inserted}/{num_rows}")
            timestamps = []
            values = []

    if timestamps:
        session.insert_tablet(Tablet(args.device, measurements, data_types, values, timestamps))
        inserted += len(timestamps)
        print(f"Inserted {inserted}/{num_rows}")

    print("SMD import finished.")


def main():
    args = parse_args()
    test_data, labels = load_smd_arrays(args.test_path, args.label_path)

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")

    try:
        import_smd_data(session, args, test_data, labels)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
