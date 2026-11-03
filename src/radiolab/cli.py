"""RadioLab 命令行。默认只生成 WAV，不访问发射硬件。"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from . import __version__
from .afsk import SAMPLE_RATE
from .audio import SoundDeviceAudio, audio_level, list_audio_devices, list_serial_ports, load_wav
from .config import load_config, validate_on_air
from .history import HistoryService
from .service import ReceiveService, SendService


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radiolab", description="RadioLab：AFSK 文本离线闭环与受控发射")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="列出音频输入/输出及串口")
    check = sub.add_parser("check", help="检查配置，不打开设备")
    check.add_argument("--config", required=True)
    text = sub.add_parser("text")
    text_sub = text.add_subparsers(dest="action", required=True)
    enc = text_sub.add_parser("encode", help="生成 AFSK WAV")
    enc.add_argument("message")
    enc.add_argument("-o", "--output", required=True)
    enc.add_argument("--sequence", type=int, default=0)
    dec = text_sub.add_parser("decode", help="从 WAV 恢复文本并检查 CRC")
    dec.add_argument("wav")
    send = text_sub.add_parser("send", help="发送文本；默认仅生成 WAV")
    send.add_argument("message")
    send.add_argument("-o", "--output", default="runtime/text-send.wav")
    send.add_argument("--sequence", type=int, default=0)
    send.add_argument("--config")
    send.add_argument("--on-air", action="store_true")
    send.add_argument("--dry-run", action="store_true")
    receive = sub.add_parser("receive")
    receive_sub = receive.add_subparsers(dest="action", required=True)
    record = receive_sub.add_parser("record", help="独立录音到 WAV")
    record.add_argument("--seconds", type=float, required=True)
    record.add_argument("-o", "--output", required=True)
    record.add_argument("--config")
    record.add_argument("--input-device", help="覆盖配置中的 RX 输入设备")
    local_audio = sub.add_parser("audio", help="本地 WAV 操作，不控制 PTT")
    local_audio_sub = local_audio.add_subparsers(dest="action", required=True)
    replay = local_audio_sub.add_parser("replay", help="向明确选择的本地扬声器回放 WAV")
    replay.add_argument("wav")
    replay.add_argument("--output-device", required=True, help="本地扬声器或耳机设备编号/唯一名称")
    replay.add_argument("--gain", type=float, default=0.05)
    replay.add_argument("--confirm-local-output", action="store_true",
                        help="确认输出未连接电台或 VOX；本命令不控制 PTT")
    history = sub.add_parser("history")
    history_sub = history.add_subparsers(dest="action", required=True)
    history_list = history_sub.add_parser("list")
    history_list.add_argument("--database", help="读取 V0.2 SQLite 历史；默认仍读 JSONL")
    history_list.add_argument("--mode", help="SQLite 中按模式筛选")
    history_list.add_argument("--search", help="SQLite 中搜索文本内容")
    history_migrate = history_sub.add_parser("migrate", help="将 JSONL 历史复制到 SQLite，保留原文件")
    history_migrate.add_argument("--source", default="runtime/history.jsonl")
    history_migrate.add_argument("-o", "--database", required=True)
    history_export = history_sub.add_parser("export", help="导出 SQLite 历史和现存媒体到 ZIP")
    history_export.add_argument("--database", required=True, help="待导出的 SQLite 历史")
    history_export.add_argument("-o", "--output", required=True, help="新的 .zip 路径；包含文本和媒体")
    sstv = sub.add_parser("sstv")
    sstv_sub = sstv.add_subparsers(dest="action", required=True)
    for action in ("encode", "send"):
        item = sstv_sub.add_parser(action, help="生成 Robot36 WAV" if action == "encode" else "受控发送 Robot36")
        item.add_argument("image")
        item.add_argument("-o", "--output", required=True)
        if action == "send":
            item.add_argument("--config")
            item.add_argument("--on-air", action="store_true")
            item.add_argument("--dry-run", action="store_true")
    sstv_decode = sstv_sub.add_parser("decode", help="使用内置算法解码 WAV")
    sstv_decode.add_argument("wav")
    sstv_decode.add_argument("-o", "--output", required=True)
    sstv_decode.add_argument("--report", help="解码日志路径；默认与图片同目录，后缀 .decode.log")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "devices":
            devices, error = list_audio_devices()
            if error:
                print(error)
            for direction, field in (("输入", "inputs"), ("输出", "outputs")):
                found = [d for d in devices if getattr(d, field) > 0]
                print(f"{direction}音频设备：")
                for d in found:
                    print(f"  {d.index}: {d.name} ({getattr(d, field)} 声道，默认 {d.default_sample_rate:g} sample/s)")
                if not found:
                    print("  无可用设备")
            ports, error = list_serial_ports()
            if error:
                print(error)
            print("串口：")
            for name, description in ports:
                print(f"  {name}: {description}")
            if not ports:
                print("  无可用串口")
        elif args.command == "check":
            config = load_config(args.config)
            display = lambda value: "未选择" if value is None or str(value).strip() == "" else value
            print(f"TX={display(config.tx_device)} RX={display(config.rx_device)} PTT={display(config.ptt_port)}")
            ready = True
            backend = SoundDeviceAudio()
            for label, device, validate in (("TX 输出", config.tx_device, backend.validate_output),
                                            ("RX 输入", config.rx_device, backend.validate_input)):
                if device is None or str(device).strip() == "":
                    print(f"{label}：未选择；运行 radiolab devices 后填写配置")
                    ready = False
                    continue
                try:
                    validate(device, SAMPLE_RATE)
                    print(f"{label}：支持 {SAMPLE_RATE} sample/s")
                except ValueError as exc:
                    print(f"{label}：{exc}")
                    ready = False
            ports, port_error = list_serial_ports()
            if port_error:
                print(port_error)
                ready = False
            elif not isinstance(config.ptt_port, str) or config.ptt_port.casefold() not in {name.casefold() for name, _ in ports}:
                print(f"PTT 串口 {config.ptt_port or '未选择'}：当前设备列表中不存在；运行 radiolab devices")
                ready = False
            else:
                print(f"PTT 串口 {config.ptt_port}：已枚举；未打开端口")
            try:
                validate_on_air(config)
                print("配置字段满足发射门槛；射频台架仍需人工验证")
            except ValueError as exc:
                print(f"真实发射未就绪：{exc}")
                ready = False
            return 0 if ready else 2
        elif args.command == "text" and args.action in ("encode", "send"):
            config = load_config(args.config) if args.action == "send" else load_config(None)
            on_air = args.action == "send" and args.on_air and not args.dry_run
            path = SendService().send_text(args.message, config=config, sequence=args.sequence,
                                           wav_path=args.output, on_air=on_air)
            from soundfile import info
            duration = info(path).duration
            print(f"已生成 {path}：{duration:.2f} s、{SAMPLE_RATE} sample/s、16-bit PCM WAV" +
                  ("；已执行受控发送" if on_air else "；未发射"))
        elif args.command == "text" and args.action == "decode":
            result = ReceiveService().decode_file(args.wav)
            print(f"CRC OK，序号 {result.frame.sequence}，起始样本 {result.start_sample}：{result.frame.text}")
        elif args.command == "receive" and args.action == "record":
            from dataclasses import replace
            config = load_config(args.config)
            if args.input_device:
                config = replace(config, rx_device=args.input_device)
            path = ReceiveService().record(args.seconds, args.output, config=config)
            level = audio_level(load_wav(path))
            status = ("电平偏低，检查 RX 输入路径" if level.low_level else
                      "峰值接近削波，降低输入增益" if level.clipped_fraction > 0.001 else
                      "有可测音频电平；仍需解码确认内容")
            print(f"录音已保存：{path}；峰值 {level.peak_dbfs:.1f} dBFS，RMS {level.rms_dbfs:.1f} dBFS；{status}")
        elif args.command == "audio" and args.action == "replay":
            if not args.confirm_local_output:
                raise ValueError("本地回放需 --confirm-local-output；先确认所选输出未连接电台或 VOX")
            duration = ReceiveService().replay_file(args.wav, output_device=args.output_device, gain=args.gain)
            print(f"本地回放完成：{args.wav}；{duration:.2f} s；未操作 PTT")
        elif args.command == "history" and args.action == "list":
            if args.database:
                from .storage import SQLiteHistoryService
                rows = SQLiteHistoryService(args.database).list_recent(mode=args.mode, search=args.search)
            else:
                if args.mode or args.search:
                    raise ValueError("--mode/--search 需要同时指定 --database")
                rows = HistoryService().list_recent()
            for row in rows:
                local_time = row.local_time or datetime.fromisoformat(row.time).astimezone(timezone(timedelta(hours=8))).isoformat()
                decoder = f"{row.decoder_name} {row.decoder_version or 'unknown'} " if row.decoder_name else ""
                print(f"{local_time} {row.run_id or '-'} {row.mode}/{row.operation} {'成功' if row.success else '失败'} "
                      f"{row.wav_path or '-'} {'[文件缺失]' if row.wav_path and not Path(row.wav_path).is_file() else ''} "
                      f"{row.image_path or ''} {'[图片缺失]' if row.image_path and not Path(row.image_path).is_file() else ''} "
                      f"{decoder}{row.reason or row.text or ''}")
        elif args.command == "history" and args.action == "migrate":
            from .storage import SQLiteHistoryService
            count = SQLiteHistoryService(args.database).migrate_jsonl(args.source)
            print(f"已导入 {count} 条到 {args.database}；JSONL 原文件保留：{args.source}")
        elif args.command == "history" and args.action == "export":
            from .storage import SQLiteHistoryService
            summary = SQLiteHistoryService(args.database).export_bundle(args.output)
            print(f"已导出 {summary.entries} 条到 {summary.path}；媒体 {summary.copied_files} 个，"
                  f"缺失 {summary.missing_files} 个，复制 {summary.total_bytes} B。"
                  "ZIP 含文本和原始路径，请妥善保管")
        elif args.command == "sstv":
            if args.action == "decode":
                from .sstv_rx import SSTVReceiveService
                result = SSTVReceiveService(history=HistoryService()).decode_file(
                    args.wav, args.output, report_path=args.report)
                print(f"已由 RadioLab 内置算法解码 Robot36 / VIS 0x08："
                      f"{result.image_path}；日志：{result.report_path}")
                return 0
            from .sstv import render_robot36_pcm
            config = load_config(args.config) if args.action == "send" else load_config(None)
            on_air = args.action == "send" and args.on_air and not args.dry_run
            if on_air:
                try:
                    validate_on_air(config)
                    pcm = render_robot36_pcm(args.image, on_air=True)
                except ValueError as exc:
                    from .history import make_entry
                    HistoryService().append(make_entry("sstv", "send", device=config.tx_device,
                                                       wav_path=args.output, image_path=args.image,
                                                       success=False, reason=str(exc)))
                    raise
            else:
                pcm = render_robot36_pcm(args.image)
            path = SendService().send_pcm(pcm, config=config, wav_path=args.output, mode="sstv",
                                          image_path=args.image, on_air=on_air)
            print(f"Robot36 WAV：{path}；{len(pcm) / SAMPLE_RATE:.1f} s、48000 sample/s、16-bit PCM；" + ("已执行受控发送" if on_air else "未发射"))
        return 0
    except KeyboardInterrupt:
        print("用户中断；若发送已启动，程序已尝试释放 PTT", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
