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

public class StanUDTFNABV3 implements UDTF {

  private static final double EPS = 1e-12;
  private static final double MAD_TO_STD = 1.4826;
  private static final int MIN_WINDOW_SIZE = 20;
  private static final int DEFAULT_WINDOW_SIZE = 64;
  private static final double DEFAULT_THRESHOLD = 3.5;
  private static final int DEFAULT_SEASONAL_PERIOD = 0;
  private static final int DEFAULT_SEASONAL_LOOKBACK = 7;
  private static final int DEFAULT_MIN_SEASONAL_SAMPLES = 3;
  private static final boolean DEFAULT_AUTO_SEASONAL = true;
  private static final int DEFAULT_AUTO_SEASONAL_MIN_PERIOD = 20;
  private static final int DEFAULT_AUTO_SEASONAL_MAX_PERIOD = 512;
  private static final double DEFAULT_AUTO_SEASONAL_MIN_CORRELATION = 0.75;
  private static final int DEFAULT_AUTO_SEASONAL_RECOMPUTE_INTERVAL = 64;
  private static final double MIN_VARIABILITY = 1e-8;
  private static final double DIFF_WEIGHT = 0.25;
  private static final double LEVEL_WEIGHT = 0.35;
  private static final double RESIDUAL_WEIGHT = 0.40;
  private static final double Z_SCORE_BOOST = 1.15;

  private Type inputType;
  private int windowSize;
  private double threshold;
  private int seasonalPeriod;
  private int seasonalLookback;
  private int minSeasonalSamples;
  private boolean autoSeasonal;
  private int autoSeasonalMinPeriod;
  private int autoSeasonalMaxPeriod;
  private double autoSeasonalMinCorrelation;
  private int autoSeasonalRecomputeInterval;
  private int historyCapacity;
  private int estimatedSeasonalPeriod;
  private long rowsSeen;
  private long lastSeasonalEstimateAt;

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
    UDFParameters parameters = validator.getParameters();

