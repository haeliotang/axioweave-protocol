# AW-001 Slice 3C：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.4.0-rc1-slice3c`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表真实随机源、真实 HMAC 校验、数据库访问控制、真实计票或 PostgreSQL 运行时。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、三动作封闭输入、决策轮/资格/选票/双侧审计对象。
- [固定注册表](fixed-action-registry.json)：三动作、权能、八项强制检查标识、允许写集合与 0–4 级锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice3c.py)、[测试](../../../tests/test_aw001_slice3c.py)、[夹具](../../../fixtures/aw001_slice3/3c/)：26 份跃迁/行为夹具、3 份注册表夹具与 1 份期望清单，共 29 个 Golden Fixture 测试方法；另有 16 个补充测试方法。
- 本次新增 **45 个测试方法通过**；全套 **448 个方法通过**，其中前序 403 项保留。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice3c.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice3c.py fixtures/aw001_slice3/3c/pt_cast_ballot.json fixtures/aw001_slice3/3c/pt_close_round.json
# 预期退出码 1：包含 schema 无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice3c.py fixtures/aw001_slice3/3c/ft_ballot_created_at.json fixtures/aw001_slice3/3c/pt_cast_ballot.json
```

前序 403 项基线在同一环境重跑（`tests` 不是包，故在 tests 目录设置 `PYTHONPATH`）：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_aw001_slice2c test_aw001_slice3a test_aw001_slice3b test_case_packet
```

