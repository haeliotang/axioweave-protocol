# AW-001 Slice 2C：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.3.0-rc1-slice2c`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表在线授权、PostgreSQL 并发事务、真实申诉、集体效力或现实救济已获验证。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、六动作封闭输入、四段申诉状态、回避与集体证明结构。2C 的 `domain_objects.operations` 直接嵌套经 2B 校验的业务域，2C 不复制或重构 2A/2B 对象定义。
- [固定注册表](fixed-action-registry.json)：六动作映射、八项强制检查、允许写集合与 0–4 级静态锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice2c.py)、[测试](../../../tests/test_aw001_slice2c.py)、[夹具](../../../fixtures/aw001_slice2/2c/)：68 份跃迁夹具、3 份注册表夹具、10 份跨 2B 完整链夹具及 1 份期望清单；共 71 个 Golden Fixture 测试方法，另有 15 个补充测试方法。
- 本次新增 **86 个测试方法通过**；全套 **283 个方法通过**，其中 2B 末态的原 197 项保留（0.1.0/Slice 1/2A 96 项 + 2B 101 项）。参数化子断言不重复计为方法数，测试方法数不等于全部风险或服务端测试族覆盖数。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice2c.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice2c.py fixtures/aw001_slice2/2c/pt_submit_minimal.json fixtures/aw001_slice2/2c/pt_adjudicate_upheld.json fixtures/aw001_slice2/2c/pt_deferral_registrar.json
# 预期退出码 1：包含无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice2c.py fixtures/aw001_slice2/2c/ft_client_identity.json fixtures/aw001_slice2/2c/pt_open_review.json
```

2B 末态的 197 项基线在同一环境下重跑；因 `tests` 不是包，基线命令为：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_case_packet
```

原始证据：[197 项基线](validation-evidence/baseline-tests.txt)、[86 项 2C 测试](validation-evidence/slice2c-tests.txt)、[283 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)、[修正前失败输出](validation-evidence/recusal-before-fix.txt)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖，其他 Python 版本未在本次验证。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 194 份继承文件不变，覆盖 0.1.0、Slice 1、2A、2B 的实现、夹具、测试、规格与既有证据（含 2B 摘要文件自身），不含本次更新的 README 与 Slice 2 路线图。[本次摘要](validation-evidence/artifact-sha256.json) 记录 99 份新增或更新的实现、夹具、文档和证据（含英文主 README 与保留的中文版 README.zh-CN.md），不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证；2A/2B 证据包中的历史文档摘要为其登记时点的记录，本文件不据此声称当前文件未变。

## 2. 六动作与允许写集合

六动作均为封闭输入。`SUBMIT_APPEAL` 无单独权能，其余五动作要求 `VERIFIED_ACTIVE` 授权与生效信封；所有动作先通过完整结构、访问、版本、命令协调与回执绑定检查，再进入业务诊断。

| 动作 | APPLIED 业务变化 | 保留边界 |
| --- | --- | --- |
| SUBMIT_APPEAL | 新建申诉对象 v1，记录挑战对象/版本/决定信封与原始陈述 | 目标为案件；需要有效受理政策（`intake_open`、版本、受理检查一致）；无生成回执或信封；不改变既有决定 |
| OPEN_APPEAL_ADMISSIBILITY_REVIEW | 申诉 `SUBMITTED→ADMISSIBILITY_REVIEW`，版本递增，记录复核 panel 与开启信封 | 必须存在回避后仍满足受理法定人数的合格复核池；不创建或解除保护令 |
| RULE_APPEAL_ADMISSIBILITY | `ADMISSIBILITY_REVIEW→ADJUDICATING`（ACCEPTED）或 `RESOLVED`（REJECTED_INADMISSIBLE）；写入受理记录 | panel 必须与开启时的 `review_panel_member_ids` 一致；拒绝受理不产生实体裁决；不隐含解除权 |
| ADJUDICATE_APPEAL | `ADJUDICATING→RESOLVED`；写入实体裁决记录与救济指令 | 指令只登记目标对象，不执行改承诺、解除令或赔付；`UPHELD_REMEDY_REQUIRED` 与是否存在指令必须一致 |
| RECORD_SOD_DEFERRAL | 在可审阶段写入暂缓/转介记录，业务对象保持逐值不变 | 仅在回避后合格人数低于法定人数时成立；须有期限、池证据和有效范围；登记路径不得携带集体 panel |
| APPROVE_COMMITMENT_COLLECTIVE | 更新资源池、创建承诺及全部分期工单；写入集体程序信封与投影操作信封 | 复用 2B 批准算式与写集合；提案人回避、全体成员证明、完整 panel 与法定人数缺一不可 |

