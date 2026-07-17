CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror
# The tower module owns its public surface: prototype and conversion
# discipline is enforced on chime.o first (bounded rollout, one module
# at a time). The harness builds with the base flags.
MODFLAGS = $(CFLAGS) -Wmissing-prototypes -Wstrict-prototypes \
           -Wold-style-definition -Wconversion

test_chime: chime.o test_chime.o
	$(CC) $(CFLAGS) -o $@ chime.o test_chime.o

chime.o: chime.c chime.h
	$(CC) $(MODFLAGS) -c chime.c

test_chime.o: test_chime.c chime.h mintest.h
	$(CC) $(CFLAGS) -c test_chime.c

.PHONY: test clean
test: test_chime
	./test_chime

clean:
	rm -f test_chime *.o
