"""Minimal MCHOSE Jet75-II protocol library (Linux hidraw, stdlib only).

Protocol reference: docs/protocol.md. Only commands verified there are used.
"""
import bisect, glob, json, os, select, time
from contextlib import contextmanager

VID, PID = 0x41E4, 0x211A
CMD_INFO, CMD_BASE, CMD_GET_FUNC, CMD_SET_FUNC = 0x03, 0x04, 0x05, 0x06
CMD_GET_DEFAULT_KEYMAP, CMD_GET_KEYMAP, CMD_SET_KEY = 0x07, 0x08, 0x09
CMD_GET_COLORS, CMD_SET_COLOR, CMD_LIVE_COLORS = 0x0A, 0x0B, 0xDD
CMD_GET_TRIGGER, CMD_SET_TRIGGER = 0xA0, 0xA1
MAX_KEYS = 128
KEY_COUNT = 80          # physical keys on the Jet75-II (matrix slots 0..79)
PROFILES, LAYERS = 4, 4

# Lighting effect ids (FuncConfig byte 8), names from the web driver (glw effect table).
EFFECTS = {0: "static", 1: "auroramapping", 2: "shadowsteps", 3: "tranquility", 4: "pulsation",
           5: "flowerdance", 6: "tidalrhythm", 7: "surgingtide", 8: "colorfulfountain",
           9: "galaxyroaming", 10: "spiral", 11: "surgerhythm", 12: "ocean", 13: "rippledance",
           14: "ripple", 15: "instantflash", 16: "matrixshadows", 17: "melodypiano", 18: "flowdance",
           19: "raining", 20: "stars", 21: "fireworks", 22: "rippleflow", 23: "swordarray", 255: "off"}
# Trigger keyMode (web driver: rtFeatureOn = keyMode > 0, continuouslyRtSwitchOn = keyMode == 2)
KEY_MODES = {0: "normal", 1: "rt", 2: "rt-continuous"}

# Advanced keys: a key's keymap code (layer-specific) points to a slot of a per-profile table.
CMD_GET_DKS, CMD_SET_DKS, CMD_GET_MT, CMD_SET_MT, CMD_GET_TGL, CMD_SET_TGL = 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7
ADV_SLOTS = 32
ADV_TYPES = {0x90: "dks", 0x91: "tgl", 0x92: "mt", 0x93: "rs", 0x94: "socd", 0x95: "lt"}
ADV_GROUP = {"dks": "dks", "tgl": "tgl", "mt": "mt", "rs": "mt", "socd": "mt", "lt": "mt"}  # rs/socd/lt use MT slots
SOCD_MODES = {0: "last-input", 1: "key1-priority", 2: "key2-priority", 3: "cancel"}  # trigger 'priority' nibble


def dks_mask(ds, de, us, ue):
    """Per-action phase mask, same as the web driver's DKS encoder (Y), including its normalisation.
    ds/de/us/ue = how many phases an action started at press-start / bottom / lift-from-bottom /
    full-lift stays active (0 = not used). Phases: 0 press past shallow point, 1 press past deep
    point, 2 lift from the deep point, 3 lift past the shallow point."""
    a, r, o, s = ds, de, us, ue
    if ds == 4:
        a, r, o = 3, 3, 2
    if ds == 3:
        a, r = 3, 2
    if de == 3:
        o = 2
    th = lambda n: (n > 0) | (n > 1) << 1 | (n > 2) << 2
    return th(a) & 7 | (th(r) << 3) & 56 | (th(o) << 6) & 448 | (th(s) << 9) & 512


def dks_unmask(v):
    """Inverse of dks_mask (web driver decoder m)."""
    a0, a1, a2, a3 = v & 7, v >> 3 & 7, v >> 6 & 7, v >> 9 & 1
    hi = lambda b: 3 if b & 4 else 2 if b & 2 else 1 if b & 1 else 0
    ds, de, us, ue = hi(a0), hi(a1), (2 if a2 & 2 else 1 if a2 & 1 else 0), a3
    if ds == 3:
        if de == 3:
            ds, de, us = 4, 0, 0
        else:
            de = 0
    elif de == 3:
        us = 0
    return ds, de, us, ue


