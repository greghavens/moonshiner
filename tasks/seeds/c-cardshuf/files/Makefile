CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_cardshuf: test_cardshuf.c cardshuf.c cardshuf.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_cardshuf.c cardshuf.c

.PHONY: test clean
test: test_cardshuf
	./test_cardshuf

clean:
	rm -f test_cardshuf
