import os

import matplotlib
import numpy as np
import pandas as pd

# Use non-interactive backend so script can run in headless environments and still save the figure.
matplotlib.use("Agg")
import matplotlib.pyplot as plt


CSV_PATH = os.path.join("..", "dataset", "Yahoo_S5_Data", "A1Benchmark", "real_1.csv")


def read_k_from_stdin(default=3.0):
    """Read one numeric value k from stdin. If stdin is empty or invalid, return default."""
    try:
        text = input()
        if text is None:
            return float(default)
        text = text.strip()
        if not text:
            return float(default)
        return float(text)
    except EOFError:
        return float(default)
    except Exception:
        return float(default)


def resolve_csv_path():
    csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), CSV_PATH))
    if os.path.exists(csv_path):
        return csv_path
    return os.path.abspath(os.path.join(os.getcwd(), "dataset", "Yahoo_S5_Data", "A1Benchmark", "real_1.csv"))


def mad_anomalies(series: pd.Series, k: float):
    """Compute MAD-based thresholds and return boolean mask of anomalies.

    MAD = median(|Xi - median(X)|). No scaling factor applied (as requested).
    Thresholds: median +/- k * MAD
    """
    median = series.median()
    abs_dev = (series - median).abs()
    mad = abs_dev.median()

    if mad == 0 or np.isclose(mad, 0.0):
        lower = median
        upper = median
        mask = (series < lower) | (series > upper)
    else:
        lower = median - k * mad
        upper = median + k * mad
        mask = (series < lower) | (series > upper)

    return mask, median, mad, lower, upper


def main():
    print("请输入 k（直接回车使用默认值 3.0）：", end="", flush=True)
    k = read_k_from_stdin(default=3.0)

    csv_path = resolve_csv_path()
    df = pd.read_csv(csv_path)

    if "value" not in df.columns:
        raise RuntimeError("CSV does not contain 'value' column")

    first100 = df.head(100)
    print("--- First 100 rows (or fewer) ---")
    print(first100.to_string(index=False))
    print(f"--- Number of rows in original data: {len(df)} ---")

    values = df["value"].astype(float)
    mask, median, mad, lower, upper = mad_anomalies(values, k)
    anomalies = df[mask]

    print(f"\n--- MAD parameters: k={k}, median={median}, MAD={mad} ---")
    print(f"--- Thresholds: lower={lower}, upper={upper} ---")

    print("\n--- Anomalies (rows where value outside thresholds) ---")
    if anomalies.empty:
        print("No anomalies detected.")
    else:
        print(anomalies.to_string(index=False))
    print(f"--- Number of anomaly rows: {len(anomalies)} ---")

    plt.figure(figsize=(8, 5))
    plt.hist(values.dropna(), bins=50, color="#4c72b0", alpha=0.8)
    plt.axvline(lower, color="red", linestyle="--", linewidth=2, label=f"lower={lower:.4g}")
    plt.axvline(upper, color="red", linestyle="--", linewidth=2, label=f"upper={upper:.4g}")
    plt.title(f"Histogram of values with MAD thresholds (k={k})")
    plt.xlabel("value")
    plt.ylabel("count")
    plt.legend()

    out_png = os.path.abspath(os.path.join(os.path.dirname(__file__), "mad_hist.png"))
    plt.tight_layout()
    plt.savefig(out_png, dpi=150)
    print(f"--- Histogram saved to: {out_png} ---")


if __name__ == "__main__":
    main()

