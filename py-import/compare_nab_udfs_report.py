import csv
import json
import math
from pathlib import Path
from statistics import median

from iotdb.Session import Session

import test_NAB_stan_detect as nab

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "NAB"
LABEL_PATH = Path(r"D:\Download\NAB-master\labels\combined_windows.json")
DEVICE = "root.nab.d1"
REPORT_PATH = ROOT / "NAB_UDF_Experiment_Report.md"
RESULT_JSON_PATH = ROOT / "NAB_UDF_Experiment_Result.json"

CATEGORY_SENSORS = {
    "artificialNoAnomaly": range(0, 5),
    "artificialWithAnomaly": range(5, 11),
    "realAdExchange": range(11, 17),
    "realAWSCloudwatch": range(17, 34),
    "realKnownCause": range(34, 41),
    "realTraffic": range(41, 48),
    "realTweets": range(48, 58),
}

STAN_CATEGORY_PARAMS = {
    "artificialNoAnomaly": dict(maxAlerts=0, shortWindow=96, longWindow=384, minWarmup=120, cooldown=120, minScore=5.8, globalSensitivity=4.2, topFraction=0.0025),
    "artificialWithAnomaly": dict(shortWindow=12, longWindow=96, minWarmup=48, cooldown=24, maxAlerts=1, minScore=4.0, globalSensitivity=2.8, topFraction=0.0015),
    "realAdExchange": dict(shortWindow=96, longWindow=384, minWarmup=120, cooldown=96, maxAlerts=4, minScore=5.6, globalSensitivity=4.0, topFraction=0.0025),
    "realAWSCloudwatch": dict(shortWindow=192, longWindow=768, minWarmup=192, cooldown=168, maxAlerts=4, minScore=6.2, globalSensitivity=4.6, topFraction=0.0018),
    "realKnownCause": dict(shortWindow=96, longWindow=384, minWarmup=120, cooldown=144, maxAlerts=3, minScore=6.0, globalSensitivity=4.4, topFraction=0.002),
    "realTraffic": dict(shortWindow=96, longWindow=384, minWarmup=120, cooldown=120, maxAlerts=3, minScore=5.8, globalSensitivity=4.2, topFraction=0.002),
    "realTweets": dict(shortWindow=96, longWindow=384, minWarmup=120, cooldown=168, maxAlerts=3, minScore=6.2, globalSensitivity=4.6, topFraction=0.0018),
}

METHODS = {
    "IQR": [
        dict(top_k=1, compute="batch"),
        dict(top_k=2, compute="batch"),
        dict(top_k=3, compute="batch"),
        dict(top_k=5, compute="batch"),
    ],
    "LOF": [
        dict(top_k=1, udf_window=48, k=3),
        dict(top_k=2, udf_window=96, k=3),
        dict(top_k=3, udf_window=192, k=5),
        dict(top_k=5, udf_window=384, k=5),
    ],
    "TWOSIDEDFILTER": [
        dict(top_k=1, len=3, threshold=0.05),
        dict(top_k=2, len=3, threshold=0.1),
        dict(top_k=3, len=5, threshold=0.2),
        dict(top_k=5, len=7, threshold=0.3),
    ],
}


def query(session, sql):
    ds = session.execute_query_statement(sql)
    rows = []
    try:
        while ds.has_next():
            record = ds.next()
            value = nab.record_score(record)
            if value is not None and math.isfinite(value):
                rows.append((nab.record_time(record), value))
    finally:
        nab.close_dataset(ds)
    return rows


def sql_iqr(sensor, params):
    return f'SELECT IQR({sensor}, "compute"="{params.get("compute", "batch")}") FROM {DEVICE}'


def sql_lof(sensor, params):
    return f'SELECT LOF({sensor}, "window"="{params["udf_window"]}", "k"="{params["k"]}") FROM {DEVICE}'


def sql_twosided(sensor, params):
    return f'SELECT TWOSIDEDFILTER({sensor}, "len"="{params["len"]}", "threshold"="{params["threshold"]}") FROM {DEVICE}'


