// SPDX-License-Identifier: GPL-2.0
/*
 * HID-BPF report descriptor fix for the MCHOSE Jet75-II (VID 0x41E4, PID 0x211A), interface 2.
 *
 * The NKRO keyboard report (Report ID 1, 120 x 1-bit bitmap of usages 0x00-0x7C) is declared
 * Input(Data,Array) = 81 00 instead of Input(Data,Variable) = 81 02. Hosts therefore read every
 * set bit as array index 1 (ErrorRollOver) and drop it, which breaks Snap Tap / SOCD (the firmware
 * sends those keys only through this report) and every key beyond the 6-key rollover buffer.
 * This program flips that one byte; nothing else in the descriptor is touched.
 *
 * Descriptor head on firmware V116 (see docs/protocol.md):
 *   05 01 09 06 a1 01 85 01 05 07 19 00 29 7c 15 00 25 01 95 78 75 01 81 00 c0 ...
 *                                                                     ^^ offset 23
 */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

#define HID_MAX_DESCRIPTOR_SIZE 4096

extern __u8 *hid_bpf_get_data(struct hid_bpf_ctx *ctx, unsigned int offset, const size_t __sz) __ksym;

static const __u8 expected[24] = {
	0x05, 0x01, 0x09, 0x06, 0xa1, 0x01, 0x85, 0x01, 0x05, 0x07, 0x19, 0x00,
	0x29, 0x7c, 0x15, 0x00, 0x25, 0x01, 0x95, 0x78, 0x75, 0x01, 0x81, 0x00,
};

SEC("struct_ops/hid_rdesc_fixup")
int BPF_PROG(jet75_rdesc_fixup, struct hid_bpf_ctx *hctx)
{
	__u8 *data = hid_bpf_get_data(hctx, 0, HID_MAX_DESCRIPTOR_SIZE);
	int i;

	if (!data || hctx->size < 25)
		return 0;
	for (i = 0; i < 24; i++)
		if (data[i] != expected[i])
			return 0;          /* not the layout we know: leave the descriptor alone */
	data[23] = 0x02;               /* Input(Data,Variable,Absolute) */
	return 0;
}

SEC(".struct_ops.link")
struct hid_bpf_ops jet75_fix = {
	.hid_rdesc_fixup = (void *)jet75_rdesc_fixup,
};

char _license[] SEC("license") = "GPL";
