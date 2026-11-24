// SPDX-License-Identifier: GPL-2.0
/* Attach jet75_fix.bpf.o to one HID device and pin the link so it survives this process.
 *   jet75-hid-fix-loader HID_ID PIN_PATH
 * HID_ID is the hex suffix of /sys/bus/hid/devices/0003:41E4:211A.XXXX (as a number).
 * Remove the fix again with: rm PIN_PATH  (the kernel then re-probes the original descriptor). */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <bpf/libbpf.h>
#include "jet75_fix.skel.h"

int main(int argc, char **argv)
{
	struct jet75_fix_bpf *skel;
	struct bpf_link *link;
	int err;

	if (argc != 3) {
		fprintf(stderr, "usage: %s HID_ID PIN_PATH\n", argv[0]);
		return 2;
	}
	skel = jet75_fix_bpf__open();
	if (!skel) {
		fprintf(stderr, "open failed: %s\n", strerror(errno));
		return 1;
	}
	{
		/* struct hid_bpf_ops starts with 'int hid_id'; older bpftool skeletons have no
		 * struct_ops shadow members, so set it through the map's initial value. */
		size_t sz = 0;
		int *ops = bpf_map__initial_value(skel->maps.jet75_fix, &sz);

		if (!ops || sz < sizeof(int)) {
			fprintf(stderr, "cannot access struct_ops initial value (libbpf too old?)\n");
			err = -1;
			goto out;
		}
		ops[0] = (int)strtol(argv[1], NULL, 0);
	}
	err = jet75_fix_bpf__load(skel);
	if (err) {
		fprintf(stderr, "load failed: %d (run as root; kernel needs CONFIG_HID_BPF)\n", err);
		goto out;
	}
	link = bpf_map__attach_struct_ops(skel->maps.jet75_fix);
	if (!link) {
		err = -errno;
		fprintf(stderr, "attach to HID device %s failed: %s\n", argv[1], strerror(errno));
		goto out;
	}
	err = bpf_link__pin(link, argv[2]);
	if (err) {
		fprintf(stderr, "pin %s failed: %s\n", argv[2], strerror(-err));
		bpf_link__destroy(link);
		goto out;
	}
	printf("attached to HID device %s, pinned at %s\n", argv[1], argv[2]);
out:
	jet75_fix_bpf__destroy(skel);
	return err ? 1 : 0;
}
