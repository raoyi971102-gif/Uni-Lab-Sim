# 统一会话宿主与发布边界

`SessionHost` 消费调用方已经构造并确认 reset 的 `CoupledSession`。它不创建世界、第二时钟、推进线程或物料记录。OS Claim 宿主可经设备包的依赖注入入口取得同一宿主；顶层 `simulation` 扩展不属于此实现。

## 生命周期与节拍

所有 `resume/pause/step/poll/set_rate/stop` 在会话所有者线程执行。`request_stop` 和 `health` 沿用会话已有的外部线程接口；在途后端未返回时它们仍可达。停止仅阻止后继调度，未知 pending 不清除；物理停稳和物料恢复不能由此推断。

`resume` 从当前墙钟重新等待一个 `wall_delay()`；`pause` 清除墙钟期限但不修改逻辑 token。`step` 仅在暂停态推进一个基础 tick。事件循环调用 `poll`，到期至多推进一帧，确认及来源交付完成后才设置下一墙钟期限。迟到不追赶；`last_timing` 记录当前 tick、lateness、实际执行墙钟及目标周期。1x 只指目标节拍，不能保证硬实时。

宿主必须显式给出允许倍率。每次推进都复核 session 当前倍率，不能通过直接修改 session 绕过限制。S06 状态工厂公开 `assembly.host`，仅准入 1x；原驱动墙钟 timeout 可能先于逻辑请求 deadline，到时宿主须取消原请求，而非再派发或回滚。

## 来源与时间

控制扫描读同一旧输入快照，输出按注册顺序封存；解析、规范及物理来源按声明顺序执行，只有全部必需来源确认合格才提交下一 token。扫描、确认、逻辑提交和采样交付有独立 trace 事件。来源缺失或单位/版本/身份错误保留 pending，不能重放已执行来源。

同一耦合量只能有一个声明权威，声明门不替代包内量值验证。实际参考泵仅积分请求进度；规范容器在整笔请求边界经原 OS 接口提交一次库存。它不声称连续流体质量积分。采样 captured 时间不随延迟交付刷新。

状态观察 profile 在暂停时不主动查询数据库；外部提交留在规范存储，仅下一显式 step/poll 读取当前版本。未知原提交时间保留 `source_time=None`，查询时间只说明观察，不能把外部旧效果当成本请求新鲜成功。

## S06 集成增量

工厂必须接收显式 `request_timeout_ticks`，从原会话构造边界计时，`tick >= deadline` 在接受/执行之前判定。暂停不消耗预算，同一确认 token 最多迁移一次请求阶段。已完成的请求不因晚到 deadline 被重开，效果 TTL 仍独立生效。

取消/超时关闭本请求受理，Done/Ready/Allow置False。已经执行、观测到规范效果、或凭据读取未知时保留 `reconciliation_required` 与原 request/效果证据；不改原库存、不伪造事务回滚。提交后丢失回执锁存未知，禁止再派发。

此工厂增量依赖尚未发布的 #29 信号工厂及 #38 规范效果接口；属于明确的外部集成证据，公共 PLC 包没有这些进口或 `/tmp` 路径依赖。公共包仅使用已发布 #27 `unilabos_sim_contracts` 与调用方注入的规范验证器。

## 复现

Python 3.11、已安装 pytest/Pydantic 环境中，从公共仓执行：

```bash
python tools/verify_clock.py --core-source /absolute/Uni-Lab-OS/packages/simulation-contracts/src --core-commit a4bb31015b0b7150b6107f14fe7ae616e6f5cd28 --output /absolute/new-clock-result.json
```

入口核对合同 Git 提交，预哈希源码并核对实际 import，拒绝覆盖旧回执。CPU用例与既有真实GPU/规范DB回执分别归因；此命令不安装依赖、不启动GPU，不代表全ROS、实体PLC或SZLab全机制验收。
