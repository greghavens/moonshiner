CC ?= cc
CPPFLAGS := -Iinclude
CFLAGS := -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror

BUILD_DIR := build
TEST_BINARY := $(BUILD_DIR)/test_bounded_slice

.PHONY: all test clean

all: $(TEST_BINARY)

$(TEST_BINARY): src/bounded_slice.c tests/test_bounded_slice.c include/bounded_slice.h
	@mkdir -p $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) src/bounded_slice.c tests/test_bounded_slice.c -o $@

test: $(TEST_BINARY)
	./$(TEST_BINARY)

clean:
	$(RM) -r $(BUILD_DIR)
