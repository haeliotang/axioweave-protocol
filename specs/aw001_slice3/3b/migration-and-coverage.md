# AW-001 Slice 3B：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.4.0-rc1-slice3b`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表真实测量、真实抽样、真实违规认定或 PostgreSQL 运行时。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、三动作封闭输入、可观测指标与评估记录对象。
- [固定注册表](fixed-action-registry.json)：三动作、权能、八项强制检查标识、允许写集合与 0–4 级锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice3b.py)、[测试](../../../tests/test_aw001_slice3b.py)、[夹具](../../../fixtures/aw001_slice3/3b/)：34 份跃迁/行为夹具、3 份注册表夹具与 1 份期望清单，共 37 个 Golden Fixture 测试方法；另有 18 个补充测试方法。
- 本次新增 **55 个测试方法通过**；全套 **403 个方法通过**，其中前序 348 项保留。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice3b.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice3b.py fixtures/aw001_slice3/3b/pt_evaluation_satisfied_lt.json fixtures/aw001_slice3/3b/pt_extend_window_late_data.json
# 预期退出码 1：包含 schema 无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice3b.py fixtures/aw001_slice3/3b/ft_schema_interpolation.json fixtures/aw001_slice3/3b/pt_evaluation_satisfied_lt.json
```

前序 348 项基线在同一环境重跑（`tests` 不是包，故在 tests 目录设置 `PYTHONPATH`）：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_aw001_slice2c test_aw001_slice3a test_case_packet
```

