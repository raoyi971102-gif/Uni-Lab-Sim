# 模型构造前的来源校验

正式入口 `plc_sim.model_loading.load_module/load_symbol` 接受可选 `source: ModelSource`。
该对象包含 OS 轻量合同包的 `SourceLock`、选定安装根目录，以及宿主来源采集器报告的
完整提交和 dirty 差异摘要。它们必须来自可信构建/来源记录，不能由待加载模型自报。

入口先检查 `find_spec` 的文件，再检查实际模块 `__file__`，并通过公共 `verify_source`
核对文件、必需包资源及锁。旧 editable 路径、缺资源、内容变化或提交不一致均在返回
模型构造器前拒绝；不搜索历史目录，不改变模型类型身份。来源失败统一为 `ModelLoadError`。
调用方应在完成全部组合协商和实际能力探针后，才实例化模型或开放动作。

此接口不创建模型/连接/世界，不持有待清理的运行资源。验证失败无新增模型资源；
已经由调用方建立的资源仍由原宿主 finally/context manager 收尾。
Python 的模块解析可能导入父包，因此包初始化应遵循既有无硬件连接规范。
这不是对任意不可信 Python 模块的沙箱或内存字节码证明；运行目录须保持静止。

未传 `source` 的历史调用保持原行为；因此这次变更只交付正式入口上的显式准入接缝，
不宣称所有历史启动器已强制启用。后续宿主装配必须显式传入选定来源合同。
能力协商与真实探针也尚未自动接入此入口，旧参考 World 不获得 OS 规范库存资格。

正式 `unilab-plc-sim` wheel 依赖 `unilabos-sim-contracts==0.1.0`，四个步进类型由后者唯一维护，
仍通过 `plc_sim.cosimulation` 重导出。PLC 整包保持 Python `>=3.11,<3.12`；
轻量合同 wheel 在 Python 3.12 可安装，不意味着整个 PLC 包的 Python 支持范围已扩大。
