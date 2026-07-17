CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_bitvec: test_bitvec.c bitvec.c bitvec.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_bitvec.c bitvec.c

.PHONY: test clean
test: test_bitvec
	./test_bitvec

clean:
	rm -f test_bitvec
