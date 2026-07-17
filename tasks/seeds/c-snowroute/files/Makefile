CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_plow: plow.o report.o test_plow.o
	$(CC) $(CFLAGS) -o $@ plow.o report.o test_plow.o

plow.o: plow.c plow.h
	$(CC) $(CFLAGS) -c plow.c

report.o: report.c report.h plow.h
	$(CC) $(CFLAGS) -c report.c

test_plow.o: test_plow.c plow.h report.h mintest.h
	$(CC) $(CFLAGS) -c test_plow.c

.PHONY: test clean
test: test_plow
	./test_plow

clean:
	rm -f test_plow *.o
