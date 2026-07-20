#define _POSIX_C_SOURCE 200809L

#include "event_time.h"

#include <errno.h>
#include <time.h>

int event_time_format_utc(time_t instant, char *destination, size_t capacity)
{
    struct tm utc;
    size_t length;

    if (destination == NULL || capacity == 0U) {
        return EINVAL;
    }

    if (capacity < EVENT_TIME_UTC_SIZE) {
        destination[0] = '\0';
        return ERANGE;
    }

    if (gmtime_s(&utc, &instant) != 0) {
        destination[0] = '\0';
        return EOVERFLOW;
    }

    length = strftime(destination, capacity, "%Y-%m-%dT%H:%M:%SZ", &utc);
    if (length != EVENT_TIME_UTC_SIZE - 1U) {
        destination[0] = '\0';
        return EOVERFLOW;
    }

    return 0;
}
