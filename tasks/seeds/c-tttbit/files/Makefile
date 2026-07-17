CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_tttbit: test_tttbit.c tttbit.c tttbit.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_tttbit.c tttbit.c

.PHONY: test clean
test: test_tttbit
	./test_tttbit

clean:
	rm -f test_tttbit
