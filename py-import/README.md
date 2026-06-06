# py-import 使用说明

本目录用于把外部数据集导入 IoTDB，并把 IoTDB UDF 的检测结果导出为可评测格式。

当前主要流程面向 NAB 数据集：

1. 将 NAB CSV 数据导入 IoTDB。
2. 在 IoTDB 中执行 Java UDF。
3. 将 UDF 输出导出为 NAB 官方 `results/<detector-name>/...` 格式。
4. 使用 NAB 自带 `run.py` 做官方 `optimize / score / normalize`。

## NAB 目录结构

默认假设 NAB 仓库位于：

```text
py-import/dataset/NAB
```

关键路径：

```text
py-import/dataset/NAB/data
py-import/dataset/NAB/labels/combined_labels.json
py-import/dataset/NAB/labels/combined_windows.json
py-import/dataset/NAB/results
```

`data` 下是原始 CSV；`labels` 下是标签；`results` 是 NAB 官方评分读取的 detector 输出目录。

## 导入 NAB 数据到 IoTDB

前提：IoTDB 已启动，Python 环境里有 IoTDB Session 依赖。

导入全部 NAB 数据：

```powershell
python py-import/import_NAB.py `
  --data-dir py-import/dataset/NAB/data `
  --force-recreate
```

导入脚本会把每个 NAB CSV 映射到一个 measurement：

```text
root.nab.d1.s0
root.nab.d1.s1
...
root.nab.d1.s57
```

注意：导入到 IoTDB 的时间戳是行号 `0, 1, 2, ...`，不是 NAB CSV 中的真实时间戳。导出脚本会再把行号映射回原始 NAB timestamp。

只查看 CSV 到 sensor 的映射：

```powershell
python py-import/import_NAB.py `
  --data-dir py-import/dataset/NAB/data `
  --list-series
```

## 注册 UDF

示例：

```sql
CREATE FUNCTION STAN_DETECT_NAB_V2 AS 'org.apache.iotdb.udf.StanUDTFNABV2';
```

如果修改了 Java UDF，需要重新打包、部署 jar、重启 IoTDB，并确认函数注册的是最新 jar。

## 导出 UDF 结果为 NAB results

脚本：

```text
py-import/export_NAB_iotdb_udf_results.py
```

它会：

1. 扫描 `--data-dir` 下的 NAB CSV。
2. 按扫描顺序把 CSV 映射到 `s0, s1, ...`。
3. 对每个 sensor 执行 IoTDB SQL：

```sql
SELECT STAN_DETECT_NAB_V2(s0, 'param'='value') AS anomaly_score FROM root.nab.d1
```

4. 将 UDF 输出的异常点补全成完整 NAB results CSV：

```csv
timestamp,value,anomaly_score
```

5. 写入：

```text
py-import/dataset/NAB/results/<detector-name>/<category>/<detector-name>_<csv-name>
```

### detector 名称规则

`--detector-name` 必须唯一，建议把算法版本和参数写进去，例如：

```text
iotdbStanNABV2W150S45T35
```

不要使用下划线 `_`。NAB 原始 `normalize()` 代码对 detector 名称的解析较脆弱，带下划线可能导致结果归类错误。

不要使用 NAB 自带 detector 名称，例如：

```text
null
numenta
twitterADVec
windowedGaussian
```

导出脚本会拒绝这些名称，避免误删或覆盖 NAB 自带 benchmark 结果。

脚本默认不会覆盖已有 detector 输出。如果同名 detector 目录下已经有结果 CSV，脚本会报错。可选策略：

- 新建 detector 名称：推荐，用于保留每轮实验。
- `--clean-detector-dir`：删除该 detector 目录下旧 CSV 后重新导出。
- `--overwrite-existing`：覆盖旧导出文件，并删除旧的 NAB `*_scores.csv`，避免评分文件过期。

### 示例：导出一组 UDF 参数

当前 `StanUDTFNABV2.java` 支持的主要参数是：

```text
window
sensitivity
threshold
```

```powershell
python py-import/export_NAB_iotdb_udf_results.py `
  --data-dir py-import/dataset/NAB/data `
  --detector-name iotdbStanNABV2W150S45T35 `
  --udf STAN_DETECT_NAB_V2 `
  --window 150 `
  --sensitivity 4.5 `
  --threshold 3.5 `
  --score-mode binary
