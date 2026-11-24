#!/usr/bin/env python3
"""Demo: live key-depth map of the whole keyboard in the terminal (curses).

Turns the depth stream on (FuncConfig byte 7 bit 3), draws the Jet75-II layout and fills
every key according to its current travel. Press q to quit; the stream is always switched
off again on exit (two 64-byte flash writes on the keyboard per start/stop).

    python3 demos/terminal_depth_map.py
"""
import curses, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from jet75 import Jet75, parse_stream  # noqa: E402

H = lambda u: "1000%02x" % u
M = lambda b: "10%02x00" % b
ROWS = [
    [("Esc", H(0x29))] + [("F%d" % i, H(0x39 + i)) for i in range(1, 13)] + [("PrtSc", H(0x46)), ("Del", H(0x4c))],
    [("`", H(0x35))] + [(str(n % 10), H(0x1d + n)) for n in range(1, 11)] +
    [("-", H(0x2d)), ("=", H(0x2e)), ("Bksp", H(0x2a), 2), ("PgUp", H(0x4b))],
    [("Tab", H(0x2b), 1.5)] + [(c, H(u)) for c, u in zip("QWERTYUIOP", (0x14, 0x1a, 0x08, 0x15, 0x17, 0x1c, 0x18, 0x0c, 0x12, 0x13))] +
    [("[", H(0x2f)), ("]", H(0x30)), ("\\", H(0x31), 1.5), ("PgDn", H(0x4e))],
    [("Caps", H(0x39), 1.75)] + [(c, H(u)) for c, u in zip("ASDFGHJKL", (0x04, 0x16, 0x07, 0x09, 0x0a, 0x0b, 0x0d, 0x0e, 0x0f))] +
    [(";", H(0x33)), ("'", H(0x34)), ("Enter", H(0x28), 2.25)],
    [("Shift", M(0x02), 2.25)] + [(c, H(u)) for c, u in zip("ZXCVBNM", (0x1d, 0x1b, 0x06, 0x19, 0x05, 0x11, 0x10))] +
    [(",", H(0x36)), (".", H(0x37)), ("/", H(0x38)), ("Shift", M(0x20), 1.75), ("Up", H(0x52))],
    [("Ctrl", M(0x01), 1.25), ("Win", M(0x08), 1.25), ("Alt", M(0x04), 1.25), ("Space", H(0x2c), 7.25),
     ("Fn", "f0ff01"), ("Ctrl", M(0x10)), ("Lt", H(0x50)), ("Dn", H(0x51)), ("Rt", H(0x4f))],
]
UNIT = 6  # characters per key unit


def draw(scr, state, info):
    scr.erase()
    scr.addstr(0, 0, info[:curses.COLS - 1])
    y0 = 2
    for r, row in enumerate(ROWS):
        x = 0
        for item in row:
            name, code = item[0], item[1]
            w = int(round((item[2] if len(item) > 2 else 1) * UNIT))
            s = state.get(code, {})
            mm, mx = s.get("mm", 0.0), s.get("max", 3.41)
            frac = max(0.0, min(1.0, mm / mx))
            y = y0 + r * 3
            if x + w >= curses.COLS or y + 2 >= curses.LINES:
                continue
            attr = curses.A_REVERSE if s.get("down") else curses.A_NORMAL
            scr.addstr(y, x, name[:w - 1].ljust(w - 1), attr | curses.A_BOLD)
            filled = int(round(frac * (w - 1)))
            color = curses.color_pair(1 if frac < 0.5 else 2 if frac < 0.85 else 3)
            scr.addstr(y + 1, x, "█" * filled, color)
            scr.addstr(y + 1, x + filled, "·" * (w - 1 - filled))
            if "mm" in s:
                scr.addstr(y + 2, x, ("%.2f" % mm)[:w - 1])
            x += w
    scr.addstr(min(curses.LINES - 1, y0 + len(ROWS) * 3 + 1), 0, "q - quit")
    scr.refresh()


def run(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    for i, c in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED), 1):
        curses.init_pair(i, c, -1)
    scr.nodelay(True)
    dev = Jet75()
    state, count = {}, [0]

    def on_stream(rep):
        p = parse_stream(rep)
        state[p["code"]] = {"mm": p["depth_mm"], "max": p["max_mm"] or 3.41, "down": p["state"] == 1}
        count[0] += 1

    dev.stream_handler = on_stream
    try:
        fw, build = dev.info()
        with dev.depth_stream() as profile:
            info = "Jet75-II fw %s, profile %d - press keys" % (fw, profile)
            last = 0
            while scr.getch() not in (ord("q"), ord("Q")):
                rep = dev.read_report(0.02)
                if rep and rep[0] == 0xA0:
                    on_stream(rep)
                if time.monotonic() - last > 1 / 30:
                    draw(scr, state, info + "   packets: %d" % count[0])
                    last = time.monotonic()
    finally:
        dev.close()


if __name__ == "__main__":
    curses.wrapper(run)
    print("debugMode off, FuncConfig restored")
