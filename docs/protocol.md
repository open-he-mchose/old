# MCHOSE Jet75-II — USB HID protocol (static reconstruction)

> **Outdated (archived).** Notes as of 22 November 2026. Several details were corrected later (for example
> `F0 FA` is "next profile", not a rapid-trigger toggle). Current documentation: <https://open-he-mchose.github.io/>.
>
> *AI-generated (Claude, Anthropic); checked on real hardware only where marked.*

Status: reconstructed from the official web driver (static reading) and the firmware's USB
descriptors, then **partly confirmed on the wire** with `captures/jet75-session1.pcapng`
(usbmon5, 2026-11-22, 150 s, keyboard at bus 5 dev 7; config channel = EP 0x04 OUT / EP 0x82 IN).

### Wire-verified (session1)

- Framing `55 cmd 00 sum size lo hi 00 data…` / reply `AA cmd …`: all 843 OUT packets have a valid checksum.
- Commands seen: 03, 05/06, 08, 09, 0B, 0C, A0/A1, DD (448× live light preview), F1/F2.
- 0xA1 trigger write: keyActuation values 150→266→148→340→150 for keys 10, 11, 61, 71 —
  consistent with **0.01 mm units** (1.50…3.40 mm). keyMode toggled 1↔2 (likely normal ↔ rapid trigger).
- 0x06 FuncConfig write: light effect byte 8 0→6, RGB bytes 14–16 `ff0303`/`ababab`, brightness byte 9 = 100.
  The 64-byte struct is written as two overlapping packets (offset 0 size 56, offset 8 size 56).
- 0x09 keymap: key index 60 (CapsLock) layer 0 written `10 00 29` (type 0x10 = plain key, HID 0x29 = Esc),
  restored to `10 00 39` (HID CapsLock).
- FuncConfig byte 7 was `0x06` throughout (travelCheck=1, bottom-RT=1, debugMode=0): **no depth stream in session1**,
  because the calibration dialog was never opened (see below).

Sources (all under `samples/`):
- Web driver `www.mchose.com.cn/cizhou/_next/static/chunks/` (downloaded 2026-11-22):
  - `3050-…js` module 83050 — device table, Jet75-II entry `0x41E4_0x211A`
  - `1266-…js` module 45474 — packet framing / command numbers (class `b`, packets `P`, `D`)
  - `1266-…js` module 14905 — struct (de)serializers: `ed`/`ec` FuncConfig, `eh`/`eu` key trigger, `er`/`eo` light
  - `3996-…js` @~680400 — `DebugAndCalibrationDetector`; @~688700 — depth-stream consumer
  - beautified copies: `work/webdriver-modules/`
- Firmware `work/firmware/V115/BIN_138.bin` — USB device/report descriptors at 0xe188–0xe2a0

## Device identity

| Item | Value |
|---|---|
| VID:PID (normal) | `0x41E4:0x211A` ("Jet 75(电竞版)", driver `type:122`) |
| VID:PID (bootloader) | `0x41E4:0x211B` |
| Latest FW known to driver | `fwVersion:116`, `minFwVersion:108` |
| Config interface | interface 1, Usage Page 0x0001 / Usage 0x0000, 64-byte IN + 64-byte OUT, **no report ID** (report id 0) |
| Max keys | 128 (`G`), 4 layers (`F$`), 4 onboard profiles (`q7`) |

## Packet framing (module 45474, class `P`)

Host → device, 64 bytes, zero padded:

| Byte | Meaning |
|---|---|
| 0 | flag `0x55` |
| 1 | command |
| 2 | 0 |
| 3 | checksum = `(size + off_lo + off_hi + 0 + Σdata) & 0xFF` (lodash `sum` over bytes 4..end) |
| 4 | size (read: bytes requested; write: bytes of data), ≤ 56 |
| 5,6 | offset, little endian (lo, hi) |
| 7 | 0 |
| 8..63 | data (writes only) |

Device → host reply: byte0 `0xAA`, byte1 = same command, byte4 = size, bytes5–6 = offset,
data from byte 8. A reply is matched when flag/command/offset agree.
Transfers > 56 bytes are split into 56-byte chunks with increasing offset.

Bootloader/ROM packets (class `D`) are raw (no 0x55 header) and are acknowledged by a reply
whose byte0 = 0 and byte1 = 0 (byte1 = 1 → error).

## Commands

