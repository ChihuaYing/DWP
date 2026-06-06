import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path

from iotdb.Session import Session


IOTDB_HOST = "127.0.0.1"
IOTDB_PORT = 6667
IOTDB_USER = "root"
IOTDB_PASSWORD = "root"

DEVICE = "root.nab.d1"
UDF_NAME = "STAN_DETECT_NAB_V2"
DATA_DIR = Path(__file__).resolve().parent / "dataset" / "NAB" / "data"
RESULTS_ROOT = Path(__file__).resolve().parent / "dataset" / "NAB" / "results"
METADATA_DIR_NAME = "_iotdb_export_metadata"
DETECTOR_NAME = "iotdbStanNABV2"
PROTECTED_DETECTOR_NAMES = {
    "ARTime",
    "bayesChangePt",
    "contextOSE",
    "earthgeckoSkyline",
    "expose",
    "htmjava",
    "knncad",
    "null",
    "numenta",
    "numentaTM",
    "random",
    "randomCutForest",
    "relativeEntropy",
    "skyline",
    "twitterADVec",
    "windowedGaussian",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export IoTDB UDF predictions as NAB-compatible results CSV files."
    )
    parser.add_argument("--host", default=IOTDB_HOST)
    parser.add_argument("--port", type=int, default=IOTDB_PORT)
    parser.add_argument("--user", default=IOTDB_USER)
    parser.add_argument("--password", default=IOTDB_PASSWORD)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--udf", default=UDF_NAME)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--detector-name", default=DETECTOR_NAME)
    parser.add_argument("--categories", default="all")
    parser.add_argument("--files", default="all")
    parser.add_argument("--sensors", default="all")
    parser.add_argument(
        "--udf-param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "UDF parameter to pass inside the SQL function call. "
            "Can be repeated, e.g. --udf-param window=150 --udf-param threshold=3.5."
        ),
    )
    parser.add_argument("--window", type=int, default=None, help="Shorthand for --udf-param window=...")
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=None,
        help="Shorthand for --udf-param sensitivity=...",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Shorthand for --udf-param threshold=...",
    )
    parser.add_argument(
        "--score-mode",
        choices=("binary", "raw", "logistic", "clipped"),
        default="binary",
        help=(
            "How to write UDF outputs into NAB anomaly_score. "
            "binary writes 1.0 for any UDF detection; raw writes the UDF score; "
            "logistic maps score to score/(1+score); clipped clamps to [0,1]."
        ),
    )
    parser.add_argument(
        "--clean-detector-dir",
        action="store_true",
        help="Delete old CSV files under results/<detector-name> before exporting.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing NAB result CSV files for this detector and remove stale NAB score files.",
    )
    parser.add_argument("--print-sql", action="store_true")
    return parser.parse_args()


def norm_csv(name):
    name = name.strip().replace("\\", "/")
    return name if name.lower().endswith(".csv") else f"{name}.csv"


def parse_filter(value):
    if value.strip().lower() == "all":
        return None
    return {norm_csv(item) for item in value.split(",") if item.strip()}


def discover_files(data_dir, categories_arg, files_arg):
    if not data_dir.exists():
        raise FileNotFoundError(f"NAB data directory not found: {data_dir}")

    categories = None
    if categories_arg.strip().lower() != "all":
        categories = {item.strip() for item in categories_arg.split(",") if item.strip()}

    file_filters = parse_filter(files_arg)
    csv_files = []
    for category_dir in sorted(path for path in data_dir.iterdir() if path.is_dir()):
        if categories is not None and category_dir.name not in categories:
            continue
        for csv_path in sorted(category_dir.glob("*.csv")):
            rel = csv_path.relative_to(data_dir).as_posix()
            names = {csv_path.name, csv_path.stem, rel, rel[:-4]}
            if file_filters is None or file_filters.intersection({norm_csv(name) for name in names}):
                csv_files.append(csv_path)

    if not csv_files:
        raise ValueError("No NAB csv files matched filters.")
    return csv_files


def parse_sensors(value, count):
    if value.strip().lower() == "all":
        return list(range(count))

    indices = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        sensor = item if item.lower().startswith("s") else f"s{item}"
        index = int(sensor[1:])
        if index < 0 or index >= count:
            raise ValueError(f"Sensor {sensor} is out of range 0..{count - 1}")
        indices.append(index)

    if not indices:
        raise ValueError("No valid sensors specified.")
    return indices


def read_nab_rows(csv_path):
    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        if "timestamp" not in reader.fieldnames or "value" not in reader.fieldnames:
            raise ValueError(f"Expected columns timestamp,value in {csv_path}")
        for row in reader:
            rows.append((row["timestamp"], row["value"]))
    return rows


