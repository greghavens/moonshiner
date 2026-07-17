CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_nursery: plant.o lot.o test_nursery.o
	$(CC) $(CFLAGS) -o $@ plant.o lot.o test_nursery.o

plant.o: plant.c plant.h units.h
	$(CC) $(CFLAGS) -c plant.c

lot.o: lot.c lot.h plant.h units.h
	$(CC) $(CFLAGS) -c lot.c

test_nursery.o: test_nursery.c plant.h units.h lot.h mintest.h
	$(CC) $(CFLAGS) -c test_nursery.c

.PHONY: test clean
test: test_nursery
	./test_nursery

clean:
	rm -f test_nursery *.o
