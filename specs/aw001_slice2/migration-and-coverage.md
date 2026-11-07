# AW-001 Slice 2A：迁移、覆盖与运行证据

日期：2026-11-05。契约版本：`0.3.0-rc1-slice2a`。状态：**Slice 2A 指定范围离线契约已验证**。仅使用 `simulation / synthetic / execution_enabled=false`；不是完整 Slice 2、SPEC_FROZEN、IMPL_CONFORMANT 或 PILOT_AUTHORIZED。

## 1. 实际交付与复现

- [Draft 2020-12 跃迁 Schema](transition-fixture.schema.json)：根 `$ref`，四段式 `before / command / server_context / after`，按动作及结果封闭分支。
- [本地固定注册表](fixed-action-registry.json)：仅激活 ISSUE_PROTECTIVE_ORDER、LIFT_PROTECTIVE_ORDER、EXTEND_PROTECTIVE_ORDER。权能、强制检查、允许写集合与锁层级由独立代码常量核对；命令不能覆盖注册表。
- [检查器](../../tools/validate_aw001_slice2.py)、[测试](../../tests/test_aw001_slice2.py)、[夹具](../../fixtures/aw001_slice2/2a/)：36 份跃迁夹具、3 份注册表夹具。测试期望位于测试代码，不在客户端输入中。
- 未增加依赖，复用原严格 JSON 读取与 UTC 秒级时间检查；实际环境 Python 3.14.0、jsonschema 4.23.0，其他 Python 版本未验证。

```sh
# 本切片：39 个 Golden Fixture 方法 + 18 个补充测试方法
python3 -m unittest discover -s tests -p test_aw001_slice2.py -v
# 全套：旧 9 + Slice 1 的 30 + Slice 2A 的 57 = 96 个测试方法
python3 -m unittest discover -s tests -v
# 均为合法记录，返回 0；包括业务拒绝及结果未知，不代表动作获准
python3 tools/validate_aw001_slice2.py fixtures/aw001_slice2/2a/pt_issue.json fixtures/aw001_slice2/2a/pt_rejected_revoked.json fixtures/aw001_slice2/2a/pt_commit_unknown.json
# 含违反契约的资料，逐输入报告，预期退出码 1
python3 tools/validate_aw001_slice2.py fixtures/aw001_slice2/2a/ft_missing_remedy.json fixtures/aw001_slice2/2a/pt_issue.json
```

已实际运行并保存：[原 39 项基线](validation-evidence/baseline-tests.txt)、[2A 的 57 项](validation-evidence/slice2a-tests.txt)、[全套 96 项](validation-evidence/all-tests.txt)、[CLI 命令及输出](validation-evidence/cli-results.json)、[环境](validation-evidence/environment.json)。39 份夹具逐项期望、层级、错误码及实际结果见 [golden-results.json](validation-evidence/golden-results.json)。方法中另有多个 `subTest`；96 是方法数，不是全部子断言数，更不表示 FT/PT-01..20 全族已通过。

[基线摘要](validation-evidence/baseline-sha256.json) 验证原 0.1.0、Slice 1 实现/证据、v0.7 主方案等原文件未变；[本次文件摘要](validation-evidence/artifact-sha256.json) 绑定本次实现、夹具和运行证据。摘要不提供数字签名、来源认证或制度授权。

## 2. 状态、版本与输入边界

### 2.1 仅三个保护令动作

`Resource` 是带版本、租户、案件、父关系及经办 member_id 的只读合成归属快照，不是已实现的完整 Commitment/FulfillmentTicket。所有动作均保持 resources 原样；不交接工单、不改变资金、不登记外部回执。

| 动作 | 允许的业务写集合 | 核验结果 |
| --- | --- | --- |
| ISSUE_PROTECTIVE_ORDER | 案件为核验目标；追加 v1 新令及新信封，案件版本不增加 | 目标必须是当前案件，新 ID 不冲突；授权包含签令权能、目标范围、可阻断权能及持续时间上限 |
| LIFT_PROTECTIVE_ORDER | 指定令 V→V+1、记录解除信封引用，追加新信封 | 令当前有效；独立解除权能；核验 member_id 不属于覆盖工单的直接经办人 |
| EXTEND_PROTECTIVE_ORDER | 旧令 V→V+1 标记被替代并引用后继，新令 v1，追加新信封 | 新授权、新 ID；旧期限及原签发信封不变；目标、阻断权能、范围模式与执行边界不扩张；新到期时间更晚 |

当前动作最多修改一条既有保护令，同时可新建一令；新信封 `resulting_versions` 列出每个结果对象版本。命令预期版本核对当前目标，回执 `domain_sha256` 绑定整个模拟读集合，不把单个版本号冒充其他对象的版本或真实数据库快照隔离。

客户端只提供 mandate_claim 与材料引用，不接受认证身份、生成回执或 skip 校验配置。输入与结果四元组均需结构完整；证据及救济引用须在模拟 evidence_snapshots 中解析。历史信封保留原版本和回执，当前撤销不改写历史声明。

