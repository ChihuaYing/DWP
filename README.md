# DWP 项目说明

本仓库围绕 IoTDB 时间序列异常检测 UDF、Yahoo S5 实验与文档整理展开。仓库中的说明、实验脚本与算法命名均以 Yahoo S5 为主，重点展示 DDAD 算法及其在 Yahoo S5 数据集上的实验结果。

## 1. 仓库文件说明

- `iotdb-udf/`
  - `src/main/java/org/apache/iotdb/udf/DualDeviationAnomalyDetectionUDTF.java`：当前主算法实现，算法名为 DDAD（Dual Deviation Anomaly Detection）。
  - `README.md`：算法说明、注册和使用方式、参数说明、注意事项。
- `experiments/`
  - `test_Yahoo_baseline.py`：Yahoo S5 内置基线算法评估脚本。
  - `test_Yahoo_DDAD.py` Yahoo S5 DDAD 方法测评
  - `plot_Yahoo_results.py`：Yahoo S5 数据与结果可视化脚本，用于画出实验图。
  - `experiments_README.md`：Yahoo S5 实验说明、调参逻辑、对比结论与图表说明。
- `py-import/`
  - `import_Yahoo_S5_All.py`：Yahoo S5 数据导入脚本。
  - 其他 Yahoo 相关测试脚本与数据处理脚本。

## 2. 算法注册与运行

算法注册、运行与调参说明位于 `iotdb-udf/README.md`。建议先阅读该文档，再按照其中的 SQL 示例和参数说明完成函数注册、查询与实验复现。

---

如需进一步开展实验，请直接使用 Yahoo S5 流程。

