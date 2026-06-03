package org.apache.iotdb.udf;

import org.apache.iotdb.udf.api.UDTF;
import org.apache.iotdb.udf.api.access.Row;
import org.apache.iotdb.udf.api.collector.PointCollector;
import org.apache.iotdb.udf.api.customizer.config.UDTFConfigurations;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameterValidator;
import org.apache.iotdb.udf.api.customizer.parameter.UDFParameters;
import org.apache.iotdb.udf.api.customizer.strategy.RowByRowAccessStrategy;
import org.apache.iotdb.udf.api.type.Type;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class MAD implements UDTF {

  private static final double EPS = 1e-12;

  private double k;
  private Type inputType;

  private final List<Double> values = new ArrayList<>();
  private final List<Long> timestamps = new ArrayList<>();

  @Override
  public void validate(UDFParameterValidator validator) throws Exception {
	UDFParameters parameters = validator.getParameters();

	validator
		.validateInputSeriesNumber(1)
		.validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE)
		.validate(
			value -> parseDouble(value, 3.0) > 0.0,
			"attribute 'k' must be a positive number",
			parameters.getString("k"));
  }

  @Override
  public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) {
	k = parameters.getDoubleOrDefault("k", 3.0);
	inputType = parameters.getDataType(0);

	values.clear();
	timestamps.clear();

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
	if (values.isEmpty()) {
	  return;
	}

	double[] series = toArray(values);
	Arrays.sort(series);
	double median = median(series);

	double[] deviations = new double[series.length];
	for (int i = 0; i < values.size(); i++) {
	  deviations[i] = Math.abs(values.get(i) - median);
	}
	Arrays.sort(deviations);
	double mad = median(deviations);

	double threshold = k * mad;
	boolean zeroMad = mad <= EPS;

	for (int i = 0; i < values.size(); i++) {
	  double value = values.get(i);
	  double deviation = Math.abs(value - median);
	  boolean anomaly = zeroMad ? deviation > EPS : deviation > threshold;
	  if (anomaly) {
		collector.putDouble(timestamps.get(i), value);
	  }
	}

	values.clear();
	timestamps.clear();
  }

  private double readAsDouble(Row row) throws Exception {
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
		throw new Exception("Unsupported input data type: " + inputType);
	}
  }

  private static double[] toArray(List<Double> list) {
	double[] result = new double[list.size()];
	for (int i = 0; i < list.size(); i++) {
	  result[i] = list.get(i);
	}
	return result;
  }

  private static double median(double[] sortedValues) {
	int n = sortedValues.length;
	if (n == 0) {
	  return 0.0;
	}
	int mid = n / 2;
	if ((n & 1) == 1) {
	  return sortedValues[mid];
	}
	return (sortedValues[mid - 1] + sortedValues[mid]) / 2.0;
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
}
