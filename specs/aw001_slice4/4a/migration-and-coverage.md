# AW-001 Slice 4A：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.5.0-rc1-slice4a`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表真实访问控制、真实图引擎、真实 AI、真实导出或 PostgreSQL 运行时。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、五动作封闭输入、标记/派生输出/依赖边/复核对象。
- [固定注册表](fixed-action-registry.json)：五动作、权能、八项强制检查标识、允许写集合与 0–4 级锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice4a.py)、[测试](../../../tests/test_aw001_slice4a.py)、[夹具](../../../fixtures/aw001_slice4/4a/)：27 份跃迁/行为夹具、3 份注册表夹具与 1 份期望清单，共 30 个 Golden Fixture 测试方法；另有 23 个补充测试方法。
- 本次新增 **53 个测试方法通过**；全套 **501 个方法通过**，其中前序 448 项保留。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice4a.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice4a.py fixtures/aw001_slice4/4a/pt_derive_restricted_output.json fixtures/aw001_slice4/4a/pt_dependency_review.json
# 预期退出码 1：包含 schema 无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice4a.py fixtures/aw001_slice4/4a/ft_pause_claimed.json fixtures/aw001_slice4/4a/pt_set_marking_case.json
```

前序 448 项基线在同一环境重跑（`tests` 不是包，故在 tests 目录设置 `PYTHONPATH`）：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_aw001_slice2c test_aw001_slice3a test_aw001_slice3b test_aw001_slice3c test_case_packet
```

