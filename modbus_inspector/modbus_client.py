"""
Modbus 通信封装模块
=====================
支持 TCP / RTU 两种模式，带重试与超时保护。
"""
import logging
import time
from typing import Any, Dict, List, Optional, Union

from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException
from pymodbus.pdu import ExceptionResponse

logger = logging.getLogger("modbus_inspector.modbus_client")


class ModbusDeviceClient:
    """Modbus 设备客户端封装。

    自动适配 TCP / RTU 模式，提供寄存器读取与重试机制。
    支持上下文管理器（with 语句）。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.device_cfg = config["device"]
        self.retry_count = config["collection"]["retry_count"]
        self.retry_delay = config["collection"]["retry_delay"]

        self._client: Optional[Union[ModbusTcpClient, ModbusSerialClient]] = None
        self._connected = False

    # ---------- 连接管理 ----------

    def connect(self) -> bool:
        """建立与设备的连接。

        Returns:
            True 连接成功 / False 连接失败。
        """
        if self._connected:
            return True

        mode = self.device_cfg.get("mode", "tcp")
        timeout = self.device_cfg.get("timeout", 5)

        try:
            if mode == "tcp":
                self._client = ModbusTcpClient(
                    host=self.device_cfg["host"],
                    port=self.device_cfg.get("port", 502),
                    timeout=timeout,
                )
            elif mode == "rtu":
                self._client = ModbusSerialClient(
                    port=self.device_cfg["serial_port"],
                    baudrate=self.device_cfg.get("baudrate", 9600),
                    bytesize=self.device_cfg.get("bytesize", 8),
                    parity=self.device_cfg.get("parity", "N"),
                    stopbits=self.device_cfg.get("stopbits", 1),
                    timeout=timeout,
                )
            else:
                logger.error("不支持的 Modbus 模式: %s", mode)
                return False

            self._connected = self._client.connect()
            if self._connected:
                logger.info(
                    "Modbus %s 连接成功 [%s]",
                    mode.upper(),
                    self.device_cfg.get("host", self.device_cfg.get("serial_port")),
                )
            else:
                logger.error("Modbus 连接失败")
            return self._connected

        except Exception as exc:
            logger.error("Modbus 连接异常: %s", exc)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """断开连接。"""
        if self._client:
            try:
                self._client.close()
            except Exception as exc:
                logger.warning("断开连接时异常: %s", exc)
            finally:
                self._client = None
                self._connected = False
                logger.info("Modbus 连接已关闭")

    def reconnect(self) -> bool:
        """强制重连。"""
        self.disconnect()
        return self.connect()

    # ---------- 寄存器读取 ----------

    def read_registers(
        self, address: int, count: int, slave_id: Optional[int] = None
    ) -> Optional[List[int]]:
        """读取保持寄存器（功能码 0x03），带自动重试。

        Args:
            address: 起始寄存器地址（0-based）。
            count:   连续读取个数。
            slave_id: 从站 ID，默认使用配置值。

        Returns:
            整数列表，全部失败返回 None。
        """
        slave_id = slave_id or self.device_cfg.get("slave_id", 1)

        for attempt in range(1, self.retry_count + 1):
            try:
                # 断线自动重连
                if not self._connected or not self._client:
                    if not self.connect():
                        time.sleep(self.retry_delay)
                        continue

                result = self._client.read_holding_registers(
                    address=address, count=count, device_id=slave_id
                )

                if isinstance(result, ExceptionResponse):
                    logger.warning(
                        "Modbus 异常响应 (attempt %d/%d): func=%s code=%d",
                        attempt,
                        self.retry_count,
                        hex(result.function_code),
                        result.exception_code,
                    )
                    time.sleep(self.retry_delay)
                    continue

                if result is None or not hasattr(result, "registers"):
                    logger.warning(
                        "Modbus 返回空 (attempt %d/%d)",
                        attempt,
                        self.retry_count,
                    )
                    time.sleep(self.retry_delay)
                    continue

                return list(result.registers)

            except (ModbusException, ConnectionException, OSError) as exc:
                logger.warning(
                    "Modbus 读取异常 (attempt %d/%d): %s",
                    attempt,
                    self.retry_count,
                    exc,
                )
                self._connected = False
                time.sleep(self.retry_delay)

        logger.error("读取寄存器 [addr=%d count=%d] 全部重试失败", address, count)
        return None

    # ---------- 上下文管理器 ----------

    def __enter__(self) -> "ModbusDeviceClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