| Cmd | Dir | Offset formula | Size | Meaning |
|---|---|---|---|---|
| 0x03 | R | 0 | 56 | Info: `fw_ver = b[1]<<8 \| b[0]` (hex), `b[6]` bit0 report-rate-enhanced, bit1 U3 mode |
| 0x04 / 0x0E | R / W | 0 | 56 | Base info (active profile etc.) |
| 0x05 / 0x06 | R / W | `64*profile` | 64 | **FuncConfig** of a profile (performance + lighting) |
| 0x07 / 0x08 | R | `512*layerArea` | `3*maxKeyCount` | Key matrix (default / user) |
| 0x09 | W | `512*layer + 3*key + profile*512*4` | 3/key | **User key mapping** |
| 0x0A / 0x0B | R / W | `512*profile + 3*key` | 3/key | Per-key RGB (custom light) |
| 0x0C / 0x0D | R / W | macro area | | Macros |
| 0xA0 | R | `1024*profile + 8*key` | 8/key (1024 all) | **Per-key trigger settings** |
| 0xA1 | W | `1024*profile` | 8/key | Write all key trigger settings |
| 0xA2 / 0xA3 | R / W | `768*profile (+24*i)` | 24/entry | DKS (dynamic keystroke) |
| 0xA4 / 0xA5 | R / W | `256*profile` | | MT (mod-tap) |
| 0xA6 / 0xA7 | R / W | `128*profile` | 6/entry | TGL (toggle) |
| 0xA8 | W | 0 | `[0]` | startCalibration |
| 0xA9 | W | 0 | `[0]` | endCalibration |
| 0xB1 | W | 0 | `[0]` | reStartCalibration |
| 0xDC/0xDD/0xDF | W | | | "music"/stream colours (side2 / main / side) |
| 0xDE | R | 0 | `3*lightKeys` | Realtime light colours |
| 0xEE | W | 0 | `[profile or 0xFF]` | Factory reset |
| 0xF1 / 0xF2 | R / W | `1024*profile + off` | | Custom data area (profile names @336, feature support @616, light effect JSON @840/896, custom param @280, light store @728) |

ROM update (raw packets): enter boot `5F 06 00 52 01 00 00 00 51 00 AA BB`; erase `81 07 …`;
data `80 len addr32le data[32]`; verify `82 …`; end `83 01 00 00 00`; success `84 01 00 00 00`.

## FuncConfig (cmd 0x05/0x06, 64 bytes per profile) — `ed`/`ec`, `er`/`eo`

| Byte | Bits | Field |
|---|---|---|
| 1 | 0–3 | macMode |
| 4 | 0–3 / 4–7 | reportRate / tickRate |
| 5 | | deadZone |
| 6 | 0,1,2 | lockWin, lockAltTab, lockAltF4 |
| 7 | 0 | tachyonMode |
| 7 | 1 | bottom rapid-trigger mode |
| 7 | 2 | travelCheck ("auto check") |
| 7 | **3** | **debugMode — enables key-depth streaming** |
| 7 | 5–7 | debounceLevel |
| 8–16 | | main light: effect, brightness, 4-speed, direction, customColorDisabled, colorIndex, R, G, B |
| 20 | | rtsmart |
| 23 | | lightScopeType index |
| 24–31 | | side light: effect, brightness, 4-speed, customColorDisabled, ?, R, G, B |
| 32 | 0–6 / 7 | RF battery / charging |
| 33–38 | | roller type, work mode, sleep time, sleep mode, RF/BT channel, 2.4G report rate |
| 40–42 | | calibration RGB |
| 44–51 | | side-light-2 effect block (if present) |

The driver always does read-modify-write: read 0x05, change bits, write the full 64 bytes with 0x06.

## Per-key trigger (cmd 0xA0/0xA1, 8 bytes per key) — `eh`/`eu`

| Byte | Field |
|---|---|
| 0 | `0xA0 \| switchType` (low nibble = switch type) |
| 1 | low nibble keyMode, high nibble priority |
| 2, 3.bit0 | keyActuation − 1 (9-bit value, units = driver precision, 0.01 mm on Jet75-II) |
| 3 bits 3–4 / 1–2 | pressPrecision / releasePrecision |
| 4, 5.bit0 | rtPress − 1 (rapid-trigger press sensitivity) |
| 5 bits 1–7 | pressDeadzone |
| 6, 7.bit0 | rtRelease − 1 |
| 7 bits 1–7 | releaseDeadzone |

