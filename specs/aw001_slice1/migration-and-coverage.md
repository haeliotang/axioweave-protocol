# AW-001 Slice 1：迁移、覆盖与运行证据

日期：2026-11-05。契约版本：`0.2.0-rc1-slice1`。状态：**Slice 1 离线契约已验证（限本文登记的合成输入与断言）**。不是 SPEC_FROZEN、IMPL_CONFORMANT 或 PILOT_AUTHORIZED；没有真实身份、数据库事务、法律适用审查或外部执行能力。

## 1. 交付范围与复现

- [跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref` 校验四段式对象；四动作 payload 逐层封闭。先检查公共结构，再检查完整根 Schema，不跳过 before/after 或服务端上下文。
- [固定动作注册表](fixed-action-registry.json)：从本地固定路径只读加载；独立代码常量核对映射、必需校验器和适用锁层级。测试可检查篡改副本，业务入口不接收注册表覆盖参数。
- [离线检查器](../../tools/validate_aw001_slice1.py)、[测试](../../tests/test_aw001_slice1.py)、[夹具目录](../../fixtures/aw001_slice1/)：17 份跃迁夹具、3 份注册表夹具，共 20 项核心断言。注册表夹具位于 registry 子目录，属于加载器输入，不冒充四段式跃迁。
- [环境](validation-evidence/environment.json)：本次 Python 3.14.0、jsonschema 4.23.0；未增加依赖。其他 Python 版本未在本次运行中验证。沿用原脚本的严格 JSON 读取和 UTC 秒级日期校验，不修改旧脚本。

在仓库根目录复现：

```sh
# 仅旧基线：9 个测试方法
python3 -m unittest discover -s tests -p test_case_packet.py -v
# 新切片：20 核心断言方法 + 10 补充测试方法
python3 -m unittest discover -s tests -p test_aw001_slice1.py -v
# 合计 39 个测试方法（部分方法包含多个 subTest）
python3 -m unittest discover -s tests -v
# 合法跃迁返回 0；不会写数据库或执行制度动作
python3 tools/validate_aw001_slice1.py fixtures/aw001_slice1/pt01a.json fixtures/aw001_slice1/pt01b.json fixtures/aw001_slice1/pt02a.json fixtures/aw001_slice1/pt02b.json
# 混合输入逐一报告，预期退出码 1；负例不会因属于测试集而变成 VALID
python3 tools/validate_aw001_slice1.py fixtures/aw001_slice1/ft01b.json fixtures/aw001_slice1/pt01a.json
```

实际已执行：旧基线命令、全套命令、上列合法/混合 CLI 以及原 0.1.0 样例 CLI。原始 [基线输出](validation-evidence/baseline-tests.txt)、[最终全套输出](validation-evidence/all-tests.txt)、[CLI 命令及输出](validation-evidence/cli-results.json) 均已保存；新切片也在开发过程中单独运行。20 项逐项结果见 [golden-results.json](validation-evidence/golden-results.json)，其中 `assertion_passed=true` 表示实际错误码/层级符合预期，负例的 `actual.is_valid` 仍为 false。

[基线文件摘要](validation-evidence/baseline-sha256.json) 包含旧 Schema、脚本、测试、两份样例、依赖及 v0.7 主方案；完成后逐文件重算一致。[实现与夹具摘要](validation-evidence/artifact-sha256.json) 记录本次实际被验证的版本。摘要不提供数字签名或独立来源认证；修改文件后应重新验证并更新证据。

## 2. 三层结果与实际断言

注册表在业务结构/语义校验前自检。`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 是三种诊断来源，不表示注册表必须最后检查。严格 JSON 读取或文件读取失败另报 JSON_INPUT。路径采用 JSON Pointer，不解析英文报错文本。

| 覆盖项 | 已运行证据 | 当前状态 |
| --- | --- | --- |
| 低门槛提交，缺 source_ref/metric_spec/四元组仍可受理 | PT-01a；拒绝受理政策及身份/目标错配补充测试 | PASSED_OFFLINE |
| 确认/拒绝输入与结果四元组，证据非空，客户端注入拒绝 | PT-01b、PT-02a、FT-01a/b/c、FT-16b-1；嵌套字段与拒绝结果缺救济补充测试 | PASSED_OFFLINE |
| 当前回执状态与命令、attempt、身份、授权快照、范围、证据版本绑定 | FT-01d/e、FT-02a；回执/授权/范围/引用变体测试 | PASSED_OFFLINE |
| 拒绝决定有效而约束非有效，不能代填立场 | PT-02a、FT-02b/c；既存立场保留测试 | PASSED_OFFLINE |
| 本人立场登记不改历史信封或实际资格；允许表达退出请求 | PT-02b、FT-02d/e/f；退出子项/案件意愿对照 | PASSED_OFFLINE |
| 四动作映射、必需校验器与锁序静态声明防降级 | PT-16a、FT-16a、FT-16b-2/3；错权能、效力标志、核验机制变体 | PASSED_OFFLINE（仅静态声明） |
| 反例只修改声明条件、CLI 逐输入结果及退出码、严格 JSON/日期 | 10 项补充测试中的相关方法；20 份夹具差异核对 | PASSED_OFFLINE |
| 原 0.1.0 回归及文件不变 | 旧 9 项方法仍通过；基线摘要一致 | PASSED，限原测试范围 |

核心 20 项逐项的期望和实际结果以 golden-results.json 为准。另有 10 个补充测试方法，含参数化子断言；39 是 unittest 方法数，不是全部行为覆盖数，不表示 FT/PT-01..20 二十个服务端测试族已全部通过。

开发中修正两处缺口：回执 attempt_id 增加与 server_context.attempt_id 的一致性检查；注册表负例原先因构造时共享列表同时删除两个动作的校验器，现已隔离为单动作变化。后者的失败复现保留在 [修正前夹具差异检查](validation-evidence/fixture-isolation-before-fix.txt)，最终通过输出单独保存，没有将失败记录当作当前通过证据。

## 3. 数据表达与有限验证边界

命令、上下文和候选 after 都是合成静态资料。检查器不产生可信认证信息，也不把 VERIFIED_ACTIVE 当作实际服务端证明。当前验证覆盖声明之间的一致性；签发源真实性、现实胁迫和篡改整个输入的攻击不因此被解决。

- `server_context.attempt_id` 绑定本次模拟尝试；提交与立场登记无审查回执。审查结果信封中的回执必须与本次 generated_auth_receipt 一致。
- `review_decision_record.version_seq=1` 独立于约束版本。后续立场登记逐值保留旧决定，不用本次版本或当前授权有效期重写历史回执。
- `MetricSpecRef` 仅含 metric_id、version_seq、definition_ref。定量确认必须有此引用，且定义证据可解析；本切片不计算阈值、单位换算、UTC 时间分桶或置信边界。定性提交不需要指标。
- evidence_snapshots 只提供声明的 evidence_id/revision/content_sha256。检查引用一致性，不重算未提供的原始证据正文哈希，不判定证据真实或充足。
- scope_spec 限定本节点、案件、目标与时间范围；本切片不分配资金，因此不声称校验资源总额。救济对象检查结构和程序引用，不证明复核机构现实可用。
- `expressed_participation_intent` 是本人意愿；实际 effective_standing_status 只可随本切片保持不变。后台或制度角色改变资格的动作未实现。

## 4. 0.1.0 字段迁移对照

来源：[specs/case-packet.schema.json](../case-packet.schema.json)。0.1.0 是表决前快照，Slice 1 是四段式状态跃迁，不能从一个旧快照推断出未记录的命令、前态、认证回执或本人立场。**本次交付映射说明，不提供自动迁移器，也未证明任一真实历史案件已迁移。** 新 Golden Fixtures 为独立合成资料。

| 0.1.0 真实字段 | 新字段/处置 | 必须补录或不能推断的部分 |
| --- | --- | --- |
| schema_version、profile | 保留来源版本/配置说明；新上下文 contract_version=0.2.0-rc1-slice1 | 不能只替换版本字符串就成为新契约。 |
| source_node_id、community_id | 保留来源归属；受控导入时明确 tenant_node_id、case_id、acl_policy_ref | source_node_id 不是本地访问权或认证主体。 |
| case_id、case_version | 案件来源标识/版本保留；约束单独 object_id、version_seq | case_version 不能充当约束或决定记录版本。 |
| exported_at | 保留来源导出时间 | 不等于 server_context.checked_at；需新的模拟核验时点或真实运行时记录。 |
| mode、data_classification、execution_enabled | 新 server_context 保持 simulation、synthetic、false | 不升级 advisory/delegated 或真实执行。 |
| question、charter_ref、rules_ref | 保留案件上下文与来源；授权条款另作 mandate_clause_ref 及快照 clause_reference | 不将提问文本或章程存在直接视为审查授权。 |
| evidence[].evidence_id / revision / source_kind / summary | evidence_id/revision 保留；来源标记及 summary 在来源资料中保留；新证据引用需 content_sha256 | 有真实资料时明确摘要对象和字节编码后计算；不能从 ID 伪造摘要，新合成示例摘要不冒充旧资料验证结果。 |
| constraints[].constraint_id / description | 可映射 object_id / raw_statement_text，补充 claim_title | 同一旧文本不能自动生成 before→after 转换历史。 |
| constraints[].kind | participant_claim 可形成 PROPOSED_CLAIM 待审材料 | applicable_law、adopted_charter 保留来源；不自动映射 CASE_RATIFIED，本切片不做外部法适用审查。 |
| constraints[].status | asserted/contested 保留待审及争议说明；adopted/inapplicable 保留原快照处理状态 | 不凭 adopted 创造 EFFECTIVE 或 RATIFIED_FOR_CASE；原 status 不提供完整审查信封。 |
| constraints[].source_ref / disposition_ref | 保留旧 evidence_id/revision 来源关联；可为空的 disposition_ref 保持未知 | 新 EvidenceRef 还需 content_sha256；不能由 disposition_ref 非空推断四元组齐备。 |
| authority_records[].authority_id / actor_id / scope / powers / status / valid_from / valid_until / basis_ref | 保留为原模拟声明；新 authority_snapshot 要有主体、目标、条款、版本摘要及来源 | actor_id 不直接变成 authenticated_principal_id；assess_constraints / freeze_round 不等于 CERTIFY_CONSTRAINT 或 REJECT_SUBMISSION。 |
| inclusion_audit 全部字段 | 保留在来源案件资料，不在 Slice 1 状态对象中压缩或伪装已执行代表性检查 | 此切片无代表性算法与表决冻结操作。 |
| gates[]、review_status | 保留原评估历史，ready_for_round 仅是旧快照状态 | 不自动生成 review_decision_record、命令或生效信封。 |
| 旧格式没有的 claimant_principal_id、立场、资格、命令和回执 | 来源不能推断者标记待补，不导入成可执行声明 | 缺件资料留在迁移工作区；不能向严格跃迁 Schema 填虚假已核验身份来通过。 |

## 5. 后续运行时验证与准入状态

| 未覆盖项 | 状态 | 后续所需证据 |
| --- | --- | --- |
| 真实 Session 身份、租户 ACL、member_id 关联及实例级职责分离 | DEFERRED_TO_PG_RUNTIME | AW-002 认证/授权路径与真实角色测试；静态主体相等不是实名证明。 |
| 授权源真实有效、实时撤销、外部源不可达与 TOCTOU | DEFERRED_TO_PG_RUNTIME | 持适用行锁后重检；外部状态新鲜度另受既定能力边界限制。 |
| 保护令实时集合、空集并发插入与到期效力 | DEFERRED_TO_PG_RUNTIME | 同 Case 互斥锁和独立连接交错测试。 |
| CommandRecord 第 0 级锁、generation 围栏、COMMIT 结果未知核对 | DEFERRED_TO_PG_RUNTIME | 真实 PostgreSQL 屏障、连接故障和迟到 worker 测试；本次 attempt 只是关联一致性。 |
| 业务、回执、审计原子提交及三路失败日志 | DEFERRED_TO_PG_RUNTIME | 事务故障注入、回滚/未知提交与独立故障通道证据。 |
| 真实处理器不可绕过、固定注册表部署防篡改 | DEFERRED_TO_PG_RUNTIME | 服务端写入入口、部署权限与受控配置测试。 |

格式可表达与有限合成用例通过，不代替组织授权或完整安全审查。历史争议回放、其他切片、完整 AW-001 交付及冻结登记均未由本次完成。

离线错误码属于本检查器诊断：ERR_CLIENT_FORBIDDEN_FIELD / ERR_UNREGISTERED_ACTION_ID 对应输入边界；ERR_INCOMPLETE_FOUR_TUPLE / ERR_UNAUTHORIZED_POWER_FOR_ACTION 对应 RC 的完整性/权能要求；ERR_RECEIPT_NOT_ACTIVE / ERR_MANDATE_SNAPSHOT_MISMATCH 是静态声明错误，不冒充线上授权已撤销的事实。版本、历史决定、立场、资格、引用与注册表错误分别报告；服务端 API 错误映射仍须在 AW-002 明确，不由本切片自动新增生产协议。
