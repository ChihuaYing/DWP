# Dual Deviation Anomaly Detection UDF

本目录提供 IoTDB 时间序列异常检测用户自定义函数（UDF）的实现与使用说明。

- **算法英文名**：Dual Deviation Anomaly Detection（DDAD）
- **中文说明**：双偏离异常检测
- **Java 类名**：`org.apache.iotdb.udf.DualDeviationAnomalyDetectionUDTF`
- **适配版本**：IoTDB 2.0.7

## 1. 算法说明

该算法面向单变量时间序列，采用“滑动窗口统计 + 自适应阈值”的方式识别异常点。其核心思想如下：

1. 使用固定长度历史窗口保存最近一段时间的数据；
2. 在窗口内计算均值、标准差和一阶自相关特征；
3. 构造统计偏离分数 `D_t`，衡量当前点相对于窗口统计特征的偏离程度；
4. 构造时序偏离分数 `T_t`，衡量当前点相对于窗口时间趋势的偏离程度；
5. 将两类偏离分数融合为最终异常评分，并结合自适应阈值输出异常点。

### 方法特点

- 只使用历史数据，适合在线检测场景；
- 同时考虑“统计特征偏离”和“时间趋势偏离”；
- 阈值会随数据分布变化而动态调整，较适合非平稳序列；
- 只输出异常点，不输出正常点。

## 2. 参数说明

| 参数名 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `window` | int | 100 | 历史窗口长度，必须不小于 20 |
| `sensitivity` | double | 3.0 | 灵敏度系数，越大越严格 |
| `minThreshold` | double | 3.0 | 最小阈值下界，用于避免阈值过低 |

### 调参

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
CREATE FUNCTION DDAD AS 'org.apache.iotdb.udf.DualDeviationAnomalyDetectionUDTF';
```


### 3.2 查看已注册函数

```sql
SHOW FUNCTIONS;
```

### 3.3 查询示例

默认参数：

```sql
SELECT DDAD(value) FROM root.yahoo.real_1;
```

自定义参数：

```sql
SELECT DDAD(value, 'window'='150', 'sensitivity'='4.0', 'minThreshold'='3.0') FROM root.yahoo.real_1;
```

仅查看异常点：

```sql
SELECT DDAD(value) FROM root.yahoo.real_1 LIMIT 20;
```

同时查看原始值与异常分数：

```sql
SELECT value, DDAD(value) FROM root.yahoo.real_1;
```

### 3.4 删除函数

```sql
DROP FUNCTION DDAD;
```

## 4. 注意事项

1. 该 UDF 需要先累积到 `window` 个历史点后才开始输出结果；
2. 返回值是异常分数，不是二值标签；
3. 如果同时查询原始列和 UDF 结果，IoTDB 会按时间对齐，未命中的时间点会显示为 `null`；
4. 优先在 Yahoo S5 数据集上根据不同序列类别做小范围调参，再进入批量实验；
5. 文档中的函数名统一使用 `DDAD`，避免与旧版本命名混淆。

## 5. 项目中的相关文件

- `src/main/java/org/apache/iotdb/udf/DualDeviationAnomalyDetectionUDTF.java`：主算法实现
- `pom.xml`：Maven 构建配置