def record_time(record):
    for attr in ("timestamp", "time"):
        if hasattr(record, attr):
            value = getattr(record, attr)
            return int(value() if callable(value) else value)
    for method in ("get_timestamp", "getTimestamp", "get_time", "getTime"):
        if hasattr(record, method):
            return int(getattr(record, method)())
    return int(str(record).replace(",", " ").split()[0])


def record_score(record):
    fields = getattr(record, "fields", None)
    if fields:
        field = fields[0]
        for attr in ("double_value", "float_value", "long_value", "int_value"):
            if hasattr(field, attr):
                value = getattr(field, attr)
                if value is not None:
                    return float(value() if callable(value) else value)
        for method in ("get_double_value", "getDoubleV", "getFloatV", "getLongV", "getIntV"):
            if hasattr(field, method):
                try:
                    return float(getattr(field, method)())
                except Exception:
                    pass
    parts = str(record).replace(",", " ").split()
    return float(parts[-1]) if len(parts) >= 2 else 1.0


def close_dataset(dataset):
    for method in ("close_operation_handle", "closeOperationHandle", "close"):
        if hasattr(dataset, method):
            try:
                getattr(dataset, method)()
            except Exception:
                pass
            return


def sql_quote(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def parse_udf_params(args):
    params = []

    for raw in args.udf_param:
        if "=" not in raw:
            raise ValueError(f"Invalid --udf-param {raw!r}; expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid --udf-param {raw!r}; key is empty.")
        params.append((key, value))

    if args.window is not None:
        params.append(("window", args.window))
    if args.sensitivity is not None:
        params.append(("sensitivity", args.sensitivity))
    if args.threshold is not None:
        params.append(("threshold", args.threshold))

    return params


def build_sql(args, sensor, udf_params):
    param_sql = "".join(
        f", '{sql_quote(key)}'='{sql_quote(value)}'" for key, value in udf_params
    )
    return f"SELECT {args.udf}({sensor}{param_sql}) AS anomaly_score FROM {args.device}"


def query_predictions(session, sql):
    dataset = session.execute_query_statement(sql)
    predictions = {}
    try:
        while dataset.has_next():
            record = dataset.next()
            predictions[record_time(record)] = record_score(record)
    finally:
        close_dataset(dataset)
    return predictions


def format_score(score, mode):
    if score is None:
        return 0.0
    if mode == "binary":
        return 1.0
    if mode == "raw":
        return float(score)
    if mode == "logistic":
        score = max(0.0, float(score))
        return score / (1.0 + score)
    if mode == "clipped":
        return min(1.0, max(0.0, float(score)))
    raise ValueError(f"Unsupported score mode: {mode}")


def clean_legacy_metadata(detector_dir):
    if not detector_dir.exists():
        return
    for metadata_name in ("export_manifest.json", "export_summary.csv"):
        metadata_path = detector_dir / metadata_name
        if metadata_path.exists():
            metadata_path.unlink()


def clean_detector_dir(detector_dir):
    if not detector_dir.exists():
        return
    for csv_path in detector_dir.rglob("*.csv"):
        csv_path.unlink()
    clean_legacy_metadata(detector_dir)


def clean_metadata_dir(metadata_dir):
    if not metadata_dir.exists():
        return
    for path in metadata_dir.rglob("*"):
        if path.is_file():
            path.unlink()
    for path in sorted((p for p in metadata_dir.rglob("*") if p.is_dir()), reverse=True):
        path.rmdir()


def clean_stale_score_files(detector_dir):
    if not detector_dir.exists():
        return
    for score_path in detector_dir.glob("*_scores.csv"):
        score_path.unlink()
    clean_legacy_metadata(detector_dir)


def write_results_file(out_path, rows, predictions, score_mode):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["timestamp", "value", "anomaly_score"])
        for index, (timestamp, value) in enumerate(rows):
            writer.writerow([timestamp, value, format_score(predictions.get(index), score_mode)])
    tmp_path.replace(out_path)


def write_export_summary(metadata_dir, rows):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    summary_path = metadata_dir / "export_summary.csv"
    tmp_path = summary_path.with_name(summary_path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as fp:
        fieldnames = ["sensor", "relative_path", "rows", "detections", "output_path"]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(summary_path)


def write_manifest(metadata_dir, args, udf_params, summary_rows):
    metadata_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = metadata_dir / "export_manifest.json"
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "detector_name": args.detector_name,
        "data_dir": str(args.data_dir),
        "results_root": str(args.results_root),
        "device": args.device,
        "udf": args.udf,
        "udf_params": [{"key": key, "value": str(value)} for key, value in udf_params],
        "score_mode": args.score_mode,
        "categories": args.categories,
        "files": args.files,
        "sensors": args.sensors,
        "exported_files": len(summary_rows),
        "total_detections": sum(row["detections"] for row in summary_rows),
        "outputs": summary_rows,
    }
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    tmp_path.replace(manifest_path)


