CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror -g

test_report: test_report.c report.c report.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_report.c report.c

.PHONY: test clean
test: test_report
	./test_report

clean:
	rm -f test_report
