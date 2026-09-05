#!/usr/bin/env python3
"""
Decoder for Brammo Empulse (Elithion Lithiumate BMS) .DRV/.CHG log files.

Format reverse-engineered by Richard Champalbert (FreepZ) and Jim Graham (flar),
recovered from the archived "enspector" project (bitbucket.org/freepz/enspector).

Frame layout:
    offset 0      : '<' (0x3C)
    offset 1-6    : timestamp, 6 bytes BCD (YY MM DD hh mm ss), all 0x20 = no date
    offset 7      : '>' (0x3E)
    offset 8      : 1-byte record code (ASCII letter)
    offset 9..    : payload, length fixed per code (see CODE_LENGTHS)
"""
import csv
import glob
import os
import struct
import sys

CODE_LENGTHS = {
    ord('I'): 16,
    ord('M'): 46,
    ord('D'): 17,
    ord('B'): 144,
    ord('E'): 32,
    ord('F'): 35,
    ord('C'): 72,
    ord('V'): 18,
}

NUM_BATTERIES = 7


def bcd(b):
    return ((b >> 4) & 0xF) * 10 + (b & 0xF)


def parse_date(buf, off):
    raw = buf[off:off + 6]
    if all(b == 0x20 for b in raw):
        return ""
    try:
        y, mo, d, h, mi, s = (bcd(b) for b in raw)
        return f"20{y:02d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"
    except Exception:
        return ""


def iter_records(data):
    off = 0
    n = len(data)
    while off + 9 <= n:
        if data[off] != 0x3C or data[off + 7] != 0x3E:
            break
        code = data[off + 8]
        datastart = off + 9
        length = CODE_LENGTHS.get(code)
        if length is None:
            i = datastart
            found = False
            while i + 7 < n:
                if data[i] == 0x3C and data[i + 7] == 0x3E:
                    found = True
                    break
                i += 1
            length = (i - datastart) if found else (n - datastart)
        if datastart + length > n:
            length = n - datastart
        payload = data[datastart:datastart + length]
        date = parse_date(data, off + 1)
        yield date, code, payload
        off = datastart + length
        if length == 0:
            break


def decode_D(payload):
    # 17-byte payload
    if len(payload) < 17:
        return None
    speed_raw, = struct.unpack_from(">H", payload, 0)
    air_temp = payload[2]
    motor_temp = payload[3]
    throttle = payload[8]
    odo, = struct.unpack_from(">f", payload, 9)
    rpm, = struct.unpack_from(">H", payload, 13)
    return {
        "speed_mph": round(speed_raw * 0.1, 1),
        "rpm": rpm,
        "odometer_mi": round(odo, 3),
        "air_temp_f": air_temp,
        "motor_temp_f": motor_temp,
        "throttle_pct": throttle,
    }


def decode_B(payload):
    # 144-byte payload. Byte offsets below were reverse-engineered by cross-
    # referencing 58E8B416.DRV @ 2024-07-08T22:32:37 against a known-good
    # decoded CSV (Downloads/2024-07-08 22-32-44.csv) for the same VIN/time -
    # every field matched exactly (28/28 cell volts, 7/7 currents, 7/7 cell
    # temps, firmware rev, customer code, high/low cell volts).
    if len(payload) < NUM_BATTERIES:
        return None
    modules = payload[0:NUM_BATTERIES]
    overall = sum(modules) / NUM_BATTERIES / 255.0 * 100.0
    row = {"overall_soc_pct": round(overall, 1)}
    for i, m in enumerate(modules, start=1):
        row[f"module{i}_soc_pct"] = round(m / 255.0 * 100.0, 1)

    if len(payload) >= 144:
        row["pack_voltage_v"] = payload[16]
        high_cell_v = struct.unpack_from("<H", payload, 36)[0] / 1000.0
        low_cell_v = struct.unpack_from("<H", payload, 38)[0] / 1000.0
        row["high_cell_v"] = round(high_cell_v, 3)
        row["low_cell_v"] = round(low_cell_v, 3)
        row["cell_imbalance_mv"] = round((high_cell_v - low_cell_v) * 1000)
        row["bms_firmware_rev"] = f"{payload[40]:02x}.{payload[41]:02x}.{payload[42]:02x}"
        row["bms_customer_code"] = decode_ascii(payload[45:48])

        cell_volts = [struct.unpack_from(">H", payload, 48 + 2 * k)[0] / 1000.0
                      for k in range(28)]
        currents = [struct.unpack_from(">h", payload, 112 + 2 * k)[0] / 100.0
                    for k in range(NUM_BATTERIES)]
        cell_temps = [payload[136 + k] - 60 for k in range(NUM_BATTERIES)]

        row["_cell_volts"] = cell_volts
        row["_currents"] = currents
        row["_cell_temps"] = cell_temps
        row["min_cell_temp_c"] = min(cell_temps)
        row["max_cell_temp_c"] = max(cell_temps)
    return row


