# 算法记录

本文记录当前 IoTDB UDF 异常检测算法的实现细节，重点是 `StanUDTFNABV2.java`。后续开发 `StanUDTFNABV3` 时，应先对照这里确认要保留、删除或重构的行为。

## StanUDTFNABV2

源码位置：

```text
iotdb-udf/src/main/java/org/apache/iotdb/udf/StanUDTFNABV2.java
```

### UDF 形态

`StanUDTFNABV2` 实现 IoTDB 的 `UDTF` 接口。

输入约束：

- 输入序列数量：1 条
- 输入类型：`INT32`、`INT64`、`FLOAT`、`DOUBLE`
- 空值处理：如果当前行输入为 `null`，直接跳过

输出：

- 输出类型：`DOUBLE`
- 输出时间戳：当前输入行的 `row.getTime()`
- 输出值：异常分数 `score`
- 输出策略：只输出判为异常的点，正常点不输出

访问策略：

```java
new RowByRowAccessStrategy()
```

也就是说，算法按时间顺序逐行处理。当前实现只使用当前点之前的历史窗口，不使用未来数据。

### 参数

当前源码只读取 3 个 UDF 参数。

| 参数 | 默认值 | 约束 | 作用 |
|---|---:|---|---|
| `window` | `64` | 最小强制为 `20` | 历史滑动窗口大小 |
| `sensitivity` | `4.5` | 最小强制为 `1.01` | 灵敏度；越大越容易报警 |
| `threshold` | `3.5` | 最小强制为 `0.0` | 基础阈值；越大越不容易报警 |

参数读取逻辑：

```java
windowSize = Math.max(MIN_WINDOW_SIZE, parameters.getIntOrDefault("window", 64));
sensitivity = Math.max(1.01, parameters.getDoubleOrDefault("sensitivity", 4.5));
threshold = Math.max(0.0, parameters.getDoubleOrDefault("threshold", 3.5));
```

SQL 示例：

```sql
SELECT STAN_DETECT_NAB_V2(
  s0,
  'window'='64',
  'sensitivity'='4.5',
  'threshold'='3.5'
) AS anomaly_score
FROM root.nab.d1;
```

### 内部常量

| 常量 | 值 | 含义 |
|---|---:|---|
| `EPS` | `1e-12` | 防止除零 |
| `MAD_TO_STD` | `1.4826` | 将 MAD 缩放为近似标准差 |
| `MIN_WINDOW_SIZE` | `20` | 最小窗口 |
| `DEFAULT_SENSITIVITY` | `4.5` | 默认灵敏度 |
| `DEFAULT_THRESHOLD` | `3.5` | 默认阈值 |
| `MIN_VARIABILITY` | `1e-8` | 最小序列波动要求 |
| `DIFF_WEIGHT` | `0.25` | 相邻差分分数权重 |
| `LEVEL_WEIGHT` | `0.35` | 水平偏移增强权重 |
| `RESIDUAL_WEIGHT` | `0.40` | 趋势预测残差权重 |
| `Z_SCORE_BOOST` | `1.15` | robust z-score 放大系数 |

其中 `DIFF_WEIGHT`、`LEVEL_WEIGHT`、`RESIDUAL_WEIGHT`、`Z_SCORE_BOOST` 在当前版本中不能通过 UDF 参数调整。

### 状态结构

算法维护一个固定长度的循环数组：

```java
private double[] buffer;
private int head;
private int count;
```

含义：

- `buffer`：保存最近 `windowSize` 个历史值
- `head`：下一个写入位置
- `count`：当前已经积累的历史点数量

当前点不会先放进窗口，而是先用窗口中已有历史计算分数，判定结束后再追加当前点。

这意味着第一个可检测点出现在：

```text
index >= windowSize
```

窗口未填满前：

```java
append(current);
return;
```

### 统计量计算

每个检测点都会从循环数组中取出按时间顺序排列的历史窗口：

```java
values[i] = buffer[(head + i) % windowSize];
```

注意：这里的 `head` 指向下一次写入位置。当窗口已满时，`head` 正好是最老值的位置，因此按 `head, head+1, ...` 可以得到从旧到新的历史窗口。

#### 中位数

```java
median = median(values)
```

计算方式：

1. 复制数组
2. 排序
3. 用线性插值取 50% 分位数

#### MAD 鲁棒尺度

```java
mad = median(abs(value - median))
scale = max(1.4826 * mad, EPS)
```

MAD 相比标准差更不容易被极端值拉大。`1.4826` 是在正态分布假设下把 MAD 转成标准差尺度的常用系数。