### 2.2 保护令覆盖与时间

纯函数 `evaluate_protective_orders` 只返回命中的保护令 ID，不授权调用者，也不执行目标动作。上层当前命令另有模拟访问门控；该函数不是公开查询接口。

- 仅 `ACTIVE_HOLD` 且 `effective_from <= evaluated_at < expires_at` 的令具有本次判定效力；过期但标签尚未更新的令不阻断，读取不顺便写状态。
- `DIRECT_ONLY` 仅命中显式目标；`INCLUDE_DESCENDANTS` 按经核对的同租户、同案件 CASE→COMMITMENT→TICKET 或 CASE→TICKET 父链覆盖。案件令也可覆盖本案保护令动作。坏链、循环、未知目标不能当成“无令”。
- 任一适用令命中即阻断；解除 PO-2 不解除 PO-1。不同对象、不同权能及其他租户/案件不误阻。
- 新令不得追溯生效，其起止时间必须落入效力信封范围。延期后旧令不再提供当前阻断依据；迟发新令保留真实空窗。未来起效的新令也不隐含空窗内的效力。
- 对历史令核对签发/替代关系、范围、时间、版本和信封摘要。函数评估的是所提供快照中的状态，不从最新快照重建任意过去时点的历史状态。
- side_effect_disclosure 是披露文本；其中 carved_out_essential_operations 不由自然语言自动变成例外授权。实际例外必须用明确对象/权能范围表达，2A 不提供文本豁免执行器。

### 2.3 命令协调、结果与审计

before 保留逻辑 CommandRecord 与当前 CommandAttempt，检查动作、主体、键、请求摘要、attempt、generation、版本一致。仅接受本次待处理的 `IN_FLIGHT` 起点，拒绝将旧 APPLIED/REJECTED 当作新执行起点；终态结果重放、重试预登记与 generation 推进的状态转换仍未实现。

| 结果 | domain_objects | 命令/结果/审计语义 |
| --- | --- | --- |
| APPLIED | 仅允许上述写集合；其余对象逐值不变 | 当前核验回执、结果版本、CommandResult、APPLIED 审计相互绑定 |
| REJECTED | 与 before 完全相等，包括版本；不偷写 BLOCKED | 正确原因、命中令集合、REJECTED 审计及回执；结果版本集合为空 |
| SYSTEM_FAULT + ROLLED_BACK_CONFIRMED | 保持 before | 协调记录保持前态；仅故障观察，持久回执/结果/审计为空；后续仍需核对 |
| OUTCOME_UNKNOWN + COMMIT_RESPONSE_LOST | 明确为 null，不宣称未修改 | 协调后态也为 null；仅 RECONCILE_REQUIRED 观察，不伪造回滚或成功 |

`is_valid=true` 表示夹具一致；REJECTED 的业务错误在 `command_rejection.rejection_error_code`，不等于检查器的 `error_code`。检查器独立计算诊断，不采信夹具自填结果。故障观察的 `persistence_claim=NOT_ASSERTED`；记录存在不证明异常日志已落盘。2A 故障分支不保留未提交核验回执，null 也不证明核验从未发生。

规则拒绝为本逻辑键终态；仅来源不可用且没有其他已确认拒绝原因时，本次 attempt 为 FAILED_RETRYABLE、协调记录仍为 IN_FLIGHT，结果标 NEW_ATTEMPT_SAME_KEY_AFTER_RECOVERY。此处只表达重试分类；实际同键新 attempt 要先由运行时核对/登记，不能拿当前失败 attempt 直接再执行。

诊断先完成结构、访问、版本、命令及回执绑定，再按活跃保护令→授权状态→效力范围→新令范围/期限或解除回避顺序保留可确认原因。来源不可用不伪装成授权已有效；同时存在终态规则拒绝时，不能用来源重试绕过它。

## 3. 已运行覆盖及开发中修正

| 已验证子范围 | 对应既有族/条款 | 实际证据 | 状态 |
| --- | --- | --- | --- |
| 签发、解除、新令延期、历史信封与各对象版本 | 04、07、16 | pt_issue、pt_second_order、pt_lift_one_of_two、pt_extend_new_order、pt_late_extension；写集合负例 | PASSED_OFFLINE |
| 时间半开区间、多令、父级覆盖、无关目标/权能/租户、坏链 | 04、07 | BoundaryTests 时间/覆盖方法；正确保护令拒绝夹具 | PASSED_OFFLINE |
| 授权撤回/过期/错误权能、来源不可用及多原因 | 03、11 契约部分 | 对应 pt_rejected_*、伪造回执/假 APPLIED 负例 | PASSED_OFFLINE |
| 解除人 member_id 与覆盖工单直接经办人互斥、必要身份未知 | RC 2.4 的 2A 子集 | self_lift/unmapped_member 及无冲突解除对照 | PASSED_OFFLINE |
| 三路结果表达、拒绝零业务变化、回执/attempt/generation/审计绑定 | 10 的结构部分 | 拒绝偷改、结果 generation、审计摘要、终态重执行、回滚/未知夹具 | PASSED_OFFLINE |
| 固定动作、强制检查、静态锁序与封闭输入 | 16 | 3 个注册表夹具、客户端注入及未注册动作负例 | PASSED_OFFLINE（锁序仅声明） |
| 单变量负例隔离、CLI、严格 JSON/UTC、旧回归 | 交付质量检查 | 19 份单路径变异自动比对、CLI/输入测试、96 项全套输出 | PASSED_OFFLINE |

