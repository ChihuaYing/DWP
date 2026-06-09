# Adaptive Robust Rolling Anomaly Detection UDF

本目录提供 IoTDB 时间序列异常检测用户自定义函数（UDF）的实现与使用说明。

- **算法英文名**：Adaptive Robust Rolling Anomaly Detection
- **中文说明**：自适应鲁棒滚动异常检测
- **Java 类名**：`org.apache.iotdb.udf.AdaptiveRobustRollingAnomalyUDTF`
- **适配版本**：IoTDB 2.0.7

## 1. 算法说明

该算法面向单变量时间序列，采用“滑动窗口统计 + 自适应阈值”的方式识别异常点。核心思想是：

1. 使用固定长度历史窗口保存最近一段时间的数据；
2. 在窗口内计算均值、标准差和一阶自相关特征；
3. 将当前值与窗口统计特征进行比较，构造偏离分数；
4. 利用指数滑动平均更新自适应阈值；
5. 当偏离分数超过阈值时，输出该时刻的异常分数。

### 方法特点

- 只使用历史数据，适合在线检测场景；
- 同时考虑“整体水平偏离”和“短期时序偏离”；
- 阈值会随数据分布变化而动态调整，较适合非平稳序列；
- 只输出异常点，不输出正常点。

## 2. 参数说明

| 参数名 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `window` | int | 100 | 历史窗口长度，必须不小于 20 |
| `sensitivity` | double | 3.0 | 灵敏度系数，越大越严格 |
| `minThreshold` | double | 3.0 | 最小阈值下界，用于避免阈值过低 |

### 调参建议

- `window`
  - 采样稳定、周期较明显：可取 100~200
  - 数据波动较快：可取 20~80
  - 窗口越大，越平滑，但响应越慢

- `sensitivity`
  - 较大：报警更少，precision 往往更高
  - 较小：更敏感，recall 往往更高，但误报也可能增加

- `minThreshold`
  - 用于控制最小报警门槛
  - 当数据噪声较大时，适当提高可减少误报

## 3. 注册与运行

### 3.1 注册函数

在 IoTDB CLI 中执行：

```sql
CREATE FUNCTION ARRAD AS 'org.apache.iotdb.udf.AdaptiveRobustRollingAnomalyUDTF';
```

> 建议函数名使用 `ARRAD`，即 **Adaptive Robust Rolling Anomaly Detection** 的缩写，中文可理解为“自适应鲁棒滚动异常检测”。

### 3.2 查看已注册函数

```sql
SHOW FUNCTIONS;
```

### 3.3 查询示例

默认参数：

```sql
SELECT ARRAD(value) FROM root.yahoo.real_1;
```

自定义参数：

```sql
SELECT ARRAD(value, 'window'='150', 'sensitivity'='4.0', 'minThreshold'='3.0') FROM root.yahoo.real_1;
```

仅查看异常点：

```sql
SELECT ARRAD(value) FROM root.yahoo.real_1 LIMIT 20;
```

同时查看原始值与异常分数：

```sql
SELECT value, ARRAD(value) FROM root.yahoo.real_1;
```

### 3.4 删除函数

```sql
DROP FUNCTION ARRAD;
```

## 4. 注意事项

1. 该 UDF 需要先累积到 `window` 个历史点后才开始输出结果；
2. 返回值是异常分数，不是二值标签；
3. 如果同时查询原始列和 UDF 结果，IoTDB 会按时间对齐，未命中的时间点会显示为 `null`；
4. 建议优先在 Yahoo S5 数据集上根据不同序列类别做小范围调参，再进入批量实验；
5. 当前项目主线已经切换到 Yahoo S5，不再保留 NAB 的运行说明。

## 5. 项目中的相关文件

- `src/main/java/org/apache/iotdb/udf/AdaptiveRobustRollingAnomalyUDTF.java`：主算法实现
- `src/main/java/org/apache/iotdb/udf/SeasonalStanUDTF.java`：季节性变体
- `src/main/java/org/apache/iotdb/udf/StanUDTFNABV2.java`：历史版本，仅保留代码，不再作为文档主线

## 6. 参考说明

本算法更适合描述为“滚动统计 + 自适应阈值”的在线异常检测方法。若后续需要写论文或实验报告，建议统一使用英文名 **Adaptive Robust Rolling Anomaly Detection** 和中文名 **自适应鲁棒滚动异常检测**。
