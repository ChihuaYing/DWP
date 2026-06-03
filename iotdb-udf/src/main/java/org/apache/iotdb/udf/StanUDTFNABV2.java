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
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

public class StanUDTFNABV2 implements UDTF {

  private static final double EPS = 1e-12;
  private static final double MAD_TO_STD = 1.4826;

  private Type inputType;
  private int shortWindow;
  private int longWindow;
  private int minWarmup;
  private int cooldown;
  private int maxAlerts;
  private int seasonalPeriod;
  private double minScore;
  private double globalSensitivity;
  private double topFraction;

  private List<Double> values;
  private List<Long> timestamps;

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
    validator
        .validateInputSeriesNumber(1)
        .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE);
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
    inputType = parameters.getDataType(0);
    shortWindow = parameters.getIntOrDefault("shortWindow", 96);
    longWindow = parameters.getIntOrDefault("longWindow", 384);
    minWarmup = parameters.getIntOrDefault("minWarmup", 120);
    cooldown = parameters.getIntOrDefault("cooldown", 72);
    maxAlerts = parameters.getIntOrDefault("maxAlerts", 12);
    seasonalPeriod = parameters.getIntOrDefault("seasonalPeriod", 0);
    minScore = parameters.getDoubleOrDefault("minScore", 5.0);
    globalSensitivity = parameters.getDoubleOrDefault("globalSensitivity", 3.5);
    topFraction = parameters.getDoubleOrDefault("topFraction", 0.004);

    values = new ArrayList<>();
    timestamps = new ArrayList<>();
    configurations.setAccessStrategy(new RowByRowAccessStrategy()).setOutputDataType(Type.DOUBLE);
  }

  @Override
  public void transform(Row row, PointCollector collector) throws Exception {
    if (row.isNull(0)) {
      return;
    }
    values.add(readAsDouble(row));
    timestamps.add(row.getTime());
  }

  @Override
  public void terminate(PointCollector collector) throws Exception {
    int n = values.size();
    if (n <= minWarmup) {
      clear();
      return;
    }

    double[] series = toArray(values);
    int period = seasonalPeriod > 0 ? seasonalPeriod : inferSeasonalPeriod(series);
    double[] scores = scoreSeries(series, period);
    double threshold = globalThreshold(scores);
    int alertLimit = Math.max(1, Math.min(maxAlerts, (int) Math.ceil(n * topFraction)));

    List<Candidate> candidates = new ArrayList<>();
    for (int i = minWarmup; i < n; i++) {
      if (scores[i] >= threshold) {
        candidates.add(new Candidate(i, scores[i]));
      }
    }
    candidates.sort(Comparator.comparingDouble((Candidate c) -> c.score).reversed());

    boolean[] suppressed = new boolean[n];
    List<Candidate> selected = new ArrayList<>();
    for (Candidate candidate : candidates) {
      if (selected.size() >= alertLimit) {
        break;
      }
      int idx = candidate.index;
      if (suppressed[idx]) {
        continue;
      }
      selected.add(candidate);
      int left = Math.max(0, idx - cooldown);
      int right = Math.min(n - 1, idx + cooldown);
      for (int j = left; j <= right; j++) {
        suppressed[j] = true;
      }
    }
    selected.sort(Comparator.comparingInt(c -> c.index));

    for (Candidate candidate : selected) {
      collector.putDouble(timestamps.get(candidate.index), candidate.score);
    }
    clear();
  }

  private double[] scoreSeries(double[] series, int period) {
    int n = series.length;
    double[] diffs = new double[n];
    diffs[0] = 0.0;
    for (int i = 1; i < n; i++) {
      diffs[i] = series[i] - series[i - 1];
    }

    double[] seasonalResiduals = new double[n];
    for (int i = 0; i < n; i++) {
      seasonalResiduals[i] = period > 0 && i >= period ? series[i] - series[i - period] : 0.0;
    }

    double[] scores = new double[n];
    for (int i = minWarmup; i < n; i++) {
      RobustStats shortStats = robustStats(series, Math.max(0, i - shortWindow), i);
      RobustStats longStats = robustStats(series, Math.max(0, i - longWindow), i);
      RobustStats diffStats = robustStats(diffs, Math.max(1, i - shortWindow), i);

      double levelZ = Math.abs(series[i] - shortStats.median) / shortStats.scale;
      double longLevelZ = Math.abs(series[i] - longStats.median) / longStats.scale;
      double diffZ = Math.abs(diffs[i] - diffStats.median) / diffStats.scale;
      double trendZ = trendResidualZ(series, i);
      double seasonalZ = 0.0;
      if (period > 0 && i >= period + minWarmup / 2) {
        RobustStats seasonalStats = robustStats(seasonalResiduals, Math.max(period, i - longWindow), i);
        seasonalZ = Math.abs(seasonalResiduals[i] - seasonalStats.median) / seasonalStats.scale;
      }

      double localScore = 0.35 * levelZ + 0.25 * diffZ + 0.20 * trendZ + 0.20 * Math.max(longLevelZ, seasonalZ);
      double agreementBonus = 0.0;
      if (levelZ > 3.0 && diffZ > 3.0) {
        agreementBonus += 1.0;
      }
      if (seasonalZ > 3.0 && diffZ > 2.0) {
        agreementBonus += 0.8;
      }
      scores[i] = localScore + agreementBonus;
    }
    return scores;
  }

  private double trendResidualZ(double[] series, int index) {
    int len = Math.min(48, index);
    if (len < 6) {
      return 0.0;
    }
    int start = index - len;
    double meanX = (len - 1) / 2.0;
    double meanY = 0.0;
    for (int i = 0; i < len; i++) {
      meanY += series[start + i];
    }
    meanY /= len;

    double num = 0.0;
    double den = 0.0;
    for (int i = 0; i < len; i++) {
      double dx = i - meanX;
      double dy = series[start + i] - meanY;
      num += dx * dy;
      den += dx * dx;
    }
    double slope = den < EPS ? 0.0 : num / den;
    double predicted = series[index - 1] + slope;

    double[] residuals = new double[len];
    for (int i = 1; i < len; i++) {
      residuals[i] = Math.abs(series[start + i] - (series[start + i - 1] + slope));
    }
    Arrays.sort(residuals);
    double scale = Math.max(MAD_TO_STD * percentile(residuals, 0.5), EPS);
    return Math.abs(series[index] - predicted) / scale;
  }

  private double globalThreshold(double[] scores) {
    double[] valid = Arrays.copyOfRange(scores, minWarmup, scores.length);
    Arrays.sort(valid);
    double median = percentile(valid, 0.5);
    double[] dev = new double[valid.length];
    for (int i = 0; i < valid.length; i++) {
      dev[i] = Math.abs(valid[i] - median);
    }
    Arrays.sort(dev);
    double scale = Math.max(MAD_TO_STD * percentile(dev, 0.5), EPS);
    double robustThreshold = median + globalSensitivity * scale;
    double percentileThreshold = percentile(valid, Math.max(0.90, 1.0 - Math.max(topFraction * 4.0, 0.01)));
    return Math.max(minScore, Math.max(robustThreshold, percentileThreshold));
  }

  private int inferSeasonalPeriod(double[] series) {
    int[] periods = new int[] {12, 24, 48, 96, 144, 288};
    int bestPeriod = 0;
    double bestCorr = 0.0;
    for (int period : periods) {
      if (series.length < period * 3) {
        continue;
      }
      double corr = autocorrelation(series, period);
      if (corr > bestCorr) {
        bestCorr = corr;
        bestPeriod = period;
      }
    }
    return bestCorr >= 0.35 ? bestPeriod : 0;
  }

  private double autocorrelation(double[] series, int lag) {
    int n = series.length - lag;
    double meanA = 0.0;
    double meanB = 0.0;
    for (int i = 0; i < n; i++) {
      meanA += series[i];
      meanB += series[i + lag];
    }
    meanA /= n;
    meanB /= n;

    double num = 0.0;
    double denA = 0.0;
    double denB = 0.0;
    for (int i = 0; i < n; i++) {
      double a = series[i] - meanA;
      double b = series[i + lag] - meanB;
      num += a * b;
      denA += a * a;
      denB += b * b;
    }
    return denA < EPS || denB < EPS ? 0.0 : num / Math.sqrt(denA * denB);
  }

  private RobustStats robustStats(double[] data, int start, int endExclusive) {
    int len = Math.max(0, endExclusive - start);
    if (len == 0) {
      return new RobustStats(0.0, 1.0);
    }
    double[] window = new double[len];
    for (int i = 0; i < len; i++) {
      window[i] = data[start + i];
    }
    Arrays.sort(window);
    double median = percentile(window, 0.5);
    double q1 = percentile(window, 0.25);
    double q3 = percentile(window, 0.75);
    double[] dev = new double[len];
    for (int i = 0; i < len; i++) {
      dev[i] = Math.abs(window[i] - median);
    }
    Arrays.sort(dev);
    double scale = Math.max(MAD_TO_STD * percentile(dev, 0.5), (q3 - q1) / 1.349);
    return new RobustStats(median, Math.max(scale, EPS));
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

  private double[] toArray(List<Double> list) {
    double[] result = new double[list.size()];
    for (int i = 0; i < list.size(); i++) {
      result[i] = list.get(i);
    }
    return result;
  }

  private void clear() {
    values.clear();
    timestamps.clear();
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

  private static class RobustStats {
    private final double median;
    private final double scale;

    private RobustStats(double median, double scale) {
      this.median = median;
      this.scale = scale;
    }
  }

  private static class Candidate {
    private final int index;
    private final double score;

    private Candidate(int index, double score) {
      this.index = index;
      this.score = score;
    }
  }
}
