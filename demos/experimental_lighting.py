#!/usr/bin/env python3
"""Demo: experimental reactive lighting effects (RAM-only live colours, no flash wear).

    python3 demos/experimental_lighting.py --mode fire     # flames rise from pressed keys; held keys keep burning
    python3 demos/experimental_lighting.py --mode sparks   # presses throw particles that fall and bounce
    python3 demos/experimental_lighting.py --mode life     # Conway's Game of Life seeded by your typing
    python3 demos/experimental_lighting.py --mode tempo    # board colour follows typing speed, comets per press

Like reactive_lighting.py: key presses come from the normal Linux key events (not grabbed),
colours go out as 0xDD live colours, and the keyboard returns to its own effect when the
program stops. The Fn key is handled inside the keyboard and cannot be lit.
"""

import argparse
import math
import os
import random
import select
import signal
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
sys.path.insert(0, HERE)
from jet75 import KEY_COUNT, Jet75  # noqa: E402

from full_gamepad import keyboard_event_nodes  # noqa: E402
from reactive_lighting import evcode_for, key_positions, rgb  # noqa: E402

COLS, ROWS_N = 32, 6  # simulation grid: half-unit columns, one cell per keyboard row
FIRE = [
    (0, 0, 0),
    (40, 0, 0),
    (120, 10, 0),
    (200, 40, 0),
    (255, 90, 0),
    (255, 160, 20),
    (255, 220, 90),
    (255, 255, 200),
]


def fire_color(h):
    h = max(0.0, min(1.0, h)) * (len(FIRE) - 1)
    i = int(h)
    if i >= len(FIRE) - 1:
        return FIRE[-1]
    f = h - i
    a, b = FIRE[i], FIRE[i + 1]
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


class Effect:
    def __init__(self, keys):
        self.keys = keys  # index -> dict(x, y, w, cells)
        self.held = set()

    def press(self, i, now):
        pass

    def release(self, i, now):
        pass

    def frame(self, now, dt):
        return [(0, 0, 0)] * KEY_COUNT


class Fire(Effect):
    def __init__(self, keys):
        super().__init__(keys)
        self.heat = [[0.0] * COLS for _ in range(ROWS_N)]

    def press(self, i, now):
        k = self.keys[i]
        for cx in k["cells"]:
            self.heat[k["row"]][cx] = 1.0
            if k["row"] > 0:
                self.heat[k["row"] - 1][cx] = max(self.heat[k["row"] - 1][cx], 0.8)

    def frame(self, now, dt):
        h = self.heat
        for i in self.held:  # held keys keep feeding the fire
            k = self.keys[i]
            for cx in k["cells"]:
                h[k["row"]][cx] = max(h[k["row"]][cx], 0.85 + 0.15 * random.random())
        new = [row[:] for row in h]
        cool = 1.0 - math.exp(-dt * 2.2)
        for y in range(ROWS_N):
            for x in range(COLS):
                below = (
                    h[y + 1][min(COLS - 1, max(0, x + random.randint(-1, 1)))]
                    if y + 1 < ROWS_N
                    else 0.0
                )
                rise = below * (
                    0.93 - 0.1 * random.random()
                )  # heat moves up with lateral flicker
                new[y][x] = max(
                    h[y][x] * (1.0 - cool * 1.6),
                    rise * (0.55 + 0.45 * min(1.0, dt * 30)),
                )
        self.heat = new
        out = [(0, 0, 0)] * KEY_COUNT
        for i, k in self.keys.items():
            out[i] = fire_color(max(new[k["row"]][cx] for cx in k["cells"]))
        return out


