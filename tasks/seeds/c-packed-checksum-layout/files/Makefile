CC ?= cc
CPPFLAGS := -Iinclude
CFLAGS := -std=c11 -O2 -Wall -Wextra -Werror -pedantic
BUILD_DIR := build

.DEFAULT_GOAL := test

.PHONY: test clean

test: $(BUILD_DIR)/test-default $(BUILD_DIR)/test-packed-host
	./$(BUILD_DIR)/test-default
	./$(BUILD_DIR)/test-packed-host

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/test-default: src/telemetry.c test_checksum_layout.c include/telemetry.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) src/telemetry.c test_checksum_layout.c -o $@

$(BUILD_DIR)/test-packed-host: src/telemetry.c test_checksum_layout.c include/telemetry.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -fpack-struct=1 src/telemetry.c test_checksum_layout.c -o $@

clean:
	rm -rf $(BUILD_DIR)
