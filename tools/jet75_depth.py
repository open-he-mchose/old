#!/usr/bin/env python3
"""MCHOSE Jet75-II key-depth monitor (Linux hidraw, no dependencies).

Enables the firmware's debug stream by setting FuncConfig byte 7 bit 3 (debugMode) on the
active profile with command 0x06, prints per-key travel from the 0xA0 stream packets, and
clears the bit again on exit. It never sends 0xA8 (that would start a real recalibration).

Each enable/disable costs two 64-byte flash writes on the keyboard (same as the web driver).
Protocol notes: docs/protocol.md
"""
import argparse, json, os, signal, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jet75 import Jet75, parse_stream  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="hidraw node (default: auto-detect)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--log", help="append every stream packet as JSON lines to this file")
    ap.add_argument("--all", action="store_true", help="print every packet, not only travel changes")
    args = ap.parse_args()

    dev = Jet75(args.device)
    log = open(args.log, "a") if args.log else None
    last = {}

    def on_stream(rep):
        p = parse_stream(rep)
        p["t"] = round(time.time(), 4)
        if log:
            log.write(json.dumps(p) + "\n")
        mm = round(p["depth_mm"], 2)
        if args.all or last.get(p["key"]) != mm:
            last[p["key"]] = mm
            bar = "#" * int(round(20 * mm / (p["max_mm"] or 3.41)))
            print("%-10s %5.2f mm |%-20s| %s firmware=%.2f adc=%d calib=%02x" % (
                p["key"], mm, bar, "DOWN" if p["state"] == 1 else "up  ",
                p["travel_mm"], p["adc"], p["calib"]), flush=True)

    dev.stream_handler = on_stream
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))

    fw, build = dev.info()
    profile = dev.active_profile()
    print("device %s  fw %s (%s)  active profile %d" % (dev.path, fw, build, profile), flush=True)
    try:
        with dev.depth_stream(profile):
            print("debugMode on. Press keys...%s" % ("" if args.duration else " (Ctrl+C to stop)"), flush=True)
            t_end = time.monotonic() + args.duration if args.duration else None
            while not stop["flag"] and (t_end is None or time.monotonic() < t_end):
                rep = dev.read_report(0.2)
                if rep and rep[0] == 0xA0:
                    on_stream(rep)
        print("debugMode off (FuncConfig restored)", flush=True)
    finally:
        if log:
            log.close()
        dev.close()


if __name__ == "__main__":
    main()