## Key mapping entry (cmd 0x09) — 3 bytes

`[type, code1, code2]`. Plain keys use HID keyboard usage codes; other `type` values select
consumer, mouse, macro (`LK`), Fn/layer (`type 240`) etc. (full type table: not yet extracted).

## Key-depth streaming — when and how

Driver logic (`DebugAndCalibrationDetector`, chunk 3996):

1. Only on the **"trigger" (触发设置) tab**, while the page is **focused and visible**, and the
   **calibration dialog is open** (`calibration` state = true). It is opened by the button with class
   `trigger-calibration-button` (i18n key "388", below the switch-type selector; chunk 9849 @~201000),
   or by the "calibrate now" prompt after a firmware upgrade.
2. `openDebug(profile)`: read FuncConfig (0x05), set byte 7 bit 3, write back (0x06).
   If the window regains focus, it also sends `startCalibration` (0xA8).
3. Leaving the tab / losing focus / unloading: `closeDebug` (clear bit 3) and `endCalibration` (0xA9).
4. Safety net: if `debugMode` is still set while on any other tab, the driver clears it after 1 s
   ("debugMode 应该只在触发设置时开启").

While enabled the keyboard sends unsolicited 64-byte IN reports on the config interface (EP 0x82).

**Wire-verified in `captures/jet75-session2-calibration.pcapng`** (calibration dialog opened 3×):
sequence per open = `0x06` FuncConfig with byte 7 `0x06→0x0E` (debugMode=1), then `0xA8`;
on close `0xA9`, then `0x06` with byte 7 back to `0x06`. 778 stream packets arrived only between
`0xA8` and `0xA9`. (Whether bit 3 alone, without `0xA8`, already streams is still untested.)
Idle keys are reported round-robin; a moving key is reported about every 16 ms.

| Byte | Meaning | Evidence |
|---|---|---|
| 0 | `0xA0` | observed |
| 1, 2, 3 | key `[type, code1, code2]`, layer-0 default code: `10 00 <HID usage>` for normal keys (`0x04` A, `0x16` S, `0x07` D…), `10 <mod-bit> 00` for modifiers (`10 08 00` = LGUI), `f0 ff 01` = Fn | observed |
| 4, 5 | big-endian 0…~1600, monotonic but non-linear in travel — probably the filtered sensor delta (inferred) | observed values |
| 6, 7 | **travel, big endian, 0.01 mm**: 0 at rest → 341 (3.41 mm) at bottom; driver treats ≤ 10 as released | observed + driver |
| 8 | idle `0xDD`; while moving, 0…29 and resets — matches rapid-trigger accumulated distance with rtPress/rtRelease = 30 (0.30 mm) (inferred) | observed |
| 9 | logical key state: `0x01` pressed, `0xFF` released (flips to 0xFF after 0.29–0.30 mm upward movement = rapid-trigger release) | observed |
| 10 | per-key calibration status: `0xFF` → `0x00` → `0x01` during calibration (driver stores it as calibrationKeyStatus) | observed + driver |
| 11–13 | decimal digits of the stored bottom calibration value (firmware: see "Stream packet" below) | observed + firmware |
| 14–15 | `01 55` = max travel 341 (3.41 mm) of the switch type | observed + firmware |
| 16, 17 | big endian ≈ `0x2FFF` at rest → `0x1B5C` at bottom: likely raw Hall ADC value (inferred) | observed |
| 21 | rises during a press, holds, resets to 0 at rest — unknown (peak value?) | observed |
| 23 | counter while held at bottom — unknown | observed |

Other unsolicited IN reports: `0xA1` profile switched (b2 = index), `0xA4` profile pre-switch,
`0xA2` reset, `0xA3` FuncConfig changed on-board, `0xAA 0xFA` light, `0xFA 0xFB …` status
(charging / battery / communication). `0xAA` + cmd 0x04/0x05/0x06/0xF1/0xF2 are chunk-update notifies.

## Firmware view (V116, static decompilation, 2026-11-22)

Image: `samples/firmware-cdn/update_jet75II_v116.bin` (official CDN file; same build "Aug 28 2025 10:45:06"
as the keyboard reports via 0x03 → `fw_ver 0x0116`). MCU: WCH CH32V30x (QingKe RISC-V, USBHS @0x40023400,
PFIC @0xE000E000, UID read from 0x1FFFF7E8). Application is linked at **flash 0x5000** (bootloader below;
the app checks the marker `0x211B41E4` at 0x08004C00).