    validator
        .validateInputSeriesNumber(1)
        .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE)
        .validate(
            value -> parseInt(value, DEFAULT_WINDOW_SIZE) >= MIN_WINDOW_SIZE,
            "attribute 'window' must be an integer greater than or equal to 20",
            parameters.getString("window"))
        .validate(
            value -> parseDouble(value, DEFAULT_THRESHOLD) > 0.0,
            "attribute 'threshold' must be a positive number",
            parameters.getString("threshold"))
        .validate(
            value -> parseInt(value, DEFAULT_SEASONAL_PERIOD) >= 0,
            "attribute 'seasonalPeriod' must be an integer greater than or equal to 0",
            parameters.getString("seasonalPeriod"))
        .validate(
            value -> parseInt(value, DEFAULT_SEASONAL_LOOKBACK) >= 1,
            "attribute 'seasonalLookback' must be an integer greater than or equal to 1",
            parameters.getString("seasonalLookback"))
        .validate(
            value -> parseInt(value, DEFAULT_MIN_SEASONAL_SAMPLES) >= 1,
            "attribute 'minSeasonalSamples' must be an integer greater than or equal to 1",
            parameters.getString("minSeasonalSamples"))
        .validate(
            value -> parseInt(value, DEFAULT_AUTO_SEASONAL_MIN_PERIOD) >= 2,
            "attribute 'autoSeasonalMinPeriod' must be an integer greater than or equal to 2",
            parameters.getString("autoSeasonalMinPeriod"))
        .validate(
            value -> parseInt(value, DEFAULT_AUTO_SEASONAL_MAX_PERIOD) >= 2,
            "attribute 'autoSeasonalMaxPeriod' must be an integer greater than or equal to 2",
            parameters.getString("autoSeasonalMaxPeriod"))
        .validate(
            value -> parseDouble(value, DEFAULT_AUTO_SEASONAL_MIN_CORRELATION) > 0.0
                && parseDouble(value, DEFAULT_AUTO_SEASONAL_MIN_CORRELATION) <= 1.0,
            "attribute 'autoSeasonalMinCorrelation' must be a number in (0, 1]",
            parameters.getString("autoSeasonalMinCorrelation"))
        .validate(
            value -> parseInt(value, DEFAULT_AUTO_SEASONAL_RECOMPUTE_INTERVAL) >= 1,
            "attribute 'autoSeasonalRecomputeInterval' must be an integer greater than or equal to 1",
            parameters.getString("autoSeasonalRecomputeInterval"));
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
    inputType = parameters.getDataType(0);
    windowSize = Math.max(MIN_WINDOW_SIZE, parameters.getIntOrDefault("window", DEFAULT_WINDOW_SIZE));
    threshold = Math.max(EPS, parameters.getDoubleOrDefault("threshold", DEFAULT_THRESHOLD));
    seasonalPeriod = Math.max(0, parameters.getIntOrDefault("seasonalPeriod", DEFAULT_SEASONAL_PERIOD));
    seasonalLookback = Math.max(1, parameters.getIntOrDefault("seasonalLookback", DEFAULT_SEASONAL_LOOKBACK));
    minSeasonalSamples = Math.max(1, parameters.getIntOrDefault("minSeasonalSamples", DEFAULT_MIN_SEASONAL_SAMPLES));
    autoSeasonal = parseBoolean(parameters.getString("autoSeasonal"), DEFAULT_AUTO_SEASONAL);
    autoSeasonalMinPeriod =
        Math.max(2, parameters.getIntOrDefault("autoSeasonalMinPeriod", DEFAULT_AUTO_SEASONAL_MIN_PERIOD));
    autoSeasonalMaxPeriod =
        Math.max(autoSeasonalMinPeriod, parameters.getIntOrDefault("autoSeasonalMaxPeriod", DEFAULT_AUTO_SEASONAL_MAX_PERIOD));
    autoSeasonalMinCorrelation =
        Math.max(EPS, Math.min(1.0, parameters.getDoubleOrDefault(
            "autoSeasonalMinCorrelation", DEFAULT_AUTO_SEASONAL_MIN_CORRELATION)));
    autoSeasonalRecomputeInterval =
        Math.max(1, parameters.getIntOrDefault(
            "autoSeasonalRecomputeInterval", DEFAULT_AUTO_SEASONAL_RECOMPUTE_INTERVAL));
    historyCapacity = Math.max(windowSize, seasonalHistoryCapacity());

    minSeriesVariability = MIN_VARIABILITY;
    zScoreBoost = Z_SCORE_BOOST;
    diffWeight = DIFF_WEIGHT;
    levelWeight = LEVEL_WEIGHT;
    residualWeight = RESIDUAL_WEIGHT;

    buffer = new double[historyCapacity];
    head = 0;
    count = 0;
    estimatedSeasonalPeriod = 0;
    rowsSeen = 0L;
    lastSeasonalEstimateAt = Long.MIN_VALUE;

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

    RollingStats localStats = localStats();
    int activeSeasonalPeriod = activeSeasonalPeriod();
    SeasonalStats seasonalStats = seasonalStats(activeSeasonalPeriod);
    RollingStats stats = chooseStats(localStats, seasonalStats);

    if (stats.scale <= minSeriesVariability) {
      append(current);
      return;
    }

    double previous = valueByAge(1);
    double levelZ = Math.abs(current - stats.median) / stats.scale;
    double diffZ = Math.abs(current - previous) / localStats.diffScale;
    double residualZ = Math.abs(current - localStats.predictNext()) / localStats.residualScale;
    double transitionGate = transitionGate(seasonalStats, levelZ);

    double score =
        zScoreBoost * levelZ
            + levelWeight * Math.max(0.0, levelZ - 1.0)
            + transitionGate * (diffWeight * diffZ + residualWeight * residualZ);
    score = Math.max(score, levelZ);
    if (!seasonalStats.available) {
      score = Math.max(score, residualZ);
    }

    if (score >= threshold) {
      collector.putDouble(time, score);
    }

    append(current);
  }

  private int seasonalHistoryCapacity() {
    if (seasonalPeriod > 0) {
      return seasonalPeriod * seasonalLookback;
    }
    if (!autoSeasonal) {
      return 0;
    }
    return autoSeasonalMaxPeriod * seasonalLookback;
  }

  private void append(double value) {
    buffer[head] = value;
    head = (head + 1) % historyCapacity;
    if (count < historyCapacity) {
      count++;
    }
    rowsSeen++;
  }

  private double valueByAge(int age) {
    return buffer[(head - age + historyCapacity) % historyCapacity];
  }

  private RollingStats localStats() {
    double[] values = new double[windowSize];
    for (int i = 0; i < windowSize; i++) {
      values[i] = valueByAge(windowSize - i);
    }
    return stats(values);
  }

  private int activeSeasonalPeriod() {
    if (seasonalPeriod > 0) {
      return seasonalPeriod;
    }
    if (!autoSeasonal) {
      return 0;
    }
    return estimateSeasonalPeriod();
  }

  private int estimateSeasonalPeriod() {
    int requiredHistory = autoSeasonalMaxPeriod * Math.max(1, minSeasonalSamples);
    if (count < requiredHistory) {
      estimatedSeasonalPeriod = 0;
      return 0;
    }

    int maxLag = Math.min(autoSeasonalMaxPeriod, count / Math.max(1, minSeasonalSamples));
    if (maxLag < autoSeasonalMinPeriod) {
      estimatedSeasonalPeriod = 0;
      return 0;
    }

    if (estimatedSeasonalPeriod > 0
        && rowsSeen - lastSeasonalEstimateAt < autoSeasonalRecomputeInterval) {
      return estimatedSeasonalPeriod;
    }

    double[] values = historyValues(count);
    double mean = mean(values);
    double bestCorrelation = Double.NEGATIVE_INFINITY;
    int bestLag = 0;

    for (int lag = autoSeasonalMinPeriod; lag <= maxLag; lag++) {
      double correlation = autocorrelation(values, mean, lag);
      if (correlation > bestCorrelation) {
        bestCorrelation = correlation;
        bestLag = lag;
      }
    }

    lastSeasonalEstimateAt = rowsSeen;
    if (bestLag > 0 && bestCorrelation >= autoSeasonalMinCorrelation) {
      estimatedSeasonalPeriod = bestLag;
    } else {
      estimatedSeasonalPeriod = 0;
    }
    return estimatedSeasonalPeriod;
  }

  private double[] historyValues(int length) {
    double[] values = new double[length];
    for (int i = 0; i < length; i++) {
      values[i] = valueByAge(length - i);
    }
    return values;
  }

  private double mean(double[] values) {
    double mean = 0.0;
    for (int i = 0; i < values.length; i++) {
      mean += (values[i] - mean) / (i + 1);
    }
    return mean;
  }

  private double autocorrelation(double[] values, double mean, int lag) {
    double numerator = 0.0;
    double leftDenominator = 0.0;
    double rightDenominator = 0.0;
    for (int i = lag; i < values.length; i++) {
      double left = values[i] - mean;
      double right = values[i - lag] - mean;
      numerator += left * right;
      leftDenominator += left * left;
      rightDenominator += right * right;
    }
    double denominator = Math.sqrt(leftDenominator * rightDenominator);
    return denominator <= EPS ? 0.0 : numerator / denominator;
  }

  private SeasonalStats seasonalStats(int activeSeasonalPeriod) {
    if (activeSeasonalPeriod <= 0) {
      return SeasonalStats.unavailable();
    }

    int samples = Math.min(seasonalLookback, count / activeSeasonalPeriod);
    if (samples < minSeasonalSamples) {
      return SeasonalStats.unavailable();
    }

    double[] values = new double[samples];
    for (int i = 0; i < samples; i++) {
      values[i] = valueByAge(activeSeasonalPeriod * (i + 1));
    }

    RollingStats stats = stats(values);
    if (stats.scale <= minSeriesVariability) {
      return SeasonalStats.unavailable();
    }
    return new SeasonalStats(true, stats);
  }

  private RollingStats chooseStats(RollingStats localStats, SeasonalStats seasonalStats) {
    return seasonalStats.available ? seasonalStats.stats : localStats;
  }

  private double transitionGate(SeasonalStats seasonalStats, double levelZ) {
    if (!seasonalStats.available) {
      return 1.0;
    }
    return Math.min(1.0, levelZ / threshold);
  }

  private RollingStats stats(double[] values) {
    double median = median(values);
    double mad = mad(values, median);
    double scale = Math.max(MAD_TO_STD * mad, EPS);

    double diffScale = scale;
    if (values.length > 1) {
      double[] diffs = new double[values.length - 1];
      for (int i = 1; i < values.length; i++) {
        diffs[i - 1] = Math.abs(values[i] - values[i - 1]);
      }
      diffScale = Math.max(MAD_TO_STD * median(diffs), EPS);
    }

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

  private static boolean parseBoolean(Object value, boolean defaultValue) {
    if (value == null) {
      return defaultValue;
    }
    if (value instanceof Boolean) {
      return (Boolean) value;
    }
    return Boolean.parseBoolean(value.toString());
  }

  private static class SeasonalStats {
    private final boolean available;
    private final RollingStats stats;

    private SeasonalStats(boolean available, RollingStats stats) {
      this.available = available;
      this.stats = stats;
    }

    private static SeasonalStats unavailable() {
      return new SeasonalStats(false, null);
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
