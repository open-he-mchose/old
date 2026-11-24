# MCHOSE Jet75-II reverse engineering — old workspace (archived)

> **Archived, not maintained.** This is the first, single-keyboard workspace from which the
> [open-he-mchose](https://github.com/open-he-mchose) project grew. It is kept for reference only:
> no fixes, no support, issues and pull requests are not reviewed. Code here was written against
> firmware V116 and may contain mistakes that were corrected later.
>
> Use instead:
> - documentation: <https://open-he-mchose.github.io/> ([docs](https://github.com/open-he-mchose/docs))
> - reverse-engineering tools: [re-tools](https://github.com/open-he-mchose/re-tools)
> - Linux NKRO / Snap Tap fix and udev rules: `linux-fixes` (planned)
> - driver and command-line tool (C++, Linux and Windows): `driver` (planned)

*AI-generated (Claude, Anthropic) together with the repository owner; tested on one Jet75-II only where
the documentation says **verified**. Use at your own risk: most tools write to the keyboard's flash.*


Protocol, tools and demos for the MCHOSE Jet75-II magnetic (Hall-effect) keyboard
(USB `41E4:211A`, WCH CH32V30x RISC-V MCU, firmware V116), reconstructed from the official web
driver, the firmware image and USB captures. Everything here talks to the keyboard's own vendor
interface; nothing replaces the firmware.

## What is where

| Path | Content |
|---|---|
| `docs/protocol.md` | Protocol and firmware notes: framing, commands, FuncConfig, trigger settings, depth stream, lighting, key codes, advanced keys, firmware internals, bugs found |
| `tools/jet75.py` | Python library (hidraw, stdlib only) |
| `tools/jet75ctl.py` | Settings CLI: `info`, `backup`/`restore`, `trigger` (sensitivity), `keymap`/`remap`, `light`, `adv` (DKS/MT/TGL/RS/SOCD/LT + `doctor`). Backs up all 4 profiles before every write |
| `tools/jet75_depth.py` | Key-depth monitor / logger |
| `tools/hid-bpf/` | Linux fix for the firmware's broken NKRO descriptor (makes Snap Tap and >6-key rollover work), auto-loaded by udev |
| `tools/udev/` | udev rules: hidraw access for `plugdev`, auto-load of the HID-BPF fix |
| `tools/extract_curves.py` | Extracts the per-switch travel curves from a firmware image you obtained yourself (`jet75_curves.json` for the tools, `curves.js` for the web visualiser); without them the tools fall back to the firmware's own depth value |
| `tools/firmware/make_elf.py`, `tools/bin2elf.py`, `tools/ghidra/` | Firmware → ELF (base 0x5000; `make_elf.py` adds function symbols, `bin2elf.py` maps the initialised .data into RAM) and a Ghidra language for the WCH QingKe "XW" instructions |
| `tools/hidlog.py` | Logs raw HID reports next to Linux key events (for verifying fixes) |
| `demos/` | Web depth visualiser (WebHID), terminal depth map, WASD/full-keyboard virtual gamepad, reactive and experimental lighting (incl. Game of Life), read-only settings dump — see `demos/README.md` |

Not published: vendor firmware, updaters and web-driver code (`samples/`), Ghidra projects and decompilation
(`work/`), data extracted from the firmware (travel curves), raw USB/key recordings (`captures/`, they contain
typed text) and settings backups (`backups/`). Paths to them in the code and notes refer to the original
local workspace.

## Main findings

- Depth streaming = FuncConfig byte 7 bit 3 (RAM copy); every host packet pauses the stream ~0.6 s;
  stream rate is fixed by firmware (~40 packets/s for the whole keyboard).
- Every settings write goes to flash; `0xA8` starts a real recalibration that is saved.
- **Firmware bug:** the NKRO report is declared `Input(Array)` instead of `Input(Variable)`, so
  Snap Tap/SOCD output and keys beyond 6 are dropped by every host. Fixed on Linux with HID-BPF.
- Web driver issues: some DKS combinations are silently changed on save; advanced-key slots are
  re-numbered on delete by rewriting all bindings; RS keys use the 6-key buffer.

## Quick start

```sh
sudo cp tools/udev/70-jet75.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw
python3 tools/jet75ctl.py info
python3 tools/jet75ctl.py adv doctor
make -C tools/hid-bpf && sudo make -C tools/hid-bpf install && sudo cp tools/udev/71-jet75-hid-fix.rules /etc/udev/rules.d/
```

## License

MIT (see `LICENSE`), except `tools/hid-bpf/`, which is GPL-2.0 (the kernel requires a GPL licence for
HID-BPF programs; see the SPDX headers).
