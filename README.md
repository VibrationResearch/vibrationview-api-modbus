# VibrationVIEW to Modbus Bridge

Polls velocity demand parameters from VibrationVIEW and writes them to a PLC over Modbus TCP.

## What it does

- Connects to VibrationVIEW via COM API
- Reads velocity fields using `ReportFields`
- Converts values to mm/s (from in/s, ft/s, m/s, or mm/s)
- Writes scaled integers (x10, 1 decimal place) to two Modbus holding registers
- Automatically reconnects if either VibrationVIEW or the PLC drops

## Modbus registers

| Register | LOGO! address | Value                       |
|----------|---------------|-----------------------------|
| 0        | VW0           | Status bits (see below)     |
| 1        | VW2           | Velocity RMS (mm/s x10)     |
| 2        | VW4           | Velocity Peak (mm/s x10)    |

**Status bits** (packed in low byte VB1):

| Bit | Flag            |
|-----|-----------------|
| 0   | Heartbeat       |
| 1   | IsRunning       |
| 2   | IsStarting      |
| 3   | IsAborted       |
| 4   | IsChangingLevel |

On the LOGO!, use **NAI** (Network Analog Input) blocks at VW2 and VW4 with
1 decimal place to display the velocity values.

## Test mode field mapping

The VibrationVIEW report field used for peak/RMS depends on the active test type:

| Test Type    | Peak source              | RMS source        |
|--------------|--------------------------|-------------------|
| Random       | DemandVelocityPeak       | DemandVelocityRMS |
| Sine         | ProfileMaxVelocityPeak   | Peak x 0.707      |
| System Check | DemandVelocity           | Peak x 0.707      |
| Shock        | DemandVelocity           | Peak              |
| Transient    | DemandVelocity           | Peak              |
| FDR (Replay) | 0                        | 0                 |

### Requirements

- Windows operating system (Windows 10 or Windows 11)
- VibrationVIEW software installed with automation option (VR9604), or running in Simulation mode
- Python 3.7 or higher
- pymodbus
- vibrationview-api

## Setup

### Python environment

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install pymodbus
pip install -e D:\Data\vibrationview-api
```

### LOGO! PLC (Soft Comfort V9.0)

A sample project is included in `LogoSOFT/logosoft_vrcdemo.snp`.

1. Open your project in Network Project view
2. Device properties > Server connections > add a **Modbus/TCP** connection
3. Download to the PLC

### Configuration

Edit the constants at the top of `vv_to_plc.py`:

```python
PLC_IP = "192.168.3.180"
PLC_PORT = 502
REG_STATUS = 0        # VW0
REG_RMS = 1           # VW2
REG_PEAK = 2          # VW4
SCALE_FACTOR = 10     # 1 decimal place, max ~3276.7 mm/s
POLL_INTERVAL_S = 1.0
```

## Usage

```
python -u vv_to_plc.py
```

VibrationVIEW must be running. Press Ctrl+C to stop.
