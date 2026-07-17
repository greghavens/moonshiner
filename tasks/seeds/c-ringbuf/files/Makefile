CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_ringbuf: test_ringbuf.c ringbuf.c ringbuf.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_ringbuf.c ringbuf.c

.PHONY: test clean
test: test_ringbuf
	./test_ringbuf

clean:
	rm -f test_ringbuf
