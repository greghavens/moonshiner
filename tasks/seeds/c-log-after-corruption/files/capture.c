#include "capture.h"

#include <stdio.h>
#include <string.h>

static char *record_source(CaptureRecord *record) {
    return (char *)&record->storage[0];
}

static char *record_expected_checksum(CaptureRecord *record) {
    return (char *)&record->storage[CAPTURE_SOURCE_CAPACITY];
}

static const char *const_record_source(const CaptureRecord *record) {
    return (const char *)&record->storage[0];
}

static const char *const_record_expected_checksum(const CaptureRecord *record) {
    return (const char *)&record->storage[CAPTURE_SOURCE_CAPACITY];
}

static void copy_checksum(char *destination, const char *checksum) {
    size_t count = strlen(checksum);
    if (count > CAPTURE_CHECKSUM_TEXT_LENGTH) {
        count = CAPTURE_CHECKSUM_TEXT_LENGTH;
    }
    memcpy(destination, checksum, count);
    destination[count] = '\0';
}

size_t capture_copy_source(char *destination, size_t capacity,
                           const char *source) {
    if (destination == NULL || capacity == 0) {
        return 0;
    }
    if (source == NULL) {
        destination[0] = '\0';
        return 0;
    }

    const size_t source_length = strlen(source);
    const size_t count = source_length < capacity ? source_length : capacity;
    memcpy(destination, source, count);
    destination[count] = '\0';
    return count;
}

void capture_record_init(CaptureRecord *record, const char *source,
                         const char *expected_checksum) {
    if (record == NULL) {
        return;
    }

    memset(record->storage, 0, sizeof(record->storage));
    copy_checksum(record_expected_checksum(record),
                  expected_checksum == NULL ? "" : expected_checksum);
    (void)capture_copy_source(record_source(record), CAPTURE_SOURCE_CAPACITY,
                              source);
}

const char *capture_record_source(const CaptureRecord *record) {
    return const_record_source(record);
}

const char *capture_record_expected_checksum(const CaptureRecord *record) {
    return const_record_expected_checksum(record);
}

int capture_record_verify(const CaptureRecord *record,
                          const char *actual_checksum,
                          char *diagnostic, size_t diagnostic_capacity) {
    const char *actual = actual_checksum == NULL ? "" : actual_checksum;

    if (diagnostic != NULL && diagnostic_capacity > 0) {
        diagnostic[0] = '\0';
    }
    if (record == NULL) {
        return 0;
    }
    if (strcmp(const_record_expected_checksum(record), actual) == 0) {
        return 1;
    }

    if (diagnostic != NULL && diagnostic_capacity > 0) {
        (void)snprintf(
            diagnostic, diagnostic_capacity,
            "checksum mismatch after capture: source=\"%.*s\" expected=%s actual=%s\n",
            CAPTURE_SOURCE_CAPACITY - 1, const_record_source(record),
            const_record_expected_checksum(record), actual);
    }
    return 0;
}
