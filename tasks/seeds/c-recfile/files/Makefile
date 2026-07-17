CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_recfile: test_recfile.c recfile.c recfile.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_recfile.c recfile.c

.PHONY: test clean
test: test_recfile
	./test_recfile

clean:
	rm -f test_recfile
