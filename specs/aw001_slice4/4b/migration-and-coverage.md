# AW-001 Slice 4B：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.5.0-rc1-slice4b`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表真实 AI 服务、真实模型提供方政策、真实门控执行或 PostgreSQL 运行时。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、七动作封闭输入、基线/分支/AI 会话/草稿对象。
- [固定注册表](fixed-action-registry.json)：七动作、权能、八项强制检查标识、允许写集合与 0–4 级锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice4b.py)、[测试](../../../tests/test_aw001_slice4b.py)、[夹具](../../../fixtures/aw001_slice4/4b/)：32 份跃迁/行为夹具、3 份注册表夹具与 1 份期望清单，共 35 个 Golden Fixture 测试方法；另有 23 个补充测试方法。
- 本次新增 **58 个测试方法通过**；全套 **559 个方法通过**，其中前序 501 项保留。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice4b.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice4b.py fixtures/aw001_slice4/4b/pt_freeze_branch_candidate.json fixtures/aw001_slice4/4b/pt_ai_draft.json
# 预期退出码 1：包含 schema 无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice4b.py fixtures/aw001_slice4/4b/ft_schema_effective_action.json fixtures/aw001_slice4/4b/pt_branch.json
```

前序 501 项基线在同一环境重跑（`tests` 不是包，故在 tests 目录设置 `PYTHONPATH`）：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_aw001_slice2c test_aw001_slice3a test_aw001_slice3b test_aw001_slice3c test_aw001_slice4a test_case_packet
```

