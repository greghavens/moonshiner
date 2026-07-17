CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_snake: test_snake.c snake.c snake.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_snake.c snake.c

.PHONY: test clean
test: test_snake
	./test_snake

clean:
	rm -f test_snake
