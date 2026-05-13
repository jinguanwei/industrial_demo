"""
日报 CSV 生成模块
===================
按日期分文件自动生成 CSV，含 BOM 头确保 Excel 兼容。
"""
import csv
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("modbus_inspector.reporter")


class DailyReporter:
    """日报生成器 —— 每天一个 CSV 文件，追加写入。"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        report_cfg = config.get("report", {})
        self.report_dir = report_cfg.get("dir", "reports")
        self.encoding = report_cfg.get("encoding", "utf-8-sig")
        self.register_groups = config.get("registers", {})

        os.makedirs(self.report_dir, exist_ok=True)

        # 构建表头与字段列表（扁平顺序）
        self.headers: List[str] = ["时间"]
        self.field_names: List[str] = ["timestamp"]
        for group_name, group_cfg in self.register_groups.items():
            labels = group_cfg.get("labels", [])
            self.headers.extend(labels)
            self.field_names.extend(labels)

        # 缓存当前日期，避免每次调用都打开文件
        self._current_date: Optional[str] = None
        self._file_handle = None
        self._csv_writer = None

    def write_record(self, record: Dict[str, Any]) -> None:
        """追加写入一条记录到当天 CSV 文件。

        Args:
            record: 包含 "timestamp" 和所有 label 字段的字典。
        """
        today = record.get("timestamp")
        if isinstance(today, datetime):
            date_str = today.strftime("%Y-%m-%d")
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")

        # 跨日时轮换文件
        if date_str != self._current_date:
            self._rotate_file(date_str)

        row = []
        for field in self.field_names:
            if field == "timestamp":
                if isinstance(record.get("timestamp"), datetime):
                    row.append(record["timestamp"].strftime("%Y-%m-%d %H:%M:%S"))
                else:
                    row.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            else:
                val = record.get(field)
                if val is not None:
                    row.append(f"{val:.2f}" if isinstance(val, float) else str(val))
                else:
                    row.append("")

        try:
            if self._csv_writer:
                self._csv_writer.writerow(row)
                if self._file_handle:
                    self._file_handle.flush()
                    os.fsync(self._file_handle.fileno())
                logger.debug("已写入日报 %s: %s", date_str, row[0])
        except Exception as exc:
            logger.exception("写入 CSV 行失败: %s", exc)

    def _rotate_file(self, date_str: str) -> None:
        """切换到指定日期的 CSV 文件。

        Args:
            date_str: "YYYY-MM-DD" 格式日期。
        """
        # 关闭旧文件
        self._close_file()

        file_path = os.path.join(self.report_dir, f"{date_str}.csv")
        file_exists = os.path.isfile(file_path)

        try:
            # 使用 utf-8-sig 写入 BOM，Excel 可直接识别
            self._file_handle = open(
                file_path, mode="a", encoding=self.encoding, newline=""
            )
            self._csv_writer = csv.writer(self._file_handle)

            if not file_exists or os.path.getsize(file_path) == 0:
                self._csv_writer.writerow(self.headers)
                self._file_handle.flush()
                logger.info("创建新日报: %s", file_path)

            self._current_date = date_str
            logger.info("切换日报文件: %s", file_path)

        except Exception as exc:
            logger.exception("打开日报文件失败: %s", file_path)
            self._file_handle = None
            self._csv_writer = None

    def _close_file(self) -> None:
        """关闭当前打开的日报文件。"""
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception as exc:
                logger.warning("关闭日报文件异常: %s", exc)
            finally:
                self._file_handle = None
                self._csv_writer = None
                self._current_date = None

    def close(self) -> None:
        """显式关闭（程序退出时调用）。"""
        self._close_file()
        logger.info("日报 reporter 已关闭")
