CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_mazeuf: test_mazeuf.c mazeuf.c mazeuf.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_mazeuf.c mazeuf.c

.PHONY: test clean
test: test_mazeuf
	./test_mazeuf

clean:
	rm -f test_mazeuf