#### 相邻差分尺度

```java
diffs[i - 1] = abs(values[i] - values[i - 1])
diffScale = max(1.4826 * median(diffs), EPS)
```

它用于衡量当前点相对上一点的变化是否异常。

这里没有使用差分的 MAD，而是直接取 `abs(diff)` 的中位数再乘 `1.4826`。这是一种近似尺度估计，但严格来说和普通 MAD 不完全一样。

#### 残差尺度

```java
residualScale = max(scale, diffScale)
```

残差尺度取水平尺度和差分尺度的较大值，目的是避免趋势预测残差被过小尺度放大。

### 三类异常分数

给定当前值：

```java
current
```

算法计算三类分数。

#### 1. Robust Z 分数

```java
robustZ = abs(current - median) / scale
```

含义：当前点相对历史窗口中位数偏离了多少个鲁棒标准差。

它主要捕捉水平值异常，例如突然升高、突然降低、落到历史分布尾部。

#### 2. 差分分数

```java
previous = lastValue()
diffZ = abs(current - previous) / diffScale
```

含义：当前点相对上一点的变化幅度是否异常。

它主要捕捉相邻点突变，对尖峰、阶跃边界、周期边界都会敏感。

#### 3. 趋势预测残差分数

预测逻辑在 `RollingStats.predictNext()` 中：

```java
delta1 = last - prev
delta2 = prev - prev2
trend = 0.7 * delta1 + 0.3 * delta2
predicted = last + trend
```

对应分数：

```java
residualZ = abs(current - predicted) / residualScale
```

含义：用最近三个点估计下一步趋势，当前点偏离该预测多少。

它主要捕捉短期趋势破坏，例如当前点没有延续最近的局部走势。

### 总分公式

当前实现的总分为：

```java
score =
  zScoreBoost * robustZ
  + diffWeight * diffZ
  + levelWeight * max(0.0, robustZ - 1.0)
  + residualWeight * residualZ;
```

代入默认常量：

```text
score =
  1.15 * robustZ
  + 0.25 * diffZ
  + 0.35 * max(0, robustZ - 1)
  + 0.40 * residualZ
```

随后强制保证：

```java
score = max(score, max(robustZ, residualZ));
```

这意味着：

- 即使加权公式给出的分数较低，最终分数也不会低于 `robustZ`
- 最终分数也不会低于 `residualZ`
- `diffZ` 没有同等保底，只通过 `0.25 * diffZ` 进入总分

### 异常判定

判定逻辑：

```java
if (score >= threshold / sensitivity) {
  collector.putDouble(time, score);
}
```

也就是：

```text
effectiveThreshold = threshold / sensitivity
```

默认值：

```text
effectiveThreshold = 3.5 / 4.5 = 0.777...
```

因此：

- 增大 `threshold` 会提高门槛，减少报警
- 增大 `sensitivity` 会降低门槛，增加报警
- 二者在当前公式中只通过比值发挥作用

这也是当前版本的一个重要设计问题：`threshold` 和 `sensitivity` 在判定层面高度冗余。不同参数组合只要比例相同，判定门槛就相同。

### 平坦序列处理

如果窗口尺度过小：

```java
if (stats.scale <= minSeriesVariability) {
  append(current);
  return;
}
```

其中：

```text
minSeriesVariability = 1e-8
```

这会让完全平坦或近似平坦序列不产生异常输出，避免除以极小数造成误报。

注意：这里只检查 `stats.scale`，不检查 `diffScale` 或 `residualScale`。

### 输出语义

如果异常成立：

```java
collector.putDouble(time, score);
```

输出的是异常分数，不是原始值，也不是布尔值。

正常点没有输出。因此在 IoTDB 查询结果中，只有异常点对应的时间戳会出现。

用于 NAB 导出时，需要把这些稀疏输出补成完整 CSV：

```text
timestamp,value,anomaly_score
```

### 算法直觉

`StanUDTFNABV2` 可以理解为一个基于鲁棒统计的流式异常检测器。

它综合了三类信号：

1. 当前值是否偏离历史分布中心
2. 当前点相对上一点是否突变
3. 当前点是否偏离最近短期趋势预测

相比使用均值和标准差的普通 z-score，它使用中位数和 MAD，更能抵抗窗口中已有异常点对统计量的污染。

### 适合的异常类型

该版本理论上更适合：

- 尖峰
- 突降
- 阶跃变化
- 局部突变
- 与短期趋势不一致的点

