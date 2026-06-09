# Yahoo S5 实验说明

本文档用于说明 `Dual Deviation Anomaly Detection`（简称 `DDAD`）在 Yahoo S5 数据集上的实验配置、评估流程、参数调优方法，以及相关图表与结果文件的生成方式。

## 1. 实验目标

- 验证 `DDAD` 在 Yahoo S5 上的异常检测效果；
- 与 IoTDB 内置算法进行点级别对比；
- 观察不同参数配置下的 `Precision`、`Recall` 和 `F1` 变化；
- 输出可复现实验图与结果文件，便于报告或论文展示。

## 2. 数据集说明

Yahoo S5 数据集按四类基准集组织：

- `A1Benchmark`
- `A2Benchmark`
- `A3Benchmark`
- `A4Benchmark`

每个 CSV 文件都包含时间戳、数值和标签列。脚本会根据 benchmark 自动识别标签字段，并按点级别计算评估指标。

## 3. 基线算法与主实验脚本

`experiments/test_Yahoo_baseline.py` 中默认对比以下 IoTDB 内置算法：

- `IQR`
- `KSIGMA`
- `TWOSIDEDFILTER`
- `OUTLIER`

`experiments/test_Yahoo_DDAD.py` 用于评估 `DDAD` 在 Yahoo S5 上的效果，支持单次评估、灵敏度扫描和网格搜索。脚本提供以下主要参数：

- `--window`：历史窗口长度；
- `--sensitivity`：灵敏度参数；
- `--min-threshold`：最小阈值；
- `--tolerance`：允许的时间点偏移容忍度；
- `--sweep-sensitivity`：对一组 `sensitivity` 值进行扫描，并输出曲线图；
- `--grid-search`：对 `window`、`min-threshold`、`sensitivity` 进行组合搜索。

## 4. 实验流程

### 4.1 数据导入

先将 Yahoo S5 数据导入 IoTDB，对应脚本位于 `py-import/import_Yahoo_S5_All.py`。

### 4.2 注册 UDF

在 IoTDB CLI 中注册 `DDAD`：

```sql
CREATE FUNCTION DDAD AS 'org.apache.iotdb.udf.DualDeviationAnomalyDetectionUDTF';
```

运行脚本时可以通过 `--udf` 指定实际函数名，默认值为 `DDAD`。

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
5. 汇总 `Precision`、`Recall`、`F1`、`TP`、`FP` 和 `FN`。

### 4.4 运行 `DDAD` 评估

`experiments/test_Yahoo_DDAD.py` 的默认执行方式如下：

```bash
python experiments/test_Yahoo_DDAD.py
```

脚本会：

1. 自动扫描 `dataset/Yahoo_S5_Data/` 下的四个 benchmark；
2. 连接 IoTDB 并调用 `DDAD`；
3. 读取每个 CSV 的时间戳和标签列；
4. 计算点级别的 `Precision`、`Recall`、`F1`、`TP`、`FP` 和 `FN`；
5. 支持按 benchmark 或按文件名筛选测试集。

### 4.5 调参逻辑

`DDAD` 的核心参数主要有三个：

- `window`：历史窗口长度；
- `sensitivity`：灵敏度控制；
- `minThreshold`：最小阈值下界。

建议的调参顺序如下：

1. 先固定 `window`，观察整体报警密度；
2. 再调整 `sensitivity`，平衡误报与漏报；
3. 最后微调 `minThreshold`，避免阈值过低带来噪声报警。

## 5. 图表与结果文件

### 5.1 原始数据可视化

`experiments/plot_Yahoo_results.py` 用于绘制 Yahoo S5 原始序列及标签点。若图中需要标注算法结果，应统一使用 `DDAD` 作为算法名。脚本会遍历每个 benchmark 的 CSV，生成单图并输出 `plot_manifest.json`。

运行方式：

```bash
python experiments/plot_Yahoo_results.py
```

输出目录默认是：

```text
experiments/Yahoo_S5-pics/
```

### 5.2 灵敏度扫描图

当运行 `experiments/test_Yahoo_DDAD.py --sweep-sensitivity` 时，脚本会对一组 `sensitivity` 值逐一评估，并生成 `F1`、`Precision`、`Recall` 三条曲线图，保存到 `experiments/Yahoo_DDAD_superpara_graph/`。

### 5.3 网格搜索结果

当运行 `experiments/test_Yahoo_DDAD.py --grid-search` 时，脚本会对 `window`、`min-threshold` 和 `sensitivity` 做组合搜索，并将每个 benchmark 的结果写成 CSV 文件，便于后续筛选最优参数。