def encode_dks(points_mm, actions):
    """points_mm: (shallow, deep) in mm (0.1 mm resolution); actions: up to 4 of
    ((type, code1, code2), (ds, de, us, ue))."""
    sh, dp = int(round(points_mm[0] * 10)), int(round(points_mm[1] * 10))
    if not 1 <= sh < dp <= 34:
        raise ValueError("DKS points must satisfy 0.1 <= shallow < deep <= 3.4 mm")
    e = bytearray(24)
    e[0:4] = bytes([sh, dp, dp, sh])
    for n, (code, spans) in enumerate(actions[:4]):
        b = 5 * n + 4
        e[b:b + 3] = bytes(code)
        m = dks_mask(*spans)
        e[b + 3], e[b + 4] = m & 0xFF, m >> 8
    return bytes(e)


def decode_dks(e):
    acts = []
    for n in range(4):
        b = 5 * n + 4
        code = tuple(e[b:b + 3])
        m = e[b + 3] | e[b + 4] << 8
        if code != (0, 0, 0) or m:
            acts.append((code, dks_unmask(m)))
    return {"points_mm": [x / 10 for x in e[0:4]], "actions": acts}

# Travel curves extracted from the firmware (tools/extract_curves.py).
_CURVES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jet75_curves.json")
try:
    _CURVES = {int(k): v for k, v in json.load(open(_CURVES_FILE))["types"].items()}
    for _v in _CURVES.values():  # enforce monotonic order for bisect (V116 type 7 has one bad entry)
        _m, _fixed = 0, []
        for _x in _v["curve"]:
            _m = max(_m, _x)
            _fixed.append(_m)
        _v["mono"] = _fixed
except OSError:
    _CURVES = {}

HID_NAMES = {
    0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D", 0x08: "E", 0x09: "F", 0x0A: "G", 0x0B: "H",
    0x0C: "I", 0x0D: "J", 0x0E: "K", 0x0F: "L", 0x10: "M", 0x11: "N", 0x12: "O", 0x13: "P",
    0x14: "Q", 0x15: "R", 0x16: "S", 0x17: "T", 0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X",
    0x1C: "Y", 0x1D: "Z", 0x1E: "1", 0x1F: "2", 0x20: "3", 0x21: "4", 0x22: "5", 0x23: "6",
    0x24: "7", 0x25: "8", 0x26: "9", 0x27: "0", 0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace",
    0x2B: "Tab", 0x2C: "Space", 0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\",
    0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/", 0x39: "CapsLock",
    0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4", 0x3E: "F5", 0x3F: "F6", 0x40: "F7",
    0x41: "F8", 0x42: "F9", 0x43: "F10", 0x44: "F11", 0x45: "F12", 0x46: "PrtSc",
    0x47: "ScrLk", 0x48: "Pause", 0x49: "Insert", 0x4A: "Home", 0x4B: "PgUp", 0x4C: "Delete",
    0x4D: "End", 0x4E: "PgDn", 0x4F: "Right", 0x50: "Left", 0x51: "Down", 0x52: "Up",
}
MOD_NAMES = {0x01: "LCtrl", 0x02: "LShift", 0x04: "LAlt", 0x08: "LGui",
             0x10: "RCtrl", 0x20: "RShift", 0x40: "RAlt", 0x80: "RGui"}


def key_name(t, c1, c2):
    if t == 0x10 and c1 == 0 and c2 in HID_NAMES:
        return HID_NAMES[c2]
    if t == 0x10 and c2 == 0 and c1 in MOD_NAMES:
        return MOD_NAMES[c1]
    if (t, c1, c2) == (0xF0, 0xFF, 0x01):
        return "Fn"
    return "key[%02x %02x %02x]" % (t, c1, c2)


def find_config_hidraw():
    """hidraw node of the vendor config interface (report descriptor starts 05 01 09 00)."""
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(node + "/device/uevent").read()
            rdesc = open(node + "/device/report_descriptor", "rb").read()
        except OSError:
            continue
        if ("HID_ID=0003:%08X:%08X" % (VID, PID)) in uevent and rdesc[:4] == b"\x05\x01\x09\x00":
            return "/dev/" + os.path.basename(node)
    return None


REST_MARGIN = 20  # filtered ADC within this of the rest value = key at rest


def precise_depth(norm, adc, adc_rest, switch_type=0):
    """Depth in mm (float) from the normalized sensor value (stream bytes 4-5).

    Same curve as the firmware but interpolated between 0.01 mm steps, and without the
    firmware's travel-field lag (that field is only refreshed while the key moves fast).
    The normalized value itself is not refreshed once the key is back at rest, so the raw
    ADC decides 'at rest'. Returns None if no curve file is available.
    """
    c = _CURVES.get(switch_type)
    if c is None:
        return None
    if adc_rest - adc < REST_MARGIN:
        return 0.0
    curve = c["mono"]
    if norm < curve[9]:  # firmware ignores everything below the 0.10 mm entry
        return 0.0
    i = bisect.bisect_right(curve, norm)
    if i >= len(curve):
        return len(curve) / 100.0
    lo, hi = curve[i - 1], curve[i]
    return (i + (norm - lo) / max(1, hi - lo)) / 100.0