### 不擅长的情况

当前实现没有显式建模：

- 周期性
- 同一日周期相位
- 长期趋势
- 上下文异常
- 异常窗口内的早报/晚报收益
- 每条序列的报警数量控制

因此在 NAB 上可能出现：

- 把正常周期边界当成异常
- 对 `artificialNoAnomaly` 的 daily pattern 产生误报
- 对真实异常窗口附近但非标签精确点的检测在点级 F1 中被惩罚
- 对趋势型或上下文型异常不敏感

### 计算复杂度

每个点都会：

1. 复制 `windowSize` 个历史值
2. 排序求中位数
3. 构造差分数组
4. 排序求差分中位数
5. 构造绝对偏差数组
6. 排序求 MAD

主要复杂度约为：

```text
O(windowSize log windowSize)
```

内存复杂度：

```text
O(windowSize)
```

但由于每个点都会创建多个临时数组，实际 GC 压力会比纯环形缓冲实现更大。

### 当前版本的关键问题

#### 1. `threshold` 和 `sensitivity` 冗余

二者只通过 `threshold / sensitivity` 发挥作用，调参空间实际是一维的。

V3 可以考虑改成单一参数：

```text
scoreThreshold
```

或者保留二者但赋予不同语义，例如：

- `threshold` 控制绝对分数门槛
- `sensitivity` 控制自适应阈值或统计尺度

#### 2. 缺少周期建模

NAB artificial daily 数据中，正常的日周期边界可能触发 `diffZ` 或 `residualZ`。

V3 可以考虑加入：

```text
seasonalPeriod
```

例如 5 分钟粒度日周期：

```text
seasonalPeriod = 288
```

然后使用同相位历史点建立 seasonal baseline。

#### 3. 权重不可调

当前权重写死：

```text
diffWeight = 0.25
levelWeight = 0.35
residualWeight = 0.40
zScoreBoost = 1.15
```

V3 可以考虑参数化，至少在实验阶段允许调参。

#### 4. 没有报警冷却机制

当前每个点独立判定。对于一个持续异常区间，可能连续输出多个点。

V3 可以考虑：

```text
cooldown
```

用于控制同一异常事件附近不要重复报警。

#### 5. 没有全局或每序列报警数量控制

如果用于 NAB 官方评分，过多窗口外报警会扣分。当前版本没有 `maxAlerts` 或 top fraction 控制。

但是如果加入数量控制，需要明确算法是否仍然是流式检测。全局 top-k 会引入对未来数据的依赖，不再是严格在线算法。

#### 6. 输出分数未归一化

输出 `score` 没有上界。用于 NAB 官方 scorer 时可以：

- 二值化：检测点为 `1.0`
- 原样输出：`raw`
- 映射到 `[0, 1]`：例如 logistic

不同导出方式会影响 NAB optimize 阈值效果。

### V3 设计备忘

后续开发 `StanUDTFNABV3` 时建议先明确目标：

1. 是否保持严格流式？
2. 是否要针对 NAB 官方 score 优化？
3. 是否要针对点级 F1 优化？
4. 是否允许使用全序列排序或 top-k？
5. 是否要显式支持周期性？

建议优先考虑的改动：

- 合并或重新定义 `threshold` / `sensitivity`
- 增加 seasonal baseline
- 增加 cooldown
- 参数化 score 权重
- 输出更多可解释调试分数，至少在 debug 模式下输出 `robustZ/diffZ/residualZ`
- 减少每行排序和临时数组创建，降低大窗口下的计算开销

## StanUDTFNABV3

源码位置：

```text
iotdb-udf/src/main/java/org/apache/iotdb/udf/StanUDTFNABV3.java
```

V3 是基于 V2 的一次定向改造，目标是先解决两个明确问题：

1. 删除 `sensitivity`，消除 `threshold / sensitivity` 造成的参数冗余。
2. 增加可选周期建模，降低正常周期边界上的误报。

### 参数

