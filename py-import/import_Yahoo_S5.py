import pandas as pd

from iotdb.Session import Session
from iotdb.utils.IoTDBConstants import TSDataType
from iotdb.utils.Tablet import Tablet

# ============================================
# 配置
# ============================================

CSV_PATH = "./dataset/Yahoo_S5_Data/A1Benchmark/real_2.csv"

DATABASE = "root.yahoo"
DEVICE = "root.yahoo.real_2"

IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

BATCH_SIZE = 1000

# ============================================
# 读取 CSV
# ============================================

print("Loading CSV...")

df = pd.read_csv(CSV_PATH)

print(df.head())

# 自动兼容字段名
if "is_anomaly" in df.columns:
    label_col = "is_anomaly"
elif "label" in df.columns:
    label_col = "label"
else:
    raise Exception("No anomaly label column found.")

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
except Exception:
    pass

# ============================================
# 创建 Timeseries
# ============================================

try:
    session.execute_non_query_statement(
        f"""
        CREATE TIMESERIES {DEVICE}.value
        WITH DATATYPE=DOUBLE, ENCODING=GORILLA
        """
    )
except Exception:
    pass

try:
    session.execute_non_query_statement(
        f"""
        CREATE TIMESERIES {DEVICE}.label
        WITH DATATYPE=INT32, ENCODING=RLE
        """
    )
except Exception:
    pass

print("Timeseries ready.")

# ============================================
# 准备 Tablet
# ============================================

measurements = ["value", "label"]

data_types = [
    TSDataType.DOUBLE,
    TSDataType.INT32
]

timestamps = []
values = []

inserted = 0

# ============================================
# 插入数据
# ============================================

print("Start inserting...")

for idx, row in df.iterrows():

    # timestamp
    if "timestamp" in df.columns:
        t = int(row["timestamp"])
    else:
        t = idx

    timestamps.append(t)

    # value
    value = float(row["value"])

    # label
    label = int(row[label_col])

    values.append([
        value,
        label
    ])

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

        print(f"Inserted {inserted}")

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

    print(f"Inserted {inserted}")

# ============================================
# 关闭 Session
# ============================================

session.close()

print("Import finished.")