def parse_stream(rep, switch_type=0):
    """Decode a 0xA0 depth-stream packet (see docs/protocol.md, 'Stream packet').

    travel_mm = firmware field (0.01 mm steps, lags on release); depth_mm = precise value.
    """
    be = lambda i: rep[i] << 8 | rep[i + 1]
    p = {
        "key": key_name(rep[1], rep[2], rep[3]), "code": bytes(rep[1:4]).hex(),
        "travel_mm": be(6) / 100.0, "state": rep[9], "calib": rep[10],
        "max_mm": be(14) / 100.0, "adc": be(16), "adc_rest": be(18) * 4,
        "norm": be(4), "b8": rep[8], "noise_peak": be(20), "hold": be(22),
    }
    d = precise_depth(p["norm"], p["adc"], p["adc_rest"], switch_type)
    p["depth_mm"] = d if d is not None else (0.0 if p["adc_rest"] - p["adc"] < REST_MARGIN else p["travel_mm"])
    return p


def encode_trigger(t):
    """Inverse of decode_trigger (web driver eu). Distances in mm, 0.01 mm resolution."""
    v = lambda mm: max(0, int(round(mm * 100)) - 1)
    act, rtp, rtr = v(t["actuation_mm"]), v(t["rtPress_mm"]), v(t["rtRelease_mm"])
    if max(act, rtp, rtr) > 0x1FF:
        raise ValueError("distance out of range (max 5.12 mm)")
    e = bytearray(8)
    e[0] = 0xA0 | (t["switchType"] & 15)
    e[1] = ((t.get("priority", 0) & 15) << 4 | (t["keyMode"] & 15)) if t.get("priority") else t["keyMode"] & 15
    e[2] = act & 0xFF
    e[3] = (act >> 8) & 1 | (t.get("pressPrecision", 0) & 3) << 3 | (t.get("releasePrecision", 0) & 3) << 1
    e[4] = rtp & 0xFF
    e[5] = (rtp >> 8) & 1 | (t.get("pressDeadzone", 0) & 127) << 1
    e[6] = rtr & 0xFF
    e[7] = (rtr >> 8) & 1 | (t.get("releaseDeadzone", 0) & 127) << 1
    return bytes(e)


def decode_light(fc):
    return {"effect": fc[8], "effect_name": EFFECTS.get(fc[8], "?"), "brightness": fc[9],
            "speed": 4 - fc[10], "direction": fc[11], "customColorDisabled": fc[12],
            "color": "#%02x%02x%02x" % tuple(fc[14:17])}


def parse_color(s):
    s = s.lstrip("#")
    if len(s) != 6:
        raise ValueError("colour must be #rrggbb")
    return bytes.fromhex(s)


def decode_funcconfig(fc):
    """FuncConfig fields (web driver serializers ed/er)."""
    return {
        "macMode": fc[1] & 15, "reportRate": fc[4] & 15, "tickRate": fc[4] >> 4, "deadZone": fc[5],
        "lockWin": fc[6] & 1, "lockAltTab": fc[6] >> 1 & 1, "lockAltF4": fc[6] >> 2 & 1,
        "tachyonMode": fc[7] & 1, "bottomRapidTrigger": fc[7] >> 1 & 1, "travelCheck": fc[7] >> 2 & 1,
        "debugMode": fc[7] >> 3 & 1, "debounceLevel": fc[7] >> 5 & 7, "rtsmart": fc[20],
        "light": {"effect": fc[8], "brightness": fc[9], "speed": 4 - fc[10], "direction": fc[11],
                  "customColorDisabled": fc[12], "color": "#%02x%02x%02x" % tuple(fc[14:17])},
        "sideLight": {"effect": fc[24], "brightness": fc[25], "speed": 4 - fc[26],
                      "color": "#%02x%02x%02x" % tuple(fc[29:32])},
        "sleepTime": fc[35], "calibrationRgb": "#%02x%02x%02x" % tuple(fc[40:43]),
    }


