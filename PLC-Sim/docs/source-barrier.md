# 声明来源的确认屏障

SourceBarrierPort 消费明确 SourceBinding 顺序，不创建时钟、线程或协议服务。
每个来源只被调用一次；全部必需来源返回合格证据后才返回 BarrierFrame，供既有
CoupledSession 提交逻辑步。BarrierFrame 不是 PhysicsFrame 的子类。它记录
source_evidence，不能把其中每项都解释成实际物理或新数据库提交确认。

来源分别验证：physics 使用原 PhysicsFrame 的 token、时长和样本；analytic 使用
#27 ContractFrame 的请求、来源摘要、序号、质量与采集时刻，并核对样本量值；
resource_state 保留原规范查询字典及 source_time=None，再调用包内
verify_canonical(evidence, samples) 读取当前规范版本并核对领域量值。
包内验证器不得只返回常量 True。量纲/信号映射由包声明，units 等原字段完整保留。

规范来源必须显式选择 resource_progress：

- effect：依据已核验新 effect_id 推进一次阶段。重复 effect 不再推进。保留
  freshness_lower_bound_ns、receipt_received_at_ns、observed_at_ns 和原 attribution；
  不将保守下界或回执到达时间声明成实际提交时刻。期限从原下界计算。
- observation：实际当前查询可以读到相同版本，但必须有独立 query_id、原 observed_at_ns
  和当前版本核验。stage_advanced=false，不把查询本身宣称为新物料效果，亦不修改
  旧 Action 效果的 TTL。供 idle、参数等待、状态扫描等阶段使用。

所有规范样本的 acquired_tick * dt 必须等于其原查询 observed_at_ns。
不支持整数边界的来源应拒绝，不能取整或重标到当前 tick。
外部查询、缺原时间的旧凭据及暂停期间缓冲策略仍由所选 profile 明确声明。
当前实现只同步消费宿主在现有扫描内调用的来源，不自动接纳异步外部事实。

quantity_authorities 只接受每个耦合量唯一声明，channels 只接受唯一采样来源。
它们是装配校验，不证明声明已覆盖所有实际模型副作用。
前一个来源已执行、后一个来源失败时，CoupledSession 保留 pending 并禁止后继步骤。
不回滚已经发生的物料变化，也不自动重发某个来源。恢复仍需对应规范恢复合同。

已验证：原 process_dynamics.exponential_increment 的 direct 解析推进，及固定 #29
状态结果适配器、真实 OS SQLite、隔离 loopback OPC 上的规范效果/当前查询和解析混合。
live126 已验证真实 Isaac 合成单关节的纯物理与物理/解析混合各 60 步；另归因 #29 的
live128 已验证实际协议入口的物理及混合参考组合。两者保留原始邮箱/轨迹及源码审计，
均不代表 SZLab 设备机制的物理资格。所选 S06 原 Claim、状态工厂与原驱动已通过同一
SessionHost 验证暂停/恢复/单步/停止，以及逻辑 deadline、取消和回执丢失；该包增量依赖
尚未发布的 #29/#38 接口，不能混作公共 PLC 包依赖。完整 ROS、安装包和全 SZLab 验收仍未完成。

每个 SourceBinding.channels 都必须由 units 明确声明工程单位，构造时冻结副本。解析 Quantity 的原单位、规范 evidence.units、物理 SourceResult.units 均在逻辑提交前核对。解析 bool/string 使用显式 SourceResult.units='1'，同时保留原类型；禁止把 BOOL 转成 Quantity 数字来绕过信号类型。

## 显式缺测

解析来源可以在有效确认帧中返回已选择端口的 `None`，但必须同时提供
`Sample(value=None, valid=False)` 和与绑定一致的显式单位。必需来源、完整端口集合、
请求身份和采集边界仍须通过校验；无效来源确认与遗漏端口不能作为缺测推进。

信号存储仅允许 sensor_input 的 unknown 质量承载 None，不执行数值转换，
不保留上一次 Good 的工程值。协议端数值占位可能沿用旧 raw，但质量立即变为
BadNoData，不能将占位解释成新观测。恢复有效采样后才恢复 Good。