原始证据：[403 项基线](validation-evidence/baseline-tests.txt)、[45 项 3C 测试](validation-evidence/slice3c-tests.txt)、[448 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 410 份继承文件不变，覆盖 0.1.0、Slice 1、2A/2B/2C、3A、3B 的实现、夹具、测试、规格、文档与既有证据。[本次摘要](validation-evidence/artifact-sha256.json) 记录 43 份本节新增或更新的文件，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

README、Slice 3 路线图与 3A/3B 证据未在本步改写：它们锁定在既有摘要中，3C 状态以本登记及本文件为准。

## 2. 对象与动作

实体沿用 `object_id / tenant_node_id / case_id / version_seq`。`before.domain_objects.operations` 是经未修改 2B 校验器验证的最小业务域；3C 新增 `decision_rounds`、`voter_eligibility`、`ballots`、`eligibility_audit_events`、`ballot_audit_events` 与本节 `effectiveness_envelopes`。所有动作不修改 `operations`。

| 动作 | 权能 | 目标 | APPLIED 写集合 | 关键拒绝 |
| --- | --- | --- | --- | --- |
| OPEN_DECISION_ROUND | OPEN_DECISION_ROUND | 案件 | 新建轮次 v1 与每名合格成员资格 v1 | 重复轮次 ID、选项重复、资格重复或未映射、截止时间已过 |
| CAST_BALLOT | CAST_BALLOT | 决策轮 | 追加选票、资格 v2 `CONSUMED`、身份侧与内容侧审计、CAST 信封 | 轮次非 OPEN、已截止、非合格成员、重复投票、选项非法、ballot_id 非 UUIDv4、nonce 复用、裸哈希指纹、请求摘要为裸哈希 |
| CLOSE_DECISION_ROUND | CLOSE_DECISION_ROUND | 决策轮 | 轮次 v2 关闭、聚合计数与关闭信封 | 重复关闭、截止时间未到、耗权数与有效票数不一致 |

允许写集合分别为 `CREATE_ROUND_AND_ELIGIBILITY`、`APPEND_BALLOT_AND_CONSUME_ELIGIBILITY`、`CLOSE_ROUND_AND_TALLY`；未声明对象必须保持不变。

## 3. 决策轮与资格

- 轮次保存选项集（代码唯一、至少两项）、合格成员 ID 名单、截止时间、状态 `OPEN/CLOSED` 与版本。开轮创建每个合格成员的资格记录（v1 `UNUSED`），资格 ID 由轮次与成员确定性派生。
- 关闭要求轮次为 OPEN 且评估时点不早于截止时间；关闭时由选票重算每个选项的计数、有效票数、合格人数与耗权数，写入轮次 v2 与关闭信封。耗权数必须等于有效票数。
- 已关闭轮次不得再次关闭，也不得再投；轮次历史版本不覆盖。所有动作不修改 2B 业务域。

## 4. 秘密选票的物理隔离

- Ballot 物理字段只有 `ballot_id / round_id / option_code / fingerprint`。不得出现创建者、principal、member、精确创建时间、命令/尝试/回执标识、插入序号或其他可对齐身份侧的字段。
- `ballot_id` 必须是安全随机源生成的 UUIDv4；自增序号、时间有序 UUIDv7 及其他形式被 Schema 拒绝。离线只能检查格式，不能证明随机源质量。
- 受控指纹为 `{key_version, nonce, hmac_sha256}`：`hmac_sha256` 必须是 `hmac-sha256:<64 hex>`；`nonce` 至少 32 位十六进制（≥128 位）且在轮内不复用；与低熵选项的裸 SHA-256（`sha256(canonical{round_id, option_code})`）相等的指纹被拒绝。密钥本身不进入仓库或夹具，离线无法验证 HMAC 真实性。
- CAST 的命令记录不允许保存裸命令摘要：`request_sha256` 必须是 `hmac-sha256:` 形式且不得等于命令的普通摘要。离线只验证形式与非相等，真实带密钥校验需要运行时密钥管理。
- 内容侧选票审计只含 `round_id / ballot_id / fingerprint`，不含任何身份或命令关联；身份侧资格审计只含 `round_id / eligibility_id / member_id / principal_id / command_id / receipt_id`，不含 ballot_id、选项或指纹。

## 5. 同事务耗权与幂等响应

- 一次 CAST 同时完成：写入选票、资格 v1 `UNUSED → CONSUMED` v2、身份侧 `BALLOT_CAST` 审计、内容侧选票审计、CAST 信封（仅绑定轮次与资格对象）。拒绝时业务域逐值不变，资格保持 `UNUSED`，不产生选票或审计。
- 业务结果与幂等响应只允许 `{round_id, eligibility_status: "BALLOT_CAST"}`；`applied_versions` 必须为空，不得包含 ballot_id、选项、内容摘要或受控存储定位符。普通命令审计事件同样不写入选票标识。
- 重复投票、资格未知、轮次关闭、选项非法、nonce 复用与裸哈希指纹都稳定拒绝；同一命令同键重试不会生成第二张有效票（离线只验证结构，不执行真实重试）。
- 关闭后的聚合计数只从选票重算，不依赖身份侧累加；普通审计角色通过聚合结果无法定位单张选票。

## 6. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`：**一份正确的拒绝记录仍为 `is_valid=true`**。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`；选票、资格、双侧审计、聚合与结果绑定逐项重算一致 |
| 正确 REJECTED | `is_valid=true`；业务域逐值不变，资格未被耗用，拒绝码与诊断一致 |
| 错误声明 APPLIED、拒绝码不符、拒绝偷改业务、APPLIED 后域被改 | `is_valid=false`，确定性层级、错误码与路径 |
| 结构无效、选票夹带身份/时间字段、序号式 ballot_id、普通结果泄露 ballot_id、审计写入选票、裸请求摘要 | `is_valid=false` |

诊断顺序：结构 → 访问/目标/版本/依赖 → 命令与回执绑定（含 CAST 请求摘要形式）→ 权能与生效范围 → 轮次状态/截止/资格/选项 → 选票 ID 与指纹 → 关闭聚合。

## 7. 已运行覆盖

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 开轮、资格创建、关闭与聚合重算 | 4 份 APPLIED 夹具与关闭计数断言 | PASSED_OFFLINE |
| 选票物理字段封闭（无身份、无时间、无序号） | Schema 与隔离断言 | PASSED_OFFLINE |
| UUIDv4 随机 ID、序号/时间有序 ID 拒绝 | 夹具与 schema 拒绝对照 | PASSED_OFFLINE |
| HMAC 指纹结构、nonce 唯一、裸哈希拒绝 | 指纹夹具与拒绝夹具 | PASSED_OFFLINE |
| CAST 同事务写入选票与耗权、失败不丢资格 | 原子性与拒绝域不变断言 | PASSED_OFFLINE |
| 幂等结果只含安全字段、审计不含选票标识 | 结果/审计隔离断言 | PASSED_OFFLINE |
| 双侧审计不交叉、命令请求摘要不裸哈希 | 隔离夹具与补充断言 | PASSED_OFFLINE |
| 轮次状态、截止、资格、选项、重复投票拒绝 | 10 份正确拒绝夹具 | PASSED_OFFLINE |
| 前序 403 项回归、原文件摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

## 8. 构造期修正与局限

构造 3C 检查器与夹具时修正了：夹具命令/信封/回执 ID 未按参与者唯一导致重复对象、关闭夹具第二票误投同选项、重复投票夹具先用同一 ballot_id 触发了对象冲突而非重复投票、关闭后投递夹具的期望版本不匹配。构造期原始失败输出未保留，只有最终运行证据；不得据此声称经历过独立评审。

诚实边界：离线契约**不能**证明随机源质量、HMAC 真实性、密钥管理、数据库访问控制、系统列（如 `xmin`）隔离、日志/APM/导出路径的实际过滤或身份与内容的不可关联。它只固定"允许出现什么字段、哪些跨侧引用被禁止、拒绝场景与聚合派生"。RC 5.2 明确：Batch 1 只承诺普通参与者与普通审计角色不能通过产品接口/导出直接连接身份与选票；不对抗数据库超级用户、应用串谋、底层事务/流量/时间相关性分析，也不提供密码学不可关联或抗胁迫保证。

## 9. 迁移与兼容边界

| 来源 | 3C 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain` 业务域 | 原样嵌套，先经未修改的 2B 校验器 | 不从静态快照推断真实并发或数据库隔离 |
| 2A 保护令、2C 申诉/集体、3A 参与/发言、3B 计量 | 不激活、不修改其文件 | 3C 不实现表决实质性阻断、申诉或参与门控联动 |
| 2B/3A/3B 命令与审计结构 | 沿用同义结构；3C 定义专用结果与审计字段 | 不向旧 Schema 添加新动作或字段 |
| README、Slice 3 路线图、3A/3B 证据 | 本步不改写 | 3C 入口更新留待独立对齐提交 |

摘要沿用既有本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS；选票内容与指纹只作为合成数据，不对应真实投票。

## 10. 未覆盖与下一步

- **AW-004 完整契约未实现**：真实选民名册与资格归并、去重/弃权/平票/法定人数、投票期限与重开、计票审计、秘密选票的数据库 DDL、访问控制视图、密钥轮换与保留期均未实现。
- 随机源质量、HMAC 计算与密钥管理、APM/日志/导出过滤、`xmin` 等系统列与备份边界、数据库超级用户风险均未验证。
- 与 3A 参与门控、3B 计量、保护令轮次覆盖的联动留给后续切片；3C 只提供独立轮次/选票/审计契约。
- **Slice 4：UNRUN**。标记、血缘、分支、AI 草稿、恢复、模板等契约尚未实现。
- PostgreSQL 并发、故障注入与持久性 `DEFERRED_TO_PG_RUNTIME`；**SPEC_FROZEN 未达成**。
