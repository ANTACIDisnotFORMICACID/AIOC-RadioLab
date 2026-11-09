# 参与开发

欢迎通过 GitHub Issues 报告缺陷、提出改进，或提交 Pull Request。请提供系统、Python 版本、复现步骤和预期/实际结果；上传日志或历史记录前请删除个人信息和设备标识。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[gui,devices,sstv,test]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

改动收发逻辑时，请分别说明离线软件验证、音频设备验证和 AIOC/PTT/RF 验证的范围。没有实机证据时不要把模拟测试描述为射频成功。真实发射需要操作者按 [硬件流程](docs/hardware.md)完成无辐射台架核对。

本仓库保留源码、公开示例和可复现文档；不要提交 `runtime/`、录音、个人配置、虚拟环境或 Windows 构建输出。
