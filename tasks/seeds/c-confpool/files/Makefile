CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror -g

test_config: test_config.c config.c config.h pool.c pool.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_config.c config.c pool.c

.PHONY: test clean
test: test_config
	./test_config

clean:
	rm -f test_config
