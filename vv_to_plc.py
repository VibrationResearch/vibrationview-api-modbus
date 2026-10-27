"""
Poll DemandVelocityRMS and DemandVelocityPeak from VibrationVIEW and write
them to a Siemens LOGO! PLC via Modbus TCP.

Values are scaled to integers (x10) and written to holding registers:
  Register 0 (VW0) = Status bits
  Register 1 (VW2) = DemandVelocityRMS   in mm/s
  Register 2 (VW4) = DemandVelocityPeak  in mm/s
"""

import time
from datetime import datetime

from pymodbus.client import ModbusTcpClient
from vibrationviewapi import VibrationVIEW


def log(msg):
    """Print a timestamped log message."""
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PLC_IP = "192.168.3.180"
PLC_PORT = 502
REG_STATUS = 0              # Holding register for status bits        (VW0, bits in VB1=low byte)
REG_RMS = 1                 # Holding register for DemandVelocityRMS  (VW2)
REG_PEAK = 2                # Holding register for DemandVelocityPeak (VW4)
SCALE_FACTOR = 10           # 239.4 mm/s -> 2394  (1 decimal place)
POLL_INTERVAL_S = 1.0       # Seconds between polls
RECONNECT_DELAY_S = 5.0     # Seconds to wait before reconnecting


def connect_vv():
    """Connect to VibrationVIEW via COM. Return instance or None."""
    try:
        vv = VibrationVIEW()
        print(f"VibrationVIEW version: {vv.GetSoftwareVersion()}")
        vv.Restore()
        return vv
    except (OSError, RuntimeError) as e:
        print(f"VibrationVIEW connection failed: {e}")
        return None


def to_mm_per_sec(value, unit):
    """Convert a velocity value to mm/s based on its unit string."""
    lower = unit.lower()
    if lower == "in/s":
        return value * 25.4
    if lower == "ft/s":
        return value * 304.8
    if lower == "m/s":
        return value * 1000.0
    return value


def parse_and_convert(raw):
    """Parse a VV report field string and convert to mm/s."""
    if raw is None:
        return 0.0
    parts = str(raw).split()
    value = float(parts[0])
    unit = parts[1] if len(parts) > 1 else ""
    return to_mm_per_sec(value, unit)


def read_report_fields(vv):
    """Read velocity fields from VibrationVIEW, return (rms_mm, peak_mm)."""
    fields = vv.ReportFields(
        "TestType,DemandVelocityRMS,DemandVelocityPeak,"
        "DemandVelocity,ProfileMaxVelocityPeak"
    )
    fmap = {row[0]: row[1] for row in fields} if fields else {}
    log(f"[READ]  {fmap}")

    # The VV report fields for peak/RMS velocity vary by test mode:
    #   FDR            -> not available, send 0
    #   Shock/TC       -> DemandVelocity (peak = rms = peak)
    #   System Check   -> DemandVelocity (rms = peak * 0.707)
    #   Sine           -> ProfileMaxVelocityPeak (rms = peak * 0.707)
    #   Random&derived -> DemandVelocityRMS, DemandVelocityPeak
    test_type = fmap.get("TestType", "")
    if test_type == "FDR":
        return 0.0, 0.0
    if test_type in ("Shock", "Transient"):
        peak_mm = parse_and_convert(fmap.get("DemandVelocity"))
        return peak_mm, peak_mm
    if test_type == "System Check":
        peak_mm = parse_and_convert(fmap.get("DemandVelocity"))
        return peak_mm * 0.707, peak_mm
    if test_type == "Sine":
        peak_mm = parse_and_convert(
            fmap.get("ProfileMaxVelocityPeak"))
        return peak_mm * 0.707, peak_mm
    rms_mm = parse_and_convert(fmap.get("DemandVelocityRMS"))
    peak_mm = parse_and_convert(fmap.get("DemandVelocityPeak"))
    return rms_mm, peak_mm


def build_status_reg(vv, heartbeat):
    """Build status register from VV state flags and heartbeat bit."""
    return (
        (heartbeat << 0)
        | (vv.IsRunning() << 1)
        | (vv.IsStarting() << 2)
        | (vv.IsAborted() << 3)
        | (vv.IsChangingLevel() << 4)
    )


def write_to_plc(status_reg, scaled_rms, scaled_peak):
    """Write registers to PLC and verify readback. Return True on success."""
    # Reconnect each cycle — the LOGO! drops idle TCP connections
    # after ~10 s and there is no user-configurable timeout.
    with ModbusTcpClient(PLC_IP, port=PLC_PORT) as conn:
        values = [status_reg, scaled_rms, scaled_peak]
        result = conn.write_registers(REG_STATUS, values)
        if result.isError():
            log(f"[ERROR] Modbus write error: {result}")
            return False
        readback = conn.read_holding_registers(
            REG_STATUS, count=3)
        if readback.isError():
            log(f"[ERROR] Modbus readback error: {readback}")
            return False

    if readback.registers != values:
        log(f"[MISMATCH] wrote {values} "
            f"read back {readback.registers}")
    return True


def handle_poll_error(exc, vv):
    """Handle an error during polling. Return new vv (None if reset)."""
    error_str = str(exc).lower()
    if ("remote procedure call failed" in error_str
            or "com_error" in error_str):
        print(f"VibrationVIEW disconnected: {exc}")
        vv.close()
        return None
    if "modbus" in error_str or isinstance(exc, ConnectionError):
        print(f"PLC connection error: {exc}")
        return vv
    print(f"Error: {exc}")
    vv.close()
    return None


def main():
    """Main polling loop: read VV, write to PLC, handle errors."""
    vv = None
    heartbeat = 0

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

            try:
                rms_mm, peak_mm = read_report_fields(vv)
                scaled_rms = max(
                    0, min(int(rms_mm * SCALE_FACTOR), 32767))
                scaled_peak = max(
                    0, min(int(peak_mm * SCALE_FACTOR), 32767))

                heartbeat ^= 1
                status_reg = build_status_reg(vv, heartbeat)

                if not write_to_plc(
                        status_reg, scaled_rms, scaled_peak):
                    continue

                log(f"[WRITE] status=0b{status_reg:05b}  "
                    f"reg {REG_RMS}={scaled_rms} "
                    f"({rms_mm:.3f} mm/s RMS)  "
                    f"reg {REG_PEAK}={scaled_peak} "
                    f"({peak_mm:.3f} mm/s Peak)")

            except KeyboardInterrupt:
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                vv = handle_poll_error(exc, vv)
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
