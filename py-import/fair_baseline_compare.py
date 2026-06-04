import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

from iotdb.Session import Session

import test_NAB_stan_detect as nab

ROOT = Path(__file__).resolve().parents[1]
NAB_DATA_DIR = Path(__file__).resolve().parent / "dataset" / "NAB"
NAB_LABEL_PATH = Path(r"D:\Download\NAB-master\labels\combined_windows.json")
YAHOO_DATA_DIR = Path(__file__).resolve().parent / "dataset" / "Yahoo_S5_Data"
OUT_PATH = ROOT / "Baseline_Fair_Comparison_Result.json"
REPORT_PATH = ROOT / "Baseline_Fair_Comparison_Report.md"

STAN_PARAMS_NAB = dict(shortWindow=96, longWindow=384, minWarmup=120, cooldown=120, maxAlerts=12, minScore=5.0, globalSensitivity=3.5, topFraction=0.004, peakRatio=0.88, minSeriesVariability=1e-8, trendWeight=0.18, eventWeight=0.42, forecastWeight=0.40, globalPercentileFloor=0.985)
STAN_PARAMS_YAHOO = dict(shortWindow=48, longWindow=192, minWarmup=96, cooldown=24, maxAlerts=50, minScore=3.8, globalSensitivity=3.0, topFraction=0.02, peakRatio=0.85, minSeriesVariability=1e-8, trendWeight=0.22, eventWeight=0.40, forecastWeight=0.38, globalPercentileFloor=0.98)

DEFAULT_TARGET = "yahoo"

BASELINE_GRIDS = {
    "IQR": [dict(compute="batch")],
    "LOF": [dict(window=48, k=3), dict(window=96, k=3), dict(window=192, k=5)],
    "TWOSIDEDFILTER": [dict(len=3, threshold=0.05), dict(len=5, threshold=0.1), dict(len=7, threshold=0.2)],
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", choices=["yahoo", "nab"], default=DEFAULT_TARGET)
    p.add_argument("--yahoo-files", default="A1Benchmark/real_1.csv")
    p.add_argument("--nab-label-path", type=Path, default=NAB_LABEL_PATH)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6667)
    p.add_argument("--user", default="root")
    p.add_argument("--password", default="root")
    return p.parse_args()


def close_dataset(dataset):
    nab.close_dataset(dataset)


def record_value(record):
    return nab.record_score(record)


def query_rows(session, sql):
    ds = session.execute_query_statement(sql)
    rows = []
    try:
        while ds.has_next():
            r = ds.next()
            v = record_value(r)
            if v is not None and math.isfinite(v):
                rows.append((nab.record_time(r), float(v)))
    finally:
        close_dataset(ds)
    return rows


def read_csv_values_labels(path):
    values, labels, timestamps = [], [], []
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        fields = reader.fieldnames or []
        label_col = "is_anomaly" if "is_anomaly" in fields else "label" if "label" in fields else None
        for idx, row in enumerate(reader):
            timestamps.append(int(float(row.get("timestamp", idx))))
            values.append(float(row["value"]))
            labels.append(int(float(row[label_col])) if label_col else 0)
    return timestamps, values, labels


def sql_stan(device, sensor, params):
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
        f'"peakRatio"="{params["peakRatio"]}", '
        f'"minSeriesVariability"="{params["minSeriesVariability"]}") FROM {device}'
    )


def sql_iqr(device, sensor, params):
    return f'SELECT IQR({sensor}, "compute"="{params.get("compute", "batch")}") FROM {device}'


def sql_lof(device, sensor, params):
    return f'SELECT LOF({sensor}, "window"="{params["window"]}", "k"="{params["k"]}") FROM {device}'


def sql_twosided(device, sensor, params):
    return f'SELECT TWOSIDEDFILTER({sensor}, "len"="{params["len"]}", "threshold"="{params["threshold"]}") FROM {device}'


def top_k_by_score(rows, k):
    if k <= 0:
        return set()
    return {int(t) for t, _ in sorted(rows, key=lambda x: abs(x[1]), reverse=True)[:k]}


def top_k_twosided(rows, values, k, timestamps=None):
    scored = []
    time_to_index = {int(t): i for i, t in enumerate(timestamps)} if timestamps is not None else None
    for t, repaired in rows:
        idx = time_to_index.get(int(t)) if time_to_index is not None else int(t)
        if idx is not None and 0 <= idx < len(values):
            scored.append((int(t), abs(values[idx] - repaired)))
    return top_k_by_score(scored, k)


def map_times_to_indices(predicted_times, timestamps):
    time_to_index = {int(t): i for i, t in enumerate(timestamps)}
    return {time_to_index[int(t)] for t in predicted_times if int(t) in time_to_index}


def point_metrics(predicted, labels):
    truth = {i for i, x in enumerate(labels) if x == 1}
    predicted = {i for i in predicted if 0 <= i < len(labels)}
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(tp=tp, fp=fp, fn=fn, precision=precision, recall=recall, f1=f1, label_count=len(truth), predicted_count=len(predicted))


def add_metrics(metrics):
    return nab.add_metrics(metrics)


def eval_baseline_grid(session, dataset_name, device, sensor, values, labels_or_windows, timestamps, method, k, is_nab):
    best = None
    for params in BASELINE_GRIDS[method]:
        if method == "IQR":
            rows = query_rows(session, sql_iqr(device, sensor, params))
            predicted = top_k_by_score(rows, k)
        elif method == "LOF":
            rows = query_rows(session, sql_lof(device, sensor, params))
            predicted = top_k_by_score(rows, k)
        elif method == "TWOSIDEDFILTER":
            rows = query_rows(session, sql_twosided(device, sensor, params))
            predicted = top_k_twosided(rows, values, k, timestamps if not is_nab else None)
        else:
            raise ValueError(method)
        if is_nab:
            metrics = nab.evaluate_window_events(predicted, timestamps, labels_or_windows, 0)
        else:
            metrics = point_metrics(map_times_to_indices(predicted, timestamps), labels_or_windows)
        item = dict(method=method, params=params, metrics=metrics)
        if best is None or (metrics["f1"], metrics["precision"], metrics["recall"]) > (best["metrics"]["f1"], best["metrics"]["precision"], best["metrics"]["recall"]):
            best = item
    return best