| 参数 | 默认值 | 约束 | 作用 |
|---|---:|---|---|
| `window` | `64` | `>= 20` | 本地鲁棒统计窗口 |
| `threshold` | `3.5` | `> 0` | 最终异常分数阈值 |
| `seasonalPeriod` | `0` | `>= 0` | 周期长度；`0` 表示关闭周期建模 |
| `seasonalLookback` | `7` | `>= 1` | 使用过去多少个周期的同相位点 |
| `minSeasonalSamples` | `3` | `>= 1` | 启用周期统计所需的最少同相位样本数 |
| `autoSeasonal` | `true` | `true/false` | 当 `seasonalPeriod=0` 时，是否用自相关自动估计周期 |
| `autoSeasonalMinPeriod` | `20` | `>= 2` | 自动周期搜索的最小 lag |
| `autoSeasonalMaxPeriod` | `512` | `>= 2` | 自动周期搜索的最大 lag |
| `autoSeasonalMinCorrelation` | `0.75` | `(0, 1]` | 接受自动周期所需的最小自相关系数 |
| `autoSeasonalRecomputeInterval` | `64` | `>= 1` | 每隔多少行重新估计一次周期 |

示例：

```sql
SELECT STAN_DETECT_NAB_V3(
  s0,
  'window'='64',
  'threshold'='3.5',
  'seasonalPeriod'='288',
  'seasonalLookback'='7',
  'minSeasonalSamples'='3'
) AS anomaly_score
FROM root.nab.d1;
```

如果 NAB 数据是 5 分钟粒度，则一天的周期为：

```text
24 * 60 / 5 = 288
```

因此人工日周期数据可以先测试：

```text
seasonalPeriod = 288
```

### 与 V2 的主要区别

#### 删除 sensitivity

V2 的判定逻辑是：

```text
score >= threshold / sensitivity
```

V3 改为：

```text
score >= threshold
```

这样 `threshold` 是唯一的异常分数门槛，参数语义更直接。

#### 增加同相位周期统计

如果 `seasonalPeriod > 0`，V3 会优先使用用户指定的周期，并收集过去同一周期相位的值：

```text
current - seasonalPeriod
current - 2 * seasonalPeriod
current - 3 * seasonalPeriod
...
```

最多取 `seasonalLookback` 个样本。若样本数少于 `minSeasonalSamples`，周期建模暂时不启用，退回本地窗口逻辑。

周期统计使用和 V2 类似的 median/MAD：

```text
seasonalMedian = median(same_phase_values)
seasonalScale = 1.4826 * MAD(same_phase_values)
seasonalZ = abs(current - seasonalMedian) / seasonalScale
```

当周期统计可用时，V3 使用 `seasonalZ` 作为主要水平偏离分数；否则使用本地窗口的 robust z-score。

#### 自相关自动估计周期

如果：

```text
seasonalPeriod = 0
autoSeasonal = true
```

V3 会尝试用历史序列的自相关系数自动估计周期。

搜索范围：

```text
autoSeasonalMinPeriod <= lag <= autoSeasonalMaxPeriod
```

对每个候选 `lag`，计算：

```text
corr(lag) =
  sum((x[t] - mean) * (x[t-lag] - mean))
  / sqrt(sum((x[t] - mean)^2) * sum((x[t-lag] - mean)^2))
```

选择自相关系数最大的 `lag`。如果最大相关系数满足：

```text
corr(lag) >= autoSeasonalMinCorrelation
```

则把该 `lag` 作为当前自动周期。

为了避免历史不足时误选短周期，自动估计至少需要积累：

```text
autoSeasonalMaxPeriod * minSeasonalSamples
```

个历史点。默认参数下是：

```text
512 * 3 = 1536
```

个点。

估计出的周期不会每一行都重算，而是每隔：

```text
autoSeasonalRecomputeInterval
```

行更新一次。默认每 64 行更新一次。

如果用户显式设置：

```text
seasonalPeriod > 0
```

则不会使用自动估计结果。

#### 周期门控 transition score

V2 中 `diffZ` 和 `residualZ` 对周期边界很敏感。例如方波或日周期在固定时刻发生正常跳变，也会产生较大的相邻差分和预测残差。

V3 在周期统计可用时，用同相位水平异常程度对 transition score 做门控：

```text
transitionGate = min(1.0, levelZ / threshold)
```

然后：

```text
score =
  1.15 * levelZ
  + 0.35 * max(0, levelZ - 1)
  + transitionGate * (0.25 * diffZ + 0.40 * residualZ)
```

含义：

- 如果当前点相对过去同一相位是正常的，`levelZ` 较小，`transitionGate` 接近 0。
- 即使相邻差分很大，也会被抑制。
- 如果当前点相对过去同一相位也异常，`levelZ` 较大，transition score 会重新参与判定。

这正是为了减少 `artificialNoAnomaly/art_daily_*` 这类正常周期边界的误报。

### 需要注意的行为

#### 冷启动更长

本地窗口冷启动仍然是：

```text
count < window
```

但周期建模还需要：

```text
seasonalPeriod * minSeasonalSamples
```

