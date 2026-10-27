"""
Poll DemandVelocityRMS and DemandVelocityPeak from VibrationVIEW and write
them to a Siemens LOGO! PLC via Modbus TCP.

Values are scaled to integers (x10) and written to holding registers:
  Register 0 (VW0) = DemandVelocityRMS   in mm/s
  Register 1 (VW2) = DemandVelocityPeak  in mm/s
"""

import time
import sys
from datetime import datetime

from pymodbus.client import ModbusTcpClient
from vibrationviewapi import VibrationVIEW


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PLC_IP = "192.168.3.180"
PLC_PORT = 502
REG_RMS = 0                 # Holding register for DemandVelocityRMS  (VW0)
REG_PEAK = 1                # Holding register for DemandVelocityPeak (VW2)
SCALE_FACTOR = 10           # 239.4 mm/s -> 2394  (1 decimal place)
POLL_INTERVAL_S = 1.0       # Seconds between polls
RECONNECT_DELAY_S = 5.0     # Seconds to wait before reconnecting


def connect_vv():
    try:
        vv = VibrationVIEW()
        print(f"VibrationVIEW version: {vv.GetSoftwareVersion()}")
        return vv
    except Exception as e:
        print(f"VibrationVIEW connection failed: {e}")
        return None


def to_mm_per_sec(value, unit):
    if unit == "in/s":
        return value * 25.4
    elif unit == "ft/s":
        return value * 304.8
    elif unit == "m/s":
        return value * 1000.0
    elif unit == "mm/s":
        return value
    return value


def parse_and_convert(raw):
    if raw is None:
        return 0.0
    parts = str(raw).split()
    value = float(parts[0])
    unit = parts[1] if len(parts) > 1 else ""
    return to_mm_per_sec(value, unit)


def main():
    vv = None

    try:
        while True:

            # Ensure VibrationVIEW connection
            if vv is None:
                print("Connecting to VibrationVIEW ...")
                vv = connect_vv()
                if vv is None:
                    print(f"Retrying in {RECONNECT_DELAY_S}s ...")
                    time.sleep(RECONNECT_DELAY_S)
                    continue

            # Poll and write
            try:
                fields = vv.ReportFields("TestType,DemandVelocityRMS,DemandVelocityPeak,DemandVelocity,ProfileMaxVelocityPeak")
                fmap = {row[0]: row[1] for row in fields} if fields else {}
                log(f"[READ]  {fmap}")

                # The VV report fields for peak/RMS velocity vary by test mode:
                #   FDR            -> not available, send 0
                #   Shock/TC       -> DemandVelocity (peak = rms = peak) - worst case assumption
                #   System Check   -> DemandVelocity (rms = peak * 0.707)
                #   Sine           -> ProfileMaxVelocityPeak (rms = peak * 0.707)
                #   Random&derived -> DemandVelocityRMS, DemandVelocityPeak
                test_type = fmap.get("TestType", "")
                if test_type == "FDR":
                    peak_mm = 0.0
                    rms_mm = 0.0
                elif test_type in ("Shock", "Transient"):
                    peak_mm = parse_and_convert(fmap.get("DemandVelocity"))
                    rms_mm = peak_mm
                elif test_type == "System Check":
                    peak_mm = parse_and_convert(fmap.get("DemandVelocity"))
                    rms_mm = peak_mm * 0.707
                elif test_type == "Sine":
                    peak_mm = parse_and_convert(fmap.get("ProfileMaxVelocityPeak"))
                    rms_mm = peak_mm * 0.707
                else:
                    rms_mm = parse_and_convert(fmap.get("DemandVelocityRMS"))
                    peak_mm = parse_and_convert(fmap.get("DemandVelocityPeak"))

                scaled_rms = max(0, min(int(rms_mm * SCALE_FACTOR), 32767))
                scaled_peak = max(0, min(int(peak_mm * SCALE_FACTOR), 32767))
                # Reconnect each cycle — the LOGO! drops idle TCP connections
                # after ~10 s and there is no user-configurable timeout.
                with ModbusTcpClient(PLC_IP, port=PLC_PORT) as conn:
                    result = conn.write_registers(REG_RMS, [scaled_rms, scaled_peak])

                if result.isError():
                    log(f"[ERROR] Modbus write error: {result}")
                else:
                    log(f"[WRITE] reg {REG_RMS}={scaled_rms} ({rms_mm:.3f} mm/s RMS)  reg {REG_PEAK}={scaled_peak} ({peak_mm:.3f} mm/s Peak)")

            except KeyboardInterrupt:
                raise
            except Exception as e:
                error_str = str(e)
                if "remote procedure call failed" in error_str.lower() or "com_error" in error_str.lower():
                    print(f"VibrationVIEW disconnected: {e}")
                    vv.close()
                    vv = None
                elif "Modbus" in error_str or isinstance(e, ConnectionError):
                    print(f"PLC connection error: {e}")
                else:
                    print(f"Error: {e}")
                    vv.close()
                    vv = None
                time.sleep(RECONNECT_DELAY_S)
                continue

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if vv is not None:
            vv.close()
        print("Connections closed.")


if __name__ == "__main__":
    main()
