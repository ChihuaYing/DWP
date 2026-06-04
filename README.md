曾辉这边的结论：
1. 试错之后，比较推荐选定数据集 NAB + Yahoo，
2. baseline 可以选 LOF + TwoSlidedFilter，这个还需要测试。
3. 针对 NAB 数据集，异常点的数据非常少，可能一个系列才几个异常点。需要测试点级别的准确率 label 使用 NAB-master/label/combined_labels.json，不要用 NAB-master/label/combined_window.json逐个窗口测试准确率。准确率会非常低。
4. 部分参数需要调整。先小规模测试一下效果，对比一下 baseline。不需要在整个数据集上都超过baseline 。先得把任务完成。
5. StanUDTFNABV2 是针对 NAB 的一个简单优化，但是效果可能也不是很好。
6. test_NAB_stan_detect 是测试算法在 NAB 数据集上效果的脚本，主要逻辑是写好 SQL, 连接 IoTDB 批量执行。

**wyx的说明：**

测试前要在iotdb cli中执行：

```SQL
CREATE FUNCTION STAN_DETECT_NAB_V2 AS 'org.apache.iotdb.udf.StanUDTFNABV2'
```

如果直接在/py-import/dataset/文件夹中执行`git clone git@github.com:numenta/NAB.git`，实际上数据的位置会位于：

```
/py-import/dataset/NAB/data
/py-import/dataset/NAB/labels
```

此时导入数据和进行测试的命令是：

```bash
# 在执行之前要启动iotdb服务端，并执行CREATE FUNCTION STAN_DETECT_NAB_V2 AS 'org.apache.iotdb.udf.StanUDTFNABV2'
# 执行位置位于DWP项目根目录

# 导入数据：
python py-import/import_NAB.py --data-dir py-import/dataset/NAB/data --label-path py-import/dataset/NAB/labels/combined_windows.json --force-recreate

# 测试：
python py-import/test_NAB_stan_detect.py   --data-dir py-import/dataset/NAB/data   --label-path py-import/dataset/NAB/labels/combined_labels.json
```

