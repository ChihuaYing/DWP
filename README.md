曾辉这边的结论：
1. 试错之后，比较推荐选定数据集 NAB + Yahoo，
2. baseline 可以选 LOF + TwoSlidedFilter，这个还需要测试。
3. 针对 NAB 数据集，异常点的数据非常少，可能一个系列才几个异常点。需要测试点级别的准确率 label 使用 NAB-master/label/combined_labels.json，不要用 NAB-master/label/combined_window.json逐个窗口测试准确率。准确率会非常低。
4. 部分参数需要调整。先小规模测试一下效果，对比一下 baseline。不需要在整个数据集上都超过baseline 。先得把任务完成。
5. StanUDTFNABV2 是针对 NAB 的一个简单优化，但是效果可能也不是很好。
6. test_NAB_stan_detect 是测试算法在 NAB 数据集上效果的脚本，主要逻辑是写好 SQL, 连接 IoTDB 批量执行。