def decode_trigger(e):
    """8-byte per-key trigger entry (web driver eh). Distances in mm."""
    v9 = lambda lo, hi: (lo | (hi & 1) << 8) + 1
    return {
        "switchType": e[0] & 15, "keyMode": e[1] & 15, "priority": e[1] >> 4,
        "actuation_mm": v9(e[2], e[3]) / 100.0,
        "pressPrecision": e[3] >> 3 & 3, "releasePrecision": e[3] >> 1 & 3,
        "rtPress_mm": v9(e[4], e[5]) / 100.0, "rtRelease_mm": v9(e[6], e[7]) / 100.0,
        "pressDeadzone": e[5] >> 1 & 127, "releaseDeadzone": e[7] >> 1 & 127,
    }


class Jet75:
    def __init__(self, path=None):
        path = path or find_config_hidraw()
        if not path:
            raise FileNotFoundError("Jet75-II config interface not found (VID %04x PID %04x)" % (VID, PID))
        self.path = path
        self.fd = os.open(path, os.O_RDWR)
        self.stream_handler = None

    def close(self):
        os.close(self.fd)

    # --- transport -------------------------------------------------------
    @staticmethod
    def packet(cmd, offset, size, data=b""):
        body = bytes([size, offset & 0xFF, (offset >> 8) & 0xFF, 0]) + bytes(data)
        return (bytes([0x55, cmd, 0x00, sum(body) & 0xFF]) + body).ljust(64, b"\x00")

    def read_report(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 64) if r else None

    def transact(self, cmd, offset, size, data=b"", timeout=1.0):
        os.write(self.fd, b"\x00" + self.packet(cmd, offset, size, data))  # report id 0
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("no reply to cmd 0x%02x offset %d" % (cmd, offset))
            rep = self.read_report(left)
            if rep is None:
                continue
            if rep[0] == 0xA0 and self.stream_handler:
                self.stream_handler(rep)
                continue
            if rep[0] == 0xAB:
                raise IOError("device error for cmd 0x%02x: %r" % (cmd, bytes(rep[8:13])))
            if rep[0] == 0xAA and rep[1] == cmd and rep[5] == offset & 0xFF and rep[6] == (offset >> 8) & 0xFF:
                return bytes(rep[8:8 + size])

    def read(self, cmd, offset, length):
        out = b""
        for pos in range(0, length, 56):
            out += self.transact(cmd, offset + pos, min(56, length - pos))
        return out

    def write64(self, cmd, offset, data):
        # same chunking as the web driver: [0..56) at offset, then [8..64) at offset+8
        assert len(data) == 64
        self.transact(cmd, offset, 56, data[0:56])
        self.transact(cmd, offset + 8, 56, data[8:64])

    # --- read-only helpers -------------------------------------------------
    def info(self):
        b = self.read(CMD_INFO, 0, 56)
        return "%x" % (b[1] << 8 | b[0]), b[2:].split(b"\x00")[0].decode("ascii", "replace")

    def active_profile(self):
        b = self.read(CMD_BASE, 0, 56)
        idx = b[0] if b[0] < b[1] else 0  # same rule as firmware fn_94b4
        prof = b[2 + idx]
        return prof if prof < 4 else 0

    def get_func(self, profile):
        return bytearray(self.read(CMD_GET_FUNC, 64 * profile, 64))

    def get_keymap(self, profile, layer=0):
        raw = self.read(CMD_GET_KEYMAP, 512 * (profile * 4 + layer), 3 * MAX_KEYS)
        return [tuple(raw[3 * i:3 * i + 3]) for i in range(MAX_KEYS)]

    def get_triggers(self, profile):
        raw = self.read(CMD_GET_TRIGGER, 1024 * profile, 8 * MAX_KEYS)
        return [raw[8 * i:8 * i + 8] for i in range(MAX_KEYS)]

    def get_default_keymap(self):
        """Factory layer-0 keymap stored in firmware (read-only)."""
        raw = self.read(CMD_GET_DEFAULT_KEYMAP, 0, 3 * MAX_KEYS)
        return [tuple(raw[3 * i:3 * i + 3]) for i in range(MAX_KEYS)]

    def get_key_colors(self, profile):
        raw = self.read(CMD_GET_COLORS, 512 * profile, 3 * MAX_KEYS)
        return [bytes(raw[3 * i:3 * i + 3]) for i in range(MAX_KEYS)]

    # --- writes (every one of these is a flash write on the keyboard, except live colours) ---
    def _write_small(self, cmd, offset, data, readback_cmd=None):
        assert 0 < len(data) <= 56
        self.transact(cmd, offset, len(data), data)
        if readback_cmd is not None and self.read(readback_cmd, offset, len(data)) != bytes(data):
            raise RuntimeError("read-back mismatch after cmd 0x%02x at offset %d" % (cmd, offset))

    def set_trigger(self, profile, index, entry):
        """Write one key's 8-byte trigger entry (cmd 0xA1), verified by reading back."""
        self._write_small(CMD_SET_TRIGGER, 1024 * profile + 8 * index, bytes(entry), CMD_GET_TRIGGER)

    def set_key(self, profile, layer, index, code):
        """Remap one key (cmd 0x09): code = (type, code1, code2). Verified by reading back."""
        off = 512 * layer + 3 * index + 2048 * profile
        self.transact(CMD_SET_KEY, off, 3, bytes(code))
        if self.get_keymap(profile, layer)[index] != tuple(code):
            raise RuntimeError("keymap read-back mismatch")

    def set_key_color(self, profile, index, rgb):
        self._write_small(CMD_SET_COLOR, 512 * profile + 3 * index, bytes(rgb), CMD_GET_COLORS)

    def set_func(self, profile, new):
        """Write a full 64-byte FuncConfig. Refuses to change the report rate (that reboots the keyboard)."""
        fc = self.get_func(profile)
        if fc[2:4] != b"\xAA\xBB" or new[2:4] != b"\xAA\xBB":
            raise RuntimeError("FuncConfig marker missing, refusing to write")
        if (fc[4] & 15) != (new[4] & 15):
            raise RuntimeError("report rate would change (keyboard reboots) - not supported here")
        if bytes(new) != bytes(fc):
            self.write64(CMD_SET_FUNC, 64 * profile, bytes(new))
            if self.get_func(profile) != bytearray(new):
                raise RuntimeError("FuncConfig read-back mismatch")

    def set_light(self, profile, effect=None, brightness=None, speed=None, direction=None, color=None):
        """Main lighting fields of FuncConfig (web driver eo): bytes 8-16."""
        fc = self.get_func(profile)
        if effect is not None:
            fc[8] = effect
        if brightness is not None:
            fc[9] = max(0, min(100, brightness))
        if speed is not None:
            fc[10] = 4 - max(0, min(4, speed))
        if direction is not None:
            fc[11] = direction
        if color is not None:
            fc[14:17] = color
            fc[12] = 0  # custom colour enabled
        self.set_func(profile, fc)

    def live_colors(self, rgb_by_index):
        """RAM-only per-key colours (cmd 0xDD, 3 bytes per key from index 0). No flash wear; the
        firmware falls back to the stored effect if no update arrives for a while.
        Note: every host packet pauses the depth stream for ~0.6 s (firmware fn_b8ee)."""
        data = b"".join(bytes(c) for c in rgb_by_index)
        for pos in range(0, len(data), 54):  # whole keys per packet
            chunk = data[pos:pos + 54]
            self.transact(CMD_LIVE_COLORS, pos, len(chunk), chunk)

    # --- advanced keys -------------------------------------------------------------
    def get_mt_table(self, profile):
        raw = self.read(CMD_GET_MT, 256 * profile, 6 * ADV_SLOTS)
        return [raw[6 * i:6 * i + 6] for i in range(ADV_SLOTS)]

    def get_tgl_table(self, profile):
        raw = self.read(CMD_GET_TGL, 128 * profile, 3 * ADV_SLOTS)
        return [raw[3 * i:3 * i + 3] for i in range(ADV_SLOTS)]

    def get_dks_table(self, profile):
        raw = self.read(CMD_GET_DKS, 768 * profile, 24 * ADV_SLOTS)
        return [raw[24 * i:24 * i + 24] for i in range(ADV_SLOTS)]

    def set_mt_entry(self, profile, slot, click, down):
        self._write_small(CMD_SET_MT, 256 * profile + 6 * slot, bytes(click) + bytes(down), CMD_GET_MT)

    def set_tgl_entry(self, profile, slot, code):
        self._write_small(CMD_SET_TGL, 128 * profile + 3 * slot, bytes(code), CMD_GET_TGL)

    def set_dks_entry(self, profile, slot, entry):
        self._write_small(CMD_SET_DKS, 768 * profile + 24 * slot, bytes(entry), CMD_GET_DKS)

    def set_priority(self, profile, index, priority):
        tr = self.get_triggers(profile)[index]
        t = decode_trigger(tr)
        t["priority"] = priority
        new = encode_trigger(t)
        if new != bytes(tr):
            self.set_trigger(profile, index, new)

    def adv_bindings(self, profile):
        """All advanced-key bindings of a profile: dicts with layer, index, kind, slot, code2."""
        out = []
        for layer in range(LAYERS):
            for i, (t, c1, c2) in enumerate(self.get_keymap(profile, layer)[:KEY_COUNT]):
                if t in ADV_TYPES:
                    out.append({"layer": layer, "index": i, "kind": ADV_TYPES[t], "slot": c1, "code2": c2})
        return out

    @staticmethod
    def free_slots(bindings, group, n=1):
        used = {b["slot"] for b in bindings if ADV_GROUP[b["kind"]] == group}
        free = [s for s in range(ADV_SLOTS) if s not in used]
        if len(free) < n:
            raise RuntimeError("no free %s slot left (%d used)" % (group, len(used)))
        return free[:n]

    # --- backup / restore -------------------------------------------------------
    def backup(self):
        b = {"fw": self.info()[0], "time": time.strftime("%Y-%m-%d %H:%M:%S"), "profiles": []}
        for p in range(PROFILES):
            b["profiles"].append({
                "func": self.get_func(p).hex(),
                "triggers": [e.hex() for e in self.get_triggers(p)[:KEY_COUNT]],
                "keymap": [[bytes(c).hex() for c in self.get_keymap(p, l)[:KEY_COUNT]] for l in range(LAYERS)],
                "colors": [c.hex() for c in self.get_key_colors(p)[:KEY_COUNT]],
                "mt": [e.hex() for e in self.get_mt_table(p)],
                "tgl": [e.hex() for e in self.get_tgl_table(p)],
                "dks": [e.hex() for e in self.get_dks_table(p)],
            })
        return b

    def restore(self, b, log=print):
        """Write back everything that differs from a backup(). Returns number of writes."""
        n = 0
        for p, prof in enumerate(b["profiles"]):
            want = bytearray.fromhex(prof["func"])
            if self.get_func(p) != want:
                self.set_func(p, want); n += 1; log("profile %d: FuncConfig restored" % p)
            cur = self.get_triggers(p)
            for i, h in enumerate(prof["triggers"]):
                if cur[i] != bytes.fromhex(h):
                    self.set_trigger(p, i, bytes.fromhex(h)); n += 1
            for l in range(LAYERS):
                cur = self.get_keymap(p, l)
                for i, h in enumerate(prof["keymap"][l]):
                    if bytes(cur[i]).hex() != h:
                        self.set_key(p, l, i, tuple(bytes.fromhex(h))); n += 1
            cur = self.get_key_colors(p)
            for i, h in enumerate(prof["colors"]):
                if cur[i] != bytes.fromhex(h):
                    self.set_key_color(p, i, bytes.fromhex(h)); n += 1
            for key, getter, setter in (("mt", self.get_mt_table, lambda s, b: self.set_mt_entry(p, s, b[:3], b[3:])),
                                        ("tgl", self.get_tgl_table, lambda s, b: self.set_tgl_entry(p, s, b)),
                                        ("dks", self.get_dks_table, lambda s, b: self.set_dks_entry(p, s, b))):
                if key not in prof:  # backups made before advanced-key support
                    continue
                cur = getter(p)
                for s, h in enumerate(prof[key]):
                    if cur[s] != bytes.fromhex(h):
                        setter(s, bytes.fromhex(h)); n += 1
        return n

    # --- debug stream ------------------------------------------------------
    def set_debug(self, profile, on):
        """Set/clear FuncConfig byte 7 bit 3. Costs two 64-byte flash writes if it changes."""
        fc = self.get_func(profile)
        if fc[2:4] != b"\xAA\xBB":
            raise RuntimeError("FuncConfig marker missing (bytes 2-3 = %s), refusing to write" % fc[2:4].hex())
        new = bytearray(fc)
        new[7] = (fc[7] | 0x08) if on else (fc[7] & 0xF7)
        if new != fc:
            self.write64(CMD_SET_FUNC, 64 * profile, bytes(new))
            if self.get_func(profile) != new:
                raise RuntimeError("FuncConfig read-back mismatch")
        return fc[7], new[7]

    @contextmanager
    def depth_stream(self, profile=None):
        """Enable the depth stream for the block, always restore on exit."""
        profile = self.active_profile() if profile is None else profile
        self.set_debug(profile, True)
        try:
            yield profile
        finally:
            for attempt in range(3):
                try:
                    self.set_debug(profile, False)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.3)
