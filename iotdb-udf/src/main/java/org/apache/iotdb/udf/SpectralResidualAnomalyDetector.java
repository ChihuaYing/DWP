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
import java.util.LinkedList;
import java.util.List;
import java.util.Queue;

/**
 * Spectral Residual 异常检测 UDF (IoTDB 2.0.7)
 * 
 * 优化版本：
 * 1. 使用历史数据滑动窗口处理，支持实时检测（只向前看）
 * 2. 局部归一化，适应非平稳时间序列
 * 3. Saliency map 高斯平滑，减少误报
 * 4. 只返回检测到的异常数据点
 * 
 * 算法原理：
 * 1. 对滑动窗口内的历史时间序列进行傅里叶变换
 * 2. 计算幅度谱的对数
 * 3. 使用平均滤波器计算谱残差
 * 4. 通过逆傅里叶变换得到显著性图
 * 5. 对显著性图进行高斯平滑
 * 6. 使用局部统计量判断异常点
 * 7. 仅输出异常点（非异常点不输出）
 * 
 * 使用示例:
 * SELECT SpectralResidualAnomalyDetector(value, 'window_size'='100', 'threshold'='3.0') 
 * FROM root.sg.d1
 */
public class SpectralResidualAnomalyDetector implements UDTF {
    
    private int windowSize;
    private double threshold;
    private int ampWindowSize;
    private int scoreWindowSize;
    private Queue<Double> slidingWindow;
    private Queue<Long> slidingTimestamps;
    private List<Double> allValues;
    private List<Long> allTimestamps;
    private List<Double> allScores;
    
    @Override
    public void validate(UDFParameterValidator validator) throws Exception {
        validator
            .validateInputSeriesNumber(1)
            .validateInputSeriesDataType(0, Type.INT32, Type.INT64, Type.FLOAT, Type.DOUBLE);
    }
    
    @Override
    public void beforeStart(UDFParameters parameters, UDTFConfigurations configurations) throws Exception {
        this.windowSize = parameters.getIntOrDefault("window_size", 100);
        this.threshold = parameters.getDoubleOrDefault("threshold", 3.0);
        this.ampWindowSize = parameters.getIntOrDefault("amp_window_size", 5);
        this.scoreWindowSize = parameters.getIntOrDefault("score_window_size", 50);
        
        if (this.windowSize <= 0) {
            throw new Exception("window_size must be positive");
        }
        if (this.threshold <= 0) {
            throw new Exception("threshold must be positive");
        }
        if (this.ampWindowSize <= 0 || this.ampWindowSize >= this.windowSize) {
            throw new Exception("amp_window_size must be positive and less than window_size");
        }
        if (this.scoreWindowSize <= 0) {
            throw new Exception("score_window_size must be positive");
        }
        
        this.slidingWindow = new LinkedList<>();
        this.slidingTimestamps = new LinkedList<>();
        this.allValues = new ArrayList<>();
        this.allTimestamps = new ArrayList<>();
        this.allScores = new ArrayList<>();
        
        configurations
            .setAccessStrategy(new RowByRowAccessStrategy())
            .setOutputDataType(Type.DOUBLE);
    }
    
    @Override
    public void transform(Row row, PointCollector collector) throws Exception {
        if (row.isNull(0)) {
            return;
        }
        
        double value = getDoubleValue(row);
        long timestamp = row.getTime();
        
        slidingWindow.offer(value);
        slidingTimestamps.offer(timestamp);
        allValues.add(value);
        allTimestamps.add(timestamp);
        
        if (slidingWindow.size() >= windowSize) {
            double[] windowData = new double[windowSize];
            int idx = 0;
            for (Double v : slidingWindow) {
                windowData[idx++] = v;
            }
            
            double[] windowScores = spectralResidualWindow(windowData);
            
            double currentScore = windowScores[windowSize - 1];
            allScores.add(currentScore);
            
            slidingWindow.poll();
            slidingTimestamps.poll();
        }
    }
    
