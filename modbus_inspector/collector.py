"""
数据采集模块
==============
定时从 Modbus 采集电压 / 电流 / 温度并写入日报。
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from modbus_client import ModbusDeviceClient

logger = logging.getLogger("modbus_inspector.collector")


class DataCollector:
    """采集编排器 —— 按配置读取所有寄存器组并写入日报。"""

    def __init__(
        self,
        config: Dict[str, Any],
        client: ModbusDeviceClient,
        reporter: "DailyReporter",  # type: ignore  # noqa: F821
    ):
        self.config = config
        self.client = client
        self.reporter = reporter
        self.register_groups = config.get("registers", {})

    def collect(self) -> Optional[Dict[str, Any]]:
        """执行一次完整的采集周期。

        Returns:
            采集成功返回数据字典，全部失败返回 None。
        """
        timestamp = datetime.now().astimezone()
        logger.info("===== 开始采集 =====")

        all_values: Dict[str, Any] = {}
        has_any_data = False

        for group_name, group_cfg in self.register_groups.items():
            values = self._read_group(group_name, group_cfg)
            if values is not None:
                all_values[group_name] = values
                has_any_data = True
            else:
                all_values[group_name] = [None] * group_cfg["count"]

        if not has_any_data:
            logger.error("本次采集所有寄存器均失败")
            return None

        # 构建扁平记录
        record: Dict[str, Any] = {"timestamp": timestamp}
        for group_name, group_cfg in self.register_groups.items():
            labels = group_cfg["labels"]
            vals = all_values.get(group_name, [None] * group_cfg["count"])
            for lbl, val in zip(labels, vals):
                record[lbl] = val

        logger.info(
            "采集结果: %s",
            " | ".join(
                f"{k}={v!r}" if k != "timestamp" else v.strftime("%H:%M:%S")
                for k, v in record.items()
            ),
        )

        # 写入日报 CSV
        try:
            self.reporter.write_record(record)
        except Exception as exc:
            logger.exception("写入日报失败: %s", exc)

        logger.info("===== 采集完成 =====")
        return record

    def _read_group(
        self, group_name: str, group_cfg: Dict[str, Any]
    ) -> Optional[List[Optional[float]]]:
        """读取一组寄存器并换算工程值。

        Args:
            group_name: 分组名称（如 "voltage"）。
            group_cfg:  分组配置。

        Returns:
            工程值列表（None 表示某路失败）。
        """
        address = group_cfg["address"]
        count = group_cfg["count"]
        scale = group_cfg.get("scale", 1.0)

        raw = self.client.read_registers(address, count)
        if raw is None:
            return None

        if len(raw) != count:
            logger.warning(
                "%s: 期望 %d 个寄存器，实际读到 %d 个",
                group_name,
                count,
                len(raw),
            )
            raw = raw[:count] + [None] * (count - len(raw))

        return [(v * scale) if v is not None else None for v in raw]
