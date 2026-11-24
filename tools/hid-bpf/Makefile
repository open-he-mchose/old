# Build the Jet75-II HID-BPF descriptor fix. Needs: clang, bpftool, libbpf-devel, gcc.
CLANG   ?= clang
BPFTOOL ?= bpftool
ARCH    := $(shell uname -m | sed -e 's/x86_64/x86/' -e 's/aarch64/arm64/')

all: jet75-hid-fix-loader

vmlinux.h:
	$(BPFTOOL) btf dump file /sys/kernel/btf/vmlinux format c > $@

jet75_fix.bpf.o: jet75_fix.bpf.c vmlinux.h
	$(CLANG) -O2 -g -target bpf -D__TARGET_ARCH_$(ARCH) -I. -c $< -o $@

jet75_fix.skel.h: jet75_fix.bpf.o
	$(BPFTOOL) gen skeleton $< name jet75_fix_bpf > $@

jet75-hid-fix-loader: loader.c jet75_fix.skel.h
	$(CC) -O2 -Wall -I. $< -lbpf -o $@

install: jet75-hid-fix-loader
	install -Dm755 jet75-hid-fix-loader $(DESTDIR)/usr/local/sbin/jet75-hid-fix-loader
	install -Dm755 jet75-hid-fix $(DESTDIR)/usr/local/sbin/jet75-hid-fix

uninstall:
	rm -f $(DESTDIR)/usr/local/sbin/jet75-hid-fix-loader $(DESTDIR)/usr/local/sbin/jet75-hid-fix

clean:
	rm -f vmlinux.h jet75_fix.bpf.o jet75_fix.skel.h jet75-hid-fix-loader

.PHONY: all clean install uninstall
