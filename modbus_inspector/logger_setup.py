"""
日志模块 - 控制台 + 文件轮转
================================
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Dict, Any


def setup_logging(config: Dict[str, Any]) -> logging.Logger:
    """初始化并返回根日志器。

    Args:
        config: 顶层配置字典。

    Returns:
        配置好的根日志器。
    """
    log_cfg = config.get("logging", {})
    level_name = log_cfg.get("level", "INFO").upper()
    log_dir = log_cfg.get("dir", "logs")
    max_bytes = log_cfg.get("max_bytes", 10 * 1024 * 1024)
    backup_count = log_cfg.get("backup_count", 30)

    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("modbus_inspector")
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    # 防止重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ---- 文件 Handler（轮转） ----
    log_path = os.path.join(log_dir, "inspector.log")
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # ---- 控制台 Handler ----
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level_name, logging.INFO))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
