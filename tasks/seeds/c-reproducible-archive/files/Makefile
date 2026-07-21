CC ?= cc
AR ?= ar
CFLAGS ?= -O2 -std=c17 -Wall -Wextra -Werror
CPPFLAGS ?= -Iinclude

PREFIX ?= /usr/local
DESTDIR ?=
OBJDIR ?= build/obj
LIBRARY ?= build/lib/libbeacon.a

SOURCES := src/checksum.c src/encode.c src/format.c
OBJECTS := $(patsubst src/%.c,$(OBJDIR)/%.o,$(SOURCES))

.PHONY: all objects archive install test clean

all: archive

objects: $(OBJECTS)

archive: $(LIBRARY)

$(LIBRARY): $(OBJECTS) scripts/archive-library.sh
	@mkdir -p "$(dir $@)"
	AR="$(AR)" scripts/archive-library.sh "$@" "$(OBJDIR)"

$(OBJDIR)/%.o: src/%.c include/beacon.h
	@mkdir -p "$(dir $@)"
	$(CC) $(CPPFLAGS) $(CFLAGS) -c "$<" -o "$@"

install: $(LIBRARY)
	install -d "$(DESTDIR)$(PREFIX)/lib" "$(DESTDIR)$(PREFIX)/include"
	install -m 0644 "$(LIBRARY)" "$(DESTDIR)$(PREFIX)/lib/libbeacon.a"
	install -m 0644 include/beacon.h "$(DESTDIR)$(PREFIX)/include/beacon.h"

test:
	python3 tests/test_reproducible.py

clean:
	rm -rf build
