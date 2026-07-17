CC      = gcc
CFLAGS  = -std=c17 -Wall -Wextra -Werror

test_grade: test_grade.c grade.c band.h silo.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_grade.c grade.c

.PHONY: test clean
test: test_grade
	./test_grade

clean:
	rm -f test_grade