def eval_stan(session, device, sensor, labels_or_windows, timestamps, params, is_nab, k=None):
    rows = query_rows(session, sql_stan(device, sensor, params))
    predicted = top_k_by_score(rows, k) if k is not None else {int(t) for t, _ in rows}
    if is_nab:
        metrics = nab.evaluate_window_events(predicted, timestamps, labels_or_windows, 0)
    else:
        metrics = point_metrics(map_times_to_indices(predicted, timestamps), labels_or_windows)
    return dict(method="STAN_DETECT_NAB_V2", params=params, metrics=metrics)


def evaluate_nab(session, label_path):
    csv_files = nab.discover_files(NAB_DATA_DIR, "all", "all")
    windows_all = nab.load_windows(label_path)
    per_method = {m: [] for m in ["STAN_DETECT_NAB_V2", "IQR", "LOF", "TWOSIDEDFILTER"]}
    per_series = []
    for i, csv_path in enumerate(csv_files):
        sensor = f"s{i}"
        rel = csv_path.relative_to(NAB_DATA_DIR).as_posix()
        timestamps_dt = nab.read_timestamps(csv_path)
        values = [float(row["value"]) for row in csv.DictReader(csv_path.open("r", encoding="utf-8", newline=""))]
        windows, _ = nab.windows_for_file(windows_all, csv_path, NAB_DATA_DIR, False)
        k = max(1, len(windows)) if windows else 0
        stan = eval_stan(session, "root.nab.d1", sensor, windows, timestamps_dt, STAN_PARAMS_NAB, True)
        per_method["STAN_DETECT_NAB_V2"].append(stan["metrics"])
        row = dict(series=rel, labels=len(windows), results={"STAN_DETECT_NAB_V2": stan})
        for method in ["IQR", "LOF", "TWOSIDEDFILTER"]:
            best = eval_baseline_grid(session, "NAB", "root.nab.d1", sensor, values, windows, timestamps_dt, method, k, True)
            per_method[method].append(best["metrics"])
            row["results"][method] = best
        per_series.append(row)
        print("NAB", i + 1, len(csv_files), rel, {m: row["results"][m]["metrics"]["f1"] for m in row["results"]})
    overall = {method: add_metrics(ms) for method, ms in per_method.items()}
    return dict(overall=overall, per_series=per_series)


def yahoo_device_for(rel):
    stem = Path(rel).stem.replace("-", "_")
    if rel.startswith("A1Benchmark/"):
        return f"root.yahoo.{stem}"
    return f"root.yahoo.{stem}"


def evaluate_yahoo(session, yahoo_files):
    per_method = {m: [] for m in ["STAN_DETECT_NAB_V2", "IQR", "LOF", "TWOSIDEDFILTER"]}
    per_series = []
    for rel in yahoo_files:
        csv_path = YAHOO_DATA_DIR / rel
        timestamps, values, labels = read_csv_values_labels(csv_path)
        k = sum(1 for x in labels if x == 1)
        if k <= 0:
            continue
        device = yahoo_device_for(rel)
        sensor = "value"
        stan = eval_stan(session, device, sensor, labels, timestamps, STAN_PARAMS_YAHOO, False)
        per_method["STAN_DETECT_NAB_V2"].append(stan["metrics"])
        row = dict(series=rel, labels=k, device=device, results={"STAN_DETECT_NAB_V2": stan})
        for method in ["IQR", "LOF", "TWOSIDEDFILTER"]:
            best = eval_baseline_grid(session, "Yahoo", device, sensor, values, labels, timestamps, method, k, False)
            per_method[method].append(best["metrics"])
            row["results"][method] = best
        per_series.append(row)
        print("YAHOO", rel, {m: row["results"][m]["metrics"]["f1"] for m in row["results"]})
    overall = {method: add_metrics(ms) for method, ms in per_method.items() if ms}
    return dict(overall=overall, per_series=per_series)


def render_table(overall):
    lines = ["| 方法 | TP | FP | FN | Precision | Recall | F1 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for method, m in sorted(overall.items(), key=lambda x: x[1]["f1"], reverse=True):
        lines.append(f"| {method} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.6f} | {m['recall']:.6f} | {m['f1']:.6f} |")
    return "\n".join(lines)


def write_report(results):
    parts = ["# Fair Baseline Comparison\n"]
    for dataset, result in results.items():
        parts.append(f"## {dataset}\n")
        parts.append(render_table(result["overall"]) + "\n")
    REPORT_PATH.write_text("\n".join(parts), encoding="utf-8")


def main():
    args = parse_args()
    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    results = {}
    try:
        datasets = {x.strip().lower() for x in args.datasets.split(",") if x.strip()}
        if "nab" in datasets:
            results["NAB"] = evaluate_nab(session, args.nab_label_path)
        if "yahoo" in datasets:
            yahoo_files = [x.strip().replace("\\", "/") for x in args.yahoo_files.split(",") if x.strip()]
            results["Yahoo"] = evaluate_yahoo(session, yahoo_files)
    finally:
        session.close()
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(results)
    print(f"RESULT {OUT_PATH}")
    print(f"REPORT {REPORT_PATH}")


if __name__ == "__main__":
    main()
