# AW-001 Slice 3A：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.4.0-rc1-slice3a`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表真实参与统计、真实身份归并、真实发言效力、真实计票或 PostgreSQL 运行时。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、七动作封闭输入、参与审计与发言/裁定对象。Schema 复用 2B 业务域的 `B_*` 定义，但不修改 2A/2B/2C 文件。
- [固定注册表](fixed-action-registry.json)：七动作、权能、八项强制检查标识、允许写集合与 0–4 级锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice3a.py)、[测试](../../../tests/test_aw001_slice3a.py)、[夹具](../../../fixtures/aw001_slice3/3a/)：43 份跃迁/行为夹具、3 份注册表夹具与 1 份期望清单，共 46 个 Golden Fixture 测试方法；另有 19 个补充测试方法。
- 本次新增 **65 个测试方法通过**；全套 **348 个方法通过**，其中前序 283 项保留。参数化子断言不重复计为方法数。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice3a.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice3a.py fixtures/aw001_slice3/3a/pt_audit_met.json fixtures/aw001_slice3/3a/pt_statement_original.json
# 预期退出码 1：包含 schema 无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice3a.py fixtures/aw001_slice3/3a/ft_statement_client_speaker.json fixtures/aw001_slice3/3a/pt_audit_met.json
```

前序 283 项基线在同一环境重跑（`tests` 不是包，故在 tests 目录设置 `PYTHONPATH`）：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_aw001_slice2c test_case_packet
```

