# GUI 极简操作

## 启动

在仓库根目录打开 PowerShell，首次运行先安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[gui,devices,sstv]'
.\.venv\Scripts\radiolab-gui.exe
```

安装生成的 `.venv\Scripts\radiolab-gui.exe` 是依赖该虚拟环境的启动器。若使用已构建的 Windows 目录包，打开其中的 `radiolab-gui\radiolab-gui.exe`；整个 `radiolab-gui` 文件夹须保持完整。启动不会打开声卡、串口或 PTT。

## 窗口操作

1. **离线文本**：在“文本”填消息（最多 32 字节 UTF-8），按“生成文本 WAV”。生成后在下方历史表选中该条记录，按“解码文本 WAV”；成功时显示文本与 CRC 通过。两步均不发射。
2. **离线 SSTV**：填写图片路径，按“生成图像 WAV”；选中 SSTV WAV 记录，按“解码选中 WAV”。两步均使用内置 Robot36 算法，无需安装 Open-SSTV。模式与互验范围见 [SSTV 说明](sstv.md)。
3. **录音/接收**：按“刷新设备”，明确选择 **RX 输入**，设置最长时长，按“开始录音”或“自动监听文本”；可按对应停止按钮提前结束。录音会保存 WAV；历史表选中录音后可手动解码。RX 手台须接独立输入或由电脑麦克风采集其扬声器。
4. **查看记录**：在下方历史表用模式、搜索框和“查询”筛选；选中图片记录可看预览。“最近文件”和“导出历史 ZIP”分别用于查找与备份已有记录。

“发射文本/发射图片”默认锁定。只有完成 [无辐射 AIOC/PTT 台架核对与合法操作确认](hardware.md)后，才选择 TX 输出与 PTT 串口，用“编辑配置”保存并核对配置，在本次勾选确认后审阅发射摘要。当前真实 AIOC/双手台 RF 链路**尚未实测**；不要把离线 WAV 或 GUI 测试当作射频成功。
