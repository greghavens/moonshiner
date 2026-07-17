# Hardened profile from the fleet CI image: optimized, fortified, warnings fatal.
CC      = gcc
CFLAGS  = -std=c17 -O2 -D_FORTIFY_SOURCE=2 -Wall -Wextra -Wshadow -Werror

test_vanlog: vanlog.o test_vanlog.o
	$(CC) $(CFLAGS) -o $@ vanlog.o test_vanlog.o

vanlog.o: vanlog.c vanlog.h
	$(CC) $(CFLAGS) -c vanlog.c

test_vanlog.o: test_vanlog.c vanlog.h mintest.h
	$(CC) $(CFLAGS) -c test_vanlog.c

.PHONY: test clean
test: test_vanlog
	./test_vanlog

clean:
	rm -f test_vanlog *.o trip_*.bin