允许写集合分别为 `CREATE_APPEAL_ONLY`、`OPEN_REVIEW_ONLY`、`ADMISSIBILITY_ONLY`、`MERITS_ONLY`、`DEFERRAL_ONLY`、`UPDATE_POOL_CREATE_COMMITMENT_ALL_TICKETS`；未列对象必须保持不变。申诉动作只递增申诉对象版本；集体批准按 2B 规则递增资源池、承诺、工单与核验引用，不修改案件、保护令或旧信封。

## 3. 申诉状态机与保护令边界

- 状态只有 `SUBMITTED / ADMISSIBILITY_REVIEW / ADJUDICATING / RESOLVED`；没有 `STAY_GRANTED`，也不允许跨阶段跳转。
- 历史一致性由域校验重算：阶段与开启信封、受理记录、实体记录和暂缓记录必须互相自洽；每个决定信封的动作、目标、版本和参与者须与记录逐项一致。
- 申诉阶段变化本身不创建、延期或解除保护令。获准暂停须另发 `ISSUE_PROTECTIVE_ORDER`（`source_type=APPEAL_STAY`）；解除令须独立受权。2C 测试以真实 2A 令核对：四个申诉阶段不改变 2A/2B 覆盖判定，裁决也不直接释放被限制的执行动作。
- 挑战对象限定为 `COMMITMENT / TICKET / PROTECTIVE_ORDER`，且版本必须落在被挑战决定信封的 `resulting_versions` 内；不得挑战未来版本或不存在的决定。

## 4. 回避集合与职责分离

回避集合只沿直接责任链和版本化政策扩展，不取整条案件血缘。每个纳入项记录理由、来源对象和被评估责任记录版本；必要成员映射未知时闭锁，不以“无关人员未知”拒绝整个案件。

| 场景 | 纳入理由 | 边界 |
| --- | --- | --- |
| 申诉人 | `APPELLANT` | 始终纳入 |
| 被申诉决定签署人 | `CHALLENGED_SIGNER` | 含集体决定全体成员 |
| 工单申诉 | `CURRENT_EXECUTOR`、`DIRECT_AUTHORIZER`、`DIRECT_AUTHORIZER_SIGNER`、`DIRECT_OPERATOR`、`CURRENT_VERIFIER`、`HISTORICAL_DIRECT_SIGNER` | 当前核验人由该工单的执行核验记录派生，不因核验晚于被挑战授权而遗漏 |
| 承诺申诉 | `DIRECT_APPROVER` | 只含批准签署人，不扩成全部执行方 |
| 保护令申诉 | `ORIGINAL_ORDER_ISSUER`、`RESTRICTED_EXECUTOR` | 受限执行人按令的覆盖判定派生 |
| 政策扩展 | `POLICY_PROPOSER`、`POLICY_PARENT_APPROVER`、`POLICY_COMMITMENT_EXECUTOR` | 只有政策字段启用时才纳入；不改写 RC 对所有共同体的硬规则 |

合格复核池由 `reviewer_pool_snapshot` 派生：完整性、有效期、租户/案件、规则与政策版本、授权清单完整性、来源可用性和证据引用都必须成立；候选 principal 唯一，成员映射为 `VERIFIED`，可用且未命中回避，并对所需权能处于 `VERIFIED_ACTIVE`。池不足时只能记录暂缓或转介；暂缓的合格人数、法定人数、期限和池证据须与重算一致。

## 5. 集体批准、候选验证与暂缓

- 集体动作要求 `collective_member_attestations` 覆盖请求的全体 principal，且必须包含当前认证主体；成员标识唯一、映射有效、授权快照与独立回执逐字段绑定，并绑定命令载荷摘要、目标版本、条款/规则/政策版本。
- panel 必须完全来自合格池且达到对应法定人数（批准、受理、实体分别取值），并与回避集合无交集；受理裁决不得更换开启时的 panel。
- 集体批准的授权信封保存完整证明与回避轨迹，并由 `projected_operational_envelope_id` 投影为 2B 可校验的操作信封；投影摘要必须与在案件上重算的 2B 域一致。
- 缺人暂缓只允许 `DEFERRED_FOR_POOL_REPLENISHMENT` 或 `REFERRED_TO_EXTERNAL_PANEL`；由登记人单独受权，不携带 panel，不改变业务对象；期限必须落在生效范围内，池证据必须指向本次复核池快照。

## 6. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝码属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`，仅合成跃迁获准；域、结果、审计与版本绑定重算一致 |
| 正确 REJECTED | `is_valid=true`，业务对象逐值不变，结果版本为空，拒绝码与诊断一致 |
| 已确认回滚 `SYSTEM_FAULT` | 业务与命令状态保留 before；不声称结果或审计持久化成功 |
| `OUTCOME_UNKNOWN + COMMIT_RESPONSE_LOST` | 业务与命令后态均为 null，仅 `RECONCILE_REQUIRED`；不伪造回滚或成功 |
| 错误放行、拒绝偷改、历史改写或错误诊断 | `is_valid=false`，确定性层级、错误码及路径 |