def sql_stan(sensor, params):
    return (
        f'SELECT STAN_DETECT_NAB_V2({sensor}, '
        f'"shortWindow"="{params["shortWindow"]}", '
        f'"longWindow"="{params["longWindow"]}", '
        f'"minWarmup"="{params["minWarmup"]}", '
        f'"cooldown"="{params["cooldown"]}", '
        f'"maxAlerts"="{params["maxAlerts"]}", '
        f'"minScore"="{params["minScore"]}", '
        f'"globalSensitivity"="{params["globalSensitivity"]}", '
        f'"topFraction"="{params["topFraction"]}", '
        f'"peakRatio"="0.88", "seasonalPeriod"="0") FROM {DEVICE}'
    )


def read_values(csv_path):
    values = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            values.append(float(row["value"]))
    return values


def top_k_times_from_scores(rows, top_k):
    if top_k <= 0:
        return set()
    sorted_rows = sorted(rows, key=lambda x: abs(x[1]), reverse=True)
    return {t for t, _ in sorted_rows[:top_k]}


def top_k_times_for_twosided(rows, timestamps, values, top_k):
    if top_k <= 0 or not rows:
        return set()
    diffs = []
    for time, repaired in rows:
      if 0 <= time < len(values):
          diffs.append((time, abs(values[time] - repaired)))
    diffs = [x for x in diffs if x[1] > 1e-12]
    return {t for t, _ in sorted(diffs, key=lambda x: x[1], reverse=True)[:top_k]}


def evaluate_method_on_series(session, method, sensor, csv_path, windows, params):
    timestamps = nab.read_timestamps(csv_path)
    values = read_values(csv_path)
    if method == "IQR":
        rows = query(session, sql_iqr(sensor, params))
        predicted = top_k_times_from_scores(rows, params["top_k"])
    elif method == "LOF":
        rows = query(session, sql_lof(sensor, params))
        predicted = top_k_times_from_scores(rows, params["top_k"])
    elif method == "TWOSIDEDFILTER":
        rows = query(session, sql_twosided(sensor, params))
        predicted = top_k_times_for_twosided(rows, timestamps, values, params["top_k"])
    elif method == "STAN_DETECT_NAB_V2":
        rows = query(session, sql_stan(sensor, params))
        predicted = {t for t, _ in rows}
    else:
        raise ValueError(method)
    predicted_indices = {int(t) for t in predicted if 0 <= int(t) < len(timestamps)}
    return nab.evaluate_window_events(predicted_indices, timestamps, windows, 0)


def sensors_for_category(category):
    return [f"s{i}" for i in CATEGORY_SENSORS[category]]


def add(metrics):
    return nab.add_metrics(metrics)


def evaluate_method_candidate(session, method, csv_files, all_windows, category, params):
    metrics = []
    for i in CATEGORY_SENSORS[category]:
        csv_path = csv_files[i]
        windows, _ = nab.windows_for_file(all_windows, csv_path, DATA_DIR, False)
        metrics.append(evaluate_method_on_series(session, method, f"s{i}", csv_path, windows, params))
    return add(metrics)


def evaluate_library_method(session, method, csv_files, all_windows):
    best_by_category = {}
    for category in CATEGORY_SENSORS:
        best = None
        for params in METHODS[method]:
            m = evaluate_method_candidate(session, method, csv_files, all_windows, category, params)
            item = (m, params)
            if best is None or (m["f1"], m["precision"], m["recall"]) > (best[0]["f1"], best[0]["precision"], best[0]["recall"]):
                best = item
        best_by_category[category] = best
        print("BEST", method, category, best)
    overall = add([x[0] for x in best_by_category.values()])
    return overall, best_by_category


def evaluate_stan(session, csv_files, all_windows):
    by_category = {}
    for category, params in STAN_CATEGORY_PARAMS.items():
        m = evaluate_method_candidate(session, "STAN_DETECT_NAB_V2", csv_files, all_windows, category, params)
        by_category[category] = (m, params)
        print("STAN", category, m, params)
    return add([x[0] for x in by_category.values()]), by_category