Tooling: stock Ghidra mis-decodes the WCH "XW" compressed byte/half ops (they reuse c.fld/c.fsd/c.fldsp/c.fsdsp
and Q0 funct3=100). A local Ghidra language `RISCV:LE:32:wch-qingke` was added
(`~/.local/opt/ghidra_12.2_PUBLIC/Ghidra/Processors/RISCV/data/languages/riscv.{wch.slaspec,xwchc.sinc,wch.ldefs}`).
ELF wrapper: `work/firmware/V116/jet75II_v116_b5000.elf`; full decompilation: `work/ghidra-out/v116_b5000.c`
(286 functions). REA's Ghidra lane pins the stock RV32 language, so this part was done with Ghidra headless directly.

Key functions: `fn_5f84` packet handler (switch table @0x13840, 243 entries), `fn_5ed8` FuncConfig write,
`fn_94b4` boot/config load, `fn_b8ee` stream scheduler, `fn_b76e` stream packet builder, `fn_ba30` calibration,
`fn_1239e` main-loop deferred flash saver, `fn_11e5e` flash write.

### Packet handling (fn_5f84)

- byte 0 `0x5F` → raw bootloader-style packet; otherwise must be `0x55`.
- **byte 2 = XOR key**: if non-zero, bytes 3..(size+4) are XOR-ed with it (driver always sends 0).
- checksum (byte 3) = sum of bytes 4..(size+7); size ≤ 0x38.
- bad checksum → reply byte0 `0xAB`, payload ASCII `cserr`; unknown command → payload `unknw`.
- reply byte0 `0xAA`, same framing, same XOR applied.

### Command table (firmware)

| Cmd | Firmware action | Flash region (app addr) |
|---|---|---|
| 0x01 / 0x02 | set / clear a host flag (gp-0x7cc) | – |
| 0x03 | fw ver `0x0116` + build date string | – |
| 0x04 / 0x0E | read / write base info (0x0E writes flash, reloads profile) | 0x20200 (0x100) |
| 0x05 / 0x06 | read / **write FuncConfig → flash**, reload RAM copy of active profile; if report-rate nibble changed → reboot | 0x20100 + 64·profile |
| 0x07 | default keymap (const) | 0x17784 |
| 0x08 / 0x09 | user keymap read / write (≤ 0x2000) | 0x25100 |
| 0x0A / 0x0B | per-key colours read / write | 0x24900 |
| 0x0C / 0x0D | macros read / write (≤ 0x2000) | 0x22900 |
| 0xA0 / 0xA1 | key trigger read / write (≤ 0x1000), then `fn_b530` applies | 0x27100 |
| 0xA2 / 0xA3 | DKS (≤ 0xC00) | 0x21D00 |
| 0xA4 / 0xA5 | MT (≤ 0x400) | 0x20300 |
| 0xA6 / 0xA7 | TGL (≤ 0x200) | 0x20700 |
| 0xA8 | start calibration session (see below) | – (RAM) |
| 0xA9 | end calibration session | – |
| 0xB0 | 12-byte chip UID | – |
| 0xB1 | reset calibration of **all** keys to default (0x73A), saved to flash by main loop | 0x20900… |
| 0xDD | live "music" colours → RAM 0x20004BE0 (no flash) | – |
| 0xDE | realtime colours from RAM | – |
| 0xEE | factory reset (flag + scope byte) | – |
| 0xF1 / 0xF2 | custom data read / write (≤ 0x1000) | 0x20D00 |

Every write command except 0xDD/0xA8/0xA9 erases+writes flash (CH32 flash: limited erase cycles).

### What really enables the depth stream

`fn_b8ee` (called from the main loop) sends stream packets only when **bit 3 of byte 7 of the RAM copy of
FuncConfig** (0x200053D7) is set. Nothing else is required.

- After every host packet the stream pauses for ~25 scheduler ticks (counter reset in fn_5f84).
- Then at most one key per 10 ticks (`fn_6d92(...,10)`):
  - normal mode: round-robin over 80 key slots, sends only keys whose travel (values < 10 forced to 0) changed;
  - after 0xA8: two complete sweeps of all 80 keys, then normal mode.
