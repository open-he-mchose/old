# Jet75-II demos

What the reverse-engineered protocol (`../docs/protocol.md`) already allows, as small runnable demos.
All demos talk to the keyboard's vendor config interface (`41E4:211A`, interface 1) and use only
commands that were verified on the wire.

| Demo | What it shows | Writes to keyboard? |
|---|---|---|
| `web-depth-visualizer/` | Browser page (WebHID): the whole keyboard, live depth of every key, actuation point, peak, per-key chart | FuncConfig debug bit on start, restored on stop |
| `terminal_depth_map.py` | Same idea in the terminal (curses) | same as above |
| `wasd_gamepad.py` | Virtual gamepad (uinput): W/A/S/D depth → analog left stick, Space → A, Left Shift depth → analog left trigger. `--selftest` works without the keyboard | same as above |
| `full_gamepad.py` | Whole keyboard as an Xbox pad: WASD + arrows = analog sticks, Q/E = analog triggers, IJKL = A/B/X/Y, TFGH = D-pad, Tab/R = LB/RB, Shifts = L3/R3, M/Esc/PrtSc = Back/Start/Guide. Grabs the keyboard so mapped keys stop typing; other keys pass through; F12 quits | same as above |
| `reactive_lighting.py` | Custom reactive lighting from key presses: `--mode fade / ripple / heat`. Live colours via 0xDD (RAM only); the keyboard returns to its own effect when it stops | RAM only, no flash |
| `experimental_lighting.py` | Experimental effects: `--mode fire` (flames rise from pressed/held keys), `sparks` (particle physics), `life` (Game of Life seeded by typing, white cells), `tempo` (colour follows typing speed + comets) | RAM only, no flash |
| `read_settings.py` | Firmware, active profile, performance + lighting settings, every key's mapping and trigger settings (actuation, rapid trigger, dead zones) | **no** (read-only) |
| `../tools/jet75_depth.py` | Line-by-line depth log, optional JSONL recording (`--log`) | same as the visualizers |

## Running

```sh
# browser page (Chrome / Chromium / Edge; WebHID needs localhost or file://)
./demos/web-depth-visualizer/serve.sh        # then open http://localhost:8765/
#   ?demo  -> http://localhost:8765/?demo shows synthetic data without a keyboard

python3 demos/terminal_depth_map.py          # q to quit
python3 demos/wasd_gamepad.py                # Ctrl+C to stop; needs /dev/uinput (group input)
#   check it: jstest /dev/input/js1, or https://gamepad-tester style pages in the browser
python3 demos/read_settings.py [--json] [--profile N]
```

Close the official MCHOSE web driver tab first — two programs talking to the same interface at once
will steal each other's replies.

## What happens on the keyboard

- Depth streaming = FuncConfig byte 7 bit 3 of the active profile (command 0x06). The demos read the
  64-byte FuncConfig, check the `AA BB` marker, set only that bit, verify by reading back, and clear it
  again when they stop. Each start/stop is two 64-byte flash writes (the official driver does the same).
- If a demo is killed hard, the bit stays set in flash but the firmware clears it in RAM at the next
  power-up, so streaming does not survive a replug.
- None of the demos sends 0xA8: that command starts a real recalibration that is saved to flash.
- The stream is change-driven and globally rate-limited (~24 ms between packets for all keys together).
  On fast releases the travel field lags; the demos zero a key when the raw sensor value is back at its
  rest level (toggle in the web page).

## Settings tool

`tools/jet75ctl.py` changes the keyboard's stored settings (sensitivity, key mapping, lighting) and
saves a full backup of all 4 profiles to `backups/` before every write; `restore FILE` puts it back.
Run `python3 tools/jet75ctl.py -h` for examples.

## Depth precision

All demos use `depth_mm` from `tools/jet75.py`: the firmware's own travel curve applied to the
normalized sensor value (stream bytes 4–5), interpolated between 0.01 mm steps and free of the
firmware travel field's lag. Curves come from the firmware image via `tools/extract_curves.py`.

## Known firmware limits

- Depth stream: ~40 packets/s for the whole keyboard, change-driven.
- Any host packet pauses the depth stream for ~0.6 s, so depth-reactive lighting (read depth while
  writing colours) is not possible; `reactive_lighting.py` uses key presses instead.
- Light index = key matrix index (verified: WASD lit by indices 71, 10, 61, 11).
