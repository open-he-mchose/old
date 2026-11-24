#!/usr/bin/env python3
"""Demo: the whole keyboard as an Xbox-style gamepad, with analog sticks and triggers.

    Left stick   W A S D        analog (key depth)
    Right stick  Up Left Down Right   analog (key depth)
    A B X Y      K L J I        (diamond like on a pad), Space is A as well
    D-pad        T F G H        (diamond: T up, F left, G down, H right)
    LT / RT      Q / E          analog (key depth)
    LB / RB      Tab / R
    L3 / R3      Left Shift / Right Shift
    Back / Start / Guide        M / Esc / PrtSc
    F12          quit

Analog values come from the keyboard's depth stream (docs/protocol.md); digital buttons come
from the keyboard's normal key events, so they follow the per-key actuation / rapid trigger
settings and have no extra delay.

By default the keyboard is grabbed (EVIOCGRAB): mapped keys reach only the virtual gamepad,
every other key is passed through a virtual keyboard, so you can still type and press Ctrl+C.
The grab is released automatically when the program exits, even if it crashes.

    python3 demos/full_gamepad.py               # Ctrl+C or F12 to quit
    python3 demos/full_gamepad.py --no-grab     # keys also keep typing normally

Needs /dev/uinput and /dev/input/event* access (group 'input'). Switches the depth stream on
(FuncConfig byte 7 bit 3, two flash writes) and always off again on exit. The keyboard sends at
most ~40 depth packets/s for all keys together, so analog axes update every ~25-100 ms.
Edit LAYOUT below to change the mapping.
"""
import argparse, fcntl, glob, math, os, select, signal, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
from jet75 import Jet75, VID, PID, parse_stream  # noqa: E402
from wasd_gamepad import (VirtualPad, EV_SYN, EV_KEY, EV_ABS, SYN_REPORT, ABS_X, ABS_Y, ABS_Z,  # noqa: E402
                          ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y, STICK_MAX,
                          UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY)

# key name -> (code in the depth stream [type code1 code2], Linux evdev keycode)
KEYS = {
    "W": ("10001a", 17), "A": ("100004", 30), "S": ("100016", 31), "D": ("100007", 32),
    "Up": ("100052", 103), "Left": ("100050", 105), "Down": ("100051", 108), "Right": ("10004f", 106),
    "I": ("10000c", 23), "J": ("10000d", 36), "K": ("10000e", 37), "L": ("10000f", 38),
    "Space": ("10002c", 57), "T": ("100017", 20), "F": ("100009", 33), "G": ("10000a", 34),
    "H": ("10000b", 35), "Q": ("100014", 16), "E": ("100008", 18), "Tab": ("10002b", 15),
    "R": ("100015", 19), "LShift": ("100200", 42), "RShift": ("102000", 54), "M": ("100010", 50),
    "Esc": ("100029", 1), "PrtSc": ("100046", 99), "F12": ("100045", 88),
}
BTN = {"A": 0x130, "B": 0x131, "X": 0x133, "Y": 0x134, "LB": 0x136, "RB": 0x137,
       "Back": 0x13A, "Start": 0x13B, "Guide": 0x13C, "L3": 0x13D, "R3": 0x13E}

LAYOUT = {
    "sticks": {"left": ("W", "A", "S", "D"), "right": ("Up", "Left", "Down", "Right")},  # up, left, down, right
    "triggers": {"LT": "Q", "RT": "E"},
    "buttons": {"K": "A", "Space": "A", "L": "B", "J": "X", "I": "Y", "Tab": "LB", "R": "RB",
                "LShift": "L3", "RShift": "R3", "M": "Back", "Esc": "Start", "PrtSc": "Guide"},
    "dpad": {"T": "up", "F": "left", "G": "down", "H": "right"},
    "quit": "F12",
}

EVIOCGRAB = 0x40044590
EVIOCGKEY_96 = 0x80604518  # _IOC(READ, 'E', 0x18, 96): bitmap of currently pressed keys