    @Override
    public void terminate(PointCollector collector) throws Exception {
        if (allScores.isEmpty()) {
            return;
        }
        
        double[] scores = new double[allScores.size()];
        for (int i = 0; i < allScores.size(); i++) {
            scores[i] = allScores.get(i);
        }
        
        boolean[] isAnomaly = detectAnomalies(scores);
        
        int startIdx = windowSize - 1;
        for (int i = 0; i < isAnomaly.length; i++) {
            if (isAnomaly[i]) {
                int dataIdx = startIdx + i;
                collector.putDouble(allTimestamps.get(dataIdx), allValues.get(dataIdx));
            }
        }
        
        if (slidingWindow != null) {
            slidingWindow.clear();
        }
        if (slidingTimestamps != null) {
            slidingTimestamps.clear();
        }
        if (allValues != null) {
            allValues.clear();
        }
        if (allTimestamps != null) {
            allTimestamps.clear();
        }
        if (allScores != null) {
            allScores.clear();
        }
    }
    
    private double[] spectralResidualWindow(double[] series) {
        int n = series.length;
        
        double[] paddedSeries = padToPowerOfTwo(series);
        int paddedN = paddedSeries.length;
        
        Complex[] fft = fft(paddedSeries);
        
        double[] amplitude = new double[paddedN];
        double[] phase = new double[paddedN];
        for (int i = 0; i < paddedN; i++) {
            amplitude[i] = Math.log(fft[i].abs() + 1e-10);
            phase[i] = fft[i].phase();
        }
        
        double[] spectralResidual = new double[paddedN];
        for (int i = 0; i < paddedN; i++) {
            double avgAmplitude = averageFilter(amplitude, i, ampWindowSize);
            spectralResidual[i] = amplitude[i] - avgAmplitude;
        }
        
        Complex[] reconstructed = new Complex[paddedN];
        for (int i = 0; i < paddedN; i++) {
            double mag = Math.exp(spectralResidual[i]);
            reconstructed[i] = Complex.fromPolar(mag, phase[i]);
        }
        
        double[] saliencyMap = ifft(reconstructed);
        
        double[] rawScores = new double[n];
        for (int i = 0; i < n; i++) {
            rawScores[i] = Math.abs(saliencyMap[i]);
        }
        
        double[] smoothedScores = gaussianSmooth(rawScores, 3);
        
        return smoothedScores;
    }
    
    private boolean[] detectAnomalies(double[] scores) {
        int n = scores.length;
        boolean[] isAnomaly = new boolean[n];
        
        for (int i = 0; i < n; i++) {
            int start = Math.max(0, i - scoreWindowSize);
            int end = i;
            
            double localMean = 0.0;
            int count = 0;
            for (int j = start; j <= end; j++) {
                localMean += scores[j];
                count++;
            }
            localMean /= count;
            
            double localStd = 0.0;
            for (int j = start; j <= end; j++) {
                localStd += (scores[j] - localMean) * (scores[j] - localMean);
            }
            localStd = Math.sqrt(localStd / count);
            
            double zScore = (scores[i] - localMean) / (localStd + 1e-10);
            isAnomaly[i] = zScore > threshold;
        }
        
        return isAnomaly;
    }
    
    private double[] gaussianSmooth(double[] data, int windowSize) {
        int n = data.length;
        double[] smoothed = new double[n];
        
        double[] kernel = gaussianKernel(windowSize);
        int halfWindow = windowSize / 2;
        
        for (int i = 0; i < n; i++) {
            double sum = 0.0;
            double weightSum = 0.0;
            
            for (int j = -halfWindow; j <= halfWindow; j++) {
                int idx = i + j;
                if (idx >= 0 && idx < n) {
                    sum += data[idx] * kernel[j + halfWindow];
                    weightSum += kernel[j + halfWindow];
                }
            }
            
            smoothed[i] = sum / weightSum;
        }
        
        return smoothed;
    }
    
    private double[] gaussianKernel(int size) {
        double[] kernel = new double[size];
        double sigma = size / 6.0;
        int center = size / 2;
        double sum = 0.0;
        
        for (int i = 0; i < size; i++) {
            int x = i - center;
            kernel[i] = Math.exp(-(x * x) / (2 * sigma * sigma));
            sum += kernel[i];
        }
        
        for (int i = 0; i < size; i++) {
            kernel[i] /= sum;
        }
        
        return kernel;
    }
    
