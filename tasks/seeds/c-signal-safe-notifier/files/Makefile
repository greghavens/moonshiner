CC ?= cc
CPPFLAGS ?= -D_POSIX_C_SOURCE=200809L -Iinclude
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -pedantic

.PHONY: all test clean

all: build/test_notifier

build:
	mkdir -p build

build/notifier.o: src/notifier.c include/notifier.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread -c $< -o $@

build/test_notifier.o: tests/test_notifier.c include/notifier.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -pthread -c $< -o $@

build/test_notifier: build/notifier.o build/test_notifier.o
	$(CC) $(CFLAGS) -pthread \
		-Wl,--wrap=poll -Wl,--wrap=clock_gettime $^ -o $@

test: build/test_notifier
	./build/test_notifier

clean:
	rm -rf build
