CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -pedantic

.PHONY: all test clean

all: build/test_packed_record

build/test_packed_record: src/packed_record.c include/packed_record.h tests/test_packed_record.c
	@mkdir -p build
	$(CC) $(CFLAGS) -Iinclude src/packed_record.c tests/test_packed_record.c -o $@

test: build/test_packed_record
	./build/test_packed_record

clean:
	rm -rf build