    private double[] padToPowerOfTwo(double[] series) {
        int n = series.length;
        int paddedN = 1;
        while (paddedN < n) {
            paddedN *= 2;
        }
        
        double[] padded = new double[paddedN];
        System.arraycopy(series, 0, padded, 0, n);
        for (int i = n; i < paddedN; i++) {
            padded[i] = 0.0;
        }
        
        return padded;
    }
    
    private double averageFilter(double[] data, int index, int windowSize) {
        int halfWindow = windowSize / 2;
        int start = Math.max(0, index - halfWindow);
        int end = Math.min(data.length - 1, index + halfWindow);
        
        double sum = 0.0;
        int count = 0;
        for (int i = start; i <= end; i++) {
            sum += data[i];
            count++;
        }
        
        return sum / count;
    }
    
    private Complex[] fft(double[] x) {
        int n = x.length;
        Complex[] result = new Complex[n];
        for (int i = 0; i < n; i++) {
            result[i] = new Complex(x[i], 0);
        }
        return fft(result);
    }
    
    private Complex[] fft(Complex[] x) {
        int n = x.length;
        
        if (n == 1) {
            return new Complex[] { x[0] };
        }
        
        if (n % 2 != 0) {
            throw new IllegalArgumentException("n must be a power of 2");
        }
        
        Complex[] even = new Complex[n / 2];
        Complex[] odd = new Complex[n / 2];
        for (int k = 0; k < n / 2; k++) {
            even[k] = x[2 * k];
            odd[k] = x[2 * k + 1];
        }
        
        Complex[] q = fft(even);
        Complex[] r = fft(odd);
        
        Complex[] y = new Complex[n];
        for (int k = 0; k < n / 2; k++) {
            double kth = -2 * k * Math.PI / n;
            Complex wk = new Complex(Math.cos(kth), Math.sin(kth));
            y[k] = q[k].plus(wk.times(r[k]));
            y[k + n / 2] = q[k].minus(wk.times(r[k]));
        }
        
        return y;
    }
    
    private double[] ifft(Complex[] x) {
        int n = x.length;
        Complex[] y = new Complex[n];
        
        for (int i = 0; i < n; i++) {
            y[i] = x[i].conjugate();
        }
        
        y = fft(y);
        
        double[] result = new double[n];
        for (int i = 0; i < n; i++) {
            result[i] = y[i].conjugate().re() / n;
        }
        
        return result;
    }
    
    
    private double getDoubleValue(Row row) throws Exception {
        switch (row.getDataType(0)) {
            case INT32:
                return row.getInt(0);
            case INT64:
                return row.getLong(0);
            case FLOAT:
                return row.getFloat(0);
            case DOUBLE:
                return row.getDouble(0);
            default:
                throw new Exception("Unsupported data type: " + row.getDataType(0));
        }
    }
    
    private static class Complex {
        private final double re;
        private final double im;
        
        public Complex(double real, double imag) {
            this.re = real;
            this.im = imag;
        }
        
        public static Complex fromPolar(double r, double theta) {
            return new Complex(r * Math.cos(theta), r * Math.sin(theta));
        }
        
        public double re() {
            return re;
        }
        
        public double im() {
            return im;
        }
        
        public Complex plus(Complex b) {
            return new Complex(this.re + b.re, this.im + b.im);
        }
        
        public Complex minus(Complex b) {
            return new Complex(this.re - b.re, this.im - b.im);
        }
        
        public Complex times(Complex b) {
            double real = this.re * b.re - this.im * b.im;
            double imag = this.re * b.im + this.im * b.re;
            return new Complex(real, imag);
        }
        
        public Complex conjugate() {
            return new Complex(re, -im);
        }
        
        public double abs() {
            return Math.sqrt(re * re + im * im);
        }
        
        public double phase() {
            return Math.atan2(im, re);
        }
    }
}