```

如果 UDF 输出的是连续异常分数，建议测试：

```powershell
--score-mode raw
```

或：

```powershell
--score-mode logistic
```

`binary` 会把所有 UDF 输出点写成 `anomaly_score=1.0`，非输出点写成 `0.0`。

### 导出产物

每次导出会在元数据目录下生成：

```text
py-import/dataset/NAB/results/_iotdb_export_metadata/<detector-name>/
export_manifest.json
export_summary.csv
```

`export_manifest.json` 记录：

- detector 名称
- UDF 名称
- UDF 参数
- score mode
- 每个文件的输出路径
- 每个文件的检测点数量

`export_summary.csv` 便于快速查看每条序列检测了多少点。

## 使用 NAB 官方评分

进入 NAB 仓库目录：

```powershell
cd py-import/dataset/NAB
```

对一个 detector 评分：

```powershell
python run.py `
  -d iotdbStanNABV2W150S45T35 `
  --optimize `
  --score `
  --normalize `
  --skipConfirmation
```

对多个 detector 一次评分：

```powershell
python run.py `
  -d detectorA,detectorB,detectorC `
  --optimize `
  --score `
  --normalize `
  --skipConfirmation
```

评分结果写入：

```text
py-import/dataset/NAB/results/final_results.json
py-import/dataset/NAB/config/thresholds.json
py-import/dataset/NAB/results/<detector-name>/*_scores.csv
```

NAB 官方评分还会原地修改 `results/<detector-name>/...` 下的结果 CSV，追加 `S(t)_standard`、`S(t)_reward_low_FP_rate`、`S(t)_reward_low_FN_rate` 三列。

说明：

- `combined_windows.json` 是 NAB 官方评分使用的窗口标签。
- 官方 NAB 分数不是 precision/recall/F1，而是窗口内早报奖励、窗口外误报扣分后的 normalized score。
- `normalize` 依赖 `results/null` 中已有的 null detector 结果。当前 NAB 仓库通常自带这些结果；如果缺失，需要先运行 null detector。

## 点级别评估

脚本：

```text
py-import/test_NAB_stan_detect.py
```

它用于直接连接 IoTDB，执行指定 UDF，并用 NAB 标签计算点级别 precision/recall/F1。这个脚本不生成 NAB 官方 `results/<detector-name>` 文件，也不调用 NAB 官方 scorer。

它会：

1. 扫描 `--data-dir` 下的 NAB CSV。
2. 按扫描顺序把 CSV 映射到 `s0, s1, ...`。
3. 对每个 sensor 执行 IoTDB SQL：

```sql
SELECT STAN_DETECT_NAB_V2(s0, "window"="150", "sensitivity"="4.5", "threshold"="3.5") FROM root.nab.d1
```

4. 把 UDF 输出的 IoTDB 时间戳当作 NAB CSV 行号。
5. 读取 CSV 中对应行的真实 timestamp。
6. 和 `combined_labels.json` 中的异常点比较，统计 `TP / FP / FN / precision / recall / F1`。

如果需要用 `combined_labels.json` 做点级别 precision/recall/F1，可以使用：

```powershell
python py-import/test_NAB_stan_detect.py `
--data-dir py-import/dataset/NAB/data `
--label-path py-import/dataset/NAB/labels/combined_labels.json `
--udf STAN_DETECT_NAB_V2
```

这套评估和 NAB 官方评分不同。点级别评估适合调试误报/漏报；NAB 官方评分适合和 benchmark scoreboard 对比。

### 常用参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--device` | `root.nab.d1` | IoTDB 中 NAB 数据所在 device |
| `--udf` | `STAN_DETECT_NAB_V2` | 要调用的 UDF 名称 |
| `--data-dir` | `py-import/dataset/NAB` | NAB CSV 根目录；本项目通常应显式传 `py-import/dataset/NAB/data` |
| `--label-path` | 自动查找 | 标签 JSON；点级别评估建议显式传 `py-import/dataset/NAB/labels/combined_labels.json` |
| `--categories` | `all` | 只测试指定 NAB 分类，例如 `realKnownCause` |
| `--files` | `all` | 只测试指定 CSV 文件，例如 `nyc_taxi.csv` |
| `--sensors` | `all` | 只测试指定 sensor，例如 `s0,s1,s2` |
| `--window` | `150` | 传给 UDF 的 `window` 参数 |
| `--sensitivity` | `4.5` | 传给 UDF 的 `sensitivity` 参数 |
| `--threshold` | `3.5` | 传给 UDF 的 `threshold` 参数 |
| `--tolerance` | `0` | 点级别标签左右放宽的采样点数 |
| `--top-k` | `10` | 输出 F1 排名前多少个文件 |
| `--print-sql` | 关闭 | 打印实际执行的 SQL |
| `--strict-labels` | 关闭 | 如果某个 CSV 找不到标签则报错；默认当作无异常序列 |

### 小规模调试

只测试人工无异常数据：

```powershell
python py-import/test_NAB_stan_detect.py `
  --data-dir py-import/dataset/NAB/data `
  --label-path py-import/dataset/NAB/labels/combined_labels.json `
  --categories artificialNoAnomaly `
  --udf STAN_DETECT_NAB_V2 `
  --print-sql
```

只测试 `s0` 到 `s4`：

```powershell
python py-import/test_NAB_stan_detect.py `
  --data-dir py-import/dataset/NAB/data `
  --label-path py-import/dataset/NAB/labels/combined_labels.json `
  --sensors s0,s1,s2,s3,s4 `
  --udf STAN_DETECT_NAB_V2
```

测试不同 UDF 参数：

```powershell
python py-import/test_NAB_stan_detect.py `
  --data-dir py-import/dataset/NAB/data `
  --label-path py-import/dataset/NAB/labels/combined_labels.json `
  --udf STAN_DETECT_NAB_V2 `
  --window 150 `
  --sensitivity 4.5 `
  --threshold 3.5
```

如果怀疑检测点和人工标签有少量时间偏移，可以用 `--tolerance` 做辅助诊断：

```powershell
python py-import/test_NAB_stan_detect.py `
  --data-dir py-import/dataset/NAB/data `
  --label-path py-import/dataset/NAB/labels/combined_labels.json `
  --udf STAN_DETECT_NAB_V2 `
  --tolerance 2
```

`--tolerance 2` 的含义是：每个标签点左右各放宽 2 个采样间隔。它不是 NAB 官方功能，只是这个点级别评估脚本自己的宽松匹配逻辑。

### 和 NAB 官方评分的区别

`test_NAB_stan_detect.py` 推荐使用 `combined_labels.json`，目标是点级别 F1：

```text
预测行号 == 标签行号
```

NAB 官方评分使用 `combined_windows.json`，目标是实时检测窗口分数：

```text
窗口内越早报警分数越高，窗口外报警扣分
```

不要只把 `--label-path` 换成 `combined_windows.json` 就认为得到了 NAB 官方分数。这个脚本即使用窗口标签，也只是把窗口内所有点展开成 truth 点再算 precision/recall/F1，和 NAB 官方 scorer 不是同一种评估。

## 大规模实验建议

1. 每一组参数使用一个唯一 `--detector-name`。
2. detector 名称中写入关键参数，避免回头无法追踪。
3. 每次导出后检查 `export_summary.csv`，确认检测点数量是否符合预期。
4. 如果多组参数导出的检测点数量完全相同，优先检查：
   - UDF 参数名是否和 Java 代码一致。
   - IoTDB 中注册的 UDF 是否来自最新 jar。
   - 是否忘记重启 IoTDB。
   - 是否用了 `binary` 导致 NAB optimize 看不到分数强弱差异。
5. 不要手工修改 `results/<detector-name>` 下的 CSV；需要重跑就换 detector 名称，或显式使用 `--clean-detector-dir`。

## 常见问题

### 为什么不用下划线 detector 名称？

NAB 原始 `runner.normalize()` 里通过文件名拆分 detector 名称，带下划线时容易解析错误。使用字母、数字和短横线最稳。

### 为什么导出脚本默认不覆盖？

大规模调参时，静默覆盖会让实验不可复现。默认报错可以迫使每轮实验有唯一名字；确实要复用目录时再显式选择覆盖策略。

### 只导出部分文件能不能跑 NAB 官方评分？

不建议。NAB 官方 `score/normalize` 默认面向完整 corpus。部分文件适合调试导出和 SQL；正式评分请导出全部 58 个 NAB CSV。
