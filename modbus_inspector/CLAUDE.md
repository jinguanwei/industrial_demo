# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Modbus industrial inspection program that periodically reads voltage, current, and temperature from PLCs via Modbus TCP/RTU, logs results, and generates daily CSV reports. Includes a built-in simulator for testing.

## Architecture

```
config.yaml / config_sim.yaml  ← YAML-driven configuration
         │
         ▼
main.py ───→ logger_setup.py   ← RotatingFileHandler + console
  │
  ├──→ modbus_client.py        ← ModbusDeviceClient (TCP/RTU + retry)
  │
  ├──→ collector.py            ← DataCollector (read registers × scale → engineering values)
  │
  └──→ reporter.py             ← DailyReporter (daily CSV with BOM, utf-8-sig)
```

### Data flow per collection cycle:
1. `DataCollector.collect()` iterates configured register groups
2. For each group, calls `ModbusDeviceClient.read_registers(address, count)` — reads holding registers (FC 0x03) with auto-retry
3. Raw values multiplied by `scale` from config to produce engineering values
4. Flat record dict assembled with `timestamp` + all register labels
5. Record logged and passed to `DailyReporter.write_record()` → appended to `reports/YYYY-MM-DD.csv`
6. CSV rotation happens automatically on date change; file handle stays open until `close()`

### Error handling:
- Modbus connection fails → retries `retry_count` times with `retry_delay` gap; `None` values for failed groups, whole cycle aborts if ALL groups fail
- Partial failures (one group fails, others succeed) → failed channels logged as `None` in CSV
- Signal handlers (SIGINT/SIGTERM) → cleanup reporter file handle + modbus connection
- All collector exceptions caught by `_collect_task` wrapper in main loop

### Configuration (`config.yaml`):
- `device`: mode (tcp/rtu), host:port or serial_port, slave_id, timeout
- `registers`: groups with address/count/scale/unit/labels — scale converts raw register values to engineering units
- `collection`: interval_seconds, retry_count, retry_delay
- `logging`: level, dir, max_bytes, backup_count
- `report`: dir, encoding (utf-8-sig for Excel compatibility)

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point, CLI arg parsing, schedule loop, graceful shutdown |
| `modbus_client.py` | `ModbusDeviceClient` — wraps pymodbus, TCP & serial modes, auto-reconnect, retry |
| `collector.py` | `DataCollector` — reads all register groups, applies scale, logs results |
| `reporter.py` | `DailyReporter` — per-date CSV files, write queue, BOM header |
| `logger_setup.py` | `setup_logging()` — one file + one console handler, rotation |
| `simulator.py` | Pure-socket Modbus TCP server for testing — no pymodbus server dependency |
| `config.yaml` | Production config template (default port 502) |
| `config_sim.yaml` | Simulator config (port 5020, faster interval) |

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run inspection (continuous mode, Ctrl+C to stop)
python main.py

# Run inspection with custom config
python main.py -c config_sim.yaml

# Single collection cycle (test mode, no continuous loop)
python main.py --once

# Start simulator (terminal 1)
python simulator.py --port 5020

# Test full pipeline (terminal 2 after simulator starts)
python main.py -c config_sim.yaml --once

# Run 3 collection cycles with simulator
python simulator.py --port 5020 &
python main.py -c config_sim.yaml
```

## Dependencies (pymodbus 3.x notes)

- pymodbus 3.13+ changed `slave` parameter → `device_id` in `read_holding_registers()`
- pymodbus 3.13+ server API uses `SimDevice`/`SimData` (deprecated old `ModbusSlaveContext`)
- `ModbusSequentialDataBlock` with address=0 raises `TypeError` due to SimData requiring address >= 1
- The simulator uses raw sockets to avoid pymodbus server API churn
- `ModbusSerialClient` replaces the old `ModbusRtuClient` name