class Sparks(Effect):
    def __init__(self, keys):
        super().__init__(keys)
        self.p = []  # [x, y, vx, vy, life, hue]
        self.hue = random.random()

    def press(self, i, now):
        k = self.keys[i]
        self.hue = (self.hue + 0.11) % 1.0
        for _ in range(10):
            a, s = (
                random.uniform(math.pi * 1.05, math.pi * 1.95),
                random.uniform(5, 11),
            )  # mostly upwards
            self.p.append(
                [
                    k["x"],
                    k["y"],
                    math.cos(a) * s,
                    math.sin(a) * s,
                    1.0,
                    (self.hue + random.uniform(-0.05, 0.05)) % 1,
                ]
            )

    def frame(self, now, dt):
        alive = []
        for q in self.p:
            q[3] += 14.0 * dt  # gravity towards the bottom row
            q[0] += q[2] * dt
            q[1] += q[3] * dt
            if q[1] > 5.3:
                q[1], q[3] = 5.3, -abs(q[3]) * 0.55
            if q[1] < -0.3:
                q[1], q[3] = -0.3, abs(q[3]) * 0.5
            if q[0] < 0:
                q[0], q[2] = 0, abs(q[2]) * 0.7
            if q[0] > 16:
                q[0], q[2] = 16, -abs(q[2]) * 0.7
            q[4] -= dt / 1.6
            if q[4] > 0:
                alive.append(q)
        self.p = alive[-300:]
        out = [(0, 0, 0)] * KEY_COUNT
        for i, k in self.keys.items():
            best, hue = 0.0, 0.0
            for x, y, _, _, life, h in self.p:
                d2 = (x - k["x"]) ** 2 + ((y - k["y"]) * 1.3) ** 2
                if d2 < 2.0:
                    v = life * math.exp(-d2 / 0.25)
                    if v > best:
                        best, hue = v, h
            for j in self.held:  # held keys glow softly
                if j == i:
                    best, hue = max(best, 0.35), self.hue
            if best > 0.02:
                out[i] = rgb(hue, 0.9, best)
        return out


class Life(Effect):
    W, H, STEP = 16, 6, 0.16

    def __init__(self, keys):
        super().__init__(keys)
        self.age = [[0] * self.W for _ in range(self.H)]
        self.t = 0.0

    def press(self, i, now):
        k = self.keys[i]
        cx, cy = int(k["x"]) % self.W, k["row"]
        for dx, dy in random.choice(
            [
                [(0, 0), (1, 0), (-1, 0)],
                [(0, 0), (1, 0), (0, 1), (1, 1)],
                [(0, 0), (1, 1), (-1, 1), (0, -1)],
                [(0, 0), (0, 1), (0, -1)],
            ]
        ):
            self.age[(cy + dy) % self.H][(cx + dx) % self.W] = max(
                1, self.age[(cy + dy) % self.H][(cx + dx) % self.W]
            )

    def step(self):
        a, W, H = self.age, self.W, self.H
        new = [[0] * W for _ in range(H)]
        for y in range(H):
            for x in range(W):
                n = sum(
                    1
                    for dy in (-1, 0, 1)
                    for dx in (-1, 0, 1)
                    if (dx or dy) and a[(y + dy) % H][(x + dx) % W]
                )
                if a[y][x] and n in (2, 3):
                    new[y][x] = a[y][x] + 1
                elif not a[y][x] and n == 3:
                    new[y][x] = 1
        self.age = new

    def frame(self, now, dt):
        self.t += dt
        while self.t >= self.STEP:
            self.t -= self.STEP
            self.step()
        out = [(0, 0, 0)] * KEY_COUNT
        for i, k in self.keys.items():
            g = self.age[k["row"]][int(k["x"]) % self.W]
            if g:
                out[i] = (255, 255, 255)  # living cells are always white
            elif i in self.held:
                out[i] = (40, 40, 40)
        return out


