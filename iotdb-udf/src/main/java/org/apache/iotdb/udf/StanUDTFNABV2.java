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

  private Type inputType;
  private int windowSize;
  private int warmupSize;
  private int cooldown;
  private int minConfirm;
  private int seasonalPeriod;
  private int maxAlerts;
  private double sensitivity;
  private double minThreshold;
  private double minScore;
  private double trendWeight;
  private double deviationWeight;
  private double changeWeight;
  private double seasonalWeight;
  private double emaAlpha;
  private double minSeriesVariability;

  private double[] buffer;
  private long[] timestamps;
  private int head;
  private int count;
  private double thresholdEma;
  private int cooldownLeft;
  private int alertsEmitted;

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
    validator
        .validateInputSeriesNumber(1)
        .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE);
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
    inputType = parameters.getDataType(0);
    windowSize = Math.max(20, parameters.getIntOrDefault("window", 100));
    warmupSize = Math.max(windowSize, parameters.getIntOrDefault("minWarmup", windowSize));
    cooldown = Math.max(0, parameters.getIntOrDefault("cooldown", Math.max(6, windowSize / 4)));
    minConfirm = Math.max(1, parameters.getIntOrDefault("confirmation", 1));
    seasonalPeriod = Math.max(0, parameters.getIntOrDefault("seasonalPeriod", 0));
    maxAlerts = Math.max(0, parameters.getIntOrDefault("maxAlerts", 0));
    sensitivity = Math.max(1.01, parameters.getDoubleOrDefault("sensitivity", 3.0));
    minThreshold = Math.max(0.0, parameters.getDoubleOrDefault("minThreshold", 3.0));
    minScore = Math.max(0.0, parameters.getDoubleOrDefault("minScore", 0.0));
    trendWeight = parameters.getDoubleOrDefault("trendWeight", 0.25);
    deviationWeight = parameters.getDoubleOrDefault("deviationWeight", 0.45);
    changeWeight = parameters.getDoubleOrDefault("changeWeight", 0.30);
    seasonalWeight = parameters.getDoubleOrDefault("seasonalWeight", 0.25);
    emaAlpha = parameters.getDoubleOrDefault("emaAlpha", 0.08);
    minSeriesVariability = parameters.getDoubleOrDefault("minSeriesVariability", 1e-8);

    buffer = new double[windowSize];
    timestamps = new long[windowSize];
    head = 0;
    count = 0;
    thresholdEma = 0.0;
    cooldownLeft = 0;
    alertsEmitted = 0;

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
      append(current, time);
      return;
    }

    WindowStats stats = stats();
    if (stats.scale <= minSeriesVariability) {
      append(current, time);
      return;
    }

    double previous = lastValue();
    double mean = stats.median;
    double robustZ = Math.abs(current - mean) / stats.scale;
    double diffZ = Math.abs(current - previous) / stats.diffScale;
    double trendZ = trendResidual(current, stats);
    double seasonalZ = seasonalPeriod > 0 && count >= seasonalPeriod
        ? Math.abs(current - valueAtLag(seasonalPeriod)) / Math.max(stats.seasonalScale, EPS)
        : 0.0;

    double score = deviationWeight * robustZ + changeWeight * diffZ + trendWeight * trendZ + seasonalWeight * seasonalZ;
    score = Math.max(score, Math.max(robustZ, diffZ));

    if (count >= warmupSize) {
      if (thresholdEma <= 0.0) {
        thresholdEma = Math.max(minThreshold, score / sensitivity);
      }
      double threshold = Math.max(minThreshold, thresholdEma * sensitivity);
      threshold = Math.max(threshold, minScore);
      boolean anomaly = score >= threshold && cooldownLeft == 0;
      if (anomaly && maxAlerts <= 0 || anomaly && alertsEmitted < maxAlerts) {
        collector.putDouble(time, score);
        alertsEmitted++;
        cooldownLeft = cooldown;
      }
      if (!anomaly) {
        thresholdEma = emaAlpha * score + (1.0 - emaAlpha) * thresholdEma;
      }
      if (cooldownLeft > 0) {
        cooldownLeft--;
      }
    }

    append(current, time);
  }

  private void append(double value, long time) {
    buffer[head] = value;
    timestamps[head] = time;
    head = (head + 1) % windowSize;
    if (count < windowSize) {
      count++;
    }
  }

  private double lastValue() {
    return buffer[(head - 1 + windowSize) % windowSize];
  }

  private double valueAtLag(int lag) {
    if (lag <= 0 || lag > count) {
      return lastValue();
    }
    return buffer[(head - lag + windowSize) % windowSize];
  }

  private WindowStats stats() {
    double[] values = new double[windowSize];
    for (int i = 0; i < windowSize; i++) {
      values[i] = buffer[(head + i) % windowSize];
    }
    java.util.Arrays.sort(values);
    double median = percentile(values, 0.5);
    double q1 = percentile(values, 0.25);
    double q3 = percentile(values, 0.75);

    double[] dev = new double[windowSize];
    for (int i = 0; i < windowSize; i++) {
      dev[i] = Math.abs(values[i] - median);
    }
    java.util.Arrays.sort(dev);
    double mad = percentile(dev, 0.5);
    double scale = Math.max(MAD_TO_STD * mad, (q3 - q1) / 1.349);

    double[] diffs = new double[windowSize - 1];
    for (int i = 1; i < windowSize; i++) {
      diffs[i - 1] = Math.abs(values[i] - values[i - 1]);
    }
    java.util.Arrays.sort(diffs);
    double diffScale = Math.max(MAD_TO_STD * percentile(diffs, 0.5), EPS);

    double seasonalScale = scale;
    if (seasonalPeriod > 0 && windowSize > seasonalPeriod) {
      double[] seasonalDiffs = new double[windowSize - seasonalPeriod];
      for (int i = seasonalPeriod; i < windowSize; i++) {
        seasonalDiffs[i - seasonalPeriod] = Math.abs(values[i] - values[i - seasonalPeriod]);
      }
      java.util.Arrays.sort(seasonalDiffs);
      seasonalScale = Math.max(MAD_TO_STD * percentile(seasonalDiffs, 0.5), scale);
    }

    return new WindowStats(median, Math.max(scale, EPS), diffScale, seasonalScale);
  }

  private double trendResidual(double current, WindowStats stats) {
    int len = Math.min(windowSize, Math.max(8, windowSize / 2));
    if (len < 8 || count < len) {
      return 0.0;
    }
    int start = (head - len + windowSize) % windowSize;
    double meanX = (len - 1) / 2.0;
    double meanY = 0.0;
    for (int i = 0; i < len; i++) {
      meanY += buffer[(start + i) % windowSize];
    }
    meanY /= len;
    double num = 0.0;
    double den = 0.0;
    for (int i = 0; i < len; i++) {
      double x = i - meanX;
      double y = buffer[(start + i) % windowSize] - meanY;
      num += x * y;
      den += x * x;
    }
    double slope = den < EPS ? 0.0 : num / den;
    double predicted = lastValue() + slope;
    return Math.abs(current - predicted) / stats.scale;
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

  private static class WindowStats {
    private final double median;
    private final double scale;
    private final double diffScale;
    private final double seasonalScale;

    private WindowStats(double median, double scale, double diffScale, double seasonalScale) {
      this.median = median;
      this.scale = scale;
      this.diffScale = diffScale;
      this.seasonalScale = seasonalScale;
    }
  }
}
