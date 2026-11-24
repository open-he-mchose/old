# Ghidra language for the WCH QingKe core (Jet75-II firmware)

The keyboard MCU (WCH CH32V30x, QingKe RISC-V) uses WCH's "XW" compressed byte/halfword loads and
stores. They reuse the encodings of c.fld / c.fsd / c.fldsp / c.fsdsp and Q0 funct3=100, so stock
Ghidra decodes them as floating-point or bad instructions and the decompilation breaks.

`riscv.wch.slaspec` builds RV32IMAFC (FPSIZE 32, so the D-extension compressed ops are gone) plus
`riscv.xwchc.sinc` (encodings inferred from code context, see docs/protocol.md):

| Encoding | Instruction |
|---|---|
| Q0 funct3=001 | `qk.c.lbu rd', off(rs1')` — off = b12 \| b6-5<<1 \| b11-10<<3 |
| Q0 funct3=101 | `qk.c.sb` (same offset) |
| Q2 funct3=001 | `qk.c.lhu` — off = b6-5<<1 \| b12-10<<3 |
| Q2 funct3=101 | `qk.c.sh` |
| Q0 funct3=100, b12-11=0 | `qk.c.{lbu,sb,lhu,sh}sp rd', off(sp)` — b6 store, b5 half, off = b10-7 (×2 for half) |

## Install and use

```sh
L=~/.local/opt/ghidra_12.2_PUBLIC/Ghidra/Processors/RISCV/data/languages
cp tools/ghidra/riscv.wch.slaspec tools/ghidra/riscv.xwchc.sinc tools/ghidra/riscv.wch.ldefs "$L"/
(cd "$L" && ../../../../../support/sleigh riscv.wch.slaspec)       # -> riscv.wch.sla

python3 tools/firmware/make_elf.py samples/firmware-cdn/update_jet75II_v116.bin work/firmware/V116/jet75II_v116_b5000.elf
~/.local/opt/ghidra_12.2_PUBLIC/support/analyzeHeadless work/ghidra-proj jet75 \
    -import work/firmware/V116/jet75II_v116_b5000.elf -processor RISCV:LE:32:wch-qingke -cspec gcc \
    -scriptPath tools/ghidra -postScript DecompileAllToFile.java work/ghidra-out/v116_b5000.c
```

The stock RISC-V languages are not modified. REA's Ghidra lane pins the stock RV32 language, so this
step runs Ghidra headless directly.