class Tempo(Effect):
    def __init__(self, keys):
        super().__init__(keys)
        self.rate, self.last, self.comets = 0.0, None, []

    def press(self, i, now):
        if self.last is not None:
            inst = 1.0 / max(0.03, now - self.last)
            self.rate = self.rate * 0.8 + inst * 0.2
        self.last = now
        k = self.keys[i]
        self.comets.append([k["x"], k["row"], random.choice((-1, 1)) * 18.0, now])

    def frame(self, now, dt):
        if self.last is not None and now - self.last > 1.0:
            self.rate *= math.exp(-dt * 1.5)  # slowly calm down when typing stops
        f = min(1.0, self.rate / 9.0)  # ~9 keys/s (~100 wpm) = full red
        base = rgb(0.66 * (1 - f), 1.0, 0.08 + 0.35 * f)
        out = [base] * KEY_COUNT
        alive = []
        for c in self.comets:
            c[0] += c[2] * dt
            if -4 < c[0] < 20:
                alive.append(c)
        self.comets = alive
        for i, k in self.keys.items():
            v = 0.0
            for x, row, vx, _ in self.comets:
                if row == k["row"]:
                    d = (x - k["x"]) * (1 if vx > 0 else -1)  # distance behind the head
                    if -0.5 < d < 3.5:
                        v = max(v, 1.0 - max(0.0, d) / 3.5)
            if i in self.held:
                v = max(v, 1.0)
            if v > 0:
                b = base
                out[i] = tuple(int(b[j] + (255 - b[j]) * v) for j in range(3))
        return out


MODES = {"fire": Fire, "sparks": Sparks, "life": Life, "tempo": Tempo}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--mode", choices=list(MODES), default="fire")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument(
        "--duration",
        type=float,
        default=0,
        help="stop after N seconds (0 = until Ctrl+C)",
    )
    args = ap.parse_args()

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *a: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *a: stop.update(flag=True))

    dev = Jet75()
    layer0 = dev.get_keymap(dev.active_profile(), 0)[:KEY_COUNT]
    pos_by_code = key_positions()
    widths = {}
    from terminal_depth_map import ROWS

    for row in ROWS:
        for item in row:
            widths[item[1]] = item[2] if len(item) > 2 else 1
    keys, ev2idx = {}, {}
    for i, code in enumerate(layer0):
        h = bytes(code).hex()
        if h not in pos_by_code:
            continue
        x, y = pos_by_code[h]
        w = widths.get(h, 1)
        cells = sorted(
            {
                min(COLS - 1, max(0, int((x - w / 2) * 2 + j)))
                for j in range(max(1, int(round(w * 2))))
            }
        )
        keys[i] = {"x": x, "y": float(y), "row": y, "w": w, "cells": cells}
        ev = evcode_for(code)
        if ev is not None:
            ev2idx[ev] = i
    fds = [os.open(p, os.O_RDONLY | os.O_NONBLOCK) for p, _ in keyboard_event_nodes()]
    if not fds:
        sys.exit("no evdev nodes of the Jet75-II found")

    fx = MODES[args.mode](keys)
    t_end = time.monotonic() + args.duration if args.duration else None
    period, last, next_frame, frames = (
        1.0 / args.fps,
        time.monotonic(),
        time.monotonic(),
        0,
    )
    print(
        "experimental lighting '%s' running (%d keys). Ctrl+C to stop."
        % (args.mode, len(keys))
    )
    try:
        while not stop["flag"] and (t_end is None or time.monotonic() < t_end):
            ready, _, _ = select.select(
                fds, [], [], max(0.0, next_frame - time.monotonic())
            )
            now = time.monotonic()
            for fd in ready:
                data = os.read(fd, 24 * 64)
                for k in range(0, len(data) - 23, 24):
                    _, _, etype, code, value = struct.unpack("<qqHHi", data[k : k + 24])
                    if etype == 1 and code in ev2idx and ev2idx[code] in keys:
                        i = ev2idx[code]
                        if value == 1:
                            fx.held.add(i)
                            fx.press(i, now)
                        elif value == 0:
                            fx.held.discard(i)
                            fx.release(i, now)
            if now < next_frame:
                continue
            next_frame = now + period
            dev.live_colors(fx.frame(now, now - last))
            last, frames = now, frames + 1
    finally:
        for fd in fds:
            os.close(fd)
        dev.close()
    print(
        "stopped after %d frames; the keyboard returns to its own effect by itself"
        % frames
    )


if __name__ == "__main__":
    main()
