#!/usr/bin/env python3
"""Wrap a raw RV32 firmware image into a minimal ELF so Ghidra/REA can load it
with the right memory map.

Jet75-II (WCH QingKe) layout, derived from the reset handler at image+0x4802:
  flash image linked at 0x5000 (bootloader below), .data LMA = image offset
  DATA_OFF, VMA 0x20000000, .bss up to BSS_END.

usage: bin2elf.py IN.bin OUT.elf [--base 0x5000] [--data-off 0x12f64]
                  [--data-size 0x280] [--bss-end 0x200053f8]
"""
import argparse
import struct

EM_RISCV = 243
EF_RISCV_RVC = 0x1


def riscv_attributes(arch):
    # Tag_RISCV_arch (5) as NTBS inside a Tag_File (1) sub-subsection
    attrs = bytes([5]) + arch.encode() + b"\0"
    file_sub = bytes([1]) + struct.pack("<I", 1 + 4 + len(attrs)) + attrs
    vendor = b"riscv\0"
    sub = struct.pack("<I", 4 + len(vendor) + len(file_sub)) + vendor + file_sub
    return b"A" + sub


def build(img, base, data_off, data_size, ram, bss_end, arch):
    data = img[data_off:data_off + data_size]
    attr = riscv_attributes(arch)
    shstr = b"\0.text\0.data\0.bss\0.shstrtab\0.riscv.attributes\0"
    ehsize, phentsize, shentsize = 52, 32, 40
    phnum, shnum = 2, 6
    ph_off = ehsize
    text_off = 0x1000
    data_file_off = (text_off + len(img) + 0xFFF) & ~0xFFF
    attr_off = data_file_off + len(data)
    shstr_off = attr_off + len(attr)
    sh_off = (shstr_off + len(shstr) + 3) & ~3

    out = bytearray(sh_off + shnum * shentsize)
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\0" * 8
    struct.pack_into("<16sHHIIIIIHHHHHH", out, 0, ident, 2, EM_RISCV, 1,
                     base, ph_off, sh_off, EF_RISCV_RVC, ehsize, phentsize,
                     phnum, shentsize, shnum, 4)
    # PT_LOAD flash (R X), PT_LOAD RAM (R W) with .data contents + .bss
    struct.pack_into("<IIIIIIII", out, ph_off, 1, text_off, base, base,
                     len(img), len(img), 5, 0x1000)
    struct.pack_into("<IIIIIIII", out, ph_off + phentsize, 1, data_file_off,
                     ram, base + data_off, len(data), bss_end - ram, 6, 0x1000)
    out[text_off:text_off + len(img)] = img
    out[data_file_off:data_file_off + len(data)] = data
    out[attr_off:attr_off + len(attr)] = attr
    out[shstr_off:shstr_off + len(shstr)] = shstr

    def sh(i, name, typ, flags, addr, off, size):
        struct.pack_into("<IIIIIIIIII", out, sh_off + i * shentsize, name, typ,
                         flags, addr, off, size, 0, 0, 4, 0)

    sh(0, 0, 0, 0, 0, 0, 0)
    sh(1, 1, 1, 0x6, base, text_off, len(img))                 # .text AX
    sh(2, 7, 1, 0x3, ram, data_file_off, len(data))            # .data WA
    sh(3, 13, 8, 0x3, ram + len(data), data_file_off + len(data),
       bss_end - ram - len(data))                              # .bss WA NOBITS
    sh(4, 18, 3, 0, 0, shstr_off, len(shstr))                  # .shstrtab
    sh(5, 28, 0x70000003, 0, 0, attr_off, len(attr))           # .riscv.attributes
    struct.pack_into("<I", out, sh_off + 5 * shentsize + 32, 1)  # sh_addralign
    return bytes(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("inp")
    p.add_argument("out")
    num = lambda s: int(s, 0)
    p.add_argument("--base", type=num, default=0x5000)
    p.add_argument("--data-off", type=num, default=0x12F64)
    p.add_argument("--data-size", type=num, default=0x280)
    p.add_argument("--ram", type=num, default=0x20000000)
    p.add_argument("--bss-end", type=num, default=0x200053F8)
    # WCH QingKe V4 is RV32IMAC(+F on some parts) with Zicsr; assumption, not read from the image
    p.add_argument("--arch", default="rv32i2p1_m2p0_a2p1_c2p0_zicsr2p0_zifencei2p0")
    a = p.parse_args()
    img = open(a.inp, "rb").read()
    open(a.out, "wb").write(build(img, a.base, a.data_off, a.data_size,
                                  a.ram, a.bss_end, a.arch))
    print(f"wrote {a.out}: flash {a.base:#x}+{len(img):#x}, "
          f".data {a.ram:#x}+{a.data_size:#x} (from img+{a.data_off:#x}), "
          f".bss to {a.bss_end:#x}")


if __name__ == "__main__":
    main()