原始证据：[283 项基线](validation-evidence/baseline-tests.txt)、[65 项 3A 测试](validation-evidence/slice3a-tests.txt)、[348 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 296 份继承文件不变，覆盖 0.1.0、Slice 1、2A/2B/2C 的实现、夹具、测试、规格、文档与既有证据。[本次摘要](validation-evidence/artifact-sha256.json) 记录 61 份本节新增或更新的文件，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

README 与既有路线图未在本步改写：它们被锁定在 2C 的 `artifact-sha256.json` 中，3A 入口更新留待独立对齐提交。

## 2. 对象与动作

实体沿用 `object_id / tenant_node_id / case_id / version_seq`。`before.domain_objects.operations` 是经未修改 2B 校验器验证的最小业务域；3A 新增 `inclusion_audits`、`speaker_statements`、`materiality_rulings`、`effectiveness_envelopes` 四个集合。所有动作不修改 `operations`。

| 动作 | 权能 | 目标 | APPLIED 写集合 | 关键拒绝 |
| --- | --- | --- | --- | --- |
| RECORD_INCLUSION_AUDIT | RECORD_INCLUSION_AUDIT | 案件 | 新建审计记录 v1 | 计数账不一致、分母/下限非法、非 MET 缺复核责任与期限、未知母体填零 |
| ESCALATE_INCLUSION_SHORTFALL | ESCALATE_INCLUSION_SHORTFALL | 审计记录 | 追加升级记录并递增审计版本 | 对 MET 记录升级、补救窗口/复核期限非法、经办人未映射 |
| PROCEED_WITH_DISCLOSURE | DECIDE_PROCEDURAL_GAP_DISCLOSURE | 审计记录 | 追加披露记录并递增审计版本 | 核心缺口、无适用预授权、预授权过期/范围不符、无具名审查或审查未通过、复核期限未到 |
| RECORD_SPEAKER_STATEMENT | 无（本人服务） | 案件 | 新建发言记录 v1（可选未确认译文） | 客户端自带发言者身份、重复 ID、成员未映射、译文 ID 冲突 |
| CONFIRM_STATEMENT_EXPRESSION | 无（本人服务） | 发言记录 | 追加确认记录并递增发言版本 | 非发言者确认、版本不存在、摘要不匹配、重复确认 ID |
| WITHDRAW_STATEMENT_PERMISSION | 无（本人服务） | 发言记录 | 追加撤回记录并递增发言版本；派生回退展示 | 撤回不存在的许可、重复撤回、非本人撤回、输入夹带计票暂停 |
| RULE_BALLOT_MATERIALITY | RULE_BALLOT_MATERIALITY | 发言记录 | 新建裁定记录 v1 | 发言版本不符、无相应权能、输入夹带停票或保护令字段 |

允许写集合分别为 `CREATE_INCLUSION_AUDIT`、`APPEND_ESCALATION`、`APPEND_DISCLOSURE`、`CREATE_SPEAKER_STATEMENT`、`APPEND_EXPRESSION_CONFIRMATION`、`APPEND_DISPLAY_WITHDRAWAL`、`CREATE_MATERIALITY_RULING`；未声明对象必须保持不变。

## 3. 参与审计语义

- 母体分母三种互斥结构：`EXACT`（正整数）、`INTERVAL`（正上下界且下界小于上界）、`UNKNOWN`（附原因）。未知与区间**不填零**，也不因区间下界高于门槛而升级为 `MET`。
- 事件分列为邀请、回应、参与、明确拒绝、无法触达、中途退出、未知处置。邀请数已知时须等于回应+拒绝+无法触达+退出+未知；参与数不得超过回应数。分层事件单独校验，重叠群体不被相加。
- 参与下限 `ratio_min_ppm` 为 0–1,000,000 的整数；`metric` 只能取 `PARTICIPATED` 或 `RESPONDED`，`denominator_basis` 固定 `ELIGIBLE_POPULATION`。覆盖率只用整数除法计算，不做浮点近似。
- 状态派生：窗口未结束为 `NOT_YET_EVALUATED`；事件账不完整、母体未知或区间为 `INDETERMINATE_INSUFFICIENT_DATA`；精确母体下按 ppm 比较得到 `MET` 或 `BELOW_REQUIRED_FLOOR`。
- 非 `MET` 记录必须登记复核责任人与复核期限（期限晚于评估时点）；`MET` 或未评估记录不得携带复核信息。门控处置只有 `MET / BLOCKED / PROCEED_WITH_DISCLOSURE` 三种。
- 升级动作只追加升级记录（补充邀请、线下协助、延长窗口、暂缓轮次、转介外部复核、终止轮次），`audit_status` 与门控不因此改变；复核期限到达不自动放行。
- 披露继续须同时满足：审计非 MET；缺口码全部属于非核心集合；评估时点不早于复核期限；存在租户/案件匹配、窗口有效且覆盖全部缺口的预授权；存在已通过的具名审查，且审查人具备 `REVIEW_PROCEDURAL_GAP` 权能并已完成成员映射。核心缺口码一律拒绝。

## 4. 发言三维语义

- 发言对象含原文版本链、译文版本链、表达确认记录、展示撤回记录以及派生的展示状态与代表状态。原文与译文的版本只追加，不覆写；`current_*_version_seq` 必须等于版本数。
- 原文每版保存语言、文本、发布许可（`PUBLIC_PERMITTED / RESTRICTED_REVIEWERS / PRIVATE_NO_DISPLAY`）与记录命令；译文每版另绑定来源原文版本号与来源文本 SHA-256。
- 表达确认绑定 `text_kind + translation_id + text_version_seq + text_sha256`，确认者必须是发言者本人；摘要必须与所引版本文本的 UTF-8 SHA-256 一致。确认只影响"是否可作为当事人表达"，不改变展示许可。
- 展示状态派生：未撤回时按当前版本许可得到 `PUBLIC / RESTRICTED / MASKED`；撤回原文即脱敏；撤回译文时仅当原文当前展示范围为 `PUBLIC` 才回退原文（`ORIGINAL_FALLBACK` 并记录回退版本），否则脱敏。
- 代表状态派生：原文始终是本人文本；译文仅在与当前版本精确匹配的确认存在时为 `CONFIRMED`，否则为 `UNCONFIRMED_DRAFT`，不得冒充当事人表达。
- 实质性裁定是独立对象，绑定发言版本与轮次引用，`automatic_ballot_pause` 恒为 `false`。裁定不创建/修改保护令、不暂停计票；是否阻断由 3C 的轮次/保护令契约另行决定。

## 5. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`：**一份正确的拒绝记录仍为 `is_valid=true`**。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`，仅合成跃迁获准；派生状态、域、结果与审计绑定重算一致 |
| 正确 REJECTED | `is_valid=true`，业务对象逐值不变，拒绝码与诊断一致 |
| 错误声明 APPLIED、拒绝码不符、拒绝偷改业务、APPLIED 后域被改 | `is_valid=false`，确定性层级、错误码与路径 |
| 结构无效、客户端夹带身份/停票字段、注册表降级 | `is_valid=false` |

诊断顺序：结构 → 访问/目标/版本/依赖 → 命令与回执绑定 → 权能与生效范围 → 动作规则（计数账/复核/预授权/版本链/确认/撤回/裁定）。

## 6. 已运行覆盖

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 精确/区间/未知母体与状态派生 | 状态真值表与 pt/ft 夹具 | PASSED_OFFLINE |
| 事件分列、拒绝/未触达/退出不合并、未知不填零 | 计数账夹具与补充断言 | PASSED_OFFLINE |
| 复核责任、升级、预授权披露、期限不自动放行 | 4 份 pt、7 份拒绝对照与截止期限反例 | PASSED_OFFLINE |
| 原文/译文许可独立、确认绑定精确文本、未确认草稿 | 发言/确认夹具与补充断言 | PASSED_OFFLINE |
| 撤回译文按原文许可回退或脱敏、撤回不暂停计票 | 两份回退对照与 2A 域不变断言 | PASSED_OFFLINE |
| 实质性裁定独立、不自动停票、不签发保护令 | 裁定夹具与域不变断言 | PASSED_OFFLINE |
| 客户端身份/停票字段封闭、注册表不可降级、CLI 与严格 JSON/UTC | 4 份 schema 无效夹具与补充方法 | PASSED_OFFLINE |
| 前序 283 项回归、原文件摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

## 7. 构造期修正与局限

构造 3A 检查器时发现并修正了以下本切片自身缺陷：回执的 `context_sha256` 在写入新信封 ID 之前计算；生效信封输入未绑定当前授权条款；审计创建信封未被对象引用链登记；`NOT_YET_EVALUATED` 状态未在窗口未结束时派生；裁定对象缺少版本号导致结果版本引用失败。以上为本次新增实现的内部修正，构造期原始失败输出未保留，只有最终运行证据；不得据此声称经历过独立评审。

已知验证边界：只覆盖合成夹具与静态离线语义；不验证真实母体推断、抽样偏差、身份归并、翻译质量、发言真实性、真实计票或数据库行为；3A 的 `is_valid=true` 不产生现实参与缺口豁免或发言效力。

## 8. 迁移与兼容边界

| 来源 | 3A 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain` 业务域 | 原样嵌套，先经未修改的 2B 校验器 | 不从静态快照推断真实并发、履行或外部交接 |
| 2A/2B 保护令、承诺、工单 | 保持逐值不变；3A 不修改其路由 | 不得借 3A 动作产生保护令或工单状态变化 |
| 2C 申诉、回避与集体批准 | 本切片不激活，也不修改其文件 | 3A 的参与/发言数据不能替代申诉或集体证明 |
| 2B/2C 回执、命令、审计结构 | 沿用同义结构，3A 扩展自身动作与权能 | 不给旧 Schema 静默添加新动作；旧证据保持原样 |
| README、Slice 2/3 路线图、2C 证据 | 本步不改写 | 3A 入口更新留待独立对齐提交，避免破坏 2C 摘要 |

摘要沿用 2A/2B/2C 的本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS；文本摘要是 UTF-8 字节的 SHA-256，仅用于离线一致性，不用于选票隐私。

## 9. 未覆盖与下一步

- **3B：UNRUN**。观测指标、UTC 时间桶、首/中/尾缺口、方向性边界（LT/LTE/GT/GTE/WITHIN_BAND）与禁止插值尚未实现。
- **3C：UNRUN**。DecisionRound/Ballot、耗权与有效票同事务、秘密选票审计隔离与 HMAC 指纹尚未实现；保护令覆盖具体轮次与动作的阻断语义也在 3C 冻结。
- 发言冻结后的误译纠正、代理确认范围、代理发布授权、撤回后的外部副本回收记录未实现；3A 只有发言者本人确认与撤回。
- 真实参与统计、身份归并、发言效力、计票、PostgreSQL 并发与故障注入均 `DEFERRED_TO_PG_RUNTIME` 或后续切片。
- **SPEC_FROZEN 未达成**；跨切片冻结清单、摘要登记与独立审阅决定仍待补齐。