诊断顺序固定：结构 → 访问/目标/版本/依赖 → 命令与回执绑定 → 授权来源与范围 → 回避/池 → panel/集体证明 → 阶段条件 → 结果/审计绑定。只有唯一原因是 `ERR_MANDATE_SOURCE_UNAVAILABLE` 时才标 `FAILED_RETRYABLE`；其他终态原因保持终态拒绝。

## 7. 已运行覆盖与开发修正

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 申诉受理、开启、受理裁决、实体裁决与状态机 | 68 份跃迁夹具中的 pt/ft 对照 | PASSED_OFFLINE |
| 阶段变化不触碰保护令、裁决不隐含解除权、多令约束 | 四阶段 active-PO 夹具与 2A 谓词对照 | PASSED_OFFLINE |
| 回避责任链、政策开关、替换授权人、集体签署人、当前核验人 | 独立合法/非法夹具与补充测试 | PASSED_OFFLINE |
| 池完整性、可用性、授权与别名去重 | 池拒绝夹具与合格人数断言 | PASSED_OFFLINE |
| 集体批准、成员证明、提案人回避、法定人数、投影一致性 | 集体批准夹具与跨 2B 完整链 | PASSED_OFFLINE |
| 缺人暂缓/转介、期限与池证据、登记人冲突 | 暂缓夹具与非法结果夹具 | PASSED_OFFLINE |
| 四结果分支、命令/回执/审计/版本绑定、CLI 与严格 JSON/UTC | 71 个核心夹具及补充方法 | PASSED_OFFLINE |
| 跨 2B 完整链（10 步）、2A 保护谓词复用 | 补充测试中的跨切片方法 | PASSED_OFFLINE |
| 原 197 项回归、原文件及摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

开发中保留一份真实失败输出：

- [当前核验人遗漏](validation-evidence/recusal-before-fix.txt)：以 `pt_open_review` 追加 2B 已验证工单构造申诉时，回避集合只含申诉人、财务批准人与执行人，遗漏核验人 `member-verifier`。原因是对历史直接签署人的版本过滤排除了晚于被挑战授权的核验记录。修正为从该工单的 `execution_verifications` 派生 `CURRENT_VERIFIER`，未放宽其他回避断言，也未修改测试期望。

全套通过只覆盖上述明确断言，不是形式化完整性证明；夹具的合法性/非法性由期望清单独立声明，检查器不读取期望值。

## 8. 迁移与兼容边界

| 来源 | 2C 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain`（案件、池、承诺、工单、核验、保护令与旧信封） | 原样嵌套为 2C 的 `operations`，先经未修改的 2B 校验器，再做 2C 语义检查 | 不从静态快照推断真实并发、履行或外部交接 |
| 2B 集体授权 | 新增集体程序信封与投影；投影必须等于 2B 在案件上重算的操作域 | 不把投影当作新的 2B 路由或允许 2A/2B 客户端提交集体字段 |
| 2A 保护令与覆盖谓词 | 直接复用未修改的 2A 函数做交叉断言 | 申诉阶段变化不自动签发/解除令；不改 2A 契约 |
| 2A/2B 注册表与权能 | 保持原样；2C 只在自身注册表登记六动作 | 不给旧 Schema 静默添加新动作或权能 |
| 0.1.0/Slice 1 主张、立场与历史 | 保留原独立契约与证据 | 不自动转成申诉、集体授权或救济效果 |

摘要沿用 2A/2B 的本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS，不用于秘密选票的隐私保护。证据正文、身份归并、集体意志与救济真实性仍需独立来源验证。本次无真实案件迁移，无历史争议效果验证。

## 9. 未覆盖与下一步

- **Slices 3–4：UNRUN**。实际计量计算、选票隐私隔离、许可传播、Outbox、互认与恢复等未由本次完成。
- **AW-002：DEFERRED_TO_PG_RUNTIME**。真实认证/ACL/成员归并、授权撤销与新鲜度、并发锁序、业务/回执/审计 ACID、重试/幂等/COMMIT 核对及故障通道持久性。
- 真实申诉受理时限、集体成员独立同意、救济执行、暂停令与费用承担均未实现；2C 只登记指令与记录，不产生现实效力。
- **SPEC_FROZEN 未达成**。跨切片字段/枚举/错误码与状态机一致性、完整契约目录、摘要范围及独立审阅决定仍须汇总验收。

下一步按 Slice 2 路线图进入 Slices 3–4；本次结果不自动扩展到后续切片或运行时验收。
