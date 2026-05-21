# IoTDB Spectral Residual 异常检测 UDF

基于 Spectral Residual 算法的 IoTDB 时间序列异常检测用户自定义函数（UDF）。

**适配版本：IoTDB 2.0.7**

## 算法简介

Spectral Residual（频谱残差）是一种基于频域分析的异常检测算法，特别适用于检测时间序列中的异常模式。该算法通过分析信号的频谱特征来识别与正常模式不同的异常点。

### 算法原理

1. **历史数据窗口处理**：使用历史数据滑动窗口（只向前看）对时间序列进行分段处理，支持实时检测
2. **傅里叶变换**：将窗口内的时间序列从时域转换到频域
3. **幅度谱计算**：计算频域信号的幅度谱并取对数
4. **频谱残差**：通过平均滤波器计算谱残差，突出异常频率成分
5. **显著性图**：通过逆傅里叶变换得到时域的显著性图
6. **高斯平滑**：对显著性图进行高斯平滑，减少噪声和误报
7. **局部归一化**：使用局部统计量（历史窗口内的均值和标准差）进行归一化
8. **异常判定**：基于局部 z-score 和阈值判断异常点
9. **仅输出异常**：只返回检测到的异常数据点，非异常点不输出

### 算法优势

- 对周期性数据中的异常特别敏感
- 无需训练，可直接应用
- 历史数据窗口处理，符合时序数据实时检测场景（无法预知未来数据）
- 局部归一化，提高检测准确率
- 高斯平滑，减少误报
- 支持实时检测
- 只输出异常点，结果简洁明了

## 功能特性

- 支持多种数值类型（INT32, INT64, FLOAT, DOUBLE）
- 可配置窗口大小和检测阈值
- 只返回异常数据点的原始值（非异常点不输出）
- 适用于实时和批量异常检测场景

## 构建步骤

### 1. 编译打包

在项目根目录执行：

```bash
mvn clean package
```

编译成功后，会在 `target` 目录下生成：
- `iotdb-udf-1.0-SNAPSHOT.jar`
- `iotdb-udf-1.0-SNAPSHOT-jar-with-dependencies.jar`（推荐使用）

### 2. 部署到 IoTDB

将 JAR 文件复制到 IoTDB 的 UDF 目录：

```bash
# Linux/Mac
cp target/iotdb-udf-1.0-SNAPSHOT-jar-with-dependencies.jar $IOTDB_HOME/ext/udf/

# Windows
copy target\iotdb-udf-1.0-SNAPSHOT-jar-with-dependencies.jar %IOTDB_HOME%\ext\udf\
```

如果 `ext/udf` 目录不存在，需要手动创建。

### 3. 重启 IoTDB

```bash
# Linux/Mac
$IOTDB_HOME/sbin/stop-standalone.sh
$IOTDB_HOME/sbin/start-standalone.sh

# Windows
%IOTDB_HOME%\sbin\stop-standalone.bat
%IOTDB_HOME%\sbin\start-standalone.bat
```

## 使用方法

### 1. 注册 UDF 函数

连接到 IoTDB CLI 后执行：

```sql
CREATE FUNCTION sr_detect AS 'org.apache.iotdb.udf.SpectralResidualAnomalyDetector';
```

### 2. 查看已注册的函数

```sql
SHOW FUNCTIONS;
```

### 3. 使用示例

#### 基本使用（使用默认参数）

```sql
SELECT sr_detect(value) AS anomaly_value FROM root.yahoo.real_1;
```

默认参数：
- `window_size`: 100（FFT 分析窗口大小）
- `threshold`: 3.0（异常判定阈值，局部 z-score）
- `amp_window_size`: 5（频谱平滑窗口）
- `score_window_size`: 50（局部归一化窗口大小）

**重要说明**：此 UDF 使用历史数据窗口（只向前看），只返回检测到的异常数据点的原始值，非异常点不会出现在结果中。如果需要同时查看原始数据和异常检测结果，请分别查询，否则会因为 IoTDB 的时间对齐机制导致非异常时间点显示 `null`。

#### 自定义参数

```sql
SELECT sr_detect(value, 'window_size'='50', 'threshold'='2.5') AS anomaly_value FROM root.yahoo.real_1;
```

