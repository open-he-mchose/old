#!/usr/bin/env python3
"""Demo: use the magnetic WASD keys as an analog gamepad stick (Linux, stdlib only).

Creates a virtual gamepad through /dev/uinput and drives it from the keyboard's depth stream:

    W / A / S / D   -> left stick (deflection = how deep the key is pressed)
    Space           -> button A (pressed state reported by the keyboard firmware)
    Left Shift      -> left trigger, analog (depth)

Games and anything using SDL / evdev / the browser Gamepad API see a normal controller.
The keyboard keeps sending ordinary key presses as well, so bind the game to the gamepad
(or unbind WASD) to avoid double input.

    python3 demos/wasd_gamepad.py                 # run until Ctrl+C
    python3 demos/wasd_gamepad.py --selftest      # circle the stick for a few seconds, no keyboard needed
    python3 demos/wasd_gamepad.py --no-uinput     # only show the stick in the terminal

Needs write access to /dev/uinput (group 'input'). Like the other demos it switches the depth
stream on (FuncConfig byte 7 bit 3, two flash writes) and always switches it off on exit.
Limitation: the keyboard sends ~40 depth packets/s for all keys together, so the stick updates
every ~25-100 ms depending on how many keys move at once.
"""
import argparse, fcntl, math, os, signal, struct, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from jet75 import Jet75, parse_stream  # noqa: E402

# ---- uinput (linux/uinput.h, linux/input-event-codes.h) ----
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_SETUP, UI_ABS_SETUP = 0x405C5503, 0x401C5504
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS, SYN_REPORT = 0, 1, 3, 0
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 0x10, 0x11
BUTTONS = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13A, 0x13B, 0x13C, 0x13D, 0x13E]  # A B X Y LB RB Back Start Guide LS RS
BTN_A = 0x130
STICK_MAX = 32767


class VirtualPad:
    """Xbox-style layout so SDL and games map it without configuration."""

    def __init__(self, name="Jet75 WASD analog stick (virtual)"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        for b in BUTTONS:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, b)
        axes = {ABS_X: (-STICK_MAX, STICK_MAX, 16, 128), ABS_Y: (-STICK_MAX, STICK_MAX, 16, 128),
                ABS_RX: (-STICK_MAX, STICK_MAX, 16, 128), ABS_RY: (-STICK_MAX, STICK_MAX, 16, 128),
                ABS_Z: (0, 255, 0, 0), ABS_RZ: (0, 255, 0, 0), ABS_HAT0X: (-1, 1, 0, 0), ABS_HAT0Y: (-1, 1, 0, 0)}
        for code, (mn, mx, fuzz, flat) in axes.items():
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("<HxxiiiiiI", code, 0, mn, mx, fuzz, flat, 0))
        # struct uinput_setup: input_id{bustype,vendor,product,version} name[80] ff_effects_max
        # IDs of a wired Xbox 360 pad, so SDL's built-in mapping applies.
        setup = struct.pack("<HHHH80sI", 0x03, 0x045E, 0x028E, 0x0114, name.encode()[:79], 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.state = {}

    def emit(self, etype, code, value):
        if etype != EV_SYN and self.state.get((etype, code)) == value:
            return False
        self.state[(etype, code)] = value
        now = time.time()
        os.write(self.fd, struct.pack("<qqHHi", int(now), int((now % 1) * 1e6), etype, code, value))
        return True

    def update(self, x, y, a_pressed, lt):
        changed = self.emit(EV_ABS, ABS_X, int(x * STICK_MAX))
        changed |= self.emit(EV_ABS, ABS_Y, int(y * STICK_MAX))
        changed |= self.emit(EV_KEY, BTN_A, 1 if a_pressed else 0)
        changed |= self.emit(EV_ABS, ABS_Z, int(lt * 255))
        if changed:
            self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


# ---- depth -> stick ----
CODES = {"W": "10001a", "A": "100004", "S": "100016", "D": "100007", "Space": "10002c", "LShift": "100200"}


def norm(mm, dead, full):
    return max(0.0, min(1.0, (mm - dead) / (full - dead)))


def stick(depth, dead, full, square):
    x = norm(depth.get(CODES["D"], 0), dead, full) - norm(depth.get(CODES["A"], 0), dead, full)
    y = norm(depth.get(CODES["S"], 0), dead, full) - norm(depth.get(CODES["W"], 0), dead, full)
    r = math.hypot(x, y)
    if not square and r > 1:  # keep diagonals on the unit circle like a real stick
        x, y = x / r, y / r
    return x, y


def show(x, y, a, lt, depth):
    w = lambda k: depth.get(CODES[k], 0.0)
    col = int(round((x + 1) * 10))
    bar = "".join("O" if i == col else ("|" if i == 10 else "-") for i in range(21))
    sys.stdout.write("\r X %+5.2f Y %+5.2f [%s]  W%4.2f A%4.2f S%4.2f D%4.2f  A:%s LT:%3d%%  " % (
        x, y, bar, w("W"), w("A"), w("S"), w("D"), "ON " if a else "off", lt * 100))
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deadzone", type=float, default=0.20, help="mm of travel ignored at the top (default 0.20)")
    ap.add_argument("--full", type=float, default=3.00, help="mm of travel that means full deflection (default 3.00)")
    ap.add_argument("--square", action="store_true", help="do not clamp diagonals to the unit circle")
    ap.add_argument("--no-uinput", action="store_true", help="do not create a virtual gamepad, just display")
    ap.add_argument("--selftest", action="store_true", help="circle the stick for --duration s without the keyboard")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    args = ap.parse_args()

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    t_end = time.monotonic() + (args.duration or (5 if args.selftest else 0)) if (args.duration or args.selftest) else None
    running = lambda: not stop["flag"] and (t_end is None or time.monotonic() < t_end)

    pad = None if args.no_uinput else VirtualPad()
    if pad:
        print("virtual gamepad created: 'Jet75 WASD analog stick (virtual)'")
    try:
        if args.selftest:
            t0 = time.monotonic()
            while running():
                a = (time.monotonic() - t0) * 2 * math.pi / 2.0  # one circle every 2 s
                x, y = math.cos(a), math.sin(a)
                if pad:
                    pad.update(x, y, int(a) % 2 == 0, (math.sin(a) + 1) / 2)
                show(x, y, int(a) % 2 == 0, (math.sin(a) + 1) / 2, {})
                time.sleep(0.01)
            if pad:
                pad.update(0, 0, False, 0)
            print()
            return

        dev = Jet75()
        depth, down = {}, {}
        try:
            with dev.depth_stream() as profile:
                print("depth stream on (profile %d). Use W A S D, Space, Left Shift. Ctrl+C to stop." % profile)
                while running():
                    rep = dev.read_report(0.1)
                    if not rep or rep[0] != 0xA0:
                        continue
                    p = parse_stream(rep)
                    depth[p["code"]] = p["depth_mm"]  # precise, lag-free (firmware curve)
                    down[p["code"]] = p["state"] == 1 and p["depth_mm"] > 0
                    x, y = stick(depth, args.deadzone, args.full, args.square)
                    a = down.get(CODES["Space"], False)
                    lt = norm(depth.get(CODES["LShift"], 0), args.deadzone, args.full)
                    if pad:
                        pad.update(x, y, a, lt)
                    show(x, y, a, lt, depth)
                if pad:
                    pad.update(0, 0, False, 0)
            print("\ndepth stream off (FuncConfig restored)")
        finally:
            dev.close()
    finally:
        if pad:
            pad.close()


if __name__ == "__main__":
    main()
