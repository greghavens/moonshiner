CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_ini: test_ini.c ini.c ini.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_ini.c ini.c

.PHONY: test clean
test: test_ini
	./test_ini

clean:
	rm -f test_ini
