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
import java.util.Arrays;

public class StanUDTFNAB implements UDTF {

  private static final double EPS = 1e-12;
  private static final double MAD_TO_STD = 1.4826;
  private static final double EMA_ALPHA = 0.02;

  private int windowSize;
  private int minWarmup;
  private int confirmation;
  private int cooldown;
  private double sensitivity;
  private double minThreshold;
  private double spikeRatio;
  private Type inputType;

  private double[] buffer;
  private int head;
  private int count;
  private long rowIndex;
  private long lastEmitIndex;
  private int consecutiveCount;
  private double emaScore;
  private boolean emaInitialized;

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
    UDFParameters parameters = validator.getParameters();

    validator
        .validateInputSeriesNumber(1)
        .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE)
        .validate(
            value -> parseInt(value, 300) >= 30,
            "attribute 'window' must be an integer greater than or equal to 30",
            parameters.getString("window"))
        .validate(
            value -> parseDouble(value, 5.0) > 1.0,
            "attribute 'sensitivity' must be a number greater than 1.0",
            parameters.getString("sensitivity"))
        .validate(
            value -> parseDouble(value, 5.0) > 0.0,
            "attribute 'minThreshold' must be a positive number",
            parameters.getString("minThreshold"));
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
    windowSize = parameters.getIntOrDefault("window", 300);
    minWarmup = parameters.getIntOrDefault("minWarmup", Math.min(windowSize, 200));
    confirmation = parameters.getIntOrDefault("confirmation", 2);
    cooldown = parameters.getIntOrDefault("cooldown", 24);
    sensitivity = parameters.getDoubleOrDefault("sensitivity", 5.0);
    minThreshold = parameters.getDoubleOrDefault("minThreshold", 5.0);
    spikeRatio = parameters.getDoubleOrDefault("spikeRatio", 0.08);
    inputType = parameters.getDataType(0);

    buffer = new double[windowSize];
    head = 0;
    count = 0;
    rowIndex = 0;
    lastEmitIndex = Long.MIN_VALUE / 4;
    consecutiveCount = 0;
    emaScore = 0.0;
    emaInitialized = false;

    configurations.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(Type.DOUBLE);
  }

  @Override
  public void transform(Row row, PointCollector collector) throws Exception {
    if (row.isNull(0)) {
      rowIndex++;
      return;
    }

    double current = readAsDouble(row);
    long timestamp = row.getTime();

    if (count < minWarmup) {
      append(current);
      rowIndex++;
      return;
    }

    WindowStats stats = calculateWindowStats();
    double robustScore = Math.abs(current - stats.median) / stats.robustScale;
    double predicted = predictNext(stats);
    double trendScore = Math.abs(current - predicted) / stats.robustScale;
    double rawScore = 0.65 * robustScore + 0.35 * trendScore;

    if (!emaInitialized) {
      emaScore = Math.max(rawScore, minThreshold / sensitivity);
      emaInitialized = true;
    }

    double dynamicThreshold = Math.max(minThreshold, emaScore * sensitivity);
    boolean largeSpike = Math.abs(current - stats.median) > Math.max(stats.iqr * spikeRatio, stats.robustScale * minThreshold);
    boolean candidate = rawScore > dynamicThreshold && largeSpike;

    if (candidate) {
      consecutiveCount++;
    } else {
      consecutiveCount = 0;
      emaScore = EMA_ALPHA * rawScore + (1.0 - EMA_ALPHA) * emaScore;
    }

    if (candidate && consecutiveCount >= confirmation && rowIndex - lastEmitIndex >= cooldown) {
      collector.putDouble(timestamp, rawScore);
      lastEmitIndex = rowIndex;
      consecutiveCount = 0;
    }

    append(current);
    rowIndex++;
  }

  private void append(double value) {
    buffer[head] = value;
    head = (head + 1) % windowSize;
    if (count < windowSize) {
      count++;
    }
  }

  private WindowStats calculateWindowStats() {
    double[] values = new double[count];
    for (int i = 0; i < count; i++) {
      values[i] = orderedValue(i);
    }
    Arrays.sort(values);

    double median = percentile(values, 0.50);
    double q1 = percentile(values, 0.25);
    double q3 = percentile(values, 0.75);
    double iqr = Math.max(q3 - q1, EPS);

    double[] deviations = new double[count];
    for (int i = 0; i < count; i++) {
      deviations[i] = Math.abs(values[i] - median);
    }
    Arrays.sort(deviations);
    double mad = percentile(deviations, 0.50);
    double robustScale = Math.max(MAD_TO_STD * mad, iqr / 1.349);
    robustScale = Math.max(robustScale, EPS);

    int trendLength = Math.min(count, Math.max(10, windowSize / 5));
    double slope = calculateSlope(trendLength);
    double last = orderedValue(count - 1);
    return new WindowStats(median, iqr, robustScale, slope, last);
  }

  private double predictNext(WindowStats stats) {
    return stats.last + stats.slope;
  }

  private double calculateSlope(int trendLength) {
    if (trendLength < 2) {
      return 0.0;
    }

    double meanX = (trendLength - 1) / 2.0;
    double meanY = 0.0;
    int start = count - trendLength;
    for (int i = 0; i < trendLength; i++) {
      meanY += orderedValue(start + i);
    }
    meanY /= trendLength;

    double numerator = 0.0;
    double denominator = 0.0;
    for (int i = 0; i < trendLength; i++) {
      double dx = i - meanX;
      double dy = orderedValue(start + i) - meanY;
      numerator += dx * dy;
      denominator += dx * dx;
    }
    return denominator < EPS ? 0.0 : numerator / denominator;
  }

  private double orderedValue(int offset) {
    return buffer[(head + offset) % windowSize];
  }

  private static double percentile(double[] sortedValues, double q) {
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

  private static int parseInt(Object value, int defaultValue) {
    if (value == null) {
      return defaultValue;
    }
    if (value instanceof Number) {
      return ((Number) value).intValue();
    }
    return Integer.parseInt(value.toString());
  }

  private static double parseDouble(Object value, double defaultValue) {
    if (value == null) {
      return defaultValue;
    }
    if (value instanceof Number) {
      return ((Number) value).doubleValue();
    }
    return Double.parseDouble(value.toString());
  }

  private static class WindowStats {
    private final double median;
    private final double iqr;
    private final double robustScale;
    private final double slope;
    private final double last;

    private WindowStats(double median, double iqr, double robustScale, double slope, double last) {
      this.median = median;
      this.iqr = iqr;
      this.robustScale = robustScale;
      this.slope = slope;
      this.last = last;
    }
  }
}
