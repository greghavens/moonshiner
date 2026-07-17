CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_vent: test_vent.o vent.o
	$(CC) $(CFLAGS) -o $@ test_vent.o vent.o

test_vent.o: test_vent.c vent.h mintest.h
	$(CC) $(CFLAGS) -c test_vent.c

vent.o: vent.c vent.h
	$(CC) $(CFLAGS) -c vent.c

.PHONY: test clean
test: test_vent
	./test_vent

clean:
	rm -f test_vent *.o
