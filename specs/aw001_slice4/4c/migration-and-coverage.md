# AW-001 Slice 4C：迁移、覆盖与运行证据

日期：2026-11-05。版本：`0.5.0-rc1-slice4c`。状态：**指定合成用例的离线契约已验证**。模式固定为 `simulation / synthetic / execution_enabled=false`，不代表真实文件操作、真实通知投递、真实恢复、真实部署或 PostgreSQL 运行时。SPEC_FROZEN、IMPL_CONFORMANT、PILOT_AUTHORIZED 均未由本次达成。

## 1. 交付与实际运行

- [独立跃迁 Schema](transition-fixture.schema.json)：Draft 2020-12，根 `$ref`；四段式夹具、七动作封闭输入、输出/暂存/撤回/通知/恢复/模板/部署对象。
- [固定注册表](fixed-action-registry.json)：七动作、权能、七项强制检查标识、允许写集合与 0–4 级锁序声明；生产入口不接受自定义注册表参数。
- [检查器](../../../tools/validate_aw001_slice4c.py)、[测试](../../../tests/test_aw001_slice4c.py)、[夹具](../../../fixtures/aw001_slice4/4c/)：31 份跃迁/行为夹具、3 份注册表夹具与 1 份期望清单，共 34 个 Golden Fixture 测试方法；另有 22 个补充测试方法。
- 本次新增 **56 个测试方法通过**；全套 **615 个方法通过**，其中前序 559 项保留。

复现命令（在仓库根目录）：

```sh
python3 -m unittest discover -s tests -p test_aw001_slice4c.py -v
python3 -m unittest discover -s tests -v
python3 tools/validate_aw001_slice4c.py fixtures/aw001_slice4/4c/pt_publish_local_output.json fixtures/aw001_slice4/4c/pt_dispatch_notification.json
# 预期退出码 1：包含 schema 无效记录；每份资料仍单独报告
python3 tools/validate_aw001_slice4c.py fixtures/aw001_slice4/4c/ft_schema_release_now.json fixtures/aw001_slice4/4c/pt_withdraw_output.json
```

前序 559 项基线在同一环境重跑（`tests` 不是包，故在 tests 目录设置 `PYTHONPATH`）：

```sh
cd tests && PYTHONPATH=.. python3 -m unittest -v test_aw001_slice1 test_aw001_slice2 test_aw001_slice2b test_aw001_slice2c test_aw001_slice3a test_aw001_slice3b test_aw001_slice3c test_aw001_slice4a test_aw001_slice4b test_case_packet
```

原始证据：[559 项基线](validation-evidence/baseline-tests.txt)、[56 项 4C 测试](validation-evidence/slice4c-tests.txt)、[615 项全套](validation-evidence/all-tests.txt)、[逐夹具结果](validation-evidence/golden-results.json)、[CLI 输出](validation-evidence/cli-results.json)、[环境及命令](validation-evidence/environment.json)。实际环境 Python 3.14.0 / jsonschema 4.23.0；无新依赖。

[原文件摘要](validation-evidence/baseline-sha256.json) 记录 550 份继承文件不变，覆盖此前全部切片的实现、夹具、测试、规格、文档与既有证据。[本次摘要](validation-evidence/artifact-sha256.json) 记录 48 份本节新增或更新的文件，不含该摘要文件自身。SHA-256 只检查文件一致性，不提供签名或来源认证。

README、既有路线图与先前切片证据未在本步改写：它们锁定在既有摘要中，4C 状态以本登记及本文件为准。

## 2. 对象与动作

实体沿用 `object_id / tenant_node_id / case_id / version_seq`。`before.domain_objects.operations` 是经未修改 2B 校验器验证的最小业务域；4C 新增 `output_stagings`、`output_withdrawals`、`recovery_manifests`、`template_installations`、`deployment_records`，并读取预置的 `output_artifacts`、`notification_tasks` 与 `case_access_policies`。所有动作不修改 `operations`。

