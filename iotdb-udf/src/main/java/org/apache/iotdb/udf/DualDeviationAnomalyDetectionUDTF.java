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

/**
 * Dual Deviation Anomaly Detection 的 IoTDB UDTF 实现。
 *
 * <p>算法面向单变量数值时间序列。它只使用当前点之前的历史窗口，不读取未来数据，因此可以按
 * RowByRowAccessStrategy 逐点在线检测。检测结果只输出异常点的异常分数，正常点不输出。
 *
 * <p>核心思路：
 * 1. 用环形缓冲区保存最近 windowSize 个历史点；
 * 2. 在历史窗口内计算均值、标准差和一阶自相关系数；
 * 3. 同时计算当前点相对窗口分布的统计偏离，以及相对时间趋势预测的时序偏离；
 * 4. 融合两类偏离得到 rawScore；
 * 5. 用正常点分数的指数滑动平均生成自适应阈值，超过阈值才输出异常。
 */
public class DualDeviationAnomalyDetectionUDTF implements UDTF {
  // 防止标准差或自相关分母为 0。
  private static final double EPS = 1e-12;

  // 正常点异常分数的指数滑动平均系数，值越小，阈值变化越平滑。
  private static final double EMA_ALPHA = 0.05;

  // 统计偏离权重更高，表示算法更看重当前点相对窗口整体分布的异常程度。
  private static final double DEVIATION_WEIGHT = 0.70;
  private static final double TEMPORAL_WEIGHT = 0.30;

  // UDF 参数：历史窗口长度、阈值灵敏度和最小阈值下界。
  private int windowSize;
  private double sensitivity;
  private double minThreshold;

  // 记录输入序列类型，transform 中统一转换为 double 后计算。
  private Type inputType;

  // 环形缓冲区：head 指向下一次写入位置；窗口填满后，head 也是最旧元素的位置。
  private double[] buffer;
  private int head;
  private int count;

  // 自适应阈值状态。emaScore 只用非异常点更新，避免异常点抬高后续阈值。
  private double emaScore;
  private boolean thresholdInitialized;

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
    UDFParameters parameters = validator.getParameters();

    // validate 在 UDF 执行前被调用，用于提前拦截输入序列数量、类型和参数错误。
    // SQL 中 UDF 参数通常以字符串传入，因此这里通过 parseInt/parseDouble 做兼容解析。
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
    // 读取用户参数；如果 SQL 未显式传入，则使用默认值。
    windowSize = parameters.getIntOrDefault("window", 100);
    sensitivity = parameters.getDoubleOrDefault("sensitivity", 3.0);
    minThreshold = parameters.getDoubleOrDefault("minThreshold", 3.0);
    inputType = parameters.getDataType(0);

    // 每次查询都会创建新的 UDF 实例状态，从空窗口开始预热。
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
      // 空值既不参与检测，也不进入历史窗口。
      return;
    }

    double current = readAsDouble(row);
    long timestamp = row.getTime();

    if (count < windowSize) {
      // 预热阶段：窗口未满时无法形成稳定历史统计量，因此只缓存数据，不输出结果。
      append(current);
      return;
    }

    WindowStats stats = calculateWindowStats();

    // 统计偏离：当前点距离窗口均值多少个标准差。
    double deviationScore = Math.abs(current - stats.mean) / stats.std;

    // 时序偏离：用一阶自相关做一步预测，再计算当前点相对预测值的标准化偏离。
    double predicted = stats.mean + stats.acf1 * (lastValue() - stats.mean);
    double temporalScore = Math.abs(current - predicted) / stats.std;

    // 融合偏离：分布异常占 70%，趋势异常占 30%。
    double rawScore = DEVIATION_WEIGHT * deviationScore + TEMPORAL_WEIGHT * temporalScore;

    if (!thresholdInitialized) {
      // 首次初始化时保证 emaScore 不低于 minThreshold / sensitivity，
      // 使初始阈值至少能达到 minThreshold。
      emaScore = Math.max(rawScore, minThreshold / sensitivity);
      thresholdInitialized = true;
    }

    // 自适应阈值由正常点历史分数的 EMA 决定，同时受 minThreshold 下界保护。
    double threshold = Math.max(minThreshold, emaScore * sensitivity);
    boolean anomaly = rawScore > threshold;
    if (anomaly) {
      // 只输出异常点。IoTDB 查询原始值和 UDF 结果时，正常时间点会显示为 null。
      collector.putDouble(timestamp, rawScore);
    }

    if (!anomaly) {
      // 只用正常点更新阈值基线，避免异常分数污染后续判断。
      emaScore = EMA_ALPHA * rawScore + (1.0 - EMA_ALPHA) * emaScore;
    }

    // 当前点处理完成后进入历史窗口，供后续点使用。
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

    // 使用在线方差更新形式计算均值和方差，比先求和再求平方和更稳定。
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

    // 一阶自相关衡量相邻点之间的线性延续关系，用于构造一步预测值。
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

  private double orderedValue(int offset) {
    // 窗口填满后，head 指向最旧值；从 head 开始偏移即可按时间从旧到新读取。
    return buffer[(head + offset) % windowSize];
  }

  private double readAsDouble(Row row) throws IOException {
    // IoTDB Row 的读取方法与原始类型绑定，这里按 validate 中允许的类型统一转成 double。
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
    // 参数不存在时使用默认值；存在时兼容 Number 和 String 两种来源。
    if (value == null) {
      return defaultValue;
    }
    if (value instanceof Number) {
      return ((Number) value).intValue();
    }
    return Integer.parseInt(value.toString());
  }

  private static double parseDouble(Object value, double defaultValue) {
    // 参数不存在时使用默认值；存在时兼容 Number 和 String 两种来源。
    if (value == null) {
      return defaultValue;
    }
    if (value instanceof Number) {
      return ((Number) value).doubleValue();
    }
    return Double.parseDouble(value.toString());
  }

  // 单次窗口统计结果，只在 transform 的一次判断中使用。
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
