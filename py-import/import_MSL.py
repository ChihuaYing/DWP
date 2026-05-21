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

DATABASE = "root.msl"
DEVICE = "root.msl.d1"

TEST_NPY_PATH = "./dataset/MSL/MSL_test.npy"
LABEL_NPY_PATH = "./dataset/MSL/MSL_test_label.npy"

BATCH_SIZE = 1000

# ============================================
# 读取数据
# ============================================

print("Loading npy files...")

test_data = np.load(TEST_NPY_PATH)
labels = np.load(LABEL_NPY_PATH)

print("test_data shape:", test_data.shape)
print("labels shape:", labels.shape)

num_rows = test_data.shape[0]
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

print("Creating timeseries...")

for i in range(num_cols):

    measurement = f"s{i}"

    sql = (
        f"CREATE TIMESERIES {DEVICE}.{measurement} "
        f"WITH DATATYPE=DOUBLE, ENCODING=GORILLA"
    )

    try:
        session.execute_non_query_statement(sql)
    except Exception:
        pass

# label

try:
    session.execute_non_query_statement(
        f"CREATE TIMESERIES {DEVICE}.label "
        f"WITH DATATYPE=INT32, ENCODING=RLE"
    )
except Exception:
    pass

print("Timeseries ready.")

# ============================================
# 准备 Tablet
# ============================================

measurements = [f"s{i}" for i in range(num_cols)]
measurements.append("label")

data_types = [TSDataType.DOUBLE] * num_cols
data_types.append(TSDataType.INT32)

# ============================================
# 批量写入
# ============================================

print("Start inserting data...")

timestamps = []
values = []

inserted = 0

for t in range(num_rows):

    timestamps.append(t)

    row = [float(x) for x in test_data[t]]
    row.append(int(labels[t]))

    values.append(row)

    # batch insert
    if len(timestamps) >= BATCH_SIZE:

        tablet = Tablet(
            DEVICE,
            measurements,
            data_types,
            values,
            timestamps
        )

        session.insert_tablet(tablet)

        inserted += len(timestamps)

        print(f"Inserted {inserted}/{num_rows}")

        # clear batch
        timestamps = []
        values = []

# ============================================
# 插入剩余数据
# ============================================

if len(timestamps) > 0:

    tablet = Tablet(
        DEVICE,
        measurements,
        data_types,
        values,
        timestamps
    )

    session.insert_tablet(tablet)

    inserted += len(timestamps)

    print(f"Inserted {inserted}/{num_rows}")

# ============================================
# 关闭 Session
# ============================================

session.close()

print("Import finished.")