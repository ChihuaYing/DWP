import argparse
import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parent / "dataset" / "Yahoo_S5_Data"

BENCHMARK_DIRS = {
    "A1Benchmark": "a1",
    "A2Benchmark": "a2",
    "A3Benchmark": "a3",
    "A4Benchmark": "a4",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze STAN NAB V3 automatic seasonal period estimation on Yahoo S5.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--benchmarks", default="all", help="all or A1,A2,A3,A4")
    parser.add_argument("--files", default="all", help="all or csv stems/names")
    parser.add_argument("--window", type=int, default=150)
    parser.add_argument("--seasonal-lookback", type=int, default=7)
    parser.add_argument("--min-seasonal-samples", type=int, default=3)
    parser.add_argument("--auto-seasonal-min-period", type=int, default=20)
    parser.add_argument("--auto-seasonal-max-period", type=int, default=512)
    parser.add_argument("--auto-seasonal-min-correlation", type=float, default=0.75)
    parser.add_argument("--auto-seasonal-recompute-interval", type=int, default=64)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--show-series", action="store_true")
    return parser.parse_args()


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


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


def discover_yahoo_files(data_dir, benchmarks_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"Yahoo S5 data directory not found: {data_dir}")

    benchmark_filters = parse_benchmark_filter(benchmarks_arg)
    file_filters = parse_file_filter(files_arg)
    result = []

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
                result.append((benchmark_key, csv_path))

    if not result:
        raise ValueError("No Yahoo S5 csv files matched filters.")
    return result


def read_values(csv_path):
    values = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if reader.fieldnames is None or "value" not in reader.fieldnames:
            raise ValueError(f"Expected value column in {csv_path}")
        for row in reader:
            value = float(row["value"])
            if math.isfinite(value):
                values.append(value)
    return values


def mean(values):
    avg = 0.0
    for index, value in enumerate(values, start=1):
        avg += (value - avg) / index
    return avg


def autocorrelation(values, avg, lag):
    numerator = 0.0
    left_denominator = 0.0
    right_denominator = 0.0
    for index in range(lag, len(values)):
        left = values[index] - avg
        right = values[index - lag] - avg
        numerator += left * right
        left_denominator += left * left
        right_denominator += right * right
    denominator = math.sqrt(left_denominator * right_denominator)
    return 0.0 if denominator <= 1e-12 else numerator / denominator


def estimate_period(history, args):
    required_history = args.auto_seasonal_max_period * max(1, args.min_seasonal_samples)
    if len(history) < required_history:
        return 0, None, 0, None

    max_lag = min(args.auto_seasonal_max_period, len(history) // max(1, args.min_seasonal_samples))
    if max_lag < args.auto_seasonal_min_period:
        return 0, None, max_lag, None

    avg = mean(history)
    centered = np.asarray(history, dtype=float) - avg
    best_lag = 0
    best_correlation = -math.inf
    for lag in range(args.auto_seasonal_min_period, max_lag + 1):
        correlation = autocorrelation_centered(centered, lag)
        if correlation > best_correlation:
            best_correlation = correlation
            best_lag = lag

    accepted = best_lag if best_lag > 0 and best_correlation >= args.auto_seasonal_min_correlation else 0
    return accepted, best_lag, max_lag, best_correlation


def autocorrelation_centered(centered, lag):
    left = centered[lag:]
    right = centered[:-lag]
    numerator = float(np.dot(left, right))
    left_denominator = float(np.dot(left, left))
    right_denominator = float(np.dot(right, right))
    denominator = math.sqrt(left_denominator * right_denominator)
    return 0.0 if denominator <= 1e-12 else numerator / denominator


def analyze_values(values, args):
    history_capacity = max(args.window, args.auto_seasonal_max_period * args.seasonal_lookback)
    history = []
    rows_seen = 0
    estimated_period = 0
    last_estimate_at = -10**18
    calls = []

    for current in values:
        if len(history) >= args.window:
            reused = False
            if estimated_period > 0 and rows_seen - last_estimate_at < args.auto_seasonal_recompute_interval:
                accepted = estimated_period
                best_lag = estimated_period
                best_correlation = None
                max_lag = min(args.auto_seasonal_max_period, len(history) // max(1, args.min_seasonal_samples))
                reused = True
            else:
                accepted, best_lag, max_lag, best_correlation = estimate_period(history, args)
                estimated_period = accepted
                last_estimate_at = rows_seen

            calls.append(
                {
                    "row": rows_seen,
                    "accepted": accepted,
                    "best_lag": best_lag,
                    "max_lag": max_lag,
                    "best_correlation": best_correlation,
                    "reused": reused,
                }
            )

        history.append(current)
        if len(history) > history_capacity:
            history.pop(0)
        rows_seen += 1

    return calls


def summarize_series(calls):
    computed = [call for call in calls if call["best_lag"] is not None and not call["reused"]]
    accepted = [call for call in calls if call["accepted"] > 0]
    accepted_computed = [call for call in computed if call["accepted"] > 0]
    final_computed = computed[-1] if computed else None
    final_accepted = accepted[-1]["accepted"] if accepted else 0
    first_accepted = accepted[0] if accepted else None
    return {
        "computed_calls": len(computed),
        "accepted_calls": len(accepted),
        "accepted_computed_calls": len(accepted_computed),
        "final_best_lag": final_computed["best_lag"] if final_computed else None,
        "final_best_correlation": final_computed["best_correlation"] if final_computed else None,
        "final_accepted": final_accepted,
        "first_accepted_row": first_accepted["row"] if first_accepted else None,
    }


def print_counter(title, counter, top_k):
    print(title)
    if not counter:
        print("  (none)")
        return
    for value, count in counter.most_common(top_k):
        print(f"  {value}: {count}")


def main():
    args = parse_args()
    yahoo_files = discover_yahoo_files(args.data_dir, args.benchmarks, args.files)
    print(f"Discovered {len(yahoo_files)} Yahoo S5 csv files.")
    print(
        "Config: "
        f"window={args.window}, seasonalLookback={args.seasonal_lookback}, "
        f"minSeasonalSamples={args.min_seasonal_samples}, "
        f"autoSeasonalMinPeriod={args.auto_seasonal_min_period}, "
        f"autoSeasonalMaxPeriod={args.auto_seasonal_max_period}, "
        f"autoSeasonalMinCorrelation={args.auto_seasonal_min_correlation}, "
        f"autoSeasonalRecomputeInterval={args.auto_seasonal_recompute_interval}"
    )
    required_history = args.auto_seasonal_max_period * max(1, args.min_seasonal_samples)
    print(f"Required history before auto-period estimation starts: {required_history}")

    rows = []
    by_benchmark = defaultdict(list)
    for benchmark_key, csv_path in yahoo_files:
        values = read_values(csv_path)
        calls = analyze_values(values, args)
        summary = summarize_series(calls)
        relative_name = csv_path.relative_to(args.data_dir).as_posix()
        item = {
            "benchmark": benchmark_key,
            "file": relative_name,
            "points": len(values),
            **summary,
        }
        rows.append(item)
        by_benchmark[benchmark_key].append(item)

    print("\n========== Overall Period Summary ==========")
    no_estimation = sum(1 for item in rows if item["computed_calls"] == 0)
    no_accepted = sum(1 for item in rows if item["final_accepted"] == 0)
    print(f"series: {len(rows)}")
    print(f"series_without_any_estimation_call: {no_estimation}")
    print(f"series_without_final_accepted_period: {no_accepted}")

    print_counter(
        "Final accepted periods:",
        Counter(item["final_accepted"] for item in rows if item["final_accepted"] > 0),
        args.top_k,
    )
    print_counter(
        "Final raw best lags before correlation threshold:",
        Counter(item["final_best_lag"] for item in rows if item["final_best_lag"] is not None),
        args.top_k,
    )

    print("\n========== By Benchmark ==========")
    for benchmark_key in sorted(by_benchmark):
        items = by_benchmark[benchmark_key]
        print(f"{benchmark_key.upper()}: series={len(items)}")
        print(f"  no_estimation={sum(1 for item in items if item['computed_calls'] == 0)}")
        print(f"  no_final_accepted={sum(1 for item in items if item['final_accepted'] == 0)}")
        accepted_counter = Counter(item["final_accepted"] for item in items if item["final_accepted"] > 0)
        raw_counter = Counter(item["final_best_lag"] for item in items if item["final_best_lag"] is not None)
        print(f"  accepted_top={accepted_counter.most_common(5)}")
        print(f"  raw_best_top={raw_counter.most_common(5)}")

    if args.show_series:
        print("\n========== Per Series ==========")
        for item in rows:
            corr = item["final_best_correlation"]
            corr_text = "None" if corr is None else f"{corr:.4f}"
            print(
                f"{item['file']}: points={item['points']}, "
                f"computed_calls={item['computed_calls']}, "
                f"accepted_calls={item['accepted_calls']}, "
                f"first_accepted_row={item['first_accepted_row']}, "
                f"final_accepted={item['final_accepted']}, "
                f"final_best_lag={item['final_best_lag']}, "
                f"final_best_corr={corr_text}"
            )


if __name__ == "__main__":
    main()
