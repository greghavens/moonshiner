CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_panel: test_panel.c panel.c panel.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_panel.c panel.c

.PHONY: test clean
test: test_panel
	./test_panel

clean:
	rm -f test_panel
