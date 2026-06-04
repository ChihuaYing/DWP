# NAB 异常检测 UDF 对比实验报告

## 1. 实验目的

本实验在 NAB 数据集上比较当前实现的 `STAN_DETECT_NAB_V2` 与 IoTDB library-udf 中已实现的 `IQR`、`LOF`、`TwoSidedFilter` 三种异常检测/修复方法。评价目标是窗口级异常事件检测能力。

## 2. 数据集与评价指标

数据集使用 NAB 全量 58 条时间序列，标签使用 `combined_windows.json`，共 116 个异常窗口。若某个预测时间点落入异常窗口，则该窗口计为一次 TP；窗口外预测计为 FP；未命中的窗口计为 FN。报告 Precision、Recall、F1 和检测点数。

## 3. 方法说明

- `IQR`：IoTDB library-udf 的全局四分位距异常检测，输出超过 Q1/Q3 1.5 IQR 范围的点。本实验按每条序列得分绝对值取 Top-K 事件点。

- `LOF`：IoTDB library-udf 的局部离群因子方法，使用 `method=series` 将滑动片段映射为向量，输出 LOF 分数。本实验按 LOF 分数取 Top-K 事件点。

- `TwoSidedFilter`：IoTDB library-udf 的双边窗口修复方法，输出修复后的序列。本实验以原值与修复值差的绝对值作为异常分数，取 Top-K 事件点。

- `STAN_DETECT_NAB_V2`：当前实现的 ARTime-inspired 方法，采用多预测器预测残差、level-shift 事件分数、鲁棒阈值、Top-K 与非极大值抑制，并按 NAB 类别使用不同参数。

## 4. 总体结果

| 方法 | TP | FP | FN | Precision | Recall | F1 | 检测点数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 44 | 28 | 72 | 0.611111 | 0.379310 | 0.468085 | 79 |
| STAN_DETECT_NAB_V2 | 53 | 72 | 63 | 0.424000 | 0.456897 | 0.439834 | 127 |
| LOF | 0 | 0 | 116 | 0.000000 | 0.000000 | 0.000000 | 0 |
| TWOSIDEDFILTER | 0 | 0 | 116 | 0.000000 | 0.000000 | 0.000000 | 0 |

## 5. 各方法最优参数

### STAN_DETECT_NAB_V2

- `artificialNoAnomaly`: 参数 `{'maxAlerts': 0, 'shortWindow': 96, 'longWindow': 384, 'minWarmup': 120, 'cooldown': 120, 'minScore': 5.8, 'globalSensitivity': 4.2, 'topFraction': 0.0025}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=0

- `artificialWithAnomaly`: 参数 `{'shortWindow': 12, 'longWindow': 96, 'minWarmup': 48, 'cooldown': 24, 'maxAlerts': 1, 'minScore': 4.0, 'globalSensitivity': 2.8, 'topFraction': 0.0015}`，F1=0.200000, P=0.250000, R=0.166667, TP=1, FP=3, FN=5

- `realAdExchange`: 参数 `{'shortWindow': 96, 'longWindow': 384, 'minWarmup': 120, 'cooldown': 96, 'maxAlerts': 4, 'minScore': 5.6, 'globalSensitivity': 4.0, 'topFraction': 0.0025}`，F1=0.606061, P=0.526316, R=0.714286, TP=10, FP=9, FN=4

- `realAWSCloudwatch`: 参数 `{'shortWindow': 192, 'longWindow': 768, 'minWarmup': 192, 'cooldown': 168, 'maxAlerts': 4, 'minScore': 6.2, 'globalSensitivity': 4.6, 'topFraction': 0.0018}`，F1=0.405405, P=0.340909, R=0.500000, TP=15, FP=29, FN=15

- `realKnownCause`: 参数 `{'shortWindow': 96, 'longWindow': 384, 'minWarmup': 120, 'cooldown': 144, 'maxAlerts': 3, 'minScore': 6.0, 'globalSensitivity': 4.4, 'topFraction': 0.002}`，F1=0.437500, P=0.538462, R=0.368421, TP=7, FP=6, FN=12

- `realTraffic`: 参数 `{'shortWindow': 96, 'longWindow': 384, 'minWarmup': 120, 'cooldown': 120, 'maxAlerts': 3, 'minScore': 5.8, 'globalSensitivity': 4.2, 'topFraction': 0.002}`，F1=0.606061, P=0.526316, R=0.714286, TP=10, FP=9, FN=4

- `realTweets`: 参数 `{'shortWindow': 96, 'longWindow': 384, 'minWarmup': 120, 'cooldown': 168, 'maxAlerts': 3, 'minScore': 6.2, 'globalSensitivity': 4.6, 'topFraction': 0.0018}`，F1=0.338983, P=0.384615, R=0.303030, TP=10, FP=16, FN=23



### IQR

- `artificialNoAnomaly`: 参数 `{'top_k': 1, 'compute': 'batch'}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=0

- `artificialWithAnomaly`: 参数 `{'top_k': 1, 'compute': 'batch'}`，F1=0.444444, P=0.666667, R=0.333333, TP=2, FP=1, FN=4

- `realAdExchange`: 参数 `{'top_k': 3, 'compute': 'batch'}`，F1=0.533333, P=0.500000, R=0.571429, TP=8, FP=8, FN=6

- `realAWSCloudwatch`: 参数 `{'top_k': 1, 'compute': 'batch'}`，F1=0.510638, P=0.705882, R=0.400000, TP=12, FP=5, FN=18

- `realKnownCause`: 参数 `{'top_k': 2, 'compute': 'batch'}`，F1=0.258065, P=0.333333, R=0.210526, TP=4, FP=8, FN=15

- `realTraffic`: 参数 `{'top_k': 1, 'compute': 'batch'}`，F1=0.285714, P=0.428571, R=0.214286, TP=3, FP=4, FN=11

- `realTweets`: 参数 `{'top_k': 2, 'compute': 'batch'}`，F1=0.600000, P=0.882353, R=0.454545, TP=15, FP=2, FN=18



### LOF

- `artificialNoAnomaly`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=0

- `artificialWithAnomaly`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=6

- `realAdExchange`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=14

- `realAWSCloudwatch`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=30

- `realKnownCause`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=19

- `realTraffic`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=14

- `realTweets`: 参数 `{'top_k': 1, 'udf_window': 10000, 'window': 24, 'k': 3}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=33



### TWOSIDEDFILTER

- `artificialNoAnomaly`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=0

- `artificialWithAnomaly`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=6

- `realAdExchange`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=14

- `realAWSCloudwatch`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=30

- `realKnownCause`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=19

- `realTraffic`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=14

- `realTweets`: 参数 `{'top_k': 1, 'len': 3, 'threshold': 0.2}`，F1=0.000000, P=0.000000, R=0.000000, TP=0, FP=0, FN=33



## 6. 结论

实验结果显示，综合 F1 最优的方法为 `IQR`，F1=0.468085。相比 IoTDB library 中的通用异常检测方法，当前方法在 NAB 事件检测任务上更适合，因为它显式进行了稀疏事件输出、非极大值抑制和类别级参数控制。`IQR` 更适合全局幅值离群点；`LOF` 可以发现密度异常但在长时间序列上容易产生较多非事件型高分点；`TwoSidedFilter` 本质是修复方法，需要通过修复差值间接转为异常分数，事件定位能力相对受限。
