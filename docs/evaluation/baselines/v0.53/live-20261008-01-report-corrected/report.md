# 比较报告：coding-benchmark-v053-main

- 批次：`67583764-8595-4c68-ad4d-8f2254f23bdf`（completed）
- 完整槽位：36 / 36
- 原始证据目录：`../live-20261008-01`（相对于本报告目录）
- 报告规则：2；能力比较完成：否
- 原始 token/失败耗时包含保守估算，只作诊断，不代表正常能力代价或实际账单。
- 题集：`coding-benchmark@1.1`
- 恢复成功率：`null`（编码比较不适用）

## 组汇总

| 组 | grader 通过 | 严格成功 | 工具调用 | 输入 token | 输出 token | Agent median ms | grader median ms | 成本 USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `old-off` | 0 / 0 | 0 / 0 | 0 | 45030 | 12288 | 5.0 | 39.5 | null |
| `current-off` | 0 / 0 | 0 / 0 | 0 | 45030 | 12288 | 5.0 | 40.0 | null |
| `current-on` | 0 / 0 | 0 / 0 | 0 | 61848 | 12288 | 5.5 | 40.0 | null |

## 样本资格

| 组 | 原始 grader 通过 | 有效能力样本 | 基础设施异常 |
|---|---:|---:|---:|
| `old-off` | 0 / 12 | 0 | 12 |
| `current-off` | 0 / 12 | 0 | 12 |
| `current-on` | 0 / 12 | 0 | 12 |

## old-off->current-off

可比较：否
有效配对：0 / 12；合同条件一致：是
不可比较原因：`one_or_more_paired_samples_not_scorable`

| 题目 | 基线通过 | 实验通过 | 通过率变化（百分点） | 输入 token 差 | 输出 token 差 | Agent median 差 ms |
|---|---:|---:|---:|---:|---:|---:|
| `pagination-boundary` | 0 / 0 | 0 / 0 | null | null | null | null |
| `orders-discount-receipt` | 0 / 0 | 0 / 0 | null | null | null | null |
| `cache-expiry-regression` | 0 / 0 | 0 / 0 | null | null | null | null |
| `config-priority-investigation` | 0 / 0 | 0 / 0 | null | null | null | null |

配对槽位证据：
- `pagination-boundary` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-f6c44f412c3e7a26ad5dc5c7/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-e51bb9f84bfc975eb2967f61/diff.patch>)）。
- `pagination-boundary` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-14d3ea0db383aeda28fb70a6/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-3041fa98adb10bc302adef20/diff.patch>)）。
- `pagination-boundary` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-042d77d65bd947a3ce214fd1/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-d1cbb51c1490c6bda1b719bb/diff.patch>)）。
- `orders-discount-receipt` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-7600f2e1ebe37ab3a1825fb1/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-9e2ba9d8f6b9df7ec99962fa/diff.patch>)）。
- `orders-discount-receipt` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-08c0b32f4c2118fb473ff67d/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-948b586cc181f28c0e9ba0ba/diff.patch>)）。
- `orders-discount-receipt` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-e8ddce8e88d67916bb8e41f4/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-9bd859ccddd6f4c671f3dd72/diff.patch>)）。
- `cache-expiry-regression` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-1b312f9bcdadc35f9a6a127b/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-2e846357942d853e412eaecc/diff.patch>)）。
- `cache-expiry-regression` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-0eefb02d76092806dec7c187/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-d5972d5235cc81d6b4749e37/diff.patch>)）。
- `cache-expiry-regression` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-0d8cb1006054cc7787441b80/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-c2a396316e14d27840f5f64f/diff.patch>)）。
- `config-priority-investigation` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-c0ee593deb638f15de4bb48d/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-98753b0f0ed5d982e94f7bb7/diff.patch>)）。
- `config-priority-investigation` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-5ca9ae2e75818e91d18383db/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-794e14e671555b9e19a2ac51/diff.patch>)）。
- `config-priority-investigation` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-5cf40d51c89e08e20ee3f1ec/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-e9ef119a329d6ae4cf191131/diff.patch>)）。

三次重复只作为观察结果；不宣称统计显著性或推及其他题集、模型与记忆学习。

## current-off->current-on

可比较：否
有效配对：0 / 12；合同条件一致：是
不可比较原因：`one_or_more_paired_samples_not_scorable`

| 题目 | 基线通过 | 实验通过 | 通过率变化（百分点） | 输入 token 差 | 输出 token 差 | Agent median 差 ms |
|---|---:|---:|---:|---:|---:|---:|
| `pagination-boundary` | 0 / 0 | 0 / 0 | null | null | null | null |
| `orders-discount-receipt` | 0 / 0 | 0 / 0 | null | null | null | null |
| `cache-expiry-regression` | 0 / 0 | 0 / 0 | null | null | null | null |
| `config-priority-investigation` | 0 / 0 | 0 / 0 | null | null | null | null |

