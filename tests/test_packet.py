import pytest

from radiolab.packet import CRCError, PacketError, crc16, decode_frame, encode_frame, encode_text


def test_crc_vector():
    assert crc16(b"123456789") == 0x29B1


@pytest.mark.parametrize("size", [0, 1, 32])
def test_payload_sizes(size):
    payload = bytes(range(size))
    assert decode_frame(encode_frame(payload, 255, kind=2)).payload == payload


def test_limits_and_utf8():
    with pytest.raises(PacketError):
        encode_frame(b"x" * 33, 0)
    with pytest.raises(PacketError):
        encode_text("中" * 11)
    with pytest.raises(PacketError):
        encode_frame(b"x", 256)


@pytest.mark.parametrize("position", [0, 32, 34, 35, 37, 38, -1])
def test_tamper_rejected(position):
    frame = bytearray(encode_frame(b"OK", 3))
    frame[position] ^= 0x80 if position != 37 else 1
    with pytest.raises(PacketError):
        decode_frame(bytes(frame))


def test_crc_type():
    frame = bytearray(encode_frame(b"OK", 3))
    frame[-1] ^= 1
    with pytest.raises(CRCError):
        decode_frame(bytes(frame))
