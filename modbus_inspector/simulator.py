#!/usr/bin/env python3
"""
Modbus TCP 模拟器（纯 socket 实现）
=====================================
不依赖 pymodbus 服务端 API，用原生 socket 实现 Modbus TCP 协议，
自动生成三相电压 / 电流 / 温度数据，用于测试巡检程序。

寄存器映射（与 config_sim.yaml 一致）：
    addr  0-2:  电压 Ua/Ub/Uc  (scale 0.1 → V)
    addr 10-12: 电流 Ia/Ib/Ic  (scale 0.01 → A)
    addr 20:    温度            (scale 1.0 → °C)
"""
import argparse
import logging
import random
import signal
import socket
import struct
import sys
import threading
import time
from typing import List, Optional

# ---------------------------------------------------------------------------
#  日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("modbus_simulator")

# ---------------------------------------------------------------------------
#  常量
# ---------------------------------------------------------------------------

# 寄存器数量
REG_COUNT = 100

# 功能码
FC_READ_HOLDING_REGS = 0x03
FC_WRITE_SINGLE_REG = 0x06
FC_WRITE_MULTI_REGS = 0x10

# 异常码
EXC_NONE = 0x00
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_DATA_ADDR = 0x02
EXC_ILLEGAL_DATA_VAL = 0x03


# ---------------------------------------------------------------------------
#  模拟器
# ---------------------------------------------------------------------------

