# Yahoo S5 实验说明

本文档只记录最终采用的 `Adaptive Robust Rolling Anomaly Detection`（简称 `ARRAD`）在 Yahoo S5 数据集上的实验设置、调参逻辑、对比基线与图表绘制方式。

## 1. 实验目标

- 验证 `Adaptive Robust Rolling Anomaly Detection`（简称 `ARRAD`）在 Yahoo S5 上的检测效果；
- 与 IoTDB 内置算法做点级别对比；
- 记录不同参数下的 F1、Precision、Recall 变化；
- 输出实验图，便于在报告或论文中展示。

## 2. 数据集说明

Yahoo S5 数据按四类基准集组织：

- `A1Benchmark`
- `A2Benchmark`
- `A3Benchmark`
- `A4Benchmark`

每个 CSV 文件包含时间戳、数值和标签列。脚本会自动识别不同 benchmark 的标签字段，并按点级别计算指标。

## 3. 基线算法

`experiments/test_Yahoo_baseline.py` 中默认对比以下 IoTDB 内置算法：

- `IQR`
- `KSIGMA`
- `TWOSIDEDFILTER`
- `OUTLIER`

## 4. 实验流程

### 4.1 数据导入

先将 Yahoo S5 数据导入 IoTDB，对应脚本位于 `py-import/import_Yahoo_S5_All.py`。

### 4.2 注册 UDF

在 IoTDB CLI 中注册算法：

```sql
CREATE FUNCTION ARRAD AS 'org.apache.iotdb.udf.AdaptiveRobustRollingAnomalyUDTF';
```

### 4.3 运行基线评估

执行：

```bash
python experiments/test_Yahoo_baseline.py
```

脚本会：

1. 扫描 Yahoo S5 四个 benchmark 的 CSV 文件；
2. 连接 IoTDB；
3. 自动注册基线函数；
4. 执行批量查询；
5. 汇总 Precision、Recall、F1、TP、FP、FN。

### 4.4 调参逻辑

当前主算法采用三个关键参数：

- `window`：历史窗口长度；
- `sensitivity`：灵敏度控制；
- `minThreshold`：最小阈值下界。

建议调参顺序：

1. 先固定 `window`，观察整体报警密度；
2. 再调 `sensitivity`，控制误报与漏报平衡；
3. 最后微调 `minThreshold`，避免阈值过低导致噪声报警。

## 5. 图表绘制

### 5.1 原始数据可视化

`experiments/plot_Yahoo_results.py` 用于绘制 Yahoo S5 原始序列及标签点；如果图中需要标注算法结果，应统一使用 `ARRAD` 作为算法名。脚本会遍历每个 benchmark 的 CSV，生成单图并输出 `plot_manifest.json`。

运行方式：

```bash
python experiments/plot_Yahoo_results.py
```

输出目录默认是：

```text
experiments/Yahoo_S5-pics/
```

### 5.2 结果图更新要求

如果算法命名调整为 `ARRAD`，图表标题、图例、实验说明中的算法名称也应同步替换。重新绘图后，建议在 `experiments/Yahoo_S5-pics/` 下保留最新一版图，并在实验报告中引用。

## 6. 分析结论写法建议

实验报告建议按以下结构书写：

1. Yahoo S5 数据集与 benchmark 说明；
2. 基线方法对比；
3. `ARRAD` 参数调优过程；
4. 不同 benchmark 下的指标对比；
5. 实验图展示与结论总结。

