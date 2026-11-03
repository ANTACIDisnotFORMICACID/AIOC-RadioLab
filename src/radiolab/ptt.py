"""PTT 控制。串口只在明确发射时打开；任何打开动作仍需先台架验证。"""
from threading import Event, Lock
from typing import Protocol


class PTTBackend(Protocol):
    def key(self) -> None: ...
    def release(self) -> None: ...


class FakePTT:
    def __init__(self) -> None:
        self.keyed = False
        self.events: list[str] = []

    def key(self) -> None:
        self.keyed = True
        self.events.append("key")

    def release(self) -> None:
        self.keyed = False
        self.events.append("release")


class AIOCSerialPTT:
    """仅支持已验证的 1.2+ 固件；打开前预设 DTR/RTS 低。"""
    def __init__(self, port: str, serial_factory=None) -> None:
        if not port:
            raise ValueError("PTT 串口不能为空")
        self.port = port
        self._serial = None
        self._serial_factory = serial_factory

    @staticmethod
    def _deassert_and_close(connection) -> None:
        """每条控制线和关闭动作都尝试一次，单步异常不跳过其余动作。"""
        failures: list[str] = []
        for line in ("dtr", "rts"):
            try:
                setattr(connection, line, False)
            except BaseException as exc:
                failures.append(f"{line}={exc}")
        try:
            connection.close()
        except BaseException as exc:
            failures.append(f"close={exc}")
        if failures:
            raise RuntimeError("PTT 释放不完整；请检查手台是否仍在发射：" + "; ".join(failures))

    def key(self) -> None:
        if self._serial_factory is None:
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError("缺少 pyserial；安装 radiolab[devices]") from exc
            factory = serial.Serial
        else:
            factory = self._serial_factory
        if self._serial is not None:
            raise RuntimeError("PTT 已处于按下状态")
        connection = factory(port=None, timeout=1, write_timeout=1)
        try:
            connection.dtr = False
            connection.rts = False
            connection.port = self.port
            connection.open()
            # AIOC 1.2+：DTR=1 且 RTS=0 按下 PTT。
            connection.rts = False
            connection.dtr = True
            self._serial = connection
        except BaseException as original:
            try:
                self._deassert_and_close(connection)
            except RuntimeError as cleanup:
                # 清理未完成时保留连接，供发送事务的 finally 再尝试释放。
                self._serial = connection
                raise RuntimeError(f"PTT 初始化失败：{original}；{cleanup}") from original
            raise

    def release(self) -> None:
        connection = self._serial
        if connection is not None:
            self._deassert_and_close(connection)
            self._serial = None


TX_LOCK = Lock()
# 释放控制线失败后，直到操作者检查设备并重启进程前拒绝新的发射。
TX_FAULT = Event()