原始证据：[348 项基线](validation-evidence/baseline-tests.txt)、[55 项 3B 测试](validation-evidence/slice3b-tests.txt)、[403 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 358 份继承文件不变，覆盖 0.1.0、Slice 1、2A/2B/2C、3A 的实现、夹具、测试、规格、文档与既有证据。[本次摘要](validation-evidence/artifact-sha256.json) 记录 51 份本节新增或更新的文件，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

README、Slice 3 路线图与 3A 证据未在本步改写：它们锁定在既有摘要中，3B 状态以本登记及本文件为准。

## 2. 对象与动作

实体沿用 `object_id / tenant_node_id / case_id / version_seq`。`before.domain_objects.operations` 是经未修改 2B 校验器验证的最小业务域；3B 新增 `observable_metrics`、`metric_evaluations` 与本节 `effectiveness_envelopes`。所有动作不修改 `operations`。

| 动作 | 权能 | 目标 | APPLIED 写集合 | 关键拒绝 |
| --- | --- | --- | --- | --- |
| DEFINE_OBSERVABLE_METRIC | DEFINE_OBSERVABLE_METRIC | 案件 | 新建指标规格 v1 | 重复指标 ID、门槛/桶长非法 |
| EVALUATE_METRIC_WINDOW | EVALUATE_METRIC_WINDOW | 指标规格 | 新建评估记录 v1 | 窗口未对齐、观测越界、接收早于观测、去重键缺失、区间/带非法、区间类型或置信水平不符、点估计未预授权、已有评估版本 |
| EXTEND_METRIC_WINDOW | EXTEND_METRIC_WINDOW | 指标规格 | 新建评估记录 v(N+1) 并 supersede 最新评估 | 无前序评估、supersede 不匹配、窗口未严格延长、评估 ID 重复 |

允许写集合分别为 `CREATE_OBSERVABLE_METRIC_SPEC`、`CREATE_METRIC_EVALUATION`、`EXTEND_METRIC_EVALUATION`；未声明对象必须保持不变。

## 3. 时间桶与覆盖语义

- 桶长 `bucket_seconds` 为不小于 60 的整数；窗口首尾必须落在 UTC 桶边界（自 epoch 起对齐），且长度是桶长的整数倍。分区采用左闭右开。不静默生成部分桶；不对齐即拒绝。
- 原始观测全部保留：观测时刻、接收时刻、整数值、质量状态（`PASS / REJECTED`）与去重键。观测时刻必须落在窗口内，接收时刻不得早于观测时刻。
- 去重规则必须显式声明：`BY_DEDUPE_KEY` 时每个观测必须有去重键，按提交顺序保留首个出现的键，后续同键观测计为重复；`NONE` 时不做去重。质量拒绝与重复项都保留在原始列表和桶统计中，但不计入有效样本。
- 桶统计由原始观测重算：样本数、拒绝数、重复数、有效样本数与是否有效；有效桶要求有效样本数不少于 `min_samples_per_valid_bucket`。单桶密集采样只影响该桶，不能填补其他桶。
- 覆盖率 `coverage_ppm = floor(valid_buckets × 1_000_000 / total_buckets)`，为整数；首部、内部连续与尾部缺口分别计算，内部缺口取最大连续无效段。窗口全部无效时首缺口等于总桶数。
- 评估时点早于窗口结束为 `NOT_YET_EVALUATED`；覆盖率低于门槛为 `INDETERMINATE_INSUFFICIENT_COVERAGE`，**不得**写成 `VIOLATED`；覆盖充足才进入方向判定。
- `interpolation_policy` 固定 `FORBIDDEN`，评估记录 `interpolation_used` 恒为 `false`；任何插值或补桶声明不进入契约。

## 4. 方向性不确定性

- 评估必须携带区间：下界、上界、区间类型与计算来源；下界不得大于上界。区间类型必须与指标规格一致；`STATISTICAL_CONFIDENCE_INTERVAL` 必须给出 1–1,000,000 ppm 的置信水平，其他类型不得携带置信水平。
- 判定规则严格绑定边界：
  - `LT`：上界小于阈值即满足；下界不小于阈值即违反；否则不确定。
  - `LTE`：上界不大于阈值即满足；下界大于阈值即违反；否则不确定。
  - `GT`：下界大于阈值即满足；上界不大于阈值即违反；否则不确定。
  - `GTE`：下界不小于阈值即满足；上界小于阈值即违反；否则不确定。
  - `WITHIN_BAND`：整个区间落在允许带内即满足；区间完全位于带外即违反；跨边界为不确定。
  - `OUTSIDE_BAND`：区间完全位于禁区一侧即满足；整个区间落入禁区即违反；其他为不确定。
- 点估计只作为可选记录：未预授权即拒绝；即使预授权，判定仍只使用区间，不因缺少不确定性而用点估计降级。
- 结果状态只用于记录评估结论，不产生外部效力；`VIOLATED` 是区间证据下的合成结论，不等于现实违规认定。

## 5. 窗口延长与版本

- 同一指标首次评估版本为 1；新评估必须 supersede 当前最新版本，版本号递增。
- 延长窗口必须满足 `new.start ≤ old.start` 且 `new.end > old.end`；旧窗口与旧评估逐值保留，不覆盖。
- 迟到观测只能进入新版本；历史评估不改写。多次延长形成连续版本链，每版都保留自己的窗口、观测、桶、覆盖率、区间与结论。

## 6. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`：**一份正确的拒绝记录仍为 `is_valid=true`**。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`；派生桶、覆盖率、方向与域、结果、审计逐项重算一致 |
| 正确 REJECTED | `is_valid=true`；业务域逐值不变，拒绝码与诊断一致 |
| 错误声明 APPLIED、拒绝码不符、拒绝偷改业务、APPLIED 后域被改 | `is_valid=false`，确定性层级、错误码与路径 |
| 结构无效、客户端夹带插值字段、注册表降级 | `is_valid=false` |

诊断顺序：结构 → 访问/目标/版本/依赖 → 命令与回执绑定 → 权能与生效范围 → 窗口/观测/去重/区间/带/点估计 → 版本与 supersede 关系。

## 7. 已运行覆盖

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| UTC 桶对齐、左闭右开、整数桶窗口 | 未对齐窗口拒绝与派生断言 | PASSED_OFFLINE |
| 高密度单桶不能填补其他桶 | 50 样本单桶夹具与覆盖率断言 | PASSED_OFFLINE |
| 首/中/尾缺口分别计算 | 2/2/2 缺口夹具与派生测试 | PASSED_OFFLINE |
| 覆盖不足只报证据不足、不报违规 | 不足覆盖夹具与状态断言 | PASSED_OFFLINE |
| 去重键、质量拒绝、重复项保留但不计入 | 重复观测夹具与桶统计断言 | PASSED_OFFLINE |
| LT/LTE/GT/GTE/WITHIN_BAND/OUTSIDE_BAND 边界 | 方向真值表与 6 份评估夹具 | PASSED_OFFLINE |
| 点估计不降级、置信区间与类型一致性 | 点估计/CI 夹具与拒绝夹具 | PASSED_OFFLINE |
| 迟到数据生成新版本、旧 eval 保留 | 延长窗口夹具与历史不变断言 | PASSED_OFFLINE |
| 前序 348 项回归、原文件摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

## 8. 构造期修正与局限

构造 3B 检查器时修正了：评估记录校验中"先派生后校验区间"的顺序（改为先校验区间与点估计预授权）；补充测试对"正确拒绝"的判定（正确拒绝的 fixture 是 `is_valid=true`，不是检查器错误）。构造期原始失败输出未保留，只有最终运行证据；不得据此声称经历过独立评审。

已知验证边界：只覆盖合成数值、合成单位与固定桶长；不验证抽样设计、测量设备、真实单位换算、真实覆盖率或现实违规认定。`is_valid=true` 不产生证据充分性或外部效力。

## 9. 迁移与兼容边界

| 来源 | 3B 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain` 业务域 | 原样嵌套，先经未修改的 2B 校验器 | 不从静态快照推断真实并发或外部数据 |
| 3A 参与/发言契约 | 不激活、不修改其文件 | 3B 的指标评估不能替代参与门控或发言确认 |
| 2A/2B 保护令与承诺 | 保持逐值不变 | 评估结论不自动产生暂停、保护令或资源变化 |
| README、Slice 3 路线图、3A 证据 | 本步不改写 | 3B 入口更新留待独立对齐提交 |

摘要沿用既有本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS；指标数值为合成整数，不做浮点换算。

## 10. 未覆盖与下一步

- **3C：UNRUN**。DecisionRound/Ballot、资格消耗与有效票同事务、秘密选票审计隔离、HMAC 指纹与保护令轮次覆盖尚未实现。
- 真实观测采集、单位换算、抽样与去重外部规则、迟到数据的真实来源核验、点估计统计方法均未实现。
- 评估与参与门控、发言实质性的跨切片联动留给后续切片；3B 只提供独立判定结构。
- PostgreSQL 并发、故障注入与持久性 `DEFERRED_TO_PG_RUNTIME`。
- **SPEC_FROZEN 未达成**；跨切片冻结清单、摘要登记与独立审阅决定仍待补齐。