def validate_detector_name(detector_name):
    if detector_name in PROTECTED_DETECTOR_NAMES:
        raise ValueError(
            f"Detector name {detector_name!r} is a built-in NAB detector name. "
            "Use a project-specific name to avoid overwriting bundled benchmark results."
        )
    if "_" in detector_name:
        raise ValueError(
            "NAB normalize() parses detector names poorly when they contain underscores. "
            "Use a detector name without underscores, e.g. iotdbStanNABV2."
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", detector_name):
        raise ValueError(
            "Detector name must contain only letters, numbers, and hyphens, "
            "and must start with a letter or number."
        )


def existing_export_csvs(detector_dir):
    if not detector_dir.exists():
        return []
    return [
        path
        for path in detector_dir.rglob("*.csv")
        if not path.name.endswith("_scores.csv") and path.name != "export_summary.csv"
    ]


def metadata_dir_for(args):
    return args.results_root / METADATA_DIR_NAME / args.detector_name


def prepare_output_dirs(args):
    detector_dir = args.results_root / args.detector_name
    metadata_dir = metadata_dir_for(args)
    if args.clean_detector_dir:
        clean_detector_dir(detector_dir)
        clean_metadata_dir(metadata_dir)
        return detector_dir, metadata_dir

    existing = existing_export_csvs(detector_dir)
    if existing and not args.overwrite_existing:
        examples = "\n".join(str(path) for path in existing[:5])
        raise FileExistsError(
            f"Detector result files already exist under {detector_dir}. "
            "Use a new --detector-name, or pass --clean-detector-dir / --overwrite-existing.\n"
            f"Existing examples:\n{examples}"
        )
    if args.overwrite_existing:
        clean_stale_score_files(detector_dir)
        clean_metadata_dir(metadata_dir)
    else:
        clean_legacy_metadata(detector_dir)
    return detector_dir, metadata_dir


def export_results(session, args, csv_files):
    detector_dir, metadata_dir = prepare_output_dirs(args)

    udf_params = parse_udf_params(args)
    sensor_indices = parse_sensors(args.sensors, len(csv_files))
    exported = 0
    total_detections = 0
    summary_rows = []

    print("\n========== NAB IoTDB UDF Export Config ==========")
    print(f"data_dir: {args.data_dir}")
    print(f"results_dir: {detector_dir}")
    print(f"metadata_dir: {metadata_dir}")
    print(f"device: {args.device}")
    print(f"udf: {args.udf}")
    print("udf_params: " + (", ".join(f"{key}={value}" for key, value in udf_params) or "<none>"))
    print(f"score_mode: {args.score_mode}")

    print("\n========== Exported Files ==========")
    for index in sensor_indices:
        sensor = f"s{index}"
        csv_path = csv_files[index]
        rel = csv_path.relative_to(args.data_dir)
        rel_posix = rel.as_posix()
        rows = read_nab_rows(csv_path)
        sql = build_sql(args, sensor, udf_params)
        if args.print_sql:
            print(sql)
        predictions = query_predictions(session, sql)

        out_name = f"{args.detector_name}_{csv_path.name}"
        out_path = detector_dir / rel.parent / out_name
        write_results_file(out_path, rows, predictions, args.score_mode)

        exported += 1
        total_detections += len(predictions)
        summary_rows.append(
            {
                "sensor": sensor,
                "relative_path": rel_posix,
                "rows": len(rows),
                "detections": len(predictions),
                "output_path": str(out_path),
            }
        )
        print(f"{sensor} {rel_posix}: rows={len(rows)}, detections={len(predictions)}, output={out_path}")

    write_export_summary(metadata_dir, summary_rows)
    write_manifest(metadata_dir, args, udf_params, summary_rows)

    print("\n========== Export Summary ==========")
    print(f"exported_files: {exported}")
    print(f"total_detections: {total_detections}")
    print(f"detector_name: {args.detector_name}")
    print(f"manifest: {metadata_dir / 'export_manifest.json'}")
    print(f"summary: {metadata_dir / 'export_summary.csv'}")


def main():
    args = parse_args()
    validate_detector_name(args.detector_name)

    csv_files = discover_files(args.data_dir, args.categories, args.files)
    print(f"Discovered {len(csv_files)} NAB csv files.")

    session = Session(args.host, args.port, args.user, args.password)
    session.open(False)
    print("Connected to IoTDB.")
    try:
        export_results(session, args, csv_files)
    finally:
        session.close()
        print("Session closed.")


if __name__ == "__main__":
    main()