`ft_revoked_claims_applied` 另检验“授权已撤销且回执如实记录撤销，却仍声明 APPLIED”的一致候选结果；它不冒充单路径变异夹具。多原因及未知结果分别单列。核心夹具命名不创建新的 FT/PT 测试族。

开发时通过补充测试复现了“新令可超过信封有效期、历史令未核对信封范围”的遗漏。保留 [修正前失败输出](validation-evidence/scope-before-fix.txt)，修正后 57/96 项结果单独登记；该历史失败文件不是当前验收结果。

## 4. 旧版本映射与不自动推断项

未自动迁移任何历史资料，也未重写旧 Schema、样例、检查器、测试或证据。三个契约版本并存，不用替换 contract_version 字符串实现升级。

| 来源真实字段/结构 | Slice 2A 对应表达 | 迁移限制 |
| --- | --- | --- |
| 0.1.0 case_id、case_version、source_node_id | Resource 的案件标识、归属、版本 | source_node_id 不证明本地 tenant 或访问权；单案版本不充当所有子对象版本 |
| 0.1.0 authority_records 的 scope、powers、status、有效期与 basis_ref | authority_snapshot 及 mandate_claim、条款、证据引用 | 旧 assess_constraints/freeze_round 不授予三个保护令权能；需新授权声明及版本 |
| 0.1.0 evidence[].evidence_id/revision | EvidenceRef | 必须补明摘要对象及 content_sha256；不从 ID 合成真实证据摘要 |
| 0.1.0 gates、review_status | 保留旧表决前评估记录 | 不据 blocked/ready 自动生成保护令、工单状态或命令 |
| Slice 1 command、server_context、before/after | 沿用四段式边界与确定性分层诊断 | 新增 CommandRecord/Attempt/Result、domain_objects；不是字段兼容的直接载入 |
| Slice 1 evaluated/resulting target version | 新信封 evaluated_target_version_seq + resulting_versions[] | 多对象分别记录；不改写 Slice 1 历史信封 |
| Slice 1 固定四动作注册表 | 独立版本的三个保护令动作注册表 | 本次不合并路由，不激活交接/申诉动作；跨切片统一注册表待完整冻结检查 |
| 旧资料未记录的父链、经办 member_id、保护令、命令尝试与审计 | 在获准模拟中补充并明确来源 | 不能填入虚假已核验身份或“已提交”状态以通过检查；真实历史仍需人工核对 |

`digest()` 使用 Python 标准 JSON 的排序键、紧凑分隔符、UTF-8（ensure_ascii=false、禁止非有限数）计算合成控制记录指纹；不是跨语言 JCS，不是身份认证或敏感内容隐私承诺。不得复用于低熵秘密选票。原证据正文不在夹具中，检查器只核对声明引用，不核验材料真实性。

## 5. 尚未覆盖与准入

| 未完成项 | 状态及下一步 |
| --- | --- |
| 2B 承诺义务、取消工单、交接许可与实际人工交接、回执与独立履行核验 | UNRUN；下一步按既有 Slice 2B 方案独立交付 |
| 2C 申诉进度、集体批准、完整回避集合、缺人暂缓 | UNRUN；2A 的 APPEAL_STAY 只是来源分类和证据引用，不是已实现申诉状态机 |
| 真实认证、ACL、member_id 归并、机构回避判断、救济可用性 | DEFERRED_TO_PG_RUNTIME / 现实制度核验；模拟字段不证明其真实 |
| 在线授权撤销、新鲜度、保护令空集并发插入、同案锁锚点 | DEFERRED_TO_PG_RUNTIME；当前没有数据库或网络授权查询 |
| 第 0 级命令锁、0–4 级锁序执行、generation 围栏、幂等重放/重试、COMMIT 核对 | DEFERRED_TO_PG_RUNTIME；静态绑定不证明互斥、提交或恰好一次 |
| 业务/回执/审计 ACID、拒绝独立事务、故障通道持久化 | DEFERRED_TO_PG_RUNTIME；必须真实数据库故障注入，不靠 JSON 证明 |
| 外部交接、签名、资金/API 控制、历史争议回放 | 未实施；2A 无外部副作用 |
| Slices 3–4、跨切片兼容、完整冻结清单与审阅决定 | UNRUN；GATE_1 尚未达成 |

结果仅适用于登记的离线契约和合成断言，不代表安全证明或现实治理效果。