```sql
SELECT sr_detect(value, 'window_size'='100', 'threshold'='2.0') AS anomaly_value FROM root.yahoo.real_1;
```

```sql
SELECT sr_detect(value, 'window_size'='200', 'threshold'='3.5') AS anomaly_value FROM root.yahoo.real_1;
```

#### 查看前 20 个异常点

```sql
SELECT sr_detect(value) AS anomaly_value FROM root.yahoo.real_1 LIMIT 20;
```

#### 同时查看原始数据（会产生时间对齐）

如果需要对比原始数据和异常检测结果，可以这样查询，但会因为时间对齐导致非异常时间点显示 `null`：

```sql
SELECT value, sr_detect(value) AS anomaly_value FROM root.yahoo.real_1;
```

### 4. 删除 UDF 函数

```sql
DROP FUNCTION sr_detect;
```

## 参数说明

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `window_size` | int | 100 | FFT 分析窗口大小（数据点数量）。较大的窗口适合长周期数据，较小的窗口适合短周期数据 |
| `threshold` | double | 3.0 | 异常判定阈值（局部 z-score）。较小的值会检测出更多异常（更敏感），较大的值只检测明显异常 |
| `amp_window_size` | int | 5 | 频谱平滑窗口大小。用于计算频谱残差时的平均滤波器窗口 |
| `score_window_size` | int | 50 | 局部归一化窗口大小。用于计算局部均值和标准差的窗口大小（只使用历史数据） |

## 参数调优建议

### window_size（FFT 窗口大小）

- **短周期数据**（如秒级采样）：使用较小窗口（50-100）
- **长周期数据**（如分钟/小时级采样）：使用较大窗口（200-500）
- **经验法则**：窗口应至少包含 2-3 个完整的周期

### threshold（阈值）

- **高敏感度**：1.5-2.0（会检测出更多异常，可能包含误报）
- **平衡模式**：2.5-3.0（推荐，平衡准确率和召回率）
- **低敏感度**：3.5-4.0（只检测明显异常，可能遗漏部分异常）
- 注意：这是局部 z-score 阈值，不是全局阈值

### amp_window_size（频谱平滑窗口）

- 通常保持默认值 5 即可
- 较大的值会使频谱残差更平滑，但可能降低检测灵敏度
- 必须小于 `window_size`

### score_window_size（局部归一化窗口）

- 通常设置为 `window_size` 的 0.5-1 倍
- 较小的值（如 30-50）：对局部变化更敏感
- 较大的值（如 100-200）：更稳定，但可能错过短期异常
- 必须小于等于数据总长度

## 应用场景

1. **工业物联网**
   - 设备传感器异常检测
   - 生产线质量监控
   - 设备故障预警

2. **智能建筑**
   - 能耗异常监测
   - 环境参数异常检测
   - HVAC 系统监控

3. **智慧城市**
   - 交通流量异常检测
   - 环境监测数据异常识别
   - 公共设施运行监控

4. **金融科技**
   - 交易量异常检测
   - 价格波动监控

## 性能考虑

- 算法复杂度：O(n log n)，其中 n 是窗口大小
- 内存占用：与窗口大小成正比
- 建议窗口大小不超过 1000，以保证实时性能
- 对于超大数据集，建议分批处理

## 开发环境要求

- JDK 1.8 或更高版本
- Maven 3.6 或更高版本
- IoTDB 2.0.7 或兼容版本

## 项目结构

```
iotdb-udf/
├── src/
│   └── main/
│       └── java/
│           └── org/
│               └── apache/
│                   └── iotdb/
│                       └── udf/
│                           └── SpectralResidualAnomalyDetector.java
├── pom.xml
└── README.md
```

## 常见问题

### Q: 为什么检测不到异常？

A: 可能的原因：
- 窗口大小不合适（太小或太大）
- 阈值设置过高
- 数据量不足（少于窗口大小）
- 数据本身没有明显的周期性模式

建议：尝试调整 `window_size` 和 `threshold` 参数。

### Q: 为什么有很多误报？

A: 可能的原因：
- 阈值设置过低
- 数据噪声较大
- 窗口大小不匹配数据周期

