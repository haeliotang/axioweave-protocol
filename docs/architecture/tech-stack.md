# AxioWeave 技术栈决策：Rust 优先

日期：2026-11-05。状态：**已决策**，自 AW-002（运行时实现）起适用。本文取代 [开发方案 §8](../development-plan.md#8-技术架构与数据模型) 中"推荐 TypeScript Web 应用"的表述，并保留其"模块化单体 + PostgreSQL + 受控文件存储 + 极简表格界面"原则。本文不改写 0.1.0、Slice 1 与 Slice 2A/2B/2C 的任何机器契约、夹具、测试或运行证据。

## 1. 决策与边界

- AxioWeave 的主技术栈切换为 **Rust 优先**：Cargo Workspace 模块化单体，PostgreSQL 作为事务与并发事实源，首版为极简表单/表格界面。不引入微服务拆分、gRPC 网格或动态脚本引擎。
- 现有 Python 离线校验器、Schema、夹具与 **615 项离线测试**作为跨语言规范基准完整保留，不因换栈重写；Rust 端口的第一道验收是与其逐用例、逐摘要等价。
- Rust 的类型系统只约束应用层代码路径：类型状态（typestate）门控可让绕过四元组、保护令与职责分离的**应用层写路径无法通过编译**，但不能防止数据库超级管理员或运维直接篡改底层表，也不能替代真实 PostgreSQL 事务、锁序与故障注入证据。所有运行时不变量仍须 AW-002 的数据库证据。
- 本决策不打断 AW-001 节奏：Slices 3–4 继续用现有 JSON Schema + Python 检查器完成并冻结；进入 AW-002 后才初始化 Cargo 工程。

## 2. 选型矩阵

| 架构层次 | Batch 1 基线（AW-002） | 选择理由 | 禁用 / 延后 |
| --- | --- | --- | --- |
| 应用架构形态 | Cargo Workspace 模块化单体（`rustc` stable，Edition 2024） | 编译期强制模块可见性边界；单二进制部署简单；与"模块化单体"原则一致 | 微服务拆分、gRPC 网格、动态脚本引擎 |
| Web 与 API 网关 | `axum` + `tower` + `tower-http` | 无宏魔法；基于提取器从服务端认证上下文生成主体，阻断客户端伪造 `authenticated_principal_id` | 未鉴权的内部状态机函数不对外暴露 |
| 数据库与事务内核 | PostgreSQL 16/17 候选 + `sqlx`（禁用重型 ORM） | `sqlx` 编译期 SQL 类型检查，同时允许手写精确的 `SELECT ... FOR UPDATE / FOR SHARE`，保证 0–4 级确定性锁序不被框架隐藏或重排 | Diesel / SeaORM 的自动关联懒加载与隐式事务 |
| 契约与序列化 | `serde` + `serde_json` + `jsonschema` | 服务端直接加载并校验现有 Draft 2020-12 Schema 与 Golden Fixtures，与 Python 检查器对齐 | 生产禁用非严格 JSON（NaN / Infinity） |
| 摘要与隐私（RC 5.2） | `sha2` + `hmac` + `rand`；秘密选票使用 `uuid` v4 | 低熵选票使用 HMAC + CSPRNG nonce；选票标识强制随机 v4，禁用含时间戳的 v7 | Batch 1 不引入公链、DID、签名或零知识证明（见 §9） |
| 时间与计量核算 | `chrono`（仅 UTC）+ 原生 `i64` / `u64` | 账本与资源池强制整数最小单位并配 `checked_add` / `checked_sub`；时间分桶强制 `DateTime<Utc>` 秒级对齐 | 账本与分桶边界禁用 `f32` / `f64` |
| 异步任务与 Outbox | `tokio` + PostgreSQL 事务型 Outbox | 同事务写入 `outbox_records`，后台 `tokio` 短事务轮询并按 Case `FOR SHARE` 重检放行；不引入 Redis/Kafka | 跨网络 I/O 期间持有数据库事务锁 |
| 前端视图层 | 方案 A（默认）：`askama` + `htmx`；方案 B：`ts-rs` + 既有 TypeScript 前端 | v0.7 要求固定表单与 HTML 表格；方案 A 让两名工程师聚焦 PostgreSQL 事务内核，方案 B 便于复用母公司前端设计系统 | 重型 Canvas 画布、复杂前端状态缓存与业务逻辑前置 |

## 3. 为什么是 sqlx 而不是 ORM

RC 契约对数据库提出三项硬要求，任何隐式 SQL 生成器都会削弱可核对性：

1. **两阶段幂等预登记**：先用独立短事务写入 `CommandRecord` / `CommandAttempt`（`IN_FLIGHT`），再开启主业务事务。
2. **0–4 级确定性锁序**：`CommandRecord → AuthorityRecord → Case / Commitment / ResourcePool → ProtectiveOrder → FulfillmentTicket`，同层按稳定 ID 排序。
3. **三路审计分离**：`APPLIED` 与业务更新同事务提交；`REJECTED` 走独立写事务且目标业务表零修改；COMMIT 响应丢失进入 `OUTCOME_UNKNOWN`，由核对器加锁判定。

`sqlx::PgPool` + 显式 `sqlx::Transaction<'_, Postgres>` 能把锁序与事务边界写成可审阅的语句；ORM 的隐式连接借用、懒加载与级联写入会破坏这一层透明性。SQLx 的编译期校验在 AW-002 启动时固定（可使用离线准备数据），不据此声称已通过运行时验证。

## 4. 类型状态门控（示意）

```rust
// 仅示意编译期约束，AW-002 实现时再定稿并测试
pub struct AuthenticatedPrincipal { /* 私有字段：客户端 JSON 无法构造 */ }

pub struct VerifiedEffectivenessToken<'tx> {
    pub tx: sqlx::Transaction<'tx, sqlx::Postgres>,
    pub receipt: AuthorizationCheckReceipt,
    _private: (),
}

/// 业务写入口按值消费该 Token：未完成四元组、保护令与 SoD 核验的调用无法编译
pub async fn apply_domain_mutation<'tx>(
    mut token: VerifiedEffectivenessToken<'tx>,
    mutation: DomainMutation,
) -> Result<CommittedOutcome, KernelFault> {
    execute_sql_mutation(&mut token.tx, &mutation).await?;
    insert_auth_receipt(&mut token.tx, &token.receipt).await?;
    insert_audit_event(&mut token.tx, &token.envelope, &mutation).await?;
    token.tx.commit().await.map_err(KernelFault::from_commit_error)?;
    Ok(CommittedOutcome::from(token.receipt))
}
```

约束目标：

- 认证主体与核验回执**不实现公开的客户端反序列化入口**，从类型上阻断夹带。
- 修改承诺、工单或保护令的写函数不接收裸 `&PgPool`，而接收受保护的 Token，避免漏检越权。
- `axio-ai` 在依赖图中不得引用数据库写接口或该 Token，只能返回草稿/摘要结构，从链接层面保证 AI 不触发任何 `EFFECTIVE` 动作。

## 5. Cargo Workspace 布局

```text
Cargo.toml                     # workspace 根；固定 rustc/edition/deps 版本策略
crates/
  axio-contract/               # 纯数据模型、枚举、错误码、摘要与离线语义纯函数；只读 I/O
  axio-db/                     # sqlx migrations、显式行锁查询、CommandRecord 预登记与核对器
  axio-kernel/                 # 状态机、typestate 门控、保护令求值、RC 2.4 SoD、三路审计协调
  axio-ai/                     # 可关闭的只读 AI 草稿；无数据库写依赖，继承标记过滤
  axio-server/                 # axum 路由、会话鉴权提取器、Outbox worker、HTML/API
specs/  fixtures/  tools/  tests/   # 既有 Python 基线保持原样
```

依赖方向固定为 `axio-server → axio-kernel → axio-db → axio-contract`；`axio-ai` 只能依赖 `axio-contract` 与只读视图，不得反向依赖写路径。

## 6. 测试 harness

- `#[sqlx::test]`：每个集成测试在真实 PostgreSQL 中创建隔离数据库/模式并自动清理，互不污染。
- `tokio::sync::Barrier`：在"读取前置状态之后、获取 Case `FOR UPDATE` 之前"对齐两个协程，确定性复现并发竞态（如交接瞬间授权被撤销、空保护令集合并发插入），不靠 `sleep` 猜调度。
- Failpoints（`fail` crate 或事务钩子）：注入"业务更新后、审计写入前失败"与"数据库已提交但响应丢失"，实证检验回滚与 `OUTCOME_UNKNOWN` 核对器。
- **跨语言等价测试**：Rust `axio-contract` 直接读取 `fixtures/` 下全部已冻结夹具，与 Python 检查器的 `is_valid / validation_layer / error_code / failed_path` 逐项一致；任何不一致都视为 Rust 端口缺陷，而不是修改基准的理由。

## 7. 前端

- 默认方案 A：`askama` 编译期模板 + `htmx` + 极简 CSS（必要时 Tailwind），服务端渲染时完成权限过滤与脱敏占位符替换，浏览器拿不到未授权 JSON 字段。
- 方案 B：若母公司 inceptless 其他产品（如 `poieweave`）已有统一 TypeScript/React 设计系统，则采用 `ts-rs` 从 `axio-contract` 导出 `.d.ts`，前端只做表单与表格渲染，零业务逻辑。
- 二选一在 AW-002 启动前，依据现有前端资产检查结果固定；未定前不假设 API 形态。首版不做拓扑画布与复杂状态同步。

## 8. 版本与固定项

- `rustc` stable，Edition 2024；workspace 依赖版本在 AW-002 启动时统一固定并登记（`Cargo.lock` 提交入库）。
- PostgreSQL 大版本：本文档与既有研究分别引用 PostgreSQL 16/17（工程候选）与 PostgreSQL 18 文档（锁/事务/时间语义核对来源）。实际大版本须在 AW-002 启动时按部署环境固定，并在测试证据中登记；不据文档版本声称与真实实例一致。
- 当前开发机已具备 `rustc`/`cargo`（1.94.0）与 Node/pnpm，但**未安装 PostgreSQL**；真实事务、并发与故障证据须在可用的 PostgreSQL 实例上采集，不能以单元测试或 mock 代替。

## 9. 摘要与规范化兼容

现有证据链使用本地 JSON 规范化：键排序、紧凑分隔符、UTF-8，摘要为未签名 SHA-256。Rust 端口**第一步必须逐字节复现该规则**，否则现有夹具摘要与跨语言断言失效。

RFC 8785（JCS）只作为 Batch 2 的候选升级：升级须单独立项，保留双摘要观测期并重新登记证据，不在 AW-002 中静默替换。Batch 1 不引入公链、DID、外部签名或零知识证明；`ed25519-dalek`、`arkworks`、`halo2` 等仅在 Batch 2 需求与契约明确后评估。

## 10. 与既有证据的关系

Slice 2C（以及 2A/2B）的 `baseline-sha256.json` / `artifact-sha256.json` 是**其提交时点**的完整性记录。本文档、开发方案与 README 的后续更新不重算这些摘要；复核某份证据时应在对应提交（例如 `0a988ee`）而非当前 HEAD 上校验。这与既有 2B 登记"不改写旧 README 以保留摘要"的做法一致：证据是快照，不是持续有效的全局哈希表。

## 11. 推进顺序

**Phase 1（已完成）：契约先行，不打断 AW-001。** AW-001 Slices 1–4 已全部交付，离线全套 615 个方法通过；冻结候选清单已生成，独立评审仍待进行。
- 继续用现有 JSON Schema + Python 检查器完成 Slice 3、Slice 4 的离线契约与 Golden Fixtures，并完成 SPEC_FROZEN 所需的跨切片盘点与独立审阅。
- 不在本阶段建 Cargo 工程，不重写既有 0.1.0 / Slice 1 / 2A / 2B / 2C 资产。

**Phase 2（已启动）：AW-002 Rust 工程。** 已初始化 Cargo workspace 与 `axio-contract` 冻结清单校验；PostgreSQL 运行时目前为临时本地 harness，Rust/sqlx 实现待补。
1. 初始化 `Cargo.toml` 与 `axio-contract`：用 Rust 跑通全部已冻结离线夹具，登记与 Python 基准的逐项等价结果。
2. 实现 `axio-db` 与 `axio-kernel`：真实 PostgreSQL 上的 sqlx 事务内核、0–4 级锁序、CommandRecord 预登记/核对、三路审计。
3. 按 `#[sqlx::test]`、Barrier、failpoints 搭建 FT/PT-01..20 的并发与故障集成测试，逐项登记证据；在此之前不声称 IMPL_CONFORMANT。
4. `axio-server` 与前端方案接入；`axio-ai` 始终保持只读与可关闭。

Rust 端口通过跨语言等价与 PostgreSQL 运行时证据之前，Python 检查器仍是契约的权威基准；两者结论冲突时以已审阅的契约与夹具为准，并先修正端口。

## 12. 未决项

- PostgreSQL 实际大版本（16/17/18）与托管环境尚未固定。
- 前端方案 A/B 待母公司前端资产检查后决定。
- JCS 升级、Batch 2 密码学扩展与签名体系均未立项。
- Rust 运行时已实现并签署于 `8ba8cfe`（`GRANTED_SCOPED`）；当前 HEAD 的后续加固不在该签名内。
