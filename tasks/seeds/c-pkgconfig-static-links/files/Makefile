CC ?= cc
AR ?= ar
INSTALL ?= install
CFLAGS ?= -O2

PREFIX ?= /usr/local
LIBDIR ?= $(PREFIX)/lib
INCLUDEDIR ?= $(PREFIX)/include
PKGCONFIGDIR ?= $(LIBDIR)/pkgconfig
BUILD_DIR ?= build

WARN_CFLAGS = -std=c17 -Wall -Wextra -Wpedantic -Werror
CPPFLAGS += -Iinclude -Isrc

STATIC_LIBS = \
	$(BUILD_DIR)/libparcel.a \
	$(BUILD_DIR)/libseal.a \
	$(BUILD_DIR)/libhash.a
SHARED_LIB = $(BUILD_DIR)/libparcel.so.1.0

.PHONY: all install clean

all: $(STATIC_LIBS) $(SHARED_LIB)

$(BUILD_DIR)/parcel.o: src/parcel.c include/parcel/parcel.h src/seal.h
	@mkdir -p "$(@D)"
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN_CFLAGS) -c "$<" -o "$@"

$(BUILD_DIR)/seal.o: src/seal.c src/seal.h src/hash.h
	@mkdir -p "$(@D)"
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN_CFLAGS) -c "$<" -o "$@"

$(BUILD_DIR)/hash.o: src/hash.c src/hash.h
	@mkdir -p "$(@D)"
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN_CFLAGS) -c "$<" -o "$@"

$(BUILD_DIR)/%_pic.o: src/%.c
	@mkdir -p "$(@D)"
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN_CFLAGS) -fPIC -c "$<" -o "$@"

$(BUILD_DIR)/libparcel.a: $(BUILD_DIR)/parcel.o
	$(AR) rcsD "$@" "$<"

$(BUILD_DIR)/libseal.a: $(BUILD_DIR)/seal.o
	$(AR) rcsD "$@" "$<"

$(BUILD_DIR)/libhash.a: $(BUILD_DIR)/hash.o
	$(AR) rcsD "$@" "$<"

$(SHARED_LIB): $(BUILD_DIR)/parcel_pic.o $(BUILD_DIR)/seal_pic.o $(BUILD_DIR)/hash_pic.o
	$(CC) -shared -Wl,-soname,libparcel.so.1 -o "$@" $^

install: all
	$(INSTALL) -d "$(DESTDIR)$(LIBDIR)" "$(DESTDIR)$(INCLUDEDIR)/parcel" "$(DESTDIR)$(PKGCONFIGDIR)"
	$(INSTALL) -m 0644 $(STATIC_LIBS) "$(DESTDIR)$(LIBDIR)/"
	$(INSTALL) -m 0755 "$(SHARED_LIB)" "$(DESTDIR)$(LIBDIR)/libparcel.so.1.0"
	ln -sfn libparcel.so.1.0 "$(DESTDIR)$(LIBDIR)/libparcel.so.1"
	ln -sfn libparcel.so.1 "$(DESTDIR)$(LIBDIR)/libparcel.so"
	$(INSTALL) -m 0644 include/parcel/parcel.h "$(DESTDIR)$(INCLUDEDIR)/parcel/parcel.h"
	$(INSTALL) -m 0644 packaging/parcel.pc "$(DESTDIR)$(PKGCONFIGDIR)/parcel.pc"

clean:
	rm -rf -- "$(BUILD_DIR)"
