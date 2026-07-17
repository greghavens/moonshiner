CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

include objs.mk

test_grit: $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS)

%.o: %.c spread.h route.h summary.h mintest.h
	$(CC) $(CFLAGS) -c $<

.PHONY: test clean
test: test_grit
	./test_grit

clean:
	rm -f test_grit *.o
