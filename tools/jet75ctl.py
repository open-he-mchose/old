#!/usr/bin/env python3
"""Jet75-II configuration tool: sensitivity, key mapping, lighting, backup/restore.

Every command that writes to the keyboard first saves a full backup of all 4 profiles to
backups/jet75-YYYYmmdd-HHMMSS.json (disable with --no-backup); `restore FILE` writes back only
what differs. Writes go to the keyboard's flash, like the official driver does.

  jet75ctl.py info
  jet75ctl.py backup [FILE]          |  restore FILE
  jet75ctl.py trigger show
  jet75ctl.py trigger set W A S D --actuation 0.8 --rt-press 0.15 --rt-release 0.15 --mode rt
  jet75ctl.py trigger set all --actuation 1.5 --mode rt   (modes: normal, rt, rt-continuous)
  jet75ctl.py keymap show [--layer N]
  jet75ctl.py remap CapsLock Esc     |  remap CapsLock --default   |  remap F1 Ctrl+C  |  remap F2 Mute
  jet75ctl.py light show
  jet75ctl.py light set --effect tidalrhythm --brightness 80 --speed 3 --color '#ff8800'
  jet75ctl.py light key Esc '#ff0000'    (per-key colour, visible with --effect static)

Keys are named after their layer-0 function (A, Esc, LShift, Fn, ...) or given as #index.
Mapping targets: key names, modifier combos (Ctrl+Shift+T), Mute / VolUp / VolDown / Play /
Next / Prev / Stop, Fn, none, or raw:TTC1C2 (hex, e.g. raw:30e200).
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jet75 as J  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MODS = {"ctrl": 0x01, "lctrl": 0x01, "shift": 0x02, "lshift": 0x02, "alt": 0x04, "lalt": 0x04,
        "gui": 0x08, "win": 0x08, "lgui": 0x08, "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "rgui": 0x80}
CONSUMER = {"mute": 0xE2, "volup": 0xE9, "voldown": 0xEA, "play": 0xCD, "next": 0xB5, "prev": 0xB6, "stop": 0xB7}
HID_BY_NAME = {v.lower(): k for k, v in J.HID_NAMES.items()}
HID_BY_NAME.update({"del": 0x4C, "bksp": 0x2A, "caps": 0x39, "pgup": 0x4B, "pgdn": 0x4E, "escape": 0x29})


def parse_target(s):
    low = s.lower()
    if low in ("none", "off", "disable"):
        return (0, 0, 0)
    if low == "fn":
        return (0xF0, 0xFF, 0x01)
    if low in CONSUMER:
        u = CONSUMER[low]
        return (0x30, u & 0xFF, u >> 8)
    if low.startswith("raw:"):
        b = bytes.fromhex(low[4:])
        if len(b) != 3:
            raise ValueError("raw target needs 3 bytes, e.g. raw:100029")
        return tuple(b)
    mods, base = 0, None
    for part in low.split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in HID_BY_NAME and base is None:
            base = HID_BY_NAME[part]
        else:
            raise ValueError("unknown key or modifier: %r" % part)
    return (0x10, mods, base or 0)


def target_name(code):
    t, c1, c2 = code
    if code == (0, 0, 0):
        return "none"
    if code == (0x10, 0, 0):
        return "(transparent)"
    if t == 0xF0 and c1 != 0xFF:
        return {0xFA: "RT-toggle", 0x34: "brightness", 0x1E: "BackHome"}.get(c1, "special:%02x%02x" % (c1, c2))
    if t == 0x10 and c1 and c2:
        return "+".join(n for b, n in J.MOD_NAMES.items() if c1 & b) + "+" + J.HID_NAMES.get(c2, hex(c2))
    if t == 0x30:
        u = c1 | c2 << 8
        return next((k for k, v in CONSUMER.items() if v == u), "consumer:%#x" % u)
    if t == 0x70:
        return "macro %d" % c1
    if t == 0xF0 and c1 == 0xFF:
        return "Fn%d" % c2
    return J.key_name(t, c1, c2)


class Ctl:
    def __init__(self, args):
        self.args = args
        self.dev = J.Jet75()
        self.profile = self.dev.active_profile() if args.profile is None else args.profile
        self._backed_up = False
        self.layer0 = self.dev.get_keymap(self.profile, 0)[:J.KEY_COUNT]
        if any(c[0] >= 0x30 and c != (0xF0, 0xF0, 0xF0) for c in self.layer0):
            # keys bound to advanced functions / macros / media: name them after their factory key
            default = self.dev.get_default_keymap()[:J.KEY_COUNT]
            self.layer0 = [d if c[0] >= 0x30 and c != (0xF0, 0xF0, 0xF0) else c for c, d in zip(self.layer0, default)]

    def key_index(self, name):
        if name.startswith("#"):
            return int(name[1:])
        low = name.lower()
        for i, c in enumerate(self.layer0):
            if J.key_name(*c).lower() == low:
                return i
        alias = {"del": "delete", "bksp": "backspace", "caps": "capslock", "esc": "esc"}.get(low)
        if alias:
            return self.key_index(alias)
        raise SystemExit("unknown key %r (use names from 'keymap show' or #index)" % name)

    def keys(self, names):
        if names == ["all"]:
            return [i for i, c in enumerate(self.layer0) if c not in ((0, 0, 0), (0xF0, 0xF0, 0xF0))]
        return [self.key_index(n) for n in names]

    def before_write(self):
        if self._backed_up or self.args.no_backup:
            return
        os.makedirs(os.path.join(ROOT, "backups"), exist_ok=True)
        path = os.path.join(ROOT, "backups", time.strftime("jet75-%Y%m%d-%H%M%S.json"))
        json.dump(self.dev.backup(), open(path, "w"), indent=1)
        print("backup saved:", os.path.relpath(path, os.getcwd()))
        self._backed_up = True


def cmd_info(c, a):
    fw, build = c.dev.info()
    fc = c.dev.get_func(c.profile)
    print("firmware %s (%s), device %s, active profile %d" % (fw, build, c.dev.path, c.dev.active_profile()))
    print("light:", J.decode_light(fc))
    print("performance:", {k: v for k, v in J.decode_funcconfig(fc).items() if k not in ("light", "sideLight")})


def cmd_backup(c, a):
    path = a.file or os.path.join(ROOT, "backups", time.strftime("jet75-%Y%m%d-%H%M%S.json"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(c.dev.backup(), open(path, "w"), indent=1)
    print("backup saved:", path)


def cmd_restore(c, a):
    b = json.load(open(a.file))
    c.before_write()
    n = c.dev.restore(b)
    print("restore done, %d writes" % n)


def show_triggers(c, idx=None):
    tr = c.dev.get_triggers(c.profile)
    print("profile %d\n%4s %-10s %-7s %7s %8s %8s %5s %5s" % (c.profile, "idx", "key", "mode", "act mm", "RT dn mm", "RT up mm", "dzP", "dzR"))
    for i, code in enumerate(c.layer0):
        if code in ((0, 0, 0), (0xF0, 0xF0, 0xF0)) or (idx is not None and i not in idx):
            continue
        t = J.decode_trigger(tr[i])
        print("%4d %-10s %-7s %7.2f %8.2f %8.2f %5d %5d" % (i, J.key_name(*code), J.KEY_MODES.get(t["keyMode"], t["keyMode"]),
              t["actuation_mm"], t["rtPress_mm"], t["rtRelease_mm"], t["pressDeadzone"], t["releaseDeadzone"]))


def cmd_trigger(c, a):
    if a.action == "show":
        return show_triggers(c, set(c.keys(a.keys)) if a.keys else None)
    if not a.keys:
        raise SystemExit("name the keys to change (or 'all')")
    idx = c.keys(a.keys)
    tr = c.dev.get_triggers(c.profile)
    mode = None
    if a.mode is not None:
        mode = {"normal": 0, "rt": 1, "rt-continuous": 2}.get(a.mode)
        mode = int(a.mode) if mode is None else mode
    changes = []
    for i in idx:
        t = J.decode_trigger(tr[i])
        for field, val in (("actuation_mm", a.actuation), ("rtPress_mm", a.rt_press), ("rtRelease_mm", a.rt_release),
                           ("keyMode", mode), ("pressDeadzone", a.press_deadzone), ("releaseDeadzone", a.release_deadzone)):
            if val is not None:
                t[field] = val
        if t["actuation_mm"] < 0.1 or t["actuation_mm"] > 3.4:
            raise SystemExit("actuation must be within 0.10..3.40 mm")
        new = J.encode_trigger(t)
        if new != bytes(tr[i]):
            changes.append((i, new))
    if not changes:
        return print("nothing to change")
    c.before_write()
    for i, new in changes:
        c.dev.set_trigger(c.profile, i, new)
    print("%d keys updated" % len(changes))
    show_triggers(c, {i for i, _ in changes})


def cmd_keymap(c, a):
    km = c.dev.get_keymap(c.profile, a.layer)
    print("profile %d layer %d" % (c.profile, a.layer))
    for i, code in enumerate(km[:J.KEY_COUNT]):
        if code == (0xF0, 0xF0, 0xF0):
            continue
        print("%4d %-10s -> %-14s %s" % (i, J.key_name(*c.layer0[i]), target_name(code), bytes(code).hex()))


def cmd_remap(c, a):
    i = c.key_index(a.key)
    if a.default:
        code = c.dev.get_default_keymap()[i] if a.layer == 0 else (0, 0, 0)
    elif a.target:
        code = parse_target(a.target)
    else:
        raise SystemExit("give a target or --default")
    cur = c.dev.get_keymap(c.profile, a.layer)[i]
    if tuple(cur) == tuple(code):
        return print("already mapped to", target_name(code))
    c.before_write()
    c.dev.set_key(c.profile, a.layer, i, code)
    print("profile %d layer %d: key #%d (%s) %s -> %s" % (c.profile, a.layer, i, J.key_name(*c.layer0[i]),
                                                         target_name(cur), target_name(code)))


def cmd_light(c, a):
    if a.action == "show":
        print("profile %d:" % c.profile, J.decode_light(c.dev.get_func(c.profile)))
        print("effects:", ", ".join("%d=%s" % kv for kv in J.EFFECTS.items()))
        return
    if a.action == "set":
        eff = None
        if a.effect is not None:
            eff = int(a.effect) if a.effect.isdigit() else next((k for k, v in J.EFFECTS.items() if v == a.effect.lower()), None)
            if eff is None:
                raise SystemExit("unknown effect; see 'light show'")
        c.before_write()
        c.dev.set_light(c.profile, effect=eff, brightness=a.brightness, speed=a.speed,
                        color=J.parse_color(a.color) if a.color else None)
        print("profile %d:" % c.profile, J.decode_light(c.dev.get_func(c.profile)))
        return
    if a.action == "key":
        if len(a.args) != 2:
            raise SystemExit("usage: light key KEY '#rrggbb'")
        i = c.key_index(a.args[0])
        c.before_write()
        c.dev.set_key_color(c.profile, i, J.parse_color(a.args[1]))
        print("key #%d colour set to %s" % (i, a.args[1]))


SWAP_PRIO = {0: 0, 1: 2, 2: 1, 3: 3}


def plain_code(c, i):
    """Code a paired/advanced function should send for key i: its current plain mapping, else factory default."""
    cur = c.layer0[i]
    if cur[0] == 0x10 and cur != (0x10, 0, 0):
        return cur
    return c.dev.get_default_keymap()[i]


def adv_describe(c, b, tables):
    mt, tgl, dks = tables
    k = J.key_name(*c.layer0[b["index"]])
    s = b["slot"]
    head = "L%d %-9s %-5s slot %2d" % (b["layer"], k, b["kind"].upper(), s)
    if s >= J.ADV_SLOTS:
        return head + "  (slot out of range!)"
    if b["kind"] == "mt":
        e = mt[s]
        return head + "  tap %s / hold %s, %d ms" % (target_name(tuple(e[0:3])), target_name(tuple(e[3:6])), 10 * b["code2"])
    if b["kind"] in ("socd", "rs", "lt"):
        e = mt[s]
        extra = ""
        if b["kind"] == "socd":
            p = J.decode_trigger(c.dev.get_triggers(c.profile)[b["index"]])["priority"]
            extra = ", priority %d (%s)" % (p, J.SOCD_MODES.get(p, "?"))
        return head + "  sends %s, partner %s (#%d)%s" % (target_name(tuple(e[0:3])), J.key_name(*c.layer0[b["code2"]]) if b["code2"] < J.KEY_COUNT else "?", b["code2"], extra)
    if b["kind"] == "tgl":
        return head + "  toggles %s" % target_name(tuple(tgl[s]))
    d = J.decode_dks(dks[s])
    acts = "; ".join("%s %s" % (target_name(code), ",".join(map(str, sp))) for code, sp in d["actions"])
    return head + "  points %.1f/%.1f mm: %s" % (d["points_mm"][0], d["points_mm"][1], acts)


def adv_doctor(c, binds, tables):
    mt, tgl, dks = tables
    problems = []
    seen = {}
    for b in binds:
        g = J.ADV_GROUP[b["kind"]]
        k = "L%d #%d %s" % (b["layer"], b["index"], J.key_name(*c.layer0[b["index"]]))
        if b["slot"] >= J.ADV_SLOTS:
            problems.append("%s: slot %d >= 32 (firmware table has 32 slots)" % (k, b["slot"])); continue
        if (g, b["slot"]) in seen:
            problems.append("%s shares %s slot %d with %s" % (k, g, b["slot"], seen[(g, b["slot"])]))
        seen[(g, b["slot"])] = k
        entry = {"mt": mt, "tgl": tgl, "dks": dks}[g][b["slot"]]
        if not any(entry):
            problems.append("%s: %s slot %d is empty" % (k, g, b["slot"]))
        if b["kind"] in ("socd", "rs", "lt"):
            j = b["code2"]
            partner = [x for x in binds if x["layer"] == b["layer"] and x["index"] == j]
            if not partner or partner[0]["kind"] != b["kind"] or partner[0]["code2"] != b["index"]:
                problems.append("%s: partner #%d does not point back" % (k, j))
            if entry[0] != 0x10:
                problems.append("%s: slot sends type %#x, firmware only uses code1/code2 (plain keys)" % (k, entry[0]))
            if b["kind"] == "socd" and partner:
                tr = c.dev.get_triggers(c.profile)
                p1 = J.decode_trigger(tr[b["index"]])["priority"]
                p2 = J.decode_trigger(tr[j])["priority"]
                if SWAP_PRIO.get(p1) != p2:
                    problems.append("%s: SOCD priorities %d/%d are not a valid pair" % (k, p1, p2))
    used = {g: {b["slot"] for b in binds if J.ADV_GROUP[b["kind"]] == g} for g in ("mt", "tgl", "dks")}
    orphans = ["%s %d" % (g, s) for g, tab in (("mt", mt), ("tgl", tgl), ("dks", dks)) for s, e in enumerate(tab) if any(e) and s not in used[g]]
    return problems, orphans


def cmd_adv(c, a):
    if a.action in ("show", "doctor"):
        # all profiles unless one was asked for: dormant bindings in other profiles matter too
        profiles = [c.profile] if a.profile is not None else list(range(J.PROFILES))
        active, total_problems = c.dev.active_profile(), 0
        for p in profiles:
            c.profile = p
            binds = c.dev.adv_bindings(p)
            tables = (c.dev.get_mt_table(p), c.dev.get_tgl_table(p), c.dev.get_dks_table(p))
            print("profile %d%s: %d advanced bindings (driver limit 40)" % (p, " (active)" if p == active else "", len(binds)))
            for b in binds:
                print("  " + adv_describe(c, b, tables))
            if a.action == "doctor":
                problems, orphans = adv_doctor(c, binds, tables)
                total_problems += len(problems)
                for x in problems:
                    print("  !", x)
                if orphans:
                    print("  unreferenced table entries (harmless leftovers):", ", ".join(orphans))
        if a.action == "doctor":
            print("problems: %d" % total_problems if total_problems else "no problems found")
        return
    p = c.profile
    binds = c.dev.adv_bindings(p)
    tables = (c.dev.get_mt_table(p), c.dev.get_tgl_table(p), c.dev.get_dks_table(p))
    layer = a.layer
    if a.action == "clear":
        i = c.key_index(a.keys[0])
        hit = [b for b in binds if b["layer"] == layer and b["index"] == i]
        if not hit:
            return print("no advanced function on that key in layer %d" % layer)
        todo = [hit[0]]
        if hit[0]["kind"] in ("socd", "rs", "lt"):
            todo += [b for b in binds if b["layer"] == layer and b["index"] == hit[0]["code2"]]
        c.before_write()
        default = c.dev.get_default_keymap()
        for b in todo:
            c.dev.set_key(p, layer, b["index"], default[b["index"]] if layer == 0 else (0x10, 0, 0))
            if b["kind"] == "socd":
                c.dev.set_priority(p, b["index"], 0)
            g = J.ADV_GROUP[b["kind"]]
            if b["slot"] < J.ADV_SLOTS:
                {"mt": lambda s: c.dev.set_mt_entry(p, s, b"\0\0\0", b"\0\0\0"),
                 "tgl": lambda s: c.dev.set_tgl_entry(p, s, b"\0\0\0"),
                 "dks": lambda s: c.dev.set_dks_entry(p, s, bytes(24))}[g](b["slot"])
            print("cleared %s on %s" % (b["kind"].upper(), J.key_name(*c.layer0[b["index"]])))
        return
    idx = [c.key_index(k) for k in a.keys]
    busy = [b for b in binds if b["layer"] == layer and b["index"] in idx]
    if busy:
        raise SystemExit("key already has an advanced function, run 'adv clear' first: " + adv_describe(c, busy[0], tables))
    kind = a.action
    if kind in ("socd", "rs", "lt"):
        if len(idx) != 2 or idx[0] == idx[1]:
            raise SystemExit("%s needs two different keys" % kind)
        s1, s2 = J.Jet75.free_slots(binds, "mt", 2)
        k1, k2 = plain_code(c, idx[0]), plain_code(c, idx[1])
        t = {"rs": 0x93, "socd": 0x94, "lt": 0x95}[kind]
        mode = 0
        if kind == "socd":
            mode = {v: k for k, v in J.SOCD_MODES.items()}.get(a.mode or "last-input")
            if mode is None:
                raise SystemExit("--mode: " + ", ".join(J.SOCD_MODES.values()))
        c.before_write()
        c.dev.set_mt_entry(p, s1, k1, k2)
        c.dev.set_mt_entry(p, s2, k2, k1)
        if kind == "socd":
            c.dev.set_priority(p, idx[0], mode)
            c.dev.set_priority(p, idx[1], SWAP_PRIO[mode])
        c.dev.set_key(p, layer, idx[0], (t, s1, idx[1]))
        c.dev.set_key(p, layer, idx[1], (t, s2, idx[0]))
    elif kind == "mt":
        if len(idx) != 1 or not (a.tap and a.hold):
            raise SystemExit("usage: adv mt KEY --tap X --hold Y [--time ms]")
        if not 10 <= a.time <= 2550:
            raise SystemExit("--time must be 10..2550 ms")
        s, = J.Jet75.free_slots(binds, "mt")
        c.before_write()
        c.dev.set_mt_entry(p, s, parse_target(a.tap), parse_target(a.hold))
        c.dev.set_key(p, layer, idx[0], (0x92, s, a.time // 10))
    elif kind == "tgl":
        if len(idx) != 1:
            raise SystemExit("usage: adv tgl KEY [--key X]")
        s, = J.Jet75.free_slots(binds, "tgl")
        code = parse_target(a.key) if a.key else plain_code(c, idx[0])
        c.before_write()
        c.dev.set_tgl_entry(p, s, code)
        c.dev.set_key(p, layer, idx[0], (0x91, s, 0))
    elif kind == "dks":
        if len(idx) != 1 or not a.action_spec:
            raise SystemExit("usage: adv dks KEY --shallow 1.0 --deep 3.0 --action KEY:ds,de,us,ue [--action ...]")
        acts = []
        for spec in a.action_spec[:4]:
            name, spans = spec.rsplit(":", 1)
            sp = tuple(int(x) for x in spans.split(","))
            if len(sp) != 4 or not (0 <= sp[0] <= 4 and 0 <= sp[1] <= 3 and 0 <= sp[2] <= 2 and 0 <= sp[3] <= 1):
                raise SystemExit("spans ds,de,us,ue must be within 0-4,0-3,0-2,0-1")
            stored = J.dks_unmask(J.dks_mask(*sp))
            if stored != sp:
                print("warning: %s spans %s cannot be stored as given; the keyboard format (same as the "
                      "official driver) keeps %s" % (name, ",".join(map(str, sp)), ",".join(map(str, stored))))
            acts.append((parse_target(name), sp))
        entry = J.encode_dks((a.shallow, a.deep), acts)
        s, = J.Jet75.free_slots(binds, "dks")
        c.before_write()
        c.dev.set_dks_entry(p, s, entry)
        c.dev.set_key(p, layer, idx[0], (0x90, s, 0))
    binds = c.dev.adv_bindings(p)
    tables = (c.dev.get_mt_table(p), c.dev.get_tgl_table(p), c.dev.get_dks_table(p))
    for b in binds:
        if b["layer"] == layer and b["index"] in idx:
            print("set: " + adv_describe(c, b, tables))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=int, help="profile 0-3 (default: active)")
    ap.add_argument("--no-backup", action="store_true", help="do not save a backup before writing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    s = sub.add_parser("backup"); s.add_argument("file", nargs="?")
    s = sub.add_parser("restore"); s.add_argument("file")
    s = sub.add_parser("trigger"); s.add_argument("action", choices=["show", "set"]); s.add_argument("keys", nargs="*")
    s.add_argument("--actuation", type=float); s.add_argument("--rt-press", type=float); s.add_argument("--rt-release", type=float)
    s.add_argument("--mode", help="normal | rt | rt-continuous | raw number"); s.add_argument("--press-deadzone", type=int)
    s.add_argument("--release-deadzone", type=int)
    s = sub.add_parser("keymap"); s.add_argument("action", choices=["show"]); s.add_argument("--layer", type=int, default=0)
    s = sub.add_parser("remap"); s.add_argument("key"); s.add_argument("target", nargs="?")
    s.add_argument("--layer", type=int, default=0); s.add_argument("--default", action="store_true")
    s = sub.add_parser("light"); s.add_argument("action", choices=["show", "set", "key"]); s.add_argument("args", nargs="*")
    s.add_argument("--effect"); s.add_argument("--brightness", type=int); s.add_argument("--speed", type=int); s.add_argument("--color")
    s = sub.add_parser("adv", help="advanced keys: show | doctor | socd | rs | lt | mt | tgl | dks | clear")
    s.add_argument("action", choices=["show", "doctor", "socd", "rs", "lt", "mt", "tgl", "dks", "clear"])
    s.add_argument("keys", nargs="*"); s.add_argument("--layer", type=int, default=0)
    s.add_argument("--mode", help="SOCD: last-input | key1-priority | key2-priority | cancel")
    s.add_argument("--tap"); s.add_argument("--hold"); s.add_argument("--time", type=int, default=200)
    s.add_argument("--key", help="TGL: key to toggle (default: the key itself)")
    s.add_argument("--shallow", type=float, default=1.0); s.add_argument("--deep", type=float, default=3.0)
    s.add_argument("--action", dest="action_spec", action="append", help="DKS action KEY:ds,de,us,ue (up to 4)")
    a = ap.parse_args()
    c = Ctl(a)
    try:
        {"info": cmd_info, "backup": cmd_backup, "restore": cmd_restore, "trigger": cmd_trigger,
         "keymap": cmd_keymap, "remap": cmd_remap, "light": cmd_light, "adv": cmd_adv}[a.cmd](c, a)
    finally:
        c.dev.close()


if __name__ == "__main__":
    main()