- At boot (`fn_94b4`) the RAM bit is **cleared**, so a debugMode bit left in flash never streams after replug.

Ways to set the RAM bit:
1. `0x06` FuncConfig write with bit 3 (what the driver does) — RAM + **flash write** of the profile's 64 bytes.
2. `0xA8` — sets the bit in RAM only, **but also starts a calibration session**.

### 0xA8 / 0xA9 calibration session

0xA8 (only if not already active): TIM2 period 149→299, set RAM debug bit, calibrating=1, full-sweep counter=2,
per-key "recalibrate" array (0x200038D0, 80 bytes) = 0xFF, `fn_b9c8` snapshots baselines.
While active, in `fn_ba30` any key that is **pressed** gets its stored bottom value reset to 0x73A
(status 0); after two stable deep presses its status goes 0 → 1 → 0xFF and the new bottom value is stored;
the main loop then **writes the calibration tables to flash** (0x20900/0x20A00/0x20B00).
0xA9: calibrating=0, TIM2 back to 149, recalibrate array = 0 (does not clear the RAM debug bit by itself —
the driver clears it with a separate 0x06).

**For custom tools:** use the 0x06 route (bit 3) for monitoring; do not send 0xA8 unless you intend to
recalibrate, because partial-but-steady presses during a session can be stored as the new "bottom".

### Stream packet (fn_b76e), 64-byte IN report on EP 0x82

| Byte | Content | Source |
|---|---|---|
| 0 | `0xA0` | |
| 1–3 | layer-0 default key `[type, code1, code2]` | const table 0x1629C |
| 4–5 | BE u16 per-key internal value (0x20002D90[i]) — meaning still open | |
| 6–7 | **travel, BE, 0.01 mm**; < 10 → 0; ≥ max−5 → max | 0x20003150[i] |
| 8 | per-key byte 0x20003420[i] (rapid-trigger accumulator, per capture) | |
| 9 | key state (0x20003880[i]: 1 = pressed) | |
| 10 | calibration status (0x200027F4[8i]: 0xFF calibrated, 0 / 1 in progress) | |
| 11–13 | stored bottom calibration value /4 as **decimal digits** (thousands, hundreds, tens) | 0x200027F0[2i] |
| 14–15 | max travel of the switch type, BE (0x0155 = 341 → 3.41 mm) | table 0x13FEC |
| 16–17 | raw Hall ADC, BE | 0x20002070[i] |
| 18–19 | baseline (rest) ADC /4, BE | 0x200022F0[i] |
| 20–21 | BE u16 0x200034C0[i] (peak-like, per capture) | |
| 22–23 | BE u16 held-at-bottom counter (increments while travel > 3.00 mm) | 0x20003600[i] |

Max travel by switch type (byte 0 low nibble of the 0xA0 trigger entry; driver `switchOptions` 0–9):
types 0–7 → 341 (3.41 mm), 8 `ztLb` → 351, 9 `ztKy` → 331.

### Precise depth (firmware fn_bd6c)

Per scan and key: raw ADC (0x20004F10) → adaptive IIR (alpha from table 0x20000214 by |Δ|>>4) →
second low-pass (accumulator ×8) = **filtered ADC** (stream bytes 16–17). If filtered ADC < baseline:
`norm = (baseline − adc) × 1024 / calibration`, clamped to 1600 (stream bytes 4–5). Travel (stream
bytes 6–7) = index+1 of the last entry ≤ norm in the per-switch-type curve (app 0x1403C + type×800,
`max_travel` u16 entries; extracted to `tools/jet75_curves.json` by `tools/extract_curves.py`).

- The travel field is only recomputed while |raw − smoothed| > 5, so it freezes when a key settles —
  that is the "lag" seen in captures. `norm` is recomputed every scan but not once the ADC is back at or
  above the baseline.
- Lag-free estimate used by the tools: 0 if baseline − filtered ADC < 20, else curve(norm) interpolated
  between 0.01 mm steps (`jet75.precise_depth`). Checked on `captures/depth-test2.jsonl`: equal to the
  firmware field to ±0.01 mm on settled samples, correct where the firmware field is frozen.
- Stream bytes 20–21 = peak |raw − smoothed| (noise peak) per key.
- V116 curve for switch type 7 has one non-monotonic entry (index 315: 1398 > 1310) — firmware data bug.
- Stream speed is fixed by firmware: one packet per 10 ticks of the TIM3 ISR (fn_7bb8), not host-configurable.

