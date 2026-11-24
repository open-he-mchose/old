#!/usr/bin/env python3
"""Demo: custom reactive lighting driven by key presses (RAM-only colours, no flash wear).

    python3 demos/reactive_lighting.py                 # fade: pressed keys flash and fade out
    python3 demos/reactive_lighting.py --mode ripple   # a wave spreads over the layout from each press
    python3 demos/reactive_lighting.py --mode heat     # heat map of how often each key is pressed

Colours are sent with command 0xDD ("live" colours, RAM only). When the program stops sending,
the keyboard returns to its own lighting effect by itself after a short timeout.

Why presses and not depth: while the host sends anything, the keyboard pauses its depth stream
(~0.6 s after the last host packet, docs/protocol.md "Host traffic stops the depth stream"), so
depth and live colours cannot be used at the same time. Presses are read from the normal Linux
key events instead (/dev/input, group 'input'); the keyboard itself is not grabbed.
The Fn key is handled inside the keyboard and cannot be lit by this demo.
"""
import argparse, colorsys, math, os, select, signal, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
from jet75 import Jet75, KEY_COUNT  # noqa: E402
from full_gamepad import keyboard_event_nodes  # noqa: E402
from terminal_depth_map import ROWS  # noqa: E402  (physical layout, used for ripple distances)

# HID usage -> Linux keycode (drivers/hid/hid-input.c hid_keyboard[])
HID2EV = {0x04: 30, 0x05: 48, 0x06: 46, 0x07: 32, 0x08: 18, 0x09: 33, 0x0A: 34, 0x0B: 35, 0x0C: 23,
          0x0D: 36, 0x0E: 37, 0x0F: 38, 0x10: 50, 0x11: 49, 0x12: 24, 0x13: 25, 0x14: 16, 0x15: 19,
          0x16: 31, 0x17: 20, 0x18: 22, 0x19: 47, 0x1A: 17, 0x1B: 45, 0x1C: 21, 0x1D: 44,
          0x28: 28, 0x29: 1, 0x2A: 14, 0x2B: 15, 0x2C: 57, 0x2D: 12, 0x2E: 13, 0x2F: 26, 0x30: 27,
          0x31: 43, 0x33: 39, 0x34: 40, 0x35: 41, 0x36: 51, 0x37: 52, 0x38: 53, 0x39: 58,
          0x44: 87, 0x45: 88, 0x46: 99, 0x47: 70, 0x48: 119, 0x49: 110, 0x4A: 102, 0x4B: 104,
          0x4C: 111, 0x4D: 107, 0x4E: 109, 0x4F: 106, 0x50: 105, 0x51: 108, 0x52: 103}
HID2EV.update({0x1E + i: 2 + i for i in range(10)})          # 1..9, 0
HID2EV.update({0x3A + i: 59 + i for i in range(10)})         # F1..F10
MOD2EV = {0x01: 29, 0x02: 42, 0x04: 56, 0x08: 125, 0x10: 97, 0x20: 54, 0x40: 100, 0x80: 126}


def evcode_for(code):
    t, c1, c2 = code
    if t == 0x10 and c1 == 0:
        return HID2EV.get(c2)
    if t == 0x10 and c2 == 0:
        return MOD2EV.get(c1)
    return None


def key_positions():
    """layer-0 code (hex) -> (x, y) centre in key units, from the demo layout."""
    pos = {}
    for y, row in enumerate(ROWS):
        x = 0.0
        for item in row:
            w = item[2] if len(item) > 2 else 1
            pos[item[1]] = (x + w / 2, y)
            x += w
    return pos


def rgb(h, s, v):
    return tuple(int(255 * c) for c in colorsys.hsv_to_rgb(h % 1.0, s, max(0.0, min(1.0, v))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["fade", "ripple", "heat"], default="fade")
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    args = ap.parse_args()

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))

    dev = Jet75()
    layer0 = dev.get_keymap(dev.active_profile(), 0)[:KEY_COUNT]
    ev2idx = {}
    for i, code in enumerate(layer0):
        ev = evcode_for(code)
        if ev is not None:
            ev2idx[ev] = i
    pos_by_code = key_positions()
    pos = {i: pos_by_code.get(bytes(code).hex()) for i, code in enumerate(layer0)}
    fds = [os.open(p, os.O_RDONLY | os.O_NONBLOCK) for p, _ in keyboard_event_nodes()]
    if not fds:
        sys.exit("no evdev nodes of the Jet75-II found")

    last_press = {}            # index -> time of last press (fade)
    hue_of = {}                # index -> hue of last press
    waves = []                 # (t0, x, y, hue) for ripple
    heat = [0] * KEY_COUNT
    hue_step = [0.0]
    t_end = time.monotonic() + args.duration if args.duration else None
    period, next_frame, frames = 1.0 / args.fps, time.monotonic(), 0
    print("reactive lighting '%s' running, %d keys mapped. Ctrl+C to stop." % (args.mode, len(ev2idx)))
    try:
        while not stop["flag"] and (t_end is None or time.monotonic() < t_end):
            ready, _, _ = select.select(fds, [], [], max(0.0, next_frame - time.monotonic()))
            now = time.monotonic()
            for fd in ready:
                data = os.read(fd, 24 * 64)
                for k in range(0, len(data) - 23, 24):
                    _, _, etype, code, value = struct.unpack("<qqHHi", data[k:k + 24])
                    if etype == 1 and value == 1 and code in ev2idx:
                        i = ev2idx[code]
                        hue_step[0] += 0.13
                        last_press[i], hue_of[i] = now, hue_step[0]
                        heat[i] += 1
                        if pos.get(i):
                            waves.append((now, pos[i][0], pos[i][1], hue_step[0]))
            if now < next_frame:
                continue
            next_frame = now + period
            colors = [(0, 0, 0)] * KEY_COUNT
            if args.mode == "fade":
                for i, t0 in last_press.items():
                    a = 1.0 - (now - t0) / 0.7
                    if a > 0:
                        colors[i] = rgb(hue_of[i], 1.0, a)
            elif args.mode == "ripple":
                waves[:] = [w for w in waves if now - w[0] < 1.2]
                for i in range(KEY_COUNT):
                    if not pos.get(i):
                        continue
                    x, y = pos[i]
                    best = (0.0, 0.0)
                    for t0, wx, wy, h in waves:
                        r = 14.0 * (now - t0)                       # wave radius, keys per second
                        d = abs(math.hypot(x - wx, (y - wy) * 1.2) - r)
                        v = max(0.0, 1.0 - d / 1.2) * (1.0 - (now - t0) / 1.2)
                        if v > best[0]:
                            best = (v, h)
                    if best[0] > 0:
                        colors[i] = rgb(best[1], 1.0, best[0])
            else:  # heat
                top = max(heat) or 1
                for i in range(KEY_COUNT):
                    if heat[i]:
                        f = heat[i] / top
                        colors[i] = rgb(0.66 * (1 - f), 1.0, 0.25 + 0.75 * f)  # blue -> red
            dev.live_colors(colors)
            frames += 1
    finally:
        for fd in fds:
            os.close(fd)
        dev.close()
    if args.mode == "heat" and any(heat):
        top = sorted(((n, i) for i, n in enumerate(heat) if n), reverse=True)[:10]
        from jet75 import key_name
        print("most pressed:", ", ".join("%s %d" % (key_name(*layer0[i]), n) for n, i in top))
    print("stopped after %d frames; the keyboard returns to its own effect by itself" % frames)


if __name__ == "__main__":
    main()
