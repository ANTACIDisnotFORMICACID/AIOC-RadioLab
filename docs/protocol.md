# Packet v0.1

| 字段 | 字节数 | 内容 |
|---|---:|---|
| 前导码 | 32 | `0x55` 重复 |
| 同步字 | 2 | `D3 91` |
| version/type/sequence/length | 各 1 | version=1；type=1 文本、2 测试字节；sequence=0..255；length=0..32 |
| payload | 0..32 | 文本类型为 UTF-8 |
| CRC-16 | 2 | CCITT-FALSE，覆盖 version 到 payload |

字节顺序和比特顺序均为 MSB-first，CRC 多项式 `0x1021`、初始 `0xFFFF`、不反射、输出异或 0。标准向量 `123456789` 的 CRC 为 `0x29B1`。任何头字段、长度或 CRC 错误均不能显示为成功接收。

AFSK：48,000 sample/s、600 bit/s、80 sample/bit、1=1200 Hz、0=2200 Hz，连续相位，固定峰值 0.25。音频链为“帧 → AFSK PCM → AIOC USB 音频 → 手台 FM”。协议不兼容 AX.25/APRS，也没有 ACK 或重传。