### Host traffic stops the depth stream (wire-verified)

Every host packet resets the stream's pause counter (fn_5f84), and the stream resumes only after
~25 scheduler periods (~0.6 s) without host packets. Test `captures/dd-test2.*`: 5 s phases while
WASD were moving — quiet: 72 / 48 / 72 packets; sending 0xDD live colours at 10 Hz: 0 / 0 / 0.
So depth-reactive lighting (read depth + write colours continuously) is not possible over this
interface; any polling or settings traffic also blanks the stream while it lasts.

### Lighting and key codes (verified 2026-11-22)

- Light index = key matrix index (0xDD test lit exactly W/A/S/D at indices 71/10/61/11).
- 0xDD live colours: payload = RGB triplets from offset 0 (3 bytes per key), RAM only; without
  updates the keyboard returns to its stored effect within a few seconds. 25 fps × 240 bytes works.
- Per-key stored colours: 0x0A / 0x0B, offset 512·profile + 3·index.

#### Worked example: live colour packet (0xDD), key W (matrix index 71) red

Written to the config interface hidraw node (descriptor starts `05 01 09 00`, e.g. `/dev/hidraw3`)
as 65 bytes:

```
00 | 55 DD 00 D7 | 03 D5 00 00 | FF 00 00 | 00 … 00 (53 bytes)
```

| Byte | Value | Meaning |
|---|---|---|
| (pre) | `00` | Linux hidraw report ID; the interface has no report IDs, the kernel strips it — not sent over USB |
| 0 | `55` | request flag (`0x5F` = bootloader packets; replies use `AA`, errors `AB`) |
| 1 | `DD` | command: live colours to RAM |
| 2 | `00` | XOR key (0 = none; non-zero XORs bytes 3..size+7) |
| 3 | `D7` | checksum = `(03 + D5 + 00 + 00 + FF + 00 + 00) & FF` = Σ bytes 4..(size+7) mod 256 |
| 4 | `03` | size = data bytes (3 per key; ≤ 54 for whole keys, protocol max 56) |
| 5–6 | `D5 00` | offset, little endian = 3 × key index (3 × 71 = 213 = 0x00D5) |
| 7 | `00` | reserved (included in the checksum) |
| 8–10 | `FF 00 00` | R, G, B of key 71; more triplets follow for index+1, +2 … if size > 3 |
| 11–63 | `00…` | padding to the 64-byte report |

Firmware (V116 `fn_5f84`, case 0xDD): requires offset + size ≤ 0x180 (128 keys × 3), copies the data to
RAM 0x20004BE0 + offset, switches to live mode and (re)arms a timeout — without new packets the keyboard
returns to its stored effect within a few seconds, so refresh at 10–30 Hz. Nothing is written to flash.
Whole keyboard: 240 bytes in 5 packets at offsets 0, 54, 108, 162, 216. Reply: `AA DD 00 sum 03 D5 00 00 …`
(same command/size/offset echoed); other input reports (e.g. `A0` depth packets) may arrive in between.

Shell one-shot (repeat in a loop to keep it lit):
`{ printf '\x00\x55\xdd\x00\xd7\x03\xd5\x00\x00\xff\x00\x00'; head -c 53 /dev/zero; } > /dev/hidraw3`
- Effect ids (FuncConfig byte 8): 0 static, 1 auroramapping, 2 shadowsteps, 3 tranquility, 4 pulsation,
  5 flowerdance, 6 tidalrhythm, 7 surgingtide, 8 colorfulfountain, 9 galaxyroaming, 10 spiral,
  11 surgerhythm, 12 ocean, 13 rippledance, 14 ripple, 15 instantflash, 16 matrixshadows,
  17 melodypiano, 18 flowdance, 19 raining, 20 stars, 21 fireworks, 22 rippleflow, 23 swordarray, 255 off.
- Key code types: 0x10 keyboard (code1 = modifier bits, code2 = HID usage; combos allowed),
  0x30 consumer (usage little endian in code1/code2: e2 Mute, e9 Vol+, ea Vol−, cd Play, b5 Next,
  b6 Prev, b7 Stop), 0x70 macro (code1 = macro index), 0xF0 special (ff NN = Fn/layer NN, fa 00 rapid
  trigger toggle, 34 00 brightness, 1e 00 BackHome), 00 00 00 = disabled, 10 00 00 in upper layers =
  transparent, f0 f0 f0 = unused slot.
