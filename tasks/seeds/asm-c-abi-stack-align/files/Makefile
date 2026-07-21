CC ?= cc
PYTHON ?= python3
CFLAGS := -std=c17 -O2 -Wall -Wextra -Werror -Iinclude
LDFLAGS := -Wl,--no-as-needed
BUILD := build
RUNNER := $(BUILD)/abi_test

.PHONY: all test clean

all: $(RUNNER)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/accumulate.o: src/accumulate.S | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/abi_fixture.o: tests/abi_fixture.S | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/test_accumulate.o: tests/test_accumulate.c include/accumulate.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(RUNNER): $(BUILD)/accumulate.o $(BUILD)/abi_fixture.o $(BUILD)/test_accumulate.o
	$(CC) $(LDFLAGS) $^ -o $@

test: $(RUNNER)
	$(RUNNER)
	$(PYTHON) -B tests/check_unwind.py $(RUNNER)

clean:
	rm -rf $(BUILD)
