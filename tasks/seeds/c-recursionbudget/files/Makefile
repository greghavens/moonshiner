CC ?= cc
CFLAGS := -std=c17 -pedantic -Wall -Wextra -Wconversion -Wsign-conversion \
	-Wshadow -Wstrict-prototypes -Werror -O2
CPPFLAGS := -Iinclude
BUILD_DIR := build
TEST_BINARY := $(BUILD_DIR)/test_tree_decoder

.PHONY: all clean test source-contract

all: $(TEST_BINARY)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(TEST_BINARY): src/tree_decoder.c tests/test_tree_decoder.c \
		include/tree_decoder.h | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) src/tree_decoder.c tests/test_tree_decoder.c \
		-o $(TEST_BINARY)

source-contract:
	PYTHONDONTWRITEBYTECODE=1 python3 tests/test_source_contract.py

test: $(TEST_BINARY)
	./$(TEST_BINARY)
	$(MAKE) source-contract

clean:
	rm -f $(TEST_BINARY)
	rmdir $(BUILD_DIR) 2>/dev/null || true