建议：增大 `threshold` 值，或调整 `window_size` 以匹配数据的周期性。

### Q: 算法对什么类型的异常最敏感？

A: Spectral Residual 算法对以下类型的异常特别敏感：
- 突发的尖峰或谷值
- 周期性模式的突然变化
- 频率成分的异常变化

对于缓慢漂移的异常，可能需要结合其他方法。

### Q: 可以用于实时检测吗？

A: 可以，算法使用历史数据窗口（只向前看），符合实时检测场景：
- 算法需要累积足够的数据点（至少达到 `window_size`）才能开始输出结果
- 对于实时场景，建议使用较小的窗口（50-100）
- 使用滑动窗口方式持续检测

### Q: 如何选择合适的窗口大小？

A: 建议步骤：
1. 观察数据的周期性特征
2. 确保窗口至少包含 2-3 个完整周期
3. 从较小的窗口开始测试，逐步调整
4. 使用历史数据验证检测效果

### Q: 为什么查询结果中有 null 值？

A: 这是 IoTDB 的时间对齐机制导致的。此 UDF 只返回异常点，当你同时查询原始数据和 UDF 结果时（如 `SELECT value, sr_detect(value) FROM ...`），IoTDB 会对齐所有时间戳，非异常时间点的 UDF 列就会显示 `null`。

解决方法：只查询 UDF 结果（`SELECT sr_detect(value) FROM ...`），这样只会返回异常点，不会有 `null`。

## 技术细节

### 算法实现

- **历史数据窗口处理**：使用队列实现滑动窗口，每次处理 `window_size` 个历史数据点
- **FFT 算法**：使用 Cooley-Tukey FFT 算法进行快速傅里叶变换
- **自动填充**：自动将输入数据填充到 2 的幂次方长度
- **复数运算**：使用自定义 Complex 类处理频域信号
- **高斯平滑**：对 saliency map 应用高斯滤波器（窗口大小为 3，sigma = 0.5）
- **局部归一化**：使用历史数据窗口计算局部均值和标准差，计算局部 z-score

### 输出说明

- 只输出检测到异常的数据点
- 输出值为异常点的原始值（不是异常分数或标记）
- 非异常点不会出现在结果中
- 输出延迟为 `window_size` 个数据点（需要累积足够的历史数据）

## 参考文献

Spectral Residual 算法基于以下研究：

- Hou, X., & Zhang, L. (2007). Saliency detection: A spectral residual approach. IEEE Conference on Computer Vision and Pattern Recognition (CVPR).
- Ren, H., et al. (2019). Time-Series Anomaly Detection Service at Microsoft. ACM SIGKDD.

## 许可证

本项目仅供学习和参考使用。

---

## IoTDB 内置异常检测 UDF 对比

IoTDB 2.0.7 提供了 7 个异常检测 UDF 的实现，需要先注册后使用。

### 注册内置 UDF

```sql
CREATE FUNCTION range AS 'org.apache.iotdb.library.anomaly.UDTFRange';
CREATE FUNCTION iqr AS 'org.apache.iotdb.library.anomaly.UDTFIQR';
CREATE FUNCTION ksigma AS 'org.apache.iotdb.library.anomaly.UDTFKSigma';
CREATE FUNCTION lof AS 'org.apache.iotdb.library.anomaly.UDTFLOF';
CREATE FUNCTION two_sided_filter AS 'org.apache.iotdb.library.anomaly.UDTFTwoSidedFilter';
CREATE FUNCTION miss_detect AS 'org.apache.iotdb.library.anomaly.UDTFMissDetect';
CREATE FUNCTION outlier AS 'org.apache.iotdb.library.anomaly.UDTFOutlier';
```

### 1. Range - 范围检测

**原理**：检测超出指定上下界的数据点

**返回**：仅返回异常值（超出范围的点）

**使用**：

```sql
SELECT range(value, 'lower_bound'='0', 'upper_bound'='1') FROM root.yahoo.real_1;
```

**适用场景**：已知正常值范围，检测超限异常

### 2. IQR - 四分位距检测

**原理**：基于四分位距（IQR）检测离群点，异常定义为 Q1-1.5×IQR 或 Q3+1.5×IQR 之外的点