class ModbusSimulator:
    """Modbus TCP 模拟器（纯 socket）。"""

    def __init__(self, host: str = "0.0.0.0", port: int = 5020):
        self.host = host
        self.port = port

        # ---- 寄存器存储 ----
        self._regs: List[int] = [0] * REG_COUNT
        self._init_defaults()

        # ---- 温度随机游走状态 ----
        self._temp_center: float = 45.0

        # ---- socket ----
        self._server: Optional[socket.socket] = None
        self._running = False
        self._update_thread: Optional[threading.Thread] = None

    # ---------- 初始值 ----------

    def _init_defaults(self) -> None:
        self._regs[0] = 2200   # 220.0 V
        self._regs[1] = 2195   # 219.5 V
        self._regs[2] = 2205   # 220.5 V
        self._regs[10] = 5000  # 50.00 A
        self._regs[11] = 4980  # 49.80 A
        self._regs[12] = 5020  # 50.20 A
        self._regs[20] = 450   # 45.0 °C

    # ---------- 定时更新 ----------

    def _update_loop(self) -> None:
        """后台线程：每 2 秒更新寄存器。"""
        while self._running:
            time.sleep(2)
            try:
                self._tick()
            except Exception:
                logger.exception("更新寄存器异常")

    def _tick(self) -> None:
        """一次数值更新。"""
        # 电压 215-225V 范围
        self._regs[0] = max(0, int(2200 + random.gauss(0, 20)))
        self._regs[1] = max(0, int(2195 + random.gauss(0, 20)))
        self._regs[2] = max(0, int(2205 + random.gauss(0, 30)))

        # 电流 45-55A 范围
        self._regs[10] = max(0, int(5000 + random.gauss(0, 100)))
        self._regs[11] = max(0, int(4980 + random.gauss(0, 100)))
        self._regs[12] = max(0, int(5020 + random.gauss(0, 150)))

        # 温度 35-55°C 慢速随机游走
        self._temp_center += random.uniform(-0.3, 0.3)
        self._temp_center = max(35.0, min(55.0, self._temp_center))
        self._regs[20] = int(self._temp_center * 10)

    # ---------- Modbus 协议处理 ----------

    def _handle_client(self, client: socket.socket, addr: tuple) -> None:
        """处理一个客户端连接。"""
        logger.info("客户端接入: %s:%d", *addr)
        try:
            while self._running:
                data = client.recv(1024)
                if not data:
                    break
                response = self._process_request(data)
                if response:
                    client.sendall(response)
        except (ConnectionError, OSError) as exc:
            logger.debug("客户端断开: %s:%d (%s)", *addr, exc)
        except Exception as exc:
            logger.warning("处理客户端异常: %s", exc)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _process_request(self, data: bytes) -> Optional[bytes]:
        """解析 Modbus TCP 请求并生成响应。"""
        if len(data) < 8:
            return None

        # ---- MBAP 头 ----
        tid = struct.unpack(">H", data[0:2])[0]       # Transaction ID
        pid = struct.unpack(">H", data[2:4])[0]       # Protocol ID
        length = struct.unpack(">H", data[4:6])[0]    # Length
        unit_id = data[6]                              # Unit ID

        if pid != 0:
            logger.warning("非 Modbus 协议 (pid=%d)", pid)
            return None

        # ---- PDU ----
        pdu = data[7:]
        if not pdu:
            return None

        fc = pdu[0]
        mbap = struct.pack(">HHHBB", tid, pid, 0, unit_id, fc)

        if fc == FC_READ_HOLDING_REGS:
            return self._handle_read_holding_regs(pdu, mbap)
        elif fc == FC_WRITE_SINGLE_REG:
            return self._handle_write_single_reg(pdu, mbap)
        elif fc == FC_WRITE_MULTI_REGS:
            return self._handle_write_multi_regs(pdu, mbap)
        else:
            # 不支持的功能码
            return mbap[:4] + struct.pack(">HBB", 3, unit_id, fc | 0x80, EXC_ILLEGAL_FUNCTION)

    def _handle_read_holding_regs(self, pdu: bytes, mbap: bytes) -> bytes:
        """功能码 0x03：读保持寄存器。"""
        fc = pdu[0]
        if len(pdu) < 4:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_VAL)

        start_addr = struct.unpack(">H", pdu[1:3])[0]
        quantity = struct.unpack(">H", pdu[3:5])[0]

        # 校验范围
        if start_addr + quantity > REG_COUNT or quantity < 1 or quantity > 125:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_ADDR)

        regs = self._regs[start_addr:start_addr + quantity]
        byte_count = quantity * 2
        body = struct.pack("B", byte_count) + struct.pack(f">{quantity}H", *regs)

        # 更新 MBAP 长度
        new_mbap = mbap[:4] + struct.pack(">H", 2 + len(body)) + mbap[6:8]
        return new_mbap + body

    def _handle_write_single_reg(self, pdu: bytes, mbap: bytes) -> bytes:
        """功能码 0x06：写单个寄存器。"""
        fc = pdu[0]
        if len(pdu) < 4:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_VAL)

        addr = struct.unpack(">H", pdu[1:3])[0]
        value = struct.unpack(">H", pdu[3:5])[0]

        if addr >= REG_COUNT:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_ADDR)

        self._regs[addr] = value

        # 回显请求即可
        new_mbap = mbap[:4] + struct.pack(">H", 6) + mbap[6:8]
        return new_mbap + pdu

    def _handle_write_multi_regs(self, pdu: bytes, mbap: bytes) -> bytes:
        """功能码 0x10：写多个寄存器。"""
        fc = pdu[0]
        if len(pdu) < 6:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_VAL)

        start_addr = struct.unpack(">H", pdu[1:3])[0]
        quantity = struct.unpack(">H", pdu[3:5])[0]
        byte_count = pdu[5]

        if start_addr + quantity > REG_COUNT or quantity < 1:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_ADDR)

        expected_bytes = quantity * 2
        if byte_count != expected_bytes or len(pdu) < 6 + expected_bytes:
            return self._build_exception(mbap, fc, EXC_ILLEGAL_DATA_VAL)

        for i in range(quantity):
            val = struct.unpack(">H", pdu[6 + i * 2: 8 + i * 2])[0]
            self._regs[start_addr + i] = val

        # 返回起始地址 + 数量
        new_mbap = mbap[:4] + struct.pack(">H", 6) + mbap[6:8]
        return new_mbap + pdu[:5]

    def _build_exception(self, mbap: bytes, func_code: int, exc_code: int) -> bytes:
        """构建 Modbus 异常响应。"""
        new_mbap = mbap[:4] + struct.pack(">H", 3) + mbap[6:8]
        return new_mbap + struct.pack("BB", func_code | 0x80, exc_code)

    # ---------- 启停 ----------

    def start(self) -> None:
        """启动模拟器（阻塞）。"""
        self._running = True

        # 后台更新线程
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

        # 创建 TCP socket
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.settimeout(1.0)  # 每秒检查 _running 标志

        try:
            self._server.bind((self.host, self.port))
            self._server.listen(5)
        except OSError as exc:
            logger.error("端口 %d 绑定失败: %s", self.port, exc)
            logger.info("提示: 尝试 --port 15020 等高位端口")
            self._running = False
            return

        logger.info("=" * 50)
        logger.info("Modbus 模拟器已启动")
        logger.info("  监听地址: %s:%d", self.host, self.port)
        logger.info("  从站 ID: 1")
        logger.info("  ──────────────────────────────────")
        logger.info("  电压: addr=0-2,  ~220V     | raw × 0.1")
        logger.info("  电流: addr=10-12, ~50A     | raw × 0.01")
        logger.info("  温度: addr=20,    ~45°C    | raw × 1.0")
        logger.info("  ──────────────────────────────────")
        logger.info("  巡检命令: python main.py -c config_sim.yaml")
        logger.info("  按 Ctrl+C 停止")
        logger.info("=" * 50)

        while self._running:
            try:
                client, addr = self._server.accept()
                # 每个客户端一个线程处理
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client, addr),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break

        self._cleanup()

    def _cleanup(self) -> None:
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        logger.info("模拟器已停止")

    def stop(self) -> None:
        """停止模拟器。"""
        self._running = False
        logger.info("正在停止模拟器...")


# ---------------------------------------------------------------------------
#  主入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Modbus TCP 模拟器（纯 socket）—— 用于测试巡检程序"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="监听地址（默认: 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5020,
        help="监听端口（默认: 5020）",
    )
    args = parser.parse_args()

    sim = ModbusSimulator(host=args.host, port=args.port)

    # 信号处理
    def _signal(signum, frame):
        logger.warning("收到停止信号")
        sim.stop()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    sim.start()


if __name__ == "__main__":
    main()