| 动作 | 权能 | 目标 | APPLIED 写集合 | 关键拒绝 |
| --- | --- | --- | --- | --- |
| STAGE_OUTPUT_RELEASE | STAGE_OUTPUT_RELEASE | 输出 | 新建暂存记录 v1（私有暂存文件） | 输出已撤回、所用策略版本与当前不符、重复暂存 ID |
| PUBLISH_LOCAL_OUTPUT | PUBLISH_LOCAL_OUTPUT | 暂存记录 | 暂存状态 PUBLISHED 与不可变文件引用 | 非 STAGED、输出已撤回、暂存策略版本已过期 |
| WITHDRAW_OUTPUT | WITHDRAW_OUTPUT | 输出 | 输出 WITHDRAWN、递增策略版本、撤回记录与复核待办 | 输出已撤回、重复撤回 ID |
| DISPATCH_NOTIFICATION | DISPATCH_NOTIFICATION | 通知任务 | 通知 DISPATCHED 一次并记录派发时点 | 已派发、输出已撤回、记录策略版本已过期 |
| RECORD_RECOVERY_MANIFEST | RECORD_RECOVERY_MANIFEST | 案件 | 新建恢复清单 v1（四维分开、隔离标记） | 隔离状态与效力声明不一致、只读标志不一致 |
| INSTALL_TEMPLATE_PACKAGE | INSTALL_TEMPLATE_PACKAGE | 案件 | 新建模板安装记录 v1（SIMULATION、本地待核验） | 契约版本不支持、重复安装 ID |
| RECORD_DEPLOYMENT_MANIFEST | RECORD_DEPLOYMENT_MANIFEST | 案件 | 新建部署登记 v1（含兼容性/动作开关） | 操作者未映射、证据引用缺失、重复部署 ID |

允许写集合分别为 `CREATE_OUTPUT_STAGING`、`PUBLISH_STAGING_LOCAL`、`WITHDRAW_ARTIFACT_AND_BUMP_POLICY`、`DISPATCH_NOTIFICATION_ONCE`、`CREATE_RECOVERY_MANIFEST`、`CREATE_TEMPLATE_INSTALLATION`、`CREATE_DEPLOYMENT_RECORD`；未声明对象与既有切片数据必须保持不变。

## 3. 撤回与本地输出放行

- 暂存记录只保存私有暂存文件引用、渲染所用策略版本与 STAGED 状态；此时不公开任何内容。
- 发布必须在短事务内重新核对：暂存仍为 STAGED、输出仍为 ACTIVE、暂存所用策略版本等于当前案件访问策略版本。任一不符即拒绝；版本变化时不发布，需重新暂存。发布只登记不可变文件引用与放行时点，不移动文件、不发起对象存储请求。
- `PUBLISHED_LOCAL` 仅表示受控文件可用，不授予永久下载权；后续下载须重新核查最新许可（本切片只固定登记语义，不实现下载路径）。
- 撤回同事务设置输出 `WITHDRAWN`、递增案件访问策略版本、登记撤回记录与受控复核待办；已暂存/已发布文件与在途外部副本的局限明确保留。撤回不撤销决定、承诺、轮次或保护令，也不自动暂停全案。
- 通知任务登记记录策略版本与实际载荷模式；派发前重核当前策略版本与输出状态。撤回或版本变化后禁止派发旧全文，重复派发被拒绝，不产生第二次制度效果。

## 4. 恢复清单

- 清单四维分开：资料完整性（完整/遮盖/缺失/损坏）、摘要与来源认证（匹配/不匹配/无可信基准）、当前程序效力（只读保留/需再授权/未知）、外部控制重绑定（不需要/需要/未核验）。
- 来源认证固定 `NOT_SIGNED_BATCH1_BASELINE`；本切片不实现签名验证器，也不把摘要匹配解释为来源可信。
- 摘要不匹配、任一记录损坏或记录摘要不匹配时必须隔离：清单 `quarantine_required=true`、全部记录只读、程序效力为再授权或未知。不得声明"已回滚"或恢复执行；Schema 封闭，客户端不能提交执行开关。
- 摘要匹配且无损坏时只能声明只读保留；外部控制重绑定与本地授权、保护令、前置条件仍是独立检查，不因重绑定成功而跳过。
- 恢复清单不删除历史决定，不伪造无损回滚；无法安全分离内容时限制访问，并把缺失项如实登记。

## 5. 模板与部署版本交换

- 模板安装记录包版本、支持的契约版本、字段映射、来源引用、本地采纳凭据与待本地核验项；模式固定 `SIMULATION`，安装不创建真实主体、授权、资金或生效保护令。支持的契约版本必须包含当前契约，否则拒绝。
- 部署登记记录构建、Schema、动作注册表、模板版本、SPEC_FROZEN 引用、测试报告、目标环境、迁移计划、备份恢复验证、操作者、批准与回退构建引用；全部引用必须在证据快照中可解析。
- 兼容时动作开关为 true；不兼容迁移只把新动作开关置为 false 并保留历史决定，不执行删除或静默回退。Schema 封闭，部署载荷不能改写章程、已冻结依据或试点范围，也不能携带"无损回滚"声明。
- 部署与回退登记本身不产生制度授权；软件更新与数据迁移仍须独立批准，真实执行不在本切片范围。

## 6. 四结果分支与诊断分层

