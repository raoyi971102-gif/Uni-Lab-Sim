# 确认时钟的停止、健康与扫描轨迹

`CoupledSession` 仍是唯一推进权威；本增量不创建推进或健康线程。
宿主在世界所有者线程调用 reset/pause/resume/step/stop。其他线程只调用
`health(max_pending_s=..., link_ok=...)` 和 `request_stop()`。

`health` 使用独立短锁读取上次发布的 token/pending/state，墙钟只计算在途调用期限。
连接状态由真正拥有端点的宿主提供。暂停不停止墙钟健康检查，健康检查也不推进
逻辑时刻或延迟队列。后端正在阻塞时，健康和停止请求仍可达。
后端、控制器或轨迹出口异常会锁存 execution_failed；stop 不会清除此错误。
只有完整显式 reset 成功才清除。reset 是原有世界重置，不是物料 UNKNOWN 恢复许可。

`request_stop` 只请求封闭后继扫描。已经发出的步若收到有效确认，仍登记其实际完成；
随后进入 stopped。若确认失败则保留 pending 与 fault，不补造停稳证明。
`stop` 封闭调度且不清 pending；这两个接口都不能据此释放物料 Claim 或宣称执行器安全。
底层调用是否可中断、物理停止确认及 OS 恢复由原宿主合同负责。

构造器可注入 `trace_sink(record)`。它同步接收深拷贝记录，宿主负责持久化，协调器
不积攒无界内存日志。sink 必须避免长时间阻塞；其失败阻断后继推进。
`plc.scan-trace/1` 输出 reset 请求/确认，随后每步依次输出：

1. scan_sealed：上一已确认 token、下一 requested token、全控制器共用的输入快照、
   按登记顺序的 controller_outputs、合并并保持的命令、需要采样的通道。
2. step_confirmed：通过身份/时长/采样检查的原始帧。
3. step_committed：实际提交的 token、命令、交付值及仍等待交付的队列。

采集 token 与 delivered_tick 分别保留。登记顺序是当前确定执行/提交顺序；
控制器各收到相同快照的独立深拷贝。缺 step_confirmed 的前缀不能当作后端完成，
缺 step_committed 的确认也不能当作协调提交成功。轨迹写入失败后不自动重放。

当前 SourceBarrierPort 已接入解析、规范状态和混合来源的独立确认屏障，
全部必需来源通过后才提交逻辑前进；规范库存凭据保留自身证据，不能包装成 PhysicsFrame。
原 S06 驱动的墙钟等待已按选定源码审计，所选 Claim 工厂通过同一 SessionHost 接入生命周期，
仅允许 1x 目标节拍，不保证硬实时；完整 ROS、全 SZLab 机制及实体 PLC 保持资格未授予。
