package org.apache.iotdb.udf;

import org.apache.iotdb.udf.api.UDTF;
import org.apache.iotdb.udf.api.access.Row;
import org.apache.iotdb.udf.api.collector.PointCollector;
import org.apache.iotdb.udf.api.customizer.config.UDTFConfigurations;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameterValidator;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameters;
import org.apache.iotdb.udf.api.customizer.strategy.RowByRowAccessStrategy;
import org.apache.iotdb.udf.api.type.Type;

import java.io.IOException;

public class StanUDTFNABV2 implements UDTF {

  private static final double EPS = 1e-12;
  private static final double MAD_TO_STD = 1.4826;
  private static final int MIN_WINDOW_SIZE = 20;
  private static final double DEFAULT_SENSITIVITY = 4.5;
  private static final double DEFAULT_THRESHOLD = 3.5;
  private static final double MIN_VARIABILITY = 1e-8;
  private static final double DIFF_WEIGHT = 0.25;
  private static final double LEVEL_WEIGHT = 0.35;
  private static final double RESIDUAL_WEIGHT = 0.40;
  private static final double Z_SCORE_BOOST = 1.15;

  private Type inputType;
  private int windowSize;
  private double sensitivity;
  private double threshold;
  private double minSeriesVariability;
  private double zScoreBoost;
  private double diffWeight;
  private double levelWeight;
  private double residualWeight;

  private double[] buffer;
  private int head;
  private int count;

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
    validator
        .validateInputSeriesNumber(1)
        .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE);
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
    inputType = parameters.getDataType(0);
    windowSize = Math.max(MIN_WINDOW_SIZE, parameters.getIntOrDefault("window", 64));
    sensitivity = Math.max(1.01, parameters.getDoubleOrDefault("sensitivity", DEFAULT_SENSITIVITY));
    threshold = Math.max(0.0, parameters.getDoubleOrDefault("threshold", DEFAULT_THRESHOLD));
    minSeriesVariability = MIN_VARIABILITY;
    zScoreBoost = Z_SCORE_BOOST;
    diffWeight = DIFF_WEIGHT;
    levelWeight = LEVEL_WEIGHT;
    residualWeight = RESIDUAL_WEIGHT;

    buffer = new double[windowSize];
    head = 0;
    count = 0;

    configurations.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(Type.DOUBLE);
  }

  @Override
  public void transform(Row row, PointCollector collector) throws Exception {
    if (row.isNull(0)) {
      return;
    }

    double current = readAsDouble(row);
    long time = row.getTime();

    if (count < windowSize) {
      append(current);
      return;
    }

    RollingStats stats = stats();
    if (stats.scale <= minSeriesVariability) {
      append(current);
      return;
    }

    double previous = lastValue();
    double robustZ = Math.abs(current - stats.median) / stats.scale;
    double diffZ = Math.abs(current - previous) / stats.diffScale;
    double residualZ = Math.abs(current - stats.predictNext()) / stats.residualScale;

    double score = zScoreBoost * robustZ + diffWeight * diffZ + levelWeight * Math.max(0.0, robustZ - 1.0) + residualWeight * residualZ;
    score = Math.max(score, Math.max(robustZ, residualZ));

    if (score >= threshold / sensitivity) {
      collector.putDouble(time, score);
    }

    append(current);
  }

  private void append(double value) {
    buffer[head] = value;
    head = (head + 1) % windowSize;
    if (count < windowSize) {
      count++;
    }
  }

  private double lastValue() {
    return buffer[(head - 1 + windowSize) % windowSize];
  }

  private RollingStats stats() {
    double[] values = new double[windowSize];
    for (int i = 0; i < windowSize; i++) {
      values[i] = buffer[(head + i) % windowSize];
    }

    double median = median(values);
    double mad = mad(values, median);
    double scale = Math.max(MAD_TO_STD * mad, EPS);

    double[] diffs = new double[windowSize - 1];
    for (int i = 1; i < windowSize; i++) {
      diffs[i - 1] = Math.abs(values[i] - values[i - 1]);
    }
    double diffScale = Math.max(MAD_TO_STD * median(diffs), EPS);

    double residualScale = Math.max(scale, diffScale);
    return new RollingStats(median, scale, diffScale, residualScale, values);
  }

  private double median(double[] values) {
    double[] copy = values.clone();
    java.util.Arrays.sort(copy);
    return percentile(copy, 0.5);
  }

  private double mad(double[] values, double median) {
    double[] dev = new double[values.length];
    for (int i = 0; i < values.length; i++) {
      dev[i] = Math.abs(values[i] - median);
    }
    java.util.Arrays.sort(dev);
    return percentile(dev, 0.5);
  }

  private double percentile(double[] sortedValues, double q) {
    if (sortedValues.length == 0) {
      return 0.0;
    }
    double pos = q * (sortedValues.length - 1);
    int lower = (int) Math.floor(pos);
    int upper = (int) Math.ceil(pos);
    if (lower == upper) {
      return sortedValues[lower];
    }
    double weight = pos - lower;
    return sortedValues[lower] * (1.0 - weight) + sortedValues[upper] * weight;
  }

  private double readAsDouble(Row row) throws IOException {
    switch (inputType) {
      case INT32:
        return row.getInt(0);
      case INT64:
        return row.getLong(0);
      case FLOAT:
        return row.getFloat(0);
      case DOUBLE:
        return row.getDouble(0);
      default:
        throw new IOException("Unsupported input data type: " + inputType);
    }
  }

  private static class RollingStats {
    private final double median;
    private final double scale;
    private final double diffScale;
    private final double residualScale;
    private final double[] values;

    private RollingStats(double median, double scale, double diffScale, double residualScale, double[] values) {
      this.median = median;
      this.scale = scale;
      this.diffScale = diffScale;
      this.residualScale = residualScale;
      this.values = values;
    }

    private double predictNext() {
      if (values.length < 3) {
        return values[values.length - 1];
      }
      int n = values.length;
      double last = values[n - 1];
      double prev = values[n - 2];
      double prev2 = values[n - 3];
      double delta1 = last - prev;
      double delta2 = prev - prev2;
      double trend = 0.7 * delta1 + 0.3 * delta2;
      return last + trend;
    }
  }
}
