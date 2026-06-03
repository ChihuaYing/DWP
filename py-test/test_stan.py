import os
import sys

import matplotlib
import numpy as np
import pandas as pd

# Use a non-interactive backend so the script can run headless and still save figures.
matplotlib.use("Agg")
import matplotlib.pyplot as plt


CSV_PATH = os.path.join("..", "dataset", "Yahoo_S5_Data", "A1Benchmark", "real_1.csv")
EPS = 1e-12
EMA_ALPHA = 0.05
DEVIATION_WEIGHT = 0.70
TEMPORAL_WEIGHT = 0.30


def read_float_from_stdin(default=3.0):
	"""Read one numeric value from stdin; fall back to default when missing/invalid."""
	try:
		line = sys.stdin.readline()
		if not line:
			return float(default)
		line = line.strip()
		if not line:
			return float(default)
		return float(line)
	except Exception:
		return float(default)


def resolve_csv_path():
	csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), CSV_PATH))
	if os.path.exists(csv_path):
		return csv_path
	return os.path.abspath(os.path.join(os.getcwd(), "dataset", "Yahoo_S5_Data", "A1Benchmark", "real_1.csv"))


def calculate_window_stats(values):
	"""Mirror SeasonalStanUDTF.calculateWindowStats()."""
	mean = 0.0
	m2 = 0.0
	n = len(values)

	for i, value in enumerate(values):
		delta = value - mean
		mean += delta / (i + 1)
		m2 += delta * (value - mean)

	variance = max(m2 / n, EPS)
	std = np.sqrt(variance)

	acf_numerator = 0.0
	acf_denominator = 0.0
	for i in range(n - 1):
		current_diff = values[i] - mean
		next_diff = values[i + 1] - mean
		acf_numerator += current_diff * next_diff
		acf_denominator += current_diff * current_diff

	acf1 = 0.0 if acf_denominator < EPS else acf_numerator / acf_denominator
	acf1 = max(-1.0, min(1.0, acf1))
	return mean, std, acf1


def calculate_stats_on_array(values):
	"""Mirror SeasonalStanUDTF.calculateStatsOnArray()."""
	mean = 0.0
	m2 = 0.0
	n = len(values)

	for i, value in enumerate(values):
		delta = value - mean
		mean += delta / (i + 1)
		m2 += delta * (value - mean)

	variance = max(m2 / n, EPS)
	std = np.sqrt(variance)

	acf_numerator = 0.0
	acf_denominator = 0.0
	for i in range(n - 1):
		current_diff = values[i] - mean
		next_diff = values[i + 1] - mean
		acf_numerator += current_diff * next_diff
		acf_denominator += current_diff * current_diff

	acf1 = 0.0 if acf_denominator < EPS else acf_numerator / acf_denominator
	acf1 = max(-1.0, min(1.0, acf1))
	return mean, std, acf1


