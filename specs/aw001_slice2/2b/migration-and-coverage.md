# AW-001 Slice 2B：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.3.0-rc1-slice2b`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表在线授权、PostgreSQL 并发事务、真实交接或履行已获验证。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、六动作封闭输入、四种结果分支。
- [固定注册表](fixed-action-registry.json)：六动作映射、强制检查、允许写集合和静态锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice2b.py)、[测试](../../../tests/test_aw001_slice2b.py)、[夹具](../../../fixtures/aw001_slice2/2b/)：73 份跃迁夹具和 3 份注册表夹具，共 76 个 Golden Fixture 测试方法；另有 25 个补充测试方法。
- 本次新增 **101 个测试方法通过**；全套 **197 个方法通过**，其中原 0.1.0/Slice 1/Slice 2A 的 96 项保留。参数化子断言不重复计为方法数，测试方法数不等于全部风险或服务端测试族覆盖数。

复现命令：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice2b.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice2b.py fixtures/aw001_slice2/2b/pt_approve_all_phases.json fixtures/aw001_slice2/2b/pt_rejected_origin_revoked.json fixtures/aw001_slice2/2b/pt_commit_unknown.json
# 预期退出码 1：包含无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice2b.py fixtures/aw001_slice2/2b/ft_missing_remedy.json fixtures/aw001_slice2/2b/pt_record_handoff.json
```

原始证据：[96 项基线](validation-evidence/baseline-tests.txt)、[101 项新增测试](validation-evidence/slice2b-tests.txt)、[197 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖，其他 Python 版本未在本次验证。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录并复核 103 份原文件不变，包含 2A 的实现、夹具、测试、证据及其既有文档摘要范围。[本次摘要](validation-evidence/artifact-sha256.json) 记录新实现、夹具、文档和证据，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

## 2. 六动作与允许写集合

实体沿用 `kind / parent_object_id / tenant_node_id / case_id / version_seq`。资源池是独立账本对象，不加入 Case→Commitment→Ticket 覆盖父链。新建对象 v1、既有被修改对象 V→V+1；`resulting_versions[]` 精确列出每个结果对象。其他对象及旧信封逐值不变。

| 动作 | APPLIED 业务变化 | 保留边界 |
| --- | --- | --- |
| APPROVE_COMMITMENT | 更新资源池版本与承诺占用；创建承诺及所有分期工单 v1；追加信封 | 单人审批；案件不修改；所有分期初始化为 QUEUED_INTERNAL，不自动解锁后期 |
| CANCEL_INTERNAL_TICKET | 当前未交接工单 V→V+1、CANCELLED；追加信封 | 父承诺与资源池完全不变；已交接不允许内部取消；不创建替代工单 |
| AUTHORIZE_TICKET_HANDOFF | QUEUED_INTERNAL→READY 或 READY→READY；版本递增，新许可和信封 | 重授权时旧许可原样追加历史，旧信封不改；许可绑定新的工单版本 |
| RECORD_TICKET_HANDOFF | READY→HANDED_OFF_TO_EXTERNAL；追加实际经办人、接收方、时间和信封 | 重检当前权限、保护令、原许可及其底层授权、承诺条款、案件规则/模板和适用阶段条件 |
| LOG_EXTERNAL_RECEIPT | 已交接或已登记回执的工单追加回执项；递增版本并进入/保持 EXTERNAL_RECEIPT_LOGGED | 不改变已履行额；同一承诺内外部回执引用号不得换 receipt_id 重复登记 |
| VERIFY_EXECUTION_RECEIPT | 新核验记录 v1，工单递增版本并追加核验引用；成功时累加工单及父承诺核验量、递增父承诺版本 | NOT_FULFILLED / INSUFFICIENT_EVIDENCE 的增量为 0，父承诺不变；不添加 VERIFIED 工单状态或自动保护令 |

当前许可由工单的 `handoff_authorization` 唯一引用；history 中保留原状态是保留原历史事实，不表示仍存在第二份当前许可。被撤销/替代的当前许可不能用于交接。撤销、修改条款及规则动作本身不在 2B 激活范围，相关夹具仅提供模拟的当前依赖快照。

命令 `dependency_versions` 在批准时绑定资源池版本，在工单动作时绑定案件及父承诺版本；当前目标另有 target_expected_version_seq。回执同时绑定 `domain_sha256` 与 `context_sha256`，后者排除 generated_auth_receipt 自身以避免循环。摘要仅绑定所给静态资料，不代替数据库读锁或真实性核验。

## 3. 账本、核验与阶段解锁

### 3.1 三层量分离

2B 当前支持 `FUNDS / USD_CENTS` 与 `COMPUTE_TIME / GPU_MINUTES` 两种明确的资源/单位配对。数量为整数最小单位，不做浮点汇率换算，不把不同资源直接相加。每个示例池限定单一案件、租户及资源单位；跨案件共享池路由不在本次实现范围。

- 资源池 `committed_obligations_minor` 等于所给完整池库存中所有承诺的 total_obligated_minor 之和，不超过容量。包括已完成承诺：本切片不回收、补充或循环使用已分配总额，也不将已履行额重复扣减。
- 承诺总义务等于全部分期 allocated_minor 之和；批准即创建每期唯一工单，连续编号 1..N。
- `internal_ticket_reserved_minor()` 是从未取消工单分配额计算的派生视图，不持久写进 CommitmentRecord。因此取消只改工单，但不会把未解除的承诺义务退回公共可用余额。
- 阶段数量、工单数量、承诺已核验总量及资源池聚合均重算，不信任汇总字段自证。`pool_inventory_complete=true` 是模拟输入的完整性声明，离线检查无法发现整个输入共同隐瞒的外部承诺；真实完整性及并发超额分配留待运行时。

资源池更新的运行时锁声明归入既有第 2 级，与案件/承诺按稳定 ID 排序，不增加新的锁层级。尚未证明实际数据库按此执行；跨案件共享资源池需要额外的明确锁范围与并发验收，不能套用本次单案示例直接宣布安全。

### 3.2 部分履行与不重复计入

每条回执绑定唯一 receipt_id、登记时工单版本、外部引用号、附件摘要及申报量；每次核验绑定工单、该回执及其登记版本。成功增量须大于 0，且不超过该回执申报量、工单剩余量或承诺剩余量。

同一 (ticket_id, receipt_id) 一旦成功核验，不允许再次计入；同一承诺内外部引用号重复登记也被拒绝。不同回执可分别确认不同增量，例如 2500 + 1500 = 第一期 4000。单份回执只确认部分后，余量必须有新的独立回执依据，不能重用已成功核验记录。

附件摘要不是付款或交付真实性证明；全部来源字段一起被伪造、同一真实交付被申报为不同引用号等问题，需要独立核验机构和外部证据处理。检查器不声称从 hash 识别现实重复交付，也不禁止合法汇总附件在不同回执中出现。

只有所有前期工单累计确认量均等于各自阶段额度，后期才可授权或实际交接。存在一条成功核验记录但未足额，不能解锁下一期。核验量为 0 的两种结论都不自动认定违约、生成暂停令或减少义务。

## 4. 权限、职责分离与保护令适配

- 仅单人审批，客户端联合签署数组被封闭 Schema 拦截。当前 principal→member 映射须匹配且为 VERIFIED；null 和字面值 UNMAPPED 均不视为已核验成员。
- 批准人与案件提案人不得同 member；核验人与工单交接授权人及实际经办人不得同 member。必要责任人未知也不放行。
- `template_sod_policy` 是案件内带版本的只读政策。仅在 exclude_commitment_executors_from_verification 启用时额外排除承诺执行方代表，报 ERR_SOD_TEMPLATE_EXECUTOR_CONFLICT；不是对所有共同体扩张 RC 硬规则。修改政策的效力动作未在本切片实现。
- `authority_snapshots` 对原交接许可的来源授权重新提供模拟状态；当前经办授权与原授权分别核验。来源不可用不被补成有效；到期、撤销、范围/权能不符、原授权主体不匹配都不允许交接。
- 交接时同时检查原许可状态、许可期限、许可所绑定工单版本、承诺 terms_version_seq、案件 rule_version_seq 和模板摘要。新授权不会覆盖旧许可历史。
- metric_evaluations 只消费带租户、案件、承诺、阶段、条款/规则版本、有效期和证据引用的模拟结论；AUTHORIZE 必须满足阶段指标，HANDOFF 按 require_fresh_compliance_at_handoff 重验。时间分桶计算仍属 Slice 3。

2B 使用自己的完整权能枚举及 Domain Schema，包括 LOG_EXTERNAL_RECEIPT。先验证完整 2B 资料，再投影 Case/Commitment/Ticket 父链给未修改的 2A 祖先/覆盖/时间及保护令历史检查函数；不把不兼容对象直接塞进 2A 的封闭入口，不捕获错误后返回“无令”。

2A 的签发、双令、解除、延期、迟发延期夹具在 2B 测试中复用，对同一父链、目标、范围模式及起止时点逐项对照，结果一致；新增回执权能另有覆盖测试。未知权能、坏父链、跨租户或跨案件关联均闭锁。2B 不新增保护令签发路由，其保护令资料是历史快照。

## 5. 四结果分支与诊断

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 JSON_INPUT。业务拒绝码属于 execution_outcome.command_rejection，不是检查器的 is_valid。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | is_valid=true，仅合成跃迁获准 |
| 正确 REJECTED | is_valid=true，业务对象按解析后的字段和值完全不变，结果版本为空 |
| 已确认回滚 SYSTEM_FAULT | 业务及协调后态保留 before；不声称结果/审计持久化成功 |
| OUTCOME_UNKNOWN + COMMIT_RESPONSE_LOST | 业务与协调后态均为 null，仅 RECONCILE_REQUIRED 观察；不伪造回滚或成功 |
| 错误放行、拒绝偷改、历史改写或错误诊断 | is_valid=false，确定性层级、错误码及路径 |

共享的授权/范围/保护令错误码沿用 2A。2B 新增的资源池、前期未完成、许可到期、依赖失效、指标不足、重复回执、核验数量及 SoD 细分码属于离线契约诊断，尚不是生产 API 协议。

先完成结构、访问、对象/依赖版本、命令/attempt/generation 与回执绑定；再按保护令→授权来源/状态→范围与资源→SoD→阶段/回执条件稳定排序诊断。唯一原因是 ERR_MANDATE_SOURCE_UNAVAILABLE 时才标 FAILED_RETRYABLE；有其他终态原因时保持终态拒绝。实际幂等重放、同键新 attempt、generation 推进与 COMMIT 核对仍未实现。

未经授权但实际已发生的外部交接，须作为事实与待审异常登记到另行定义的事件/申诉通道；不得补造合法交接，也不能在审查前凭一条异常报告自动定罪。2B 不实现该事件受理入口，不能当作现实系统拒绝记录事实的依据。

## 6. 已运行覆盖与开发修正

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 批准全部阶段、资源上限、单人审批、取消不免责 | 对应 pt/ft 夹具；账本与单位补充测试 | PASSED_OFFLINE |
| 授权/交接分离、过期许可续期、前期部分履行不解锁 | 分期完整链及历史许可断言 | PASSED_OFFLINE |
| 交接前新令、到期令失权、原授权撤销、条款/规则变化、指标过期/不合规 | 独立合法拒绝夹具，来源不可用补充测试 | PASSED_OFFLINE |
| 多回执与增量核验、两种零增量结果、重复计入与换 ID 重复登记 | 第一阶段 2500+1500、第二阶段 6000；错误额度及版本测试 | PASSED_OFFLINE |
| 授权人/经办人互斥、模板启用/关闭对照、未知成员 | 三类独立 SoD 夹具与 sentinel 回归 | PASSED_OFFLINE |
| 多对象版本、读集合摘要、写集合限制、四结果表达及审计绑定 | 76 个核心夹具及结果方法 | PASSED_OFFLINE |
| 2A/2B 覆盖规则一致、28 份单路径反例隔离、CLI、严格 JSON/UTC | 25 个补充方法中的相关断言 | PASSED_OFFLINE |
| 原 96 项回归、原文件及摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

开发时保留三份真实失败证据，修正后结果另存：

1. [夹具隔离失败](validation-evidence/fixture-isolation-before-fix.txt)：重复核验示例原先同时超出工单剩余量，调整测试增量后只触发重复核验；未弱化实现诊断。
2. [未知成员失败](validation-evidence/member-sentinel-before-fix.txt)：补齐 null 以外 UNMAPPED 字符串的拒绝。
3. [资源池引用失败](validation-evidence/pool-reference-before-fix.txt)：引用已有案件 ID 时原先触发 KeyError；增加资源池类型核验，改为 ERR_INVALID_RESOURCE_POOL_REFERENCE。

复合拒绝优先级另用专门夹具，不伪装成单故障。每个合法拒绝场景还测试“偷改业务”和“声称 APPLIED”的无效结果。全套通过仅覆盖这些明确断言，不是形式化完整性证明。

## 7. 迁移与兼容边界

| 来源 | 2B 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2A Resource.kind / parent_object_id / tenant_node_id / case_id / version_seq | 保留名称，分别展开 cases、commitments、tickets | 不从只读桩推断阶段额度、预算来源、外部回执或已经履行 |
| 2A Domain / 保护令及其旧信封 | 独立 protection_envelopes；经过 2B Schema 校验后做覆盖投影 | 不改旧保护令动作路由或旧来源回执；无自动迁移器 |
| 2A CommandRecord / Attempt / Result、四结果分支 | 同义结构并存，新动作使用 2B 枚举与注册表 | 保持旧版本文件；不静默给旧 Schema 添加新权能 |
| 2A evaluated_target_version_seq / resulting_versions[] | 保留并覆盖资源池、承诺、工单和独立核验记录 | 父承诺版本变化不能用工单版本代替 |
| 2A receipt.domain_sha256 | 保留，并新增 context_sha256、命令 dependency_versions | 不把静态摘要等同于数据库锁或真实外部源证明 |
| 2A ScopeSpec | 增加 resource_spec 与 max_amount_minor | 旧无资源量范围不自动授予预算分配权 |
| 0.1.0 authority/evidence 与 Slice 1 主张/审查历史 | 仅保留来源版本，沿用原独立契约 | 不自动转成资源池额度、执行授权、已核验 member 或履行证明 |

摘要沿用 2A 的本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS，不用于秘密选票的隐私保护。证据正文、外部回执真实性及现实身份仍需独立来源验证。本次无真实案件迁移，无历史争议效果验证。

## 8. 未覆盖与下一步

- **Slice 2C：UNRUN**。集体批准/裁决、申诉完整责任链、缺人暂缓与申诉状态机尚未实现。
- **Slices 3–4：UNRUN**。实际计量计算、选票隐私隔离、许可传播、Outbox、互认与恢复等未由本次完成。
- **AW-002：DEFERRED_TO_PG_RUNTIME**。真实认证/ACL/成员归并、授权撤销与新鲜度、保护令空集插入、资源池并发分配、全部锁序、业务/回执/审计 ACID、重试/幂等/COMMIT 核对及故障通道持久性。
- 外部账户与实际交接、核验机构责任、救济可用性、替代工单、义务修改/资源回收均未实现，不借本切片自动增加效力。
- **SPEC_FROZEN 未达成**。跨切片字段/枚举/错误码与状态机一致性、完整契约目录、摘要范围及独立审阅决定仍须汇总验收。

下一步按既有 Slice 2C 方案补齐申诉与完整实例级职责分离；本次结果不自动扩展到后续切片。