def decode_F(payload):
    if len(payload) < 3:
        return None
    kickstand_raw = payload[2]
    state = "down" if kickstand_raw == 32 else ("up" if kickstand_raw == 0 else f"unknown({kickstand_raw})")
    return {"kickstand": state, "kickstand_raw": kickstand_raw}


def decode_M(payload):
    # 46-byte per-module status record. byte0 = module number ('1'-'7').
    # offset 11 = heater current (mA), offset 31 = bq116 rebuild count -
    # both verified against the same reference CSV as decode_B.
    if len(payload) < 32:
        return None
    module = payload[0] - 0x30
    return {
        "module": module,
        "heater_current_ma": payload[11],
        "bq116_rebuilds": payload[31],
    }


def decode_ascii(payload):
    return payload.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def decode_vin(payload):
    # Last byte is a terminator/unused, not part of the 17-char VIN.
    return decode_ascii(payload[:17])


def main():
    log_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/LOGS")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/LOGS_csv")
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(log_dir, "*.DRV")) +
                    glob.glob(os.path.join(log_dir, "*.CHG")) +
                    glob.glob(os.path.join(log_dir, "*.drv")) +
                    glob.glob(os.path.join(log_dir, "*.chg")))

    if not files:
        print(f"No .DRV/.CHG files found in {log_dir}")
        return

    drive_path = os.path.join(out_dir, "drive_telemetry.csv")
    soc_path = os.path.join(out_dir, "battery_soc.csv")
    cells_path = os.path.join(out_dir, "cell_voltages.csv")
    modtemp_path = os.path.join(out_dir, "module_current_temp.csv")
    modstatus_path = os.path.join(out_dir, "module_status.csv")
    flags_path = os.path.join(out_dir, "status_flags.csv")
    other_path = os.path.join(out_dir, "other_records.csv")

    drive_fields = ["source_file", "session_type", "timestamp", "speed_mph", "rpm",
                     "odometer_mi", "air_temp_f", "motor_temp_f", "throttle_pct"]
    soc_fields = ["source_file", "session_type", "timestamp", "overall_soc_pct"] + \
                 [f"module{i}_soc_pct" for i in range(1, NUM_BATTERIES + 1)] + \
                 ["pack_voltage_v", "high_cell_v", "low_cell_v", "cell_imbalance_mv",
                  "min_cell_temp_c", "max_cell_temp_c", "bms_firmware_rev", "bms_customer_code"]
    cells_fields = ["source_file", "session_type", "timestamp"] + \
                   [f"module{m}_cell{c}_v" for m in range(1, NUM_BATTERIES + 1) for c in range(1, 5)]
    modtemp_fields = ["source_file", "session_type", "timestamp"] + \
                      [f"module{i}_current_a" for i in range(1, NUM_BATTERIES + 1)] + \
                      [f"module{i}_cell_temp_c" for i in range(1, NUM_BATTERIES + 1)]
    modstatus_fields = ["source_file", "session_type", "timestamp", "module",
                         "heater_current_ma", "bq116_rebuilds"]
    flags_fields = ["source_file", "session_type", "timestamp", "kickstand", "kickstand_raw"]
    other_fields = ["source_file", "session_type", "timestamp", "code", "length", "data_ascii_or_hex"]

    n_files = 0
    n_records = 0
    n_bad_files = 0

    with open(drive_path, "w", newline="") as fd, \
         open(soc_path, "w", newline="") as fs, \
         open(cells_path, "w", newline="") as fc, \
         open(modtemp_path, "w", newline="") as fmt, \
         open(modstatus_path, "w", newline="") as fms, \
         open(flags_path, "w", newline="") as ff, \
         open(other_path, "w", newline="") as fo:

        wd = csv.DictWriter(fd, fieldnames=drive_fields)
        wd.writeheader()
        ws = csv.DictWriter(fs, fieldnames=soc_fields)
        ws.writeheader()
        wc = csv.DictWriter(fc, fieldnames=cells_fields)
        wc.writeheader()
        wmt = csv.DictWriter(fmt, fieldnames=modtemp_fields)
        wmt.writeheader()
        wms = csv.DictWriter(fms, fieldnames=modstatus_fields)
        wms.writeheader()
        wf = csv.DictWriter(ff, fieldnames=flags_fields)
        wf.writeheader()
        wo = csv.DictWriter(fo, fieldnames=other_fields)
        wo.writeheader()

        for path in files:
            fname = os.path.basename(path)
            session_type = "drive" if fname.lower().endswith(".drv") else "charge"
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except Exception as e:
                print(f"Could not read {fname}: {e}")
                n_bad_files += 1
                continue

            n_files += 1
            try:
                for date, code, payload in iter_records(data):
                    n_records += 1
                    ch = chr(code) if 32 <= code < 127 else f"0x{code:02x}"
                    if code == ord('D'):
                        dec = decode_D(payload)
                        if dec:
                            wd.writerow({"source_file": fname, "session_type": session_type,
                                         "timestamp": date, **dec})
                    elif code == ord('B'):
                        dec = decode_B(payload)
                        if dec:
                            cell_volts = dec.pop("_cell_volts", None)
                            currents = dec.pop("_currents", None)
                            cell_temps = dec.pop("_cell_temps", None)
                            base = {"source_file": fname, "session_type": session_type, "timestamp": date}
                            ws.writerow({**base, **dec})
                            if cell_volts:
                                row = dict(base)
                                for m in range(NUM_BATTERIES):
                                    for c in range(4):
                                        row[f"module{m+1}_cell{c+1}_v"] = cell_volts[m * 4 + c]
                                wc.writerow(row)
                            if currents and cell_temps:
                                row = dict(base)
                                for i in range(NUM_BATTERIES):
                                    row[f"module{i+1}_current_a"] = currents[i]
                                    row[f"module{i+1}_cell_temp_c"] = cell_temps[i]
                                wmt.writerow(row)
                    elif code == ord('F'):
                        dec = decode_F(payload)
                        if dec:
                            wf.writerow({"source_file": fname, "session_type": session_type,
                                         "timestamp": date, **dec})
                    elif code == ord('M'):
                        dec = decode_M(payload)
                        if dec:
                            wms.writerow({"source_file": fname, "session_type": session_type,
                                          "timestamp": date, **dec})
                    elif code in (ord('C'), ord('V')):
                        text = decode_vin(payload) if code == ord('V') else decode_ascii(payload)
                        wo.writerow({"source_file": fname, "session_type": session_type,
                                     "timestamp": date, "code": ch, "length": len(payload),
                                     "data_ascii_or_hex": text})
                    else:
                        wo.writerow({"source_file": fname, "session_type": session_type,
                                     "timestamp": date, "code": ch, "length": len(payload),
                                     "data_ascii_or_hex": payload.hex()})
            except Exception as e:
                print(f"Error parsing {fname} at record {n_records}: {e}")
                n_bad_files += 1

    print(f"Files processed: {n_files} ({n_bad_files} with errors)")
    print(f"Total records:   {n_records}")
    print(f"Output:")
    print(f"  {drive_path}")
    print(f"  {soc_path}")
    print(f"  {cells_path}")
    print(f"  {modtemp_path}")
    print(f"  {modstatus_path}")
    print(f"  {flags_path}")
    print(f"  {other_path}")


if __name__ == "__main__":
    main()