def seasonal_stan_anomalies(values, window_size=100, sensitivity=3.0, min_threshold=3.0):
	"""Pure Python port of SeasonalStanUDTF.java.

	Returns:
	  anomalies: list of (timestamp, value, raw_score)
	  raw_scores: list of raw_score for each processed point after warmup
	  thresholds: list of dynamic thresholds used for each processed point after warmup
	"""
	buffer = [0.0] * window_size
	head = 0
	count = 0
	ema_score = 0.0
	threshold_initialized = False

	anomalies = []
	raw_scores = []
	thresholds = []

	def ordered_value(offset):
		return buffer[(head + offset) % window_size]

	for idx, current in enumerate(values):
		if count < window_size:
			buffer[head] = current
			head = (head + 1) % window_size
			count += 1
			continue

		window_values = [ordered_value(i) for i in range(window_size)]

		# Original score on raw data
		orig_mean, orig_std, orig_acf1 = calculate_window_stats(window_values)
		orig_deviation_score = abs(current - orig_mean) / orig_std
		orig_predicted = orig_mean + orig_acf1 * (window_values[-1] - orig_mean)
		orig_temporal_score = abs(current - orig_predicted) / orig_std
		orig_raw_score = DEVIATION_WEIGHT * orig_deviation_score + TEMPORAL_WEIGHT * orig_temporal_score

		# Detrended score: linear regression in window, then stats on residuals
		x = np.arange(window_size, dtype=float)
		y = np.asarray(window_values, dtype=float)

		sum_x = float(x.sum())
		sum_y = float(y.sum())
		sum_xy = float((x * y).sum())
		sum_xx = float((x * x).sum())
		denom = window_size * sum_xx - sum_x * sum_x

		slope = 0.0
		intercept = 0.0
		if denom > EPS:
			slope = (window_size * sum_xy - sum_x * sum_y) / denom
			intercept = (sum_y - slope * sum_x) / window_size

		residuals = [window_values[i] - (slope * i + intercept) for i in range(window_size)]
		det_mean, det_std, det_acf1 = calculate_stats_on_array(residuals)

		current_trend = slope * window_size + intercept
		current_residual = current - current_trend
		last_residual = window_values[-1] - (slope * (window_size - 1) + intercept)

		det_deviation_score = abs(current_residual - det_mean) / det_std
		det_predicted = det_mean + det_acf1 * (last_residual - det_mean)
		det_temporal_score = abs(current_residual - det_predicted) / det_std
		det_raw_score = DEVIATION_WEIGHT * det_deviation_score + TEMPORAL_WEIGHT * det_temporal_score

		raw_score = max(orig_raw_score, det_raw_score)

		if not threshold_initialized:
			ema_score = max(raw_score, min_threshold / sensitivity)
			threshold_initialized = True

		threshold = max(min_threshold, ema_score * sensitivity)
		anomaly = raw_score > threshold

		raw_scores.append(raw_score)
		thresholds.append(threshold)

		if anomaly:
			anomalies.append((idx, current, raw_score))
		else:
			ema_score = EMA_ALPHA * raw_score + (1.0 - EMA_ALPHA) * ema_score

		buffer[head] = current
		head = (head + 1) % window_size

	return anomalies, raw_scores, thresholds


def plot_histogram(values, thresholds, out_png):
	"""Plot histogram of original values and add dashed guide lines.

	SeasonalStan's threshold is dynamic and score-based, not a fixed value threshold.
	For visualization, this projects a representative score threshold back to the
	value axis using the data's median and standard deviation as a guide only.
	"""
	plt.figure(figsize=(8, 5))
	plt.hist(values.dropna(), bins=50, color="#4c72b0", alpha=0.8)

	median_value = float(values.median())
	threshold_score = float(np.median(thresholds)) if thresholds else 0.0
	value_std = float(values.std(ddof=0))
	if value_std <= 0 or np.isclose(value_std, 0.0):
		value_std = 1.0
	visual_half_width = threshold_score * value_std
	lower = median_value - visual_half_width
	upper = median_value + visual_half_width

	plt.axvline(lower, color="red", linestyle="--", linewidth=2, label=f"guide lower={lower:.4g}")
	plt.axvline(upper, color="red", linestyle="--", linewidth=2, label=f"guide upper={upper:.4g}")
	plt.title("Histogram of values with SeasonalStan guide lines")
	plt.xlabel("value")
	plt.ylabel("count")
	plt.legend()
	plt.tight_layout()
	plt.savefig(out_png, dpi=150)


def main():
	print("请输入 k（直接回车使用默认值 3.0）：", end="", flush=True)
	k = read_float_from_stdin(default=3.0)

	csv_path = resolve_csv_path()
	df = pd.read_csv(csv_path)

	if "value" not in df.columns:
		raise RuntimeError("CSV does not contain 'value' column")

	first100 = df.head(100)
	print("--- First 100 rows (or fewer) ---")
	print(first100.to_string(index=False))
	print(f"--- Number of rows in original data: {len(df)} ---")

	values = df["value"].astype(float)
	anomalies, raw_scores, thresholds = seasonal_stan_anomalies(
		values.tolist(),
		window_size=100,
		sensitivity=float(k),
		min_threshold=3.0,
	)

	print(f"\n--- SeasonalStan parameters: k={float(k)} ---")
	print("--- Algorithm: window=100, minThreshold=3.0, EMA alpha=0.05 ---")

	print("\n--- Anomalies (rows detected by SeasonalStan) ---")
	if not anomalies:
		print("No anomalies detected.")
	else:
		anomaly_indices = [idx for idx, _, _ in anomalies]
		anomaly_df = df.iloc[anomaly_indices]
		print(anomaly_df.to_string(index=False))
	print(f"--- Number of anomaly rows: {len(anomalies)} ---")

	out_png = os.path.abspath(os.path.join(os.path.dirname(__file__), "stan_hist.png"))
	plot_histogram(values, thresholds, out_png)
	print(f"--- Histogram saved to: {out_png} ---")


if __name__ == "__main__":
	main()
