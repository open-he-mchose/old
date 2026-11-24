#!/usr/bin/env python3
"""Demo: dump the keyboard's settings, read-only (no writes, no flash wear).

Shows firmware version, active profile, decoded FuncConfig (performance + lighting) and,
for every key of layer 0, its mapping and its magnetic-switch trigger settings
(actuation point, rapid-trigger press/release sensitivity, dead zones).

    python3 demos/read_settings.py            # human readable
    python3 demos/read_settings.py --json     # machine readable
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from jet75 import Jet75, decode_funcconfig, decode_trigger, key_name  # noqa: E402

from jet75 import KEY_MODES  # noqa: E402  0 normal, 1 rapid trigger, 2 continuous rapid trigger


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=int, help="profile 0-3 (default: active)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    dev = Jet75()
    try:
        fw, build = dev.info()
        active = dev.active_profile()
        prof = active if args.profile is None else args.profile
        fc = decode_funcconfig(dev.get_func(prof))
        keymap = dev.get_keymap(prof, 0)
        trig = dev.get_triggers(prof)
    finally:
        dev.close()

    keys = []
    for i, code in enumerate(keymap):
        if code in ((0, 0, 0), (0xFF, 0xFF, 0xFF), (0xF0, 0xF0, 0xF0)):  # empty / filler slots
            continue
        k = {"index": i, "key": key_name(*code), "code": bytes(code).hex()}
        k.update(decode_trigger(trig[i]))
        keys.append(k)

    if args.json:
        print(json.dumps({"fw": fw, "build": build, "activeProfile": active, "profile": prof,
                          "funcConfig": fc, "keys": keys}, indent=1))
        return

    print("firmware %s (%s), active profile %d, showing profile %d\n" % (fw, build, active, prof))
    light = fc.pop("light"); side = fc.pop("sideLight")
    print("performance:", ", ".join("%s=%s" % kv for kv in fc.items()))
    print("main light :", light)
    print("side light :", side, "\n")
    print("%4s %-10s %-9s %-13s %7s %8s %8s %5s %5s" % (
        "idx", "key", "code", "mode", "act mm", "RT dn mm", "RT up mm", "dzP", "dzR"))
    for k in keys:
        print("%4d %-10s %-9s %-13s %7.2f %8.2f %8.2f %5d %5d" % (
            k["index"], k["key"], k["code"], KEY_MODES.get(k["keyMode"], k["keyMode"]),
            k["actuation_mm"], k["rtPress_mm"], k["rtRelease_mm"], k["pressDeadzone"], k["releaseDeadzone"]))
    print("\n%d mapped keys" % len(keys))


if __name__ == "__main__":
    main()