原始证据：[501 项基线](validation-evidence/baseline-tests.txt)、[58 项 4B 测试](validation-evidence/slice4b-tests.txt)、[559 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 500 份继承文件不变，覆盖此前全部切片的实现、夹具、测试、规格、文档与既有证据。[本次摘要](validation-evidence/artifact-sha256.json) 记录 49 份本节新增或更新的文件，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

README、既有路线图与先前切片证据未在本步改写：它们锁定在既有摘要中，4B 状态以本登记及本文件为准。

## 2. 对象与动作

实体沿用 `object_id / tenant_node_id / case_id / version_seq`。`before.domain_objects.operations` 是经未修改 2B 校验器验证的最小业务域；4B 新增 `baseline_snapshots`、`proposal_branches`、`ai_sessions`、`ai_drafts`，并读取 4A 的 `governance_markings` 作为 AI 输入策略依据。所有动作不修改 `operations`。

| 动作 | 权能 | 目标 | APPLIED 写集合 | 关键拒绝 |
| --- | --- | --- | --- | --- |
| RECORD_BASELINE_SNAPSHOT | RECORD_BASELINE_SNAPSHOT | 案件 | 新建或显式更新基线版本 | 重复 ID、supersede 不是最新版、版本链断裂 |
| RECORD_PROPOSAL_BRANCH | RECORD_PROPOSAL_BRANCH | 案件 | 新建分支 v1 | 基线不存在/版本不符、参数不在白名单、重复分支 ID |
| OPEN_AI_DRAFT_SESSION | OPEN_AI_DRAFT_SESSION | 案件 | 新建 AI 会话 v1 | 重复会话、输入未知/跨案/无标记、AI 策略为禁止、用途不在允许用途 |
| RECORD_AI_DRAFT | RECORD_AI_DRAFT | AI 会话 | 新建草稿 v1（仅 DRAFT） | 会话已撤销、来源不在允许输入、重复草稿 ID |
| CONFIRM_AI_DRAFT | 无（人类本人） | AI 草稿 | 追加确认记录并改变草稿状态 | 非请求者成员确认、草稿已确认/拒绝、重复确认 ID |
| REVOKE_AI_SESSION | REVOKE_AI_SESSION | AI 会话 | 会话状态 REVOKED 与撤销时点/原因 | 会话已撤销 |
| FREEZE_BRANCH_CANDIDATE | FREEZE_BRANCH_CANDIDATE | 分支 | 分支 v2 冻结候选与冻结证据 | 分支已冻结、基线已过期、存在假设性约束变更、门控不完整 |

允许写集合分别为 `CREATE_OR_UPDATE_BASELINE`、`CREATE_BRANCH`、`CREATE_AI_SESSION`、`CREATE_AI_DRAFT`、`CONFIRM_DRAFT_ONLY`、`REVOKE_SESSION`、`FREEZE_BRANCH_CANDIDATE`；未声明对象与既有切片数据必须保持不变。

## 3. 固定基线与分支

- 基线快照保存约束集合（`binding_external` 与 `case_ratified` 分列）、参数白名单与来源引用。更新必须显式 supersede 当前最新基线并递增版本；旧版本原样保留，分支不随后续基线自动 rebase。
- 分支记录基准基线 ID 与版本、方案代码、参数键值与假设性约束变更。参数键必须属于该基线版本的白名单，不得重复；不得在分支中移除或替换已生效约束。
- 存在假设性约束变更的分支 `directly_votable=false`，只能作为探索记录，不得冻结候选；修改约束须走原有权限程序（不在本切片实现）。
- 冻结候选要求：分支为 DRAFT、基准基线仍是最新版本、无假设性约束变更，且服务端门控快照同时满足参与 `MET`、约束检查 `PASSED`、授权 `VERIFIED_ACTIVE`。冻结只写分支 v2 与冻结证据，不创建决定、承诺或任何业务变化；决定与创建承诺仍是后续独立受权动作。
- 基线更新后，旧基准分支在读取时表现为过期（基准版本小于最新版本），冻结被拒绝；不自动改写分支或轮次。

## 4. AI 草稿边界

- 会话记录请求者（principal 与 member）、用途（`CASE_ANALYSIS / TRANSLATION / OPTION_COMPARISON`）、任务委托、模型、提供方政策与允许输入集合。输入必须同案、对象存在且版本不越界；每个输入必须有 4A 治理标记，AI 处理策略不得为 `FORBIDDEN`，且会话用途必须在该输入的允许用途内。任一条件不满足即拒绝，不允许"人类可读"替代"可发送给模型"。
- 草稿只有 `DRAFT` 状态；来源输入必须是会话允许输入的子集；草稿模型不得携带生效命令、actor 身份或"人类已批准"字段（Schema 封闭）。草稿不改变任何业务对象或标记。
- 人类确认由请求者成员本人执行，追加独立确认记录并把草稿标记为 `HUMAN_CONFIRMED` 或 `HUMAN_REJECTED`；确认只确认草稿，不提交受理、不授予预算、不执行动作、不改变标记。非请求者确认被拒绝。
- 会话撤销记录时点与原因；撤销后不再允许生成草稿，已有草稿仍可被人类确认/拒绝。本切片没有恢复会话的动作，因此不存在回放已失去访问权私密上下文的入口。
- 模型关闭时，人工流程仍可独立完成确认等动作；本切片不声称真实凭证、路由或模型政策隔离已实现。

## 5. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`：**一份正确的拒绝记录仍为 `is_valid=true`**。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`；基线/分支/会话/草稿/确认与结果绑定逐项重算一致 |
| 正确 REJECTED | `is_valid=true`；业务域逐值不变，拒绝码与诊断一致 |
| 错误声明 APPLIED、拒绝码不符、拒绝偷改业务、APPLIED 后域被改 | `is_valid=false`，确定性层级、错误码与路径 |
| 结构无效、草稿夹带生效命令/actor/人类批准字段、注册表降级 | `is_valid=false` |

诊断顺序：结构 → 访问/目标/版本/依赖 → 命令与回执绑定 → 权能与生效范围 → 基线链/分支参数 → 会话输入与标记 → 草稿来源/会话状态 → 人类确认主体 → 冻结门控。

## 6. 已运行覆盖

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 基线创建、显式更新与版本链 | 2 份 APPLIED 与链式断言 | PASSED_OFFLINE |
| 分支固定基线、参数白名单、假设性约束不可表决 | 合法/非法对照 | PASSED_OFFLINE |
| 基线更新后旧分支过期、冻结拒绝 | 过期分支夹具 | PASSED_OFFLINE |
| 三类门控齐全才冻结、冻结不产生决定/承诺 | 冻结夹具与域不变断言 | PASSED_OFFLINE |
| AI 输入标记策略与用途交集、跨案输入拒绝 | 4 份正确拒绝夹具 | PASSED_OFFLINE |
| 草稿仅 DRAFT、来源子集、撤销会话禁止新草稿 | 草稿/撤销夹具 | PASSED_OFFLINE |
| 人类确认只改草稿、非请求者与重复确认拒绝 | 确认夹具与补充断言 | PASSED_OFFLINE |
| 草稿夹带生效/actor/批准字段封闭 | 3 份 schema 拒绝对照 | PASSED_OFFLINE |
| 前序 501 项回归、原文件摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

## 7. 构造期修正与局限

构造 4B 时修正了：同一案件中多次基线导致信封 ID 重复、撤销动作负载缺少四元组输入、草稿/会话对象无版本字段时的目标版本校验、冻结测试对"正确拒绝"的判定。构造期原始失败输出未保留，只有最终运行证据；不得据此声称经历过独立评审。

已知验证边界：离线契约不调用任何模型、不执行真实提供方政策、不验证凭证/路由/网络隔离；AI 输入过滤仅按合成标记与用途做静态判定；门控快照是模拟输入，不代表真实参与审计或授权系统；冻结候选不产生决定或承诺。`is_valid=true` 不产生真实授权或制度效力。

## 8. 迁移与兼容边界

| 来源 | 4B 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain` 业务域 | 原样嵌套，先经未修改的 2B 校验器 | 不从静态快照推断真实并发或执行 |
| 4A 治理标记 | 只读取最新标记做 AI 输入策略判定，不修改标记 | 派生输出/血缘不自动成为 AI 可用输入 |
| 2A/2C/3A/3B/3C | 不激活、不修改其文件 | 分支冻结不替代参与、授权、保护令或计票 |
| README、Slice 4 路线图、先前证据 | 本步不改写 | 4B 入口更新留待独立对齐提交 |

摘要沿用既有本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS；基线与分支内容为合成数据。

## 9. 未覆盖与下一步

- **4C：UNRUN**。撤回/本地输出放行、恢复清单、模板包与部署版本交换尚未实现。
- 真实 AI 服务、模型提供方合规、凭证与路由隔离、真实门控执行、跨案 AI 输入人工核查均未实现。
- 分支比较的"方案 × 模型 × 指标 × 受影响群体"展示、独立主体评价与真实冻结轮次联动未在本切片实现。
- PostgreSQL 并发、故障注入与持久性 `DEFERRED_TO_PG_RUNTIME`；**SPEC_FROZEN 未达成**。