**返回**：仅返回异常值（离群点）

**使用**：

```sql
SELECT iqr(value) FROM root.yahoo.real_1;
```

```sql
SELECT iqr(value, 'compute'='stream', 'q1'='0.1', 'q3'='0.3') FROM root.yahoo.real_1;
```

**适用场景**：数据分布相对稳定，检测统计离群点

### 3. KSigma - K倍标准差检测

**原理**：滑动窗口内计算均值和标准差，检测偏离均值超过 k×σ 的点

**返回**：仅返回异常值（超出 k×σ 的点）

**使用**：

```sql
SELECT ksigma(value, 'k'='3', 'window'='100') FROM root.yahoo.real_1;
```

**适用场景**：数据近似正态分布，检测统计异常

### 4. LOF - 局部离群因子

**原理**：基于密度的异常检测，计算每个点相对于其邻居的局部密度偏离程度

**返回**：返回所有点的 LOF 分数（分数越高越异常，通常 >1 为异常）

**使用**：

```sql
SELECT lof(value, 'method'='default', 'k'='3', 'window'='10000') FROM root.yahoo.real_1;
```

```sql
SELECT lof(value, 'method'='series', 'k'='3', 'window'='5') FROM root.yahoo.real_1;
```

**适用场景**：多维数据或时间序列模式异常检测

### 5. TwoSidedFilter - 双侧窗口滤波

**原理**：基于双侧窗口检测并修复异常点，输出修复后的序列

**返回**：返回修复后的完整数值序列（异常值被平滑值替换）

**使用**：

```sql
SELECT two_sided_filter(value, 'len'='5', 'threshold'='0.4') FROM root.yahoo.real_1;
```

**适用场景**：异常检测与数据修复，平滑时间序列

### 6. MissDetect - 缺失值检测

**原理**：检测时间序列中的缺失段（连续 NULL 值超过指定长度）

**返回**：返回所有点的布尔值（true=缺失，false=正常）

**使用**：

```sql
SELECT miss_detect(value, 'minlen'='10') FROM root.yahoo.real_1;
```

**注意**：此函数用于检测数据中的 NULL 值段，如果数据没有 NULL 值则可能报错或无输出

**适用场景**：检测数据采集中断或传感器故障

### 7. Outlier - 距离异常检测

**原理**：滑动窗口内，如果某点周围半径 r 内的邻居数少于 k，则判定为异常

**返回**：仅返回异常值（距离异常点）

**使用**：

```sql
SELECT outlier(value, 'k'='3', 'r'='5', 'w'='1000', 's'='500') FROM root.yahoo.real_1;
```

**适用场景**：基于距离的离群点检测

### 算法对比总结

| 算法 | 类型 | 复杂度 | 适用数据 | 优势 | 局限 |
|------|------|--------|----------|------|------|
| Range | 规则 | O(1) | 已知范围 | 简单快速 | 需要先验知识 |
| IQR | 统计 | O(n log n) | 稳定分布 | 鲁棒性好 | 不适应动态变化 |
| KSigma | 统计 | O(n) | 正态分布 | 实时检测 | 对非正态分布敏感 |
| LOF | 密度 | O(n²) | 多维数据 | 检测局部异常 | 计算开销大 |
| TwoSidedFilter | 滤波 | O(n) | 平滑序列 | 同时修复 | 可能过度平滑 |
| MissDetect | 缺失 | O(n) | 时间序列 | 检测中断 | 仅针对缺失 |
| Outlier | 距离 | O(n²) | 密集数据 | 基于邻域 | 参数敏感 |
| **Spectral Residual** | **频域** | **O(n log n)** | **周期数据** | **检测模式变化** | **需要周期性** |

### 选择建议

- **已知正常范围**：使用 Range
- **统计离群点**：使用 IQR 或 KSigma
- **周期性异常**：使用 Spectral Residual（本项目）
- **多维异常**：使用 LOF
- **需要修复**：使用 TwoSidedFilter
- **检测中断**：使用 MissDetect
- **距离异常**：使用 Outlier

## 联系方式

如有问题或建议，欢迎提交 Issue。