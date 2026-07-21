CC = cc
CFLAGS = -std=c17 -O0 -Wall -Wextra -Werror -pedantic

.PHONY: all test clean

all: test_capture

test_capture: capture.c capture.h test_capture.c
	$(CC) $(CFLAGS) -o $@ capture.c test_capture.c

test: test_capture
	./test_capture

clean:
	rm -f test_capture *.o
