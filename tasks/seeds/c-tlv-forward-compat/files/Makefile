CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -pedantic -O2
CPPFLAGS ?= -Iinclude

.PHONY: all test clean

all: build/test_tlv

build/test_tlv: src/tlv.c tests/test_tlv.c include/tlv.h
	@mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/tlv.c tests/test_tlv.c -o $@

test: build/test_tlv
	./build/test_tlv

clean:
	rm -rf build