`REGISTRY_LOADER / JSON_SCHEMA / OFFLINE_SEMANTIC` 表示契约检查来源；输入解析错误另为 `JSON_INPUT`。业务拒绝属于 `execution_outcome.command_rejection`，不是检查器的 `is_valid`：**一份正确的拒绝记录仍为 `is_valid=true`**。

| 记录 | 夹具判定与业务含义 |
| --- | --- |
| 正确 APPLIED | `is_valid=true`；暂存/发布/撤回/通知/恢复/模板/部署与结果绑定逐项重算一致 |
| 正确 REJECTED | `is_valid=true`；业务域逐值不变，拒绝码与诊断一致 |
| 错误声明 APPLIED、拒绝码不符、拒绝偷改业务、APPLIED 后域被改 | `is_valid=false`，确定性层级、错误码与路径 |
| 结构无效、客户端夹带放行/执行/章程替换/授权字段、注册表降级 | `is_valid=false` |

诊断顺序：结构 → 访问/目标/版本/依赖 → 命令与回执绑定 → 权能与生效范围 → 输出状态与策略版本 → 通知状态 → 恢复隔离一致性 → 模板契约 → 部署引用与操作者。

## 7. 已运行覆盖

| 覆盖内容 | 证据 | 当前状态 |
| --- | --- | --- |
| 暂存、发布、策略版本过期与撤回后发布拒绝 | 3 份 APPLIED/拒绝夹具 | PASSED_OFFLINE |
| 撤回递增策略版本、输出脱敏状态、复核待办 | 撤回夹具与补充断言 | PASSED_OFFLINE |
| 撤回不暂停、不改决定/承诺/保护令 | 域不变断言 | PASSED_OFFLINE |
| 通知派发策略重核、撤回后拒绝旧全文、重复派发拒绝 | 3 份通知夹具 | PASSED_OFFLINE |
| 恢复清单四维分开、隔离与只读、无执行恢复入口 | 2 份恢复夹具与 schema 拒绝 | PASSED_OFFLINE |
| 模板 SIMULATION、契约版本支持、安装不授权 | 模板夹具与域不变断言 | PASSED_OFFLINE |
| 部署引用完整、兼容/不兼容动作开关、不能改章程 | 2 份部署夹具与 schema 拒绝 | PASSED_OFFLINE |
| 前序 559 项回归、原文件摘要不变 | baseline/all-tests 与基线摘要 | PASSED，限已有测试范围 |

## 8. 构造期修正与局限

构造 4C 时修正了：目标类型识别顺序（暂存/通知对象含 artifact_id 被误判为输出）、暂存创建与发布信封未分开登记、部署证据缺失夹具应归为检查器检测而非正确拒绝。构造期原始失败输出未保留，只有最终运行证据；不得据此声称经历过独立评审。

已知验证边界：只覆盖合成夹具与静态离线语义；不执行真实文件暂存/发布/删除、对象存储请求、通知投递、恢复导入或部署编排；不验证缓存与权限传播的实际并发窗口。`PUBLISHED_LOCAL` 不授予下载权，本切片也未实现下载再鉴权路径。`is_valid=true` 不产生访问许可、制度效力或恢复结论。

## 9. 迁移与兼容边界

| 来源 | 4C 处置 | 不可推断的内容 |
| --- | --- | --- |
| 2B `B_Domain` 业务域 | 原样嵌套，先经未修改的 2B 校验器 | 不从静态快照推断真实并发或执行 |
| 4A 标记/派生、4B 分支/AI | 不激活、不修改其文件 | 输出撤回不替代标记变更、分支冻结或 AI 会话撤销 |
| 2A/2C/3A/3B/3C | 保持逐值不变 | 撤回不产生保护令、不暂停计票、不改决定 |
| README、Slice 4 路线图、先前证据 | 本步不改写 | 4C 入口更新留待独立对齐提交 |

摘要沿用既有本地 JSON 排序键/紧凑分隔符/UTF-8 规则，不是跨语言 JCS；文件引用与摘要为合成数据。

## 10. 未覆盖与下一步

- 真实文件暂存/发布/下载、对象存储、通知投递、恢复导入、备份恢复验证、部署与回退执行均未实现。
- 撤回与缓存/权限传播的并发窗口、在途外部副本、PUBLISHED_LOCAL 之后的下载再鉴权未实现。
- 签名验证器、不可信来源隔离、模板市场、多环境部署平台与真实试点授权均不在本批范围。
- **Slice 4 三个子切片已全部交付**；下一步为 AW-002 PostgreSQL 运行时与 SPEC_FROZEN 独立收口。PostgreSQL 并发、故障注入与持久性 `DEFERRED_TO_PG_RUNTIME`；**SPEC_FROZEN 未达成**。
