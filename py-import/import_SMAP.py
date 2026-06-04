from pathlib import Path

import numpy as np

from iotdb.Session import Session
from iotdb.utils.IoTDBConstants import TSDataType
from iotdb.utils.Tablet import Tablet

# ============================================
# 配置
# ============================================

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DATABASE = "root.smap"
TRAIN_DEVICE = "root.smap.train"
TEST_DEVICE = "root.smap.test"

DATA_DIR = Path(__file__).resolve().parent / "dataset" / "SMAP"
TRAIN_NPY_PATH = DATA_DIR / "SMAP_train.npy"
TEST_NPY_PATH = DATA_DIR / "SMAP_test.npy"
LABEL_NPY_PATH = DATA_DIR / "SMAP_test_label.npy"

BATCH_SIZE = 1000


def create_timeseries(session, device, num_cols, with_label=False):
    for i in range(num_cols):

        measurement = f"s{i}"

        sql = (
            f"CREATE TIMESERIES {device}.{measurement} "
            f"WITH DATATYPE=DOUBLE, ENCODING=GORILLA"
        )

        try:
            session.execute_non_query_statement(sql)
        except Exception:
            pass

    if with_label:
        try:
            session.execute_non_query_statement(
                f"CREATE TIMESERIES {device}.label "
                f"WITH DATATYPE=INT32, ENCODING=RLE"
            )
        except Exception:
            pass


def insert_data(session, device, data, labels=None):
    num_rows = data.shape[0]
    num_cols = data.shape[1]

    measurements = [f"s{i}" for i in range(num_cols)]
    data_types = [TSDataType.DOUBLE] * num_cols

    if labels is not None:
        measurements.append("label")
        data_types.append(TSDataType.INT32)

    timestamps = []
    values = []

    inserted = 0

    for t in range(num_rows):

        timestamps.append(t)

        row = [float(x) for x in data[t]]

        if labels is not None:
            row.append(int(labels[t]))

        values.append(row)

        # batch insert
        if len(timestamps) >= BATCH_SIZE:

            tablet = Tablet(
                device,
                measurements,
                data_types,
                values,
                timestamps
            )

            session.insert_tablet(tablet)

            inserted += len(timestamps)

            print(f"Inserted {device}: {inserted}/{num_rows}")

            # clear batch
            timestamps = []
            values = []

    if len(timestamps) > 0:

        tablet = Tablet(
            device,
            measurements,
            data_types,
            values,
            timestamps
        )

        session.insert_tablet(tablet)

        inserted += len(timestamps)

        print(f"Inserted {device}: {inserted}/{num_rows}")


# ============================================
# 读取数据
# ============================================

print("Loading npy files...")

train_data = np.load(TRAIN_NPY_PATH)
test_data = np.load(TEST_NPY_PATH)
labels = np.load(LABEL_NPY_PATH)

print("train_data shape:", train_data.shape)
print("test_data shape:", test_data.shape)
print("labels shape:", labels.shape)

if test_data.shape[0] != labels.shape[0]:
    raise Exception("SMAP_test.npy and SMAP_test_label.npy row counts do not match.")

if train_data.shape[1] != test_data.shape[1]:
    raise Exception("SMAP_train.npy and SMAP_test.npy column counts do not match.")

num_cols = test_data.shape[1]

# ============================================
# 创建 Session
# ============================================

session = Session(
    IOTDB_HOST,
    IOTDB_PORT,
    IOTDB_USER,
    IOTDB_PASSWORD
)

session.open(False)

print("Connected to IoTDB.")

# ============================================
# 创建 Database
# ============================================

try:
    session.execute_non_query_statement(
        f"CREATE DATABASE {DATABASE}"
    )
    print(f"Database {DATABASE} created.")
except Exception as e:
    print(f"Database may already exist: {e}")

# ============================================
# 创建 Timeseries
# ============================================

print("Creating train timeseries...")
create_timeseries(session, TRAIN_DEVICE, num_cols, with_label=False)

print("Creating test timeseries...")
create_timeseries(session, TEST_DEVICE, num_cols, with_label=True)

print("Timeseries ready.")

# ============================================
# 批量写入
# ============================================

print("Start inserting train data...")
insert_data(session, TRAIN_DEVICE, train_data)

print("Start inserting test data...")
insert_data(session, TEST_DEVICE, test_data, labels)

# ============================================
# 关闭 Session
# ============================================

session.close()

print("Import finished.")
