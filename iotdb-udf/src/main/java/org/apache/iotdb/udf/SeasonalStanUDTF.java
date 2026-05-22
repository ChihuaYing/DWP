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

public class SeasonalStanUDTF implements UDTF {

  private static final double EPS = 1e-12;
  private static final double EMA_ALPHA = 0.05;
  private static final double DEVIATION_WEIGHT = 0.70;
  private static final double TEMPORAL_WEIGHT = 0.30;

  private int windowSize;
  private double sensitivity;
  private double minThreshold;
  private Type inputType;

  private double[] buffer;
  private int head;
  private int count;

  private double emaScore;
  private boolean thresholdInitialized;

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
    UDFParameters parameters = validator.getParameters();

    validator
        .validateInputSeriesNumber(1)
        .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE)
        .validate(
            value -> parseInt(value, 100) >= 20,
            "attribute 'window' must be an integer greater than or equal to 20",
            parameters.getString("window"))
        .validate(
            value -> parseDouble(value, 3.0) > 1.0,
            "attribute 'sensitivity' must be a number greater than 1.0",
            parameters.getString("sensitivity"))
        .validate(
            value -> parseDouble(value, 3.0) > 0.0,
            "attribute 'minThreshold' must be a positive number",
            parameters.getString("minThreshold"));
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
    windowSize = parameters.getIntOrDefault("window", 100);
    sensitivity = parameters.getDoubleOrDefault("sensitivity", 3.0);
    minThreshold = parameters.getDoubleOrDefault("minThreshold", 3.0);
    inputType = parameters.getDataType(0);

    buffer = new double[windowSize];
    head = 0;
    count = 0;
    emaScore = 0.0;
    thresholdInitialized = false;

    configurations.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(Type.DOUBLE);
  }

  @Override
  public void transform(Row row, PointCollector collector) throws Exception {
    if (row.isNull(0)) {
      return;
    }

    double current = readAsDouble(row);
    long timestamp = row.getTime();

    if (count < windowSize) {
      append(current);
      return;
    }

    // Original score on raw data
    WindowStats origStats = calculateWindowStats();
    double origDeviationScore = Math.abs(current - origStats.mean) / origStats.std;
    double origPredicted = origStats.mean + origStats.acf1 * (lastValue() - origStats.mean);
    double origTemporalScore = Math.abs(current - origPredicted) / origStats.std;
    double origRawScore = DEVIATION_WEIGHT * origDeviationScore + TEMPORAL_WEIGHT * origTemporalScore;

    // Detrended score: linear regression in window, then stats on residuals
    double slope = 0.0;
    double intercept = 0.0;
    double sumX = 0.0, sumY = 0.0, sumXY = 0.0, sumXX = 0.0;
    for (int i = 0; i < windowSize; i++) {
      double x = i;
      double y = orderedValue(i);
      sumX += x;
      sumY += y;
      sumXY += x * y;
      sumXX += x * x;
    }
    double denom = windowSize * sumXX - sumX * sumX;
    if (denom > EPS) {
      slope = (windowSize * sumXY - sumX * sumY) / denom;
      intercept = (sumY - slope * sumX) / windowSize;
    }

    double[] residuals = new double[windowSize];
    for (int i = 0; i < windowSize; i++) {
      residuals[i] = orderedValue(i) - (slope * i + intercept);
    }
    WindowStats detStats = calculateStatsOnArray(residuals);

    double currentTrend = slope * windowSize + intercept;
    double currentResidual = current - currentTrend;
    double lastResidual = lastValue() - (slope * (windowSize - 1) + intercept);

    double detDeviationScore = Math.abs(currentResidual - detStats.mean) / detStats.std;
    double detPredicted = detStats.mean + detStats.acf1 * (lastResidual - detStats.mean);
    double detTemporalScore = Math.abs(currentResidual - detPredicted) / detStats.std;
    double detRawScore = DEVIATION_WEIGHT * detDeviationScore + TEMPORAL_WEIGHT * detTemporalScore;

    double rawScore = Math.max(origRawScore, detRawScore);

    if (!thresholdInitialized) {
      emaScore = Math.max(rawScore, minThreshold / sensitivity);
      thresholdInitialized = true;
    }

    double threshold = Math.max(minThreshold, emaScore * sensitivity);
    boolean anomaly = rawScore > threshold;
    if (anomaly) {
      collector.putDouble(timestamp, rawScore);
    }

    if (!anomaly) {
      emaScore = EMA_ALPHA * rawScore + (1.0 - EMA_ALPHA) * emaScore;
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
    int index = (head - 1 + windowSize) % windowSize;
    return buffer[index];
  }

  private WindowStats calculateWindowStats() {
    double mean = 0.0;
    double m2 = 0.0;

    for (int i = 0; i < windowSize; i++) {
      double value = orderedValue(i);
      double delta = value - mean;
      mean += delta / (i + 1);
      m2 += delta * (value - mean);
    }

    double variance = Math.max(m2 / windowSize, EPS);
    double std = Math.sqrt(variance);

    double acfNumerator = 0.0;
    double acfDenominator = 0.0;
    for (int i = 0; i < windowSize - 1; i++) {
      double currentDiff = orderedValue(i) - mean;
      double nextDiff = orderedValue(i + 1) - mean;
      acfNumerator += currentDiff * nextDiff;
      acfDenominator += currentDiff * currentDiff;
    }

    double acf1 = acfDenominator < EPS ? 0.0 : acfNumerator / acfDenominator;
    acf1 = Math.max(-1.0, Math.min(1.0, acf1));
    return new WindowStats(mean, std, acf1);
  }

  private WindowStats calculateStatsOnArray(double[] values) {
    double mean = 0.0;
    double m2 = 0.0;
    int n = values.length;

    for (int i = 0; i < n; i++) {
      double value = values[i];
      double delta = value - mean;
      mean += delta / (i + 1);
      m2 += delta * (value - mean);
    }

    double variance = Math.max(m2 / n, EPS);
    double std = Math.sqrt(variance);

    double acfNumerator = 0.0;
    double acfDenominator = 0.0;
    for (int i = 0; i < n - 1; i++) {
      double currentDiff = values[i] - mean;
      double nextDiff = values[i + 1] - mean;
      acfNumerator += currentDiff * nextDiff;
      acfDenominator += currentDiff * currentDiff;
    }

    double acf1 = acfDenominator < EPS ? 0.0 : acfNumerator / acfDenominator;
    acf1 = Math.max(-1.0, Math.min(1.0, acf1));
    return new WindowStats(mean, std, acf1);
  }

  private double orderedValue(int offset) {
    return buffer[(head + offset) % windowSize];
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
    private final double mean;
    private final double std;
    private final double acf1;

    private WindowStats(double mean, double std, double acf1) {
      this.mean = mean;
      this.std = std;
      this.acf1 = acf1;
    }
  }
}