个历史点后才可能启用。

例如：

```text
seasonalPeriod = 288
minSeasonalSamples = 3
```

至少需要约 3 天的同相位历史，周期统计才会启用。

#### 历史缓冲区变大

V3 的历史缓冲区大小为：

```text
max(window, seasonalPeriod * seasonalLookback)
```

如果启用自动周期且没有手动指定 `seasonalPeriod`，缓冲区大小为：

```text
max(window, autoSeasonalMaxPeriod * seasonalLookback)
```

例如：

```text
window = 64
seasonalPeriod = 288
seasonalLookback = 7
```

需要保存：

```text
288 * 7 = 2016
```

个历史点。

默认自动周期参数下：

```text
autoSeasonalMaxPeriod = 512
seasonalLookback = 7
```

会保存：

```text
512 * 7 = 3584
```

个历史点。

#### 周期建模关闭时接近 V2，但不完全等价

当：

```text
seasonalPeriod = 0
autoSeasonal = false
```

V3 不使用周期统计，整体思路接近 V2。

但由于 V3 删除了 `sensitivity`，默认判定门槛从 V2 的：

```text
3.5 / 4.5 = 0.777...
```

变成：

```text
3.5
```

因此 V3 默认会明显更保守。

### 后续可验证的问题

开发完成后优先验证：

1. `seasonalPeriod=288` 是否减少 `artificialNoAnomaly` 中 s0/s1/s2 的误报。
2. `seasonalPeriod=0` 时，V3 是否仍能检测 V2 擅长的尖峰/阶跃异常。
3. `threshold` 在 `2.0, 3.0, 3.5, 5.0` 附近的敏感性。
4. `seasonalLookback` 对短序列和长序列的影响。
5. 点级 F1 和 NAB 官方 score 是否出现不同趋势。

## Yahoo S5 内置异常检测 Baseline

使用 `test_Yahoo_baseline.py` 在 Yahoo S5 全量 367 条序列上测试 IoTDB 内置异常检测 UDF，标签按点级别精确匹配计算 `precision / recall / F1`。MISSDETECT 用于排除完美线性段，RANGE 需要手动指定上下界，二者不再纳入 Yahoo S5 baseline 对比。

### A1

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.240876 | 0.612942 | 0.345842 | 4247 | 1023 | 3224 | 646 |
| KSIGMA | 0.648936 | 0.328939 | 0.436581 | 846 | 549 | 297 | 1120 |
| TWOSIDEDFILTER | 0.025633 | 0.143799 | 0.043510 | 9363 | 240 | 9123 | 1429 |
| OUTLIER | 0.020248 | 0.687238 | 0.039337 | 56648 | 1147 | 55501 | 522 |

### A2

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.994764 | 0.407725 | 0.578387 | 191 | 190 | 1 | 276 |
| KSIGMA | 0.995146 | 0.439914 | 0.610119 | 206 | 205 | 1 | 261 |
| TWOSIDEDFILTER | 0.003772 | 0.982833 | 0.007515 | 121416 | 458 | 120958 | 8 |
| OUTLIER | 0.003283 | 1.000000 | 0.006545 | 141928 | 466 | 141462 | 0 |

### A3

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.992063 | 0.132556 | 0.233863 | 126 | 125 | 1 | 818 |
| KSIGMA | 1.000000 | 0.115589 | 0.207224 | 109 | 109 | 0 | 834 |
| TWOSIDEDFILTER | 0.006411 | 0.568399 | 0.012678 | 83610 | 536 | 83074 | 407 |
| OUTLIER | 0.005625 | 0.998940 | 0.011187 | 167470 | 942 | 166528 | 1 |

### A4

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.057177 | 0.138889 | 0.081006 | 2536 | 145 | 2391 | 899 |
| KSIGMA | 0.223195 | 0.097701 | 0.135909 | 457 | 102 | 355 | 942 |
| TWOSIDEDFILTER | 0.006591 | 0.589080 | 0.013035 | 93314 | 615 | 92699 | 429 |
| OUTLIER | 0.006210 | 0.996169 | 0.012344 | 167460 | 1040 | 166420 | 4 |

分集合看，`KSIGMA` 在 A1、A2、A4 上 F1 最高，主要优势是报警数少、precision 高；`IQR` 在 A3 上 F1 最高。`TWOSIDEDFILTER` 和 `OUTLIER` 的召回通常更高，但检测点数过多，FP 过大，点级 F1 明显偏低。

## Yahoo S5 上 STAN V2 Threshold 调参

