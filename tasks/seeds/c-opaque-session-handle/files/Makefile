CC ?= cc
CFLAGS = -std=c17 -Wall -Wextra -Werror -pedantic -g
CPPFLAGS = -Iinclude -I.

SOURCES = src/session.c src/session_adapter.c src/session_pump.c
HEADERS = include/session.h include/session_adapter.h include/session_pump.h \
	adapter/session_wire.h mintest.h

.PHONY: test check-api check-opaque check-source-contract clean

test: check-api check-opaque check-source-contract test_session
	./test_session

check-api:
	@rm -f .session_api_probe.o
	@$(CC) $(CPPFLAGS) $(CFLAGS) -c session_api_probe.c \
		-o .session_api_probe.o || { \
		rm -f .session_api_probe.o; \
		echo "FAIL: public session API shape changed" >&2; \
		exit 1; \
	}
	@rm -f .session_api_probe.o

check-opaque:
	@rm -f .opaque_probe.o
	@if $(CC) $(CPPFLAGS) $(CFLAGS) -c opaque_probe.c -o .opaque_probe.o \
		2>/dev/null; then \
		rm -f .opaque_probe.o; \
		echo "FAIL: public callers can still dereference session fields" >&2; \
		exit 1; \
	else \
		rm -f .opaque_probe.o; \
		 echo "ok: session is opaque to public callers"; \
	fi

check-source-contract:
	@sh source_contract.sh

test_session: test_session.c $(SOURCES) $(HEADERS)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test_session.c $(SOURCES)

clean:
	rm -f test_session .opaque_probe.o .session_api_probe.o *.o
