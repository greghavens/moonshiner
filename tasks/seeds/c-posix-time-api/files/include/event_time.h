#ifndef EVENT_TIME_H
#define EVENT_TIME_H

#include <stddef.h>
#include <time.h>

/* Storage required for "YYYY-MM-DDTHH:MM:SSZ" and its terminating null. */
#define EVENT_TIME_UTC_SIZE 21U

/*
 * Format instant as UTC.
 *
 * Returns 0 on success, EINVAL for an invalid destination, ERANGE when the
 * destination is too small, and EOVERFLOW when the time cannot be converted
 * or represented in the required format.
 */
int event_time_format_utc(time_t instant, char *destination, size_t capacity);

#endif