固定 `window=150`、`sensitivity=4.5`，只调整 `threshold`。由于 V2 的实际判定门槛是 `threshold / sensitivity`，提高 `threshold` 可以显著减少报警数量和 FP。

| threshold | Precision | Recall | F1 | 检测点数 | FP |
|---:|---:|---:|---:|---:|---:|
| 3.5 | 0.009841 | 0.920184 | 0.019473 | 385434 | 381641 |
| 10 | 0.032082 | 0.782387 | 0.061636 | 100524 | 97299 |
| 20 | 0.167304 | 0.594614 | 0.261134 | 14650 | 12199 |
| 30 | 0.264226 | 0.385250 | 0.313462 | 6010 | 4422 |
| 31 | 0.268525 | 0.369238 | 0.310930 | 5668 | 4146 |
| 35 | 0.277001 | 0.317322 | 0.295794 | 4722 | 3414 |
| 40 | 0.292732 | 0.280446 | 0.286458 | 3949 | 2793 |

当前结果中 `threshold=30` 最好，F1 达到 `0.313462`，相比默认 `threshold=3.5` 大幅减少 FP。继续增大 threshold 会继续降低 FP，但 recall 下降更明显，整体 F1 开始回落。另测 `window` 未带来更好的结果，后续 V2 调参可优先围绕 `threshold=30` 附近做小范围确认。

## Yahoo S5 Baseline 与 STANNABV2 分集合对比

STANNABV2 使用 `window=150`、`sensitivity=4.5`、`top-k=0`，并对每个 Yahoo S5 子集合单独扫描 `threshold` 后取点级 F1 最好的结果。

### A1

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.240876 | 0.612942 | 0.345842 | 4247 | 1023 | 3224 | 646 |
| KSIGMA | 0.648936 | 0.328939 | 0.436581 | 846 | 549 | 297 | 1120 |
| TWOSIDEDFILTER | 0.025633 | 0.143799 | 0.043510 | 9363 | 240 | 9123 | 1429 |
| OUTLIER | 0.020248 | 0.687238 | 0.039337 | 56648 | 1147 | 55501 | 522 |
| STANNABV2 (threshold=59) | 0.334858 | 0.438586 | 0.379767 | 2186 | 732 | 1454 | 937 |

### A2

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.994764 | 0.407725 | 0.578387 | 191 | 190 | 1 | 276 |
| KSIGMA | 0.995146 | 0.439914 | 0.610119 | 206 | 205 | 1 | 261 |
| TWOSIDEDFILTER | 0.003772 | 0.982833 | 0.007515 | 121416 | 458 | 120958 | 8 |
| OUTLIER | 0.003283 | 1.000000 | 0.006545 | 141928 | 466 | 141462 | 0 |
| STANNABV2 (threshold=37) | 0.568862 | 0.611588 | 0.589452 | 501 | 285 | 216 | 181 |

### A3

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.992063 | 0.132556 | 0.233863 | 126 | 125 | 1 | 818 |
| KSIGMA | 1.000000 | 0.115589 | 0.207224 | 109 | 109 | 0 | 834 |
| TWOSIDEDFILTER | 0.006411 | 0.568399 | 0.012678 | 83610 | 536 | 83074 | 407 |
| OUTLIER | 0.005625 | 0.998940 | 0.011187 | 167470 | 942 | 166528 | 1 |
| STANNABV2 (threshold=22) | 0.726483 | 0.532344 | 0.614443 | 691 | 502 | 189 | 441 |

### A4

| 算法 | Precision | Recall | F1 | 检测点数 | TP | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| IQR | 0.057177 | 0.138889 | 0.081006 | 2536 | 145 | 2391 | 899 |
| KSIGMA | 0.223195 | 0.097701 | 0.135909 | 457 | 102 | 355 | 942 |
| TWOSIDEDFILTER | 0.006591 | 0.589080 | 0.013035 | 93314 | 615 | 92699 | 429 |
| OUTLIER | 0.006210 | 0.996169 | 0.012344 | 167460 | 1040 | 166420 | 4 |
| STANNABV2 (threshold=21) | 0.196241 | 0.430077 | 0.269508 | 2288 | 449 | 1839 | 595 |

分集合调参后，STANNABV2 在 A3、A4 上明显优于内置 baseline，在 A1 上低于 KSIGMA 但高于 IQR，在 A2 上略低于 KSIGMA、略高于 IQR。A4 仍是最困难的集合，STANNABV2 虽然提高了 F1，但 FP 仍然较多。