- Writes verified by read-back with `tools/jet75ctl.py` (trigger 0xA1 8-byte entry, remap 0x09,
  FuncConfig light bytes, per-key colour 0x0B) and full restore to the original backup (0 differences).

## Open questions

- ~~Unit of travel / keyActuation~~ — 0.01 mm. ~~0xA8 required?~~ — no, RAM bit 3 alone streams.
  ~~FuncConfig persisted?~~ — yes, 0x06 writes flash. ~~Does 0xA8 touch calibration?~~ — yes (see above).
- ~~Wire test of "0x06 with bit 3, no 0xA8"~~ — confirmed 2026-11-22 with `tools/jet75_depth.py`
  (`captures/depth-test1.*`): stream starts right after the 0x06 write, travel 0…3.41 mm, raw ADC
  ≈12200–12800 at rest → ≈6740 at bottom; a moving key is reported every ~24 ms; bit restored to 0x06 on exit.
  Run 2 (`captures/depth-test2.*`, 694 packets / 99 s, 14 keys, up to 7 keys moving at once): packets are
  globally rate-limited (≈24 ms apart, shared by all moving keys), so the stream suits visualisation, not
  precise timing. Release state flips ≈0.3 mm above bottom (rapid trigger, rtRelease 0.30 mm).
  On fast releases 36 packets showed travel 2.5–3.1 mm while the raw ADC (bytes 16–17) was already at
  rest level: the travel field lags the ADC; use ADC vs. rest/bottom if instantaneous position matters.
- Meaning of stream bytes 4–5 and 20–21; full key `type` table; light effect id list.

### Advanced keys and the NKRO descriptor bug (2026-11-22)

- Binding = keymap code of the key: DKS `90 slot 00`, TGL `91 slot 00`, MT `92 slot time/10ms`,
  RS `93 slot partner`, SOCD `94 slot partner`, LT `95 slot partner`. RS/SOCD/LT use 2 MT slots per pair
  (`{click=own, down=partner}`); SOCD mode in the trigger `priority` nibble (0 last-input, 1 key1, 2 key2,
  3 cancel; partner gets 1↔2 swapped). Tables per profile: MT 0x20300+0x100·p (6 B×32), TGL 0x20700+0x80·p
  (3 B×32), DKS 0x21D00+0x300·p (24 B×32; points in 0.1 mm). Tool: `jet75ctl.py adv ...`.
- Verified on the keyboard (`captures/adv-test-events.txt`, `adv-functions-test.txt`, `rs-lt-test2.txt` + `rs-lt-depth2.jsonl`):
  MT (tap Esc / hold LCtrl); DKS (4 phase taps W/E/R/T → `wert` per full press, `wt` on a partial press);
  TGL (B held between two presses); RS (the deeper key wins: 0 of 20 single-key reports picked the
  shallower key; both keys are sent when depths are ~equal or a key is within ~0.2 mm of the bottom —
  firmware rule `own travel >= max − 0x14` in fn_7c88); LT (releasing one key taps the other for ~40–50 ms).
  SOCD works once the NKRO descriptor is fixed (see below).
- **Firmware bug:** SOCD key output is written only to the NKRO bitmap report (ID 1, interface 2),
  and that report is declared `Input(Data,Array)` (0x81 0x00) instead of `Variable` (0x81 0x02).
  Hosts decode every set bit as usage 0x01 (ErrorRollOver) and drop it. Wire proof
  `captures/nkro-socd-raw.txt`: raw ID-1 reports alternate A/D while SOCD keys are pressed, evdev shows
  no A/D events. The same affects normal keys beyond the first 6 (they overflow into that report).
  **Fixed on Linux** with a HID-BPF report-descriptor fixup (`tools/hid-bpf/`, byte 23: 0x81 0x00 → 0x81 0x02).
  Verified (`captures/nkro-socd-fixed.txt`): SOCD A/D 0 → 366 key events, never both down; max simultaneous
  keys in Linux 6 → 19. Windows has no equivalent user fix; only MCHOSE can correct the firmware descriptor.
- Driver weaknesses: DKS combos silently changed by the encoder; slot re-numbering on delete relies on
  rewriting every bound key; RS uses the 6-key buffer (lost when 6 keys are already down).