原始证据：[448 项基线](validation-evidence/baseline-tests.txt)、[53 项 4A 测试](validation-evidence/slice4a-tests.txt)、[501 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 454 份继承文件不变，覆盖 0.1.0、Slice 1、2A/2B/2C、3A/3B/3C 的实现、夹具、测试、规格、文档与既有证据。[本次摘要](validation-evidence/artifact-sha256.json) 记录 44 份本节新增或更新的文件，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

README、既有路线图与先前切片证据未在本步改写：它们锁定在既有摘要中，4A 状态以本登记及本文件为准。

## 2. 对象与动作

实体沿用 `object_id / tenant_node_id / case_id / version_seq`。`before.domain_objects.operations` 是经未修改 2B 校验器验证的最小业务域；4A 新增 `case_access_policies`、`governance_markings`、`derived_outputs`、`lineage_edges`、`dependency_reviews` 与本节 `effectiveness_envelopes`。所有动作不修改 `operations`。

| 动作 | 权能 | 目标 | APPLIED 写集合 | 关键拒绝 |
| --- | --- | --- | --- | --- |
| SET_GOVERNANCE_MARKINGS | SET_GOVERNANCE_MARKINGS | 对象 | 新建/收紧标记版本，递增案件策略版本 | 目标不属于本案件、目标未知、更新包含放宽 |
| RELAX_GOVERNANCE_MARKINGS | RELAX_GOVERNANCE_MARKINGS | 对象 | 放宽标记版本，递增案件策略版本 | 无审查、审查未通过/无权/未映射、更新并非单调放宽 |
| RECORD_DERIVED_OUTPUT | RECORD_DERIVED_OUTPUT | 案件 | 新建派生输出 v1 与所用策略版本 | 输入未知/跨案/版本越界、任一输入无标记、用途交集为空 |
| RECORD_LINEAGE_EDGE | RECORD_LINEAGE_EDGE | 案件 | 新建依赖边 v1 | 端点未知/跨案、自环或成环、可见范围超出端点标记 |
| RECORD_DEPENDENCY_REVIEW | RECORD_DEPENDENCY_REVIEW | 案件 | 新建复核待办 v1 | 边不存在或非关键前提、变更端点不符、期限非法、复核人未映射、同一变更重复复核 |

允许写集合分别为 `ADD_OR_TIGHTEN_MARKING`、`RELAX_MARKING`、`CREATE_DERIVED_OUTPUT`、`CREATE_LINEAGE_EDGE`、`CREATE_DEPENDENCY_REVIEW`；未声明对象必须保持不变。

## 3. 多维标记与派生控制

- 标记维度：访问资格要求（合取集合）、允许用途（交集语义）、AI 处理策略（`ALLOWED/RESTRICTED/FORBIDDEN`）、导出策略（同三值）、保存级别（`SHORT/STANDARD/LONG/LEGAL_HOLD`）、固定 `SINGLE_CASE_ONLY`。访问资格与用途分开，避免把"有权阅读"当作"有权公开"。
- 每次标记变更同事务递增案件访问策略版本；标记记录对象版本与所用策略版本。收紧必须至少在一个维度更严且不得放宽任何维度；放宽必须至少在一个维度更宽且不得收紧任何维度，并附具名审查（`REVIEW_MARKING_RELAXATION` 权能、审批结论、审查时点不晚于评估时点、审查证据可解析）。
- 派生输出记录**实际读入的全部输入**及精确版本；有效标记按输入重算：访问要求取并集、用途取交集、AI/导出取最严、保存级别取最严、固定单案。用途交集为空拒绝派生。客户端不得提交有效标记。
- `asserted_as_fact` 恒为 false；表达状态（`NOT_APPLICABLE/UNCONFIRMED/CONFIRMED`）单独保存，确认不升级为事实真实。来源缺失、跨案引用与版本越界都拒绝。
- 标记变更只影响后续读取与派生；离线契约不执行真实访问控制，也不声称传播索引等价于访问控制。

## 4. 单案依赖图与复核

- 依赖边记录端点在精确版本上的引用、类型（`MATERIAL_PREMISE / SUPPORTING / DERIVED_FROM`）与可见范围（`INTERNAL / REVIEWERS / PUBLIC`）。全部边必须同案；写入时拒绝自环与任何成环；端点必须存在且版本不超过当前对象版本。
- 可见范围受端点最新标记约束：`REVIEWERS` 要求两端都有标记；`PUBLIC` 要求两端访问要求为空且导出策略为 `ALLOWED`。边本身不携带业务效力。
- 依赖边与复核待办**不修改**任何业务对象、轮次、保护令或承诺；标签不得创造无限期暂停。真正的暂停须另行取得 2A 保护令，本切片不创建。
- 关键前提变化可生成复核待办：仅 `MATERIAL_PREMISE` 边、变更端点为该边端点、期限晚于评估时点、复核人已映射；同一（边，变更对象）只允许一个 OPEN 待办。
- "未建立边"不证明不存在实质性影响；遗漏依赖的受理与更正属于后续申诉/讨论切片，本切片不新增该入口。

## 5. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`：**一份正确的拒绝记录仍为 `is_valid=true`**。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`；标记、派生、边、复核与结果绑定逐项重算一致 |
| 正确 REJECTED | `is_valid=true`；业务域逐值不变，拒绝码与诊断一致 |
| 错误声明 APPLIED、拒绝码不符、拒绝偷改业务、APPLIED 后域被改 | `is_valid=false`，确定性层级、错误码与路径 |
| 结构无效、客户端夹带有效标记/事实声明/暂停字段、注册表降级 | `is_valid=false` |

诊断顺序：结构 → 访问/目标/版本/依赖 → 命令与回执绑定 → 权能与生效范围 → 目标归属/单调性/审查 → 派生输入与标记推导 → 边端点/防环/可见性 → 复核期限。

## 6. 已运行覆盖

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 首次标记、第二对象标记、收紧与策略版本递增 | 3 份 APPLIED 与版本断言 | PASSED_OFFLINE |
| 放宽需具名审查、非单调放宽/收紧拒绝 | 合法与非法对照 | PASSED_OFFLINE |
| 派生标记推导（并集/交集/最严/单案） | 派生夹具与推导断言 | PASSED_OFFLINE |
| 用途交集为空、输入无标记拒绝 | 两份正确拒绝夹具 | PASSED_OFFLINE |
| 单案限制、防环、自环与跨案拒绝 | 3 份正确拒绝夹具 | PASSED_OFFLINE |
| 可见范围泄露、内部/评审/公开边对照 | 合法与非法可见性夹具 | PASSED_OFFLINE |
| 依赖边/复核不暂停、关键前提复核待办 | 域不变断言与复核夹具 | PASSED_OFFLINE |
| 事实声明与客户端有效标记封闭、暂停字段封闭 | schema 拒绝对照 | PASSED_OFFLINE |
| 前序 448 项回归、原文件摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

## 7. 构造期修正与局限

构造 4A 时修正了：标记动作目标类型判断（案件对象也应允许作为标记目标）；同一案件中多次标记导致信封 ID 重复；若干拒绝夹具一度先调用"预期应用"路径导致构造异常。构造期原始失败输出未保留，只有最终运行证据；不得据此声称经历过独立评审。

已知验证边界：只覆盖合成夹具与静态离线语义；不执行真实 ACL、图查询、缓存传播、导出渲染或 AI 处理；防环检查针对本切片记录的边，不证明完整影响闭包；可见性规则是保守近似，不替代运行时逐次鉴权。`is_valid=true` 不产生访问许可、制度效力或事实认定。

## 8. 迁移与兼容边界

| 来源 | 4A 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain` 业务域 | 原样嵌套，先经未修改的 2B 校验器 | 不从静态快照推断真实并发或访问控制 |
| 2A 保护令、2C 申诉/集体、3A 参与/发言、3B 计量、3C 轮次/选票 | 不激活、不修改其文件 | 标签不替代保护令；派生不替代参与门控或计票 |
| 既有命令/回执/审计结构 | 沿用同义结构，4A 扩展自身动作与对象 | 不向旧 Schema 静默添加新动作或字段 |
| README、Slice 4 路线图、先前证据 | 本步不改写 | 4A 入口更新留待独立对齐提交 |

摘要沿用既有本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS；对象 ID 与标记为合成数据。

## 9. 未覆盖与下一步

- **4B：UNRUN**。固定基线方案分支、分支冻结门控、AI 草稿会话与撤权后旧会话隔离尚未实现。
- **4C：UNRUN**。撤回输出的本地放行、恢复清单、模板包与部署版本交换尚未实现。
- 真实访问控制执行、缓存/传播一致性、血缘视图 ACL、跨案影响人工核查、遗漏依赖申诉入口均未实现。
- PostgreSQL 并发、故障注入与持久性 `DEFERRED_TO_PG_RUNTIME`；**SPEC_FROZEN 未达成**。
