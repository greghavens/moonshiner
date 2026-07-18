CC = gcc
CFLAGS = -std=c17 -Wall -Wextra -Werror -g -I.

SOURCES = firmware/config_parser.c firmware/runtime_config.c \
	firmware/hardware_adapter.c firmware/update_service.c

test_config_owner: test_config_owner.c $(SOURCES) firmware/config_parser.h \
	firmware/runtime_config.h firmware/hardware_adapter.h \
	firmware/update_service.h protocol/wire_config.h mintest.h
	$(CC) $(CFLAGS) -o $@ test_config_owner.c $(SOURCES)

.PHONY: test clean
test: test_config_owner
	./test_config_owner

clean:
	rm -f test_config_owner *.o
