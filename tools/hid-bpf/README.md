# Jet75-II NKRO / Snap Tap fix (HID-BPF, Linux)

The keyboard (firmware V116) declares its NKRO report (interface 2, Report ID 1, 120-bit bitmap)
as `Input(Data,Array)` (`81 00`) instead of `Input(Data,Variable)` (`81 02`). Hosts read every set
bit as ErrorRollOver and drop it, so:

- Snap Tap / SOCD keys never arrive (the firmware sends them only through that report);
- any key beyond the 6-key rollover buffer is lost.

`jet75_fix.bpf.c` is a HID-BPF `hid_rdesc_fixup` that flips that single byte when the descriptor
matches the known layout. Nothing is written to the keyboard; unloading or replugging restores the
original descriptor.

Verified 2026-11-22 (`captures/nkro-socd-raw.txt` before, `captures/nkro-socd-fixed.txt` after):
SOCD A/D produced 0 → 366 key events in Linux (never both down: last-input works), maximum
simultaneously held keys 6 → 19.

## Build and use

```sh
sudo xbps-install -S clang bpftool libbpf-devel     # Void; any distro: clang, bpftool, libbpf headers
make -C tools/hid-bpf
sudo tools/hid-bpf/jet75-hid-fix load                # attach to the connected keyboard
tools/hid-bpf/jet75-hid-fix status                   # "fixed: ..." / "original (broken): ..."
sudo tools/hid-bpf/jet75-hid-fix unload              # remove
```

Automatic loading on every plug-in / boot:

```sh
sudo make -C tools/hid-bpf install                   # -> /usr/local/sbin/jet75-hid-fix{,-loader}
sudo cp tools/udev/71-jet75-hid-fix.rules /etc/udev/rules.d/ && sudo udevadm control --reload
```

Requirements: kernel with `CONFIG_HID_BPF=y` and BTF (`/sys/kernel/btf/vmlinux`), root to load.
The loader sets `hid_id` through the struct_ops map's initial value, so it also works with older
bpftool versions whose skeletons lack struct_ops members.
