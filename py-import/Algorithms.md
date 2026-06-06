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