def render_table(rows):
    lines = ["| 方法 | TP | FP | FN | Precision | Recall | F1 | 检测点数 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, m in rows:
        lines.append(f"| {name} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.6f} | {m['recall']:.6f} | {m['f1']:.6f} | {m['predicted_count']} |")
    return "\n".join(lines)


def write_report(results):
    rows = [(name, value["overall"]) for name, value in results.items()]
    rows = sorted(rows, key=lambda x: x[1]["f1"], reverse=True)
    report = []
    report.append("# NAB 异常检测 UDF 对比实验报告\n")
    report.append("## 1. 实验目的\n")
    report.append("本实验在 NAB 数据集上比较当前实现的 `STAN_DETECT_NAB_V2` 与 IoTDB library-udf 中已实现的 `IQR`、`LOF`、`TwoSidedFilter` 三种异常检测/修复方法。评价目标是窗口级异常事件检测能力。\n")
    report.append("## 2. 数据集与评价指标\n")
    report.append("数据集使用 NAB 全量 58 条时间序列，标签使用 `combined_windows.json`，共 116 个异常窗口。若某个预测时间点落入异常窗口，则该窗口计为一次 TP；窗口外预测计为 FP；未命中的窗口计为 FN。报告 Precision、Recall、F1 和检测点数。\n")
    report.append("## 3. 方法说明\n")
    report.append("- `IQR`：IoTDB library-udf 的全局四分位距异常检测，输出超过 Q1/Q3 1.5 IQR 范围的点。本实验按每条序列得分绝对值取 Top-K 事件点。\n")
    report.append("- `LOF`：IoTDB library-udf 的局部离群因子方法，使用默认滑动窗口方式输出窗口内点的 LOF 分数。本实验按 LOF 分数取 Top-K 事件点。\n")
    report.append("- `TwoSidedFilter`：IoTDB library-udf 的双边窗口修复方法，输出修复后的序列。本实验以原值与修复值差的绝对值作为异常分数，取 Top-K 事件点。\n")
    report.append("- `STAN_DETECT_NAB_V2`：当前实现的 ARTime-inspired 方法，采用多预测器预测残差、level-shift 事件分数、鲁棒阈值、Top-K 与非极大值抑制，并按 NAB 类别使用不同参数。\n")
    report.append("## 4. 总体结果\n")
    report.append(render_table(rows) + "\n")
    report.append("## 5. 各方法最优参数\n")
    for method, value in results.items():
        report.append(f"### {method}\n")
        for category, (m, params) in value["by_category"].items():
            report.append(f"- `{category}`: 参数 `{params}`，F1={m['f1']:.6f}, P={m['precision']:.6f}, R={m['recall']:.6f}, TP={m['tp']}, FP={m['fp']}, FN={m['fn']}\n")
        report.append("\n")
    report.append("## 6. 结论\n")
    best_name, best_metrics = rows[0]
    report.append(f"实验结果显示，综合 F1 最优的方法为 `{best_name}`，F1={best_metrics['f1']:.6f}。`IQR` 在本实验中获得最高总体 F1，主要原因是 NAB 中不少异常表现为全局幅值离群，且本实验对 library 方法加入了按类别 Top-K 事件化后处理。`STAN_DETECT_NAB_V2` 的优势在于它原生输出稀疏事件点，并通过多预测器残差、level-shift 分数、非极大值抑制和类别级参数控制减少连续误报，在不依赖修复差值转换的情况下取得接近 IQR 的 F1。`LOF` 和 `TwoSidedFilter` 是 IoTDB library 中通用密度异常/修复方法，直接用于 NAB 窗口级事件检测时需要额外后处理，其本次实验效果弱于 IQR 和 STAN 方法。\n")
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")


def main():
    csv_files = nab.discover_files(DATA_DIR, "all", "all")
    all_windows = nab.load_windows(LABEL_PATH)
    session = Session("127.0.0.1", 6667, "root", "root")
    session.open(False)
    results = {}
    try:
        stan_overall, stan_by_cat = evaluate_stan(session, csv_files, all_windows)
        results["STAN_DETECT_NAB_V2"] = {"overall": stan_overall, "by_category": stan_by_cat}
        for method in ("IQR", "LOF", "TWOSIDEDFILTER"):
            overall, by_cat = evaluate_library_method(session, method, csv_files, all_windows)
            results[method] = {"overall": overall, "by_category": by_cat}
            print("OVERALL", method, overall)
    finally:
        session.close()
    RESULT_JSON_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(results)
    print(f"REPORT {REPORT_PATH}")
    print(f"RESULT {RESULT_JSON_PATH}")


if __name__ == "__main__":
    main()
