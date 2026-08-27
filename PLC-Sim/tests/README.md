# 测试说明

在 `PLC-Sim` 目录运行自动化测试：

```bash
python -m pytest
```

`test_*.py` 是可由 pytest 自动执行的单元/集成测试。`integration/` 中的脚本
需要独立进程或已启动的服务，因此按需手动运行：

```bash
python tests/integration/remote_attach_check.py
```
