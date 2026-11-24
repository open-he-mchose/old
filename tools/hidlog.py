#!/usr/bin/env python3
"""Log what the Jet75-II sends (raw HID reports of interfaces 0 and 2) next to what Linux makes of
it (key events), for N seconds. Used to verify SOCD / NKRO and the HID-BPF descriptor fix.

    python3 tools/hidlog.py 40 captures/log.txt      # then: python3 tools/hidlog.py --analyze captures/log.txt
"""
import glob, os, select, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "demos"))
EVN = {30: "A", 32: "D", 16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U", 23: "I", 24: "O", 25: "P",
       31: "S", 33: "F", 34: "G", 35: "H", 36: "J", 37: "K", 38: "L", 44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N", 50: "M"}
HID = {0x04: "A", 0x07: "D", 0x14: "Q", 0x1A: "W", 0x08: "E", 0x15: "R", 0x17: "T", 0x1C: "Y", 0x18: "U", 0x0C: "I"}


def record(dur, path):
    from full_gamepad import keyboard_event_nodes
    raw = {}
    for n in glob.glob("/sys/class/hidraw/hidraw*"):
        try:
            ue = open(n + "/device/uevent").read()
        except OSError:
            continue
        if "000041E4:0000211A" in ue:
            iface = os.path.basename(os.path.dirname(os.path.realpath(n + "/device")))
            if not iface.endswith(".1"):
                raw[os.open("/dev/" + os.path.basename(n), os.O_RDONLY | os.O_NONBLOCK)] = iface
    ev = [os.open(p, os.O_RDONLY | os.O_NONBLOCK) for p, _ in keyboard_event_nodes()]
    out, t0 = open(path, "w"), time.monotonic()
    print("recording %g s (raw interfaces: %s)" % (dur, ", ".join(raw.values())), flush=True)
    while time.monotonic() - t0 < dur:
        for fd in select.select(list(raw) + ev, [], [], 0.2)[0]:
            t = time.monotonic() - t0
            if fd in raw:
                out.write("%.3f RAW %s %s\n" % (t, raw[fd], os.read(fd, 64).hex()))
            else:
                d = os.read(fd, 24 * 64)
                for k in range(0, len(d) - 23, 24):
                    _, _, ty, code, val = struct.unpack("<qqHHi", d[k:k + 24])
                    if ty == 1 and val in (0, 1):
                        out.write("%.3f EV %d %d\n" % (t, code, val))
            out.flush()


def analyze(path):
    lines = [l.split() for l in open(path)]
    nkro = [l for l in lines if l[1] == "RAW" and l[2].endswith(".2") and l[3].startswith("01")]
    nk_keys = set()
    for l in nkro:
        b = bytes.fromhex(l[3])
        nk_keys |= {u for u in range(120) if b[1 + u // 8] >> (u % 8) & 1}
    print("NKRO (report 1) reports: %d, keys seen in them: %s" % (len(nkro), sorted(HID.get(u, hex(u)) for u in nk_keys)))
    held, mx, mxset, ad = set(), 0, [], []
    for l in lines:
        if l[1] != "EV":
            continue
        c, v = int(l[2]), int(l[3])
        if c in (30, 32):
            ad.append((l[0], EVN[c], v))
        held.add(c) if v == 1 else held.discard(c)
        if len(held) > mx:
            mx, mxset = len(held), sorted(EVN.get(x, str(x)) for x in held)
    both = 0
    cur = set()
    for _, k, v in ad:
        cur.add(k) if v else cur.discard(k)
        both += cur == {"A", "D"}
    print("Linux A/D events: %d (moments with A and D down together: %d)" % (len(ad), both))
    print("first A/D events:", ad[:12])
    print("max keys held at once in Linux: %d %s" % (mx, mxset))


if __name__ == "__main__":
    if sys.argv[1] == "--analyze":
        analyze(sys.argv[2])
    else:
        record(float(sys.argv[1]), sys.argv[2])
