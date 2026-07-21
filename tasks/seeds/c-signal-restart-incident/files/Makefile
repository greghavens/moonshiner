CC ?= cc
CPPFLAGS := -D_POSIX_C_SOURCE=200809L -Iinclude
CFLAGS := -std=c17 -O2 -Wall -Wextra -Werror -Wpedantic \
	-Wconversion -Wsign-conversion -Wshadow -Wstrict-prototypes \
	-Wmissing-prototypes -Wformat=2
LDFLAGS :=
LDLIBS :=

TARGET := build/signal_restart_harness
SOURCES := src/snapshot_reader.c tests/signal_restart_harness.c
HEADERS := include/snapshot_reader.h

.PHONY: all test clean

all: $(TARGET)

$(TARGET): $(SOURCES) $(HEADERS)
	@mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SOURCES) $(LDFLAGS) \
		-Wl,--wrap=read $(LDLIBS) -o $@

test: $(TARGET)
	./$(TARGET) incident/receiver.log incident/syscall_trace.log

clean:
	rm -rf build
