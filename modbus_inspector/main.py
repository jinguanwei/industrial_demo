#!/usr/bin/env python3
"""
Modbus 工控巡检程序
=====================================
功能：
  1. 从 YAML 配置文件加载设备 / 寄存器 / 调度参数
  2. 定时通过 Modbus TCP/RTU 采集电压 · 电流 · 温度
  3. 日志轮转记录
  4. 自动生成按日分文件的 CSV 报告（含 BOM，Excel 友好）

用法：
  python main.py                          # 使用默认 config.yaml
  python main.py -c /path/to/config.yaml  # 指定配置文件
  python main.py --once                   # 只执行一次采集后退出

退出：
  Ctrl+C 触发优雅关闭。
"""
import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict

import yaml
import schedule

from collector import DataCollector
from logger_setup import setup_logging
from modbus_client import ModbusDeviceClient
from reporter import DailyReporter

logger = None  # 初始化后赋值


# ============================================================
#  配置加载
# ============================================================

def load_config(path: str) -> Dict[str, Any]:
    """加载 YAML 配置文件。

    Args:
        path: 配置文件路径。

    Returns:
        配置字典。

    Raises:
        FileNotFoundError: 配置文件不存在。
        yaml.YAMLError:    YAML 格式错误。
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path.resolve()}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


# ============================================================
#  信号处理
# ============================================================

_running = True


def _handle_signal(signum, frame) -> None:
    """捕获 SIGINT/SIGTERM 触发优雅关闭。"""
    global _running
    if not _running:
        # 第二次信号则强制退出
        sys.exit(1)
    signame = "SIGINT" if signum == 2 else "SIGTERM"
    logger.warning("接收到 %s，正在优雅关闭...", signame)
    _running = False


def _setup_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


# ============================================================
#  采集任务
# ============================================================

def _collect_task(collector: DataCollector) -> None:
    """单次采集任务包装（给 schedule 使用）。"""
    try:
        collector.collect()
    except Exception as exc:
        logger.exception("采集任务异常: %s", exc)


# ============================================================
#  Main
# ============================================================

def main() -> None:
    """程序入口。"""
    global logger

    parser = argparse.ArgumentParser(
        description="Modbus 工控巡检程序 —— 定时采集电压/电流/温度并生成日报 CSV"
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一次采集后退出（不进入循环）",
    )
    args = parser.parse_args()

    # ---- 加载配置 ----
    config = load_config(args.config)

    # ---- 初始化日志 ----
    logger = setup_logging(config)
    logger.info("=" * 50)
    logger.info("Modbus 工控巡检程序启动")
    logger.info("配置文件: %s", Path(args.config).resolve())
    logger.info("设备: %s (%s)", config["device"]["name"], config["device"].get("host", ""))
    logger.info("采集间隔: %d 秒", config["collection"]["interval_seconds"])
    logger.info("=" * 50)

    # ---- 初始化组件 ----
    reporter = DailyReporter(config)
    client = ModbusDeviceClient(config)
    collector = DataCollector(config, client, reporter)

    # ---- 信号处理 ----
    _setup_signal_handlers()

    # ---- 首次连接 ----
    if not client.connect():
        logger.error("首次 Modbus 连接失败，将按调度自动重试")
    else:
        client.disconnect()

    # ---- 调度 & 主循环 ----
    interval = config["collection"]["interval_seconds"]
    schedule.every(interval).seconds.do(_collect_task, collector=collector)

    # 如果使用 --once 则只执行一次
    if args.once:
        logger.info("单次模式: 执行一次采集")
        _collect_task(collector)
        reporter.close()
        client.disconnect()
        logger.info("单次采集完成，退出")
        return

    logger.info("调度已启动，间隔 %d 秒 | 按 Ctrl+C 停止", interval)

    try:
        while _running:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        # 信号处理器已设置，这里作为兜底
        pass
    finally:
        logger.info("正在清理资源...")
        reporter.close()
        client.disconnect()
        logger.info("程序已退出")


if __name__ == "__main__":
    main()
