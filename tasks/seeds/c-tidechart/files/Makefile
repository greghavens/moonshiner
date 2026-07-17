CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_tide: tide.o chart.o test_tide.o
	$(CC) $(CFLAGS) -o $@ tide.o chart.o test_tide.o

tide.o: tide.c tide.h
	$(CC) $(CFLAGS) -c tide.c

chart.o: chart.c chart.h tide.h
	$(CC) $(CFLAGS) -c chart.c

test_tide.o: test_tide.c tide.h chart.h mintest.h
	$(CC) $(CFLAGS) -c test_tide.c

.PHONY: test clean
test: test_tide
	./test_tide

clean:
	rm -f test_tide *.o