def wait_all_released(fds, timeout=5.0):
    """Grabbing while a key is down leaves it 'stuck' for the desktop, so wait until nothing is held."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        held = False
        for fd in fds:
            buf = bytearray(96)
            fcntl.ioctl(fd, EVIOCGKEY_96, buf)
            held |= any(buf)
        if not held:
            return True
        time.sleep(0.02)
    return False


class PassthroughKeyboard:
    """Virtual keyboard used to forward keys that are not part of the gamepad layout."""

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in range(1, 0x2FF):
            if not 0x100 <= code < 0x160:  # leave joystick/mouse button ranges out
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("<HHHH80sI", 0x06, 0, 0, 1,
                                                       b"Jet75 passthrough keyboard (virtual)", 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def write(self, etype, code, value):
        now = time.time()
        os.write(self.fd, struct.pack("<qqHHi", int(now), int((now % 1) * 1e6), etype, code, value))

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


def keyboard_event_nodes():
    """evdev nodes that belong to the physical Jet75-II (all its interfaces)."""
    nodes = []
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        try:
            vid = int(open(ev + "/device/id/vendor").read(), 16)
            pid = int(open(ev + "/device/id/product").read(), 16)
            name = open(ev + "/device/name").read().strip()
        except OSError:
            continue
        if (vid, pid) == (VID, PID) and "virtual" not in name:
            nodes.append(("/dev/input/" + os.path.basename(ev), name))
    return nodes


def norm(mm, dead, full):
    return max(0.0, min(1.0, (mm - dead) / (full - dead)))


class Mapper:
    def __init__(self, pad, dead, full):
        self.pad, self.dead, self.full = pad, dead, full
        self.depth = {}          # stream code -> mm
        self.held = set()        # key names currently held (from evdev)
        self.by_evcode = {ev: name for name, (_, ev) in KEYS.items()}
        self.mapped_ev = {KEYS[n][1] for n in self.mapped_names()}

    @staticmethod
    def mapped_names():
        names = set(LAYOUT["buttons"]) | set(LAYOUT["dpad"]) | set(LAYOUT["triggers"].values()) | {LAYOUT["quit"]}
        for keys in LAYOUT["sticks"].values():
            names |= set(keys)
        return names

    def mm(self, name):
        return self.depth.get(KEYS[name][0], 0.0)

    def stick(self, up, left, down, right):
        x = norm(self.mm(right), self.dead, self.full) - norm(self.mm(left), self.dead, self.full)
        y = norm(self.mm(down), self.dead, self.full) - norm(self.mm(up), self.dead, self.full)
        r = math.hypot(x, y)
        return (x / r, y / r) if r > 1 else (x, y)

    def state(self):
        lx, ly = self.stick(*LAYOUT["sticks"]["left"])
        rx, ry = self.stick(*LAYOUT["sticks"]["right"])
        lt = norm(self.mm(LAYOUT["triggers"]["LT"]), self.dead, self.full)
        rt = norm(self.mm(LAYOUT["triggers"]["RT"]), self.dead, self.full)
        buttons = {b: False for b in BTN}
        for key, b in LAYOUT["buttons"].items():
            buttons[b] = buttons[b] or key in self.held
        d = {v: k in self.held for k, v in LAYOUT["dpad"].items()}
        hat = (int(d["right"]) - int(d["left"]), int(d["down"]) - int(d["up"]))
        return lx, ly, rx, ry, lt, rt, buttons, hat

    def push(self):
        lx, ly, rx, ry, lt, rt, buttons, hat = s = self.state()
        if self.pad:
            p, ch = self.pad, False
            for code, v in ((ABS_X, lx), (ABS_Y, ly), (ABS_RX, rx), (ABS_RY, ry)):
                ch |= p.emit(EV_ABS, code, int(v * STICK_MAX))
            ch |= p.emit(EV_ABS, ABS_Z, int(lt * 255))
            ch |= p.emit(EV_ABS, ABS_RZ, int(rt * 255))
            ch |= p.emit(EV_ABS, ABS_HAT0X, hat[0])
            ch |= p.emit(EV_ABS, ABS_HAT0Y, hat[1])
            for b, code in BTN.items():
                ch |= p.emit(EV_KEY, code, int(buttons[b]))
            if ch:
                p.emit(EV_SYN, SYN_REPORT, 0)
        return s


def show(s):
    lx, ly, rx, ry, lt, rt, buttons, hat = s
    pressed = " ".join(b for b, v in buttons.items() if v)
    dp = {(0, -1): "↑", (0, 1): "↓", (-1, 0): "←", (1, 0): "→", (-1, -1): "↖", (1, -1): "↗",
          (-1, 1): "↙", (1, 1): "↘"}.get(hat, "·")
    sys.stdout.write("\r LS %+5.2f %+5.2f  RS %+5.2f %+5.2f  LT %3d%% RT %3d%%  D-pad %s  [%-30s] " % (
        lx, ly, rx, ry, lt * 100, rt * 100, dp, pressed))
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deadzone", type=float, default=0.20, help="mm ignored at the top of analog keys (default 0.20)")
    ap.add_argument("--full", type=float, default=3.00, help="mm meaning full deflection (default 3.00)")
    ap.add_argument("--no-grab", action="store_true", help="do not grab the keyboard (mapped keys keep typing)")
    ap.add_argument("--no-uinput", action="store_true", help="no virtual gamepad, only show the state")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until F12 / Ctrl+C)")
    args = ap.parse_args()

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))
    t_end = time.monotonic() + args.duration if args.duration else None

    nodes = keyboard_event_nodes()
    if not nodes:
        sys.exit("no evdev nodes of the Jet75-II found under /sys/class/input")
    dev = Jet75()
    pad = None if args.no_uinput else VirtualPad("Jet75 full gamepad (virtual)")
    passthru = None if args.no_grab else PassthroughKeyboard()
    evfds = []
    mapper = Mapper(pad, args.deadzone, args.full)
    try:
        for path, name in nodes:
            evfds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        if passthru:
            if not wait_all_released(evfds):
                sys.exit("keys are still held after 5 s, not grabbing the keyboard")
            for fd in evfds:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
        print("gamepad: %s | keyboard %s: %s" % (
            "virtual 'Jet75 full gamepad'" if pad else "off",
            "grabbed, other keys passed through" if passthru else "not grabbed",
            ", ".join(p for p, _ in nodes)))
        quit_ev = KEYS[LAYOUT["quit"]][1]
        with dev.depth_stream() as profile:
            print("depth stream on (profile %d). F12 or Ctrl+C to quit." % profile)
            forwarded = False
            while not stop["flag"] and (t_end is None or time.monotonic() < t_end):
                ready, _, _ = select.select([dev.fd] + evfds, [], [], 0.1)
                changed = False
                for fd in ready:
                    if fd == dev.fd:
                        rep = os.read(fd, 64)
                        if rep and rep[0] == 0xA0:
                            p = parse_stream(rep)
                            mapper.depth[p["code"]] = p["depth_mm"]  # precise, lag-free (firmware curve)
                            changed = True
                        continue
                    data = os.read(fd, 24 * 64)
                    for i in range(0, len(data) - 23, 24):
                        _, _, etype, code, value = struct.unpack("<qqHHi", data[i:i + 24])
                        if etype == EV_KEY and code == quit_ev and value == 1:
                            stop["flag"] = True
                        elif etype == EV_KEY and code in mapper.mapped_ev:
                            name = mapper.by_evcode[code]
                            if value == 1:
                                mapper.held.add(name)
                            elif value == 0:
                                mapper.held.discard(name)
                            changed = True
                        elif etype == EV_KEY and passthru:
                            passthru.write(etype, code, value)
                            forwarded = True
                        elif etype == EV_SYN and passthru and forwarded:
                            passthru.write(EV_SYN, SYN_REPORT, 0)
                            forwarded = False
                if changed:
                    show(mapper.push())
            mapper.depth.clear(); mapper.held.clear(); mapper.push()
        print("\ndepth stream off (FuncConfig restored)")
    finally:
        for fd in evfds:
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            os.close(fd)
        if passthru:
            passthru.close()
        if pad:
            pad.close()
        dev.close()


if __name__ == "__main__":
    main()
