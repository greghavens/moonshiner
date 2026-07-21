.PHONY: test clean

test:
	mkdir -p build
	cc -std=c11 -O2 -Wall -Wextra -Werror -pedantic -fno-builtin-memcpy \
		-Iinclude src/ring_buffer.c tests/test_ring_buffer.c \
		-Wl,--wrap=malloc -Wl,--wrap=memcpy -o build/test_ring_buffer
	./build/test_ring_buffer

clean:
	rm -rf build
