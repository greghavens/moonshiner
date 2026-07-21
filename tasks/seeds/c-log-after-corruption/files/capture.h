#ifndef CAPTURE_H
#define CAPTURE_H

#include <stddef.h>

enum {
    CAPTURE_SOURCE_CAPACITY = 12,
    CAPTURE_CHECKSUM_TEXT_LENGTH = 8,
    CAPTURE_CHECKSUM_CAPACITY = CAPTURE_CHECKSUM_TEXT_LENGTH + 1,
    CAPTURE_DIAGNOSTIC_CAPACITY = 160
};

/*
 * One backing array makes the deployed layout explicit: the logical source
 * destination is followed immediately by the expected checksum text.
 */
typedef struct {
    unsigned char storage[CAPTURE_SOURCE_CAPACITY + CAPTURE_CHECKSUM_CAPACITY];
} CaptureRecord;

/*
 * Store a C string in destination.  Capacity includes the terminating NUL.
 * Return the number of source bytes stored.  Capacity zero performs no write.
 */
size_t capture_copy_source(char *destination, size_t capacity,
                           const char *source);

void capture_record_init(CaptureRecord *record, const char *source,
                         const char *expected_checksum);

const char *capture_record_source(const CaptureRecord *record);
const char *capture_record_expected_checksum(const CaptureRecord *record);

/* Return 1 for a match and 0 for a mismatch. */
int capture_record_verify(const CaptureRecord *record,
                          const char *actual_checksum,
                          char *diagnostic, size_t diagnostic_capacity);

#endif
