"""Packet v0.1。多字节字段大端，比特 MSB-first。"""
from dataclasses import dataclass

PREAMBLE = b"\x55" * 32
SYNC = b"\xd3\x91"
VERSION = 1
MAX_PAYLOAD = 32
MIN_FRAME_SIZE = 32 + 2 + 4 + 2


class PacketError(ValueError):
    """无效帧或参数。"""


class CRCError(PacketError):
    """CRC 校验失败。"""


@dataclass(frozen=True)
class PacketFrame:
    version: int
    kind: int
    sequence: int
    payload: bytes

    @property
    def text(self) -> str:
        if self.kind != 1:
            raise PacketError("此帧不是文本类型")
        try:
            return self.payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PacketError("文本 payload 不是有效 UTF-8") from exc


def crc16(data: bytes) -> int:
    crc = 0xffff
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xffff
    return crc


def encode_frame(payload: bytes, sequence: int = 0, kind: int = 1) -> bytes:
    if not isinstance(payload, bytes):
        raise PacketError("payload 必须是 bytes")
    if len(payload) > MAX_PAYLOAD:
        raise PacketError("payload 超过 32 字节")
    if not isinstance(sequence, int) or not 0 <= sequence <= 255:
        raise PacketError("sequence 必须为 0..255")
    if not isinstance(kind, int) or kind not in (1, 2):
        raise PacketError("type 仅支持 1=文本、2=测试字节")
    if kind == 1:
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PacketError("文本 payload 不是有效 UTF-8") from exc
    body = bytes((VERSION, kind, sequence, len(payload))) + payload
    return PREAMBLE + SYNC + body + crc16(body).to_bytes(2, "big")


def encode_text(text: str, sequence: int = 0) -> bytes:
    return encode_frame(text.encode("utf-8"), sequence)


def decode_frame(frame: bytes) -> PacketFrame:
    if len(frame) < MIN_FRAME_SIZE:
        raise PacketError("帧被截断")
    if frame[:32] != PREAMBLE or frame[32:34] != SYNC:
        raise PacketError("前导码或同步字无效")
    version, kind, sequence, length = frame[34:38]
    if version != VERSION:
        raise PacketError(f"不支持版本 {version}")
    if kind not in (1, 2):
        raise PacketError(f"不支持 type {kind}")
    if length > MAX_PAYLOAD or len(frame) != MIN_FRAME_SIZE + length:
        raise PacketError("长度字段与帧大小不符或超过 32 字节")
    body = frame[34:-2]
    expected = int.from_bytes(frame[-2:], "big")
    if crc16(body) != expected:
        raise CRCError("CRC-16 校验失败，不能作为成功接收")
    result = PacketFrame(version, kind, sequence, frame[38:-2])
    if kind == 1:
        _ = result.text
    return result