配对槽位证据：
- `pagination-boundary` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-e51bb9f84bfc975eb2967f61/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-8fbfbd3399f858eed112dab9/diff.patch>)）。
- `pagination-boundary` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-3041fa98adb10bc302adef20/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-6c2b268dffe33493953f4199/diff.patch>)）。
- `pagination-boundary` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-d1cbb51c1490c6bda1b719bb/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-8b2c27738559d14e51feba37/diff.patch>)）。
- `orders-discount-receipt` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-9e2ba9d8f6b9df7ec99962fa/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-6875a880cee9662915250daf/diff.patch>)）。
- `orders-discount-receipt` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-948b586cc181f28c0e9ba0ba/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-714ae45ea143610f2fb2ea54/diff.patch>)）。
- `orders-discount-receipt` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-9bd859ccddd6f4c671f3dd72/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-2cd4ba2502efca142fd07368/diff.patch>)）。
- `cache-expiry-regression` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-2e846357942d853e412eaecc/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-e1b7251d25e68d3b95103acc/diff.patch>)）。
- `cache-expiry-regression` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-d5972d5235cc81d6b4749e37/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-0cddd863a4df1e793c08297a/diff.patch>)）。
- `cache-expiry-regression` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-c2a396316e14d27840f5f64f/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-906f4f68d4c4de673f90a9f8/diff.patch>)）。
- `config-priority-investigation` 重复 1：基线 `null`（[diff](<../live-20261008-01/trials/s-98753b0f0ed5d982e94f7bb7/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-c98da8f80943f27e9acf50e6/diff.patch>)）。
- `config-priority-investigation` 重复 2：基线 `null`（[diff](<../live-20261008-01/trials/s-794e14e671555b9e19a2ac51/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-c1ddda5db0a100b66bf2ce67/diff.patch>)）。
- `config-priority-investigation` 重复 3：基线 `null`（[diff](<../live-20261008-01/trials/s-e9ef119a329d6ae4cf191131/diff.patch>)），实验 `null`（[diff](<../live-20261008-01/trials/s-bff0af1ab2a3b19a19e9f989/diff.patch>)）。

三次重复只作为观察结果；不宣称统计显著性或推及其他题集、模型与记忆学习。

## 复核队列

- `excluded_capability_sample`：s-f6c44f412c3e7a26ad5dc5c7
- `excluded_capability_sample`：s-e51bb9f84bfc975eb2967f61
- `excluded_capability_sample`：s-8fbfbd3399f858eed112dab9
- `excluded_capability_sample`：s-9e2ba9d8f6b9df7ec99962fa
- `excluded_capability_sample`：s-6875a880cee9662915250daf
- `excluded_capability_sample`：s-7600f2e1ebe37ab3a1825fb1
- `excluded_capability_sample`：s-e1b7251d25e68d3b95103acc
- `excluded_capability_sample`：s-1b312f9bcdadc35f9a6a127b
- `excluded_capability_sample`：s-2e846357942d853e412eaecc
- `excluded_capability_sample`：s-c0ee593deb638f15de4bb48d
- `excluded_capability_sample`：s-98753b0f0ed5d982e94f7bb7
- `excluded_capability_sample`：s-c98da8f80943f27e9acf50e6
- `excluded_capability_sample`：s-3041fa98adb10bc302adef20
- `excluded_capability_sample`：s-6c2b268dffe33493953f4199
- `excluded_capability_sample`：s-14d3ea0db383aeda28fb70a6
- `excluded_capability_sample`：s-714ae45ea143610f2fb2ea54
- `excluded_capability_sample`：s-08c0b32f4c2118fb473ff67d
- `excluded_capability_sample`：s-948b586cc181f28c0e9ba0ba
- `excluded_capability_sample`：s-0eefb02d76092806dec7c187
- `excluded_capability_sample`：s-d5972d5235cc81d6b4749e37
- `excluded_capability_sample`：s-0cddd863a4df1e793c08297a
- `excluded_capability_sample`：s-794e14e671555b9e19a2ac51
- `excluded_capability_sample`：s-c1ddda5db0a100b66bf2ce67
- `excluded_capability_sample`：s-5ca9ae2e75818e91d18383db
- `excluded_capability_sample`：s-8b2c27738559d14e51feba37
- `excluded_capability_sample`：s-042d77d65bd947a3ce214fd1
- `excluded_capability_sample`：s-d1cbb51c1490c6bda1b719bb
- `excluded_capability_sample`：s-e8ddce8e88d67916bb8e41f4
- `excluded_capability_sample`：s-9bd859ccddd6f4c671f3dd72
- `excluded_capability_sample`：s-2cd4ba2502efca142fd07368
- `excluded_capability_sample`：s-c2a396316e14d27840f5f64f
- `excluded_capability_sample`：s-906f4f68d4c4de673f90a9f8
- `excluded_capability_sample`：s-0d8cb1006054cc7787441b80
- `excluded_capability_sample`：s-bff0af1ab2a3b19a19e9f989
- `excluded_capability_sample`：s-5cf40d51c89e08e20ee3f1ec
- `excluded_capability_sample`：s-e9ef119a329d6ae4cf191131

## 限制

- 固定 coding-benchmark@1.1 四题与本次模型绑定。
- Memory 结论仅适用于冻结语义种子的自动摘要检索，不代表跨任务学习或其他记忆能力。
- 所有原始样本均保留；基础设施失败只排除能力分母，不删除证据。
- 组用量与耗时含失败尝试，仅作原始观测；能力差异只使用双方均可评分的配对。
- 缺失值保持 null；价格估算不代表 provider 的精确账单。
- 每题三次重复仅形成描述性观察，不宣称统计显著。
