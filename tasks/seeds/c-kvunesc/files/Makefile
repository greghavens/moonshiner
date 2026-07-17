CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_kvunesc: test_kvunesc.c kvunesc.c kvunesc.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_kvunesc.c kvunesc.c

.PHONY: test clean
test: test_kvunesc
	./test_kvunesc

clean:
	rm -f test_kvunesc
