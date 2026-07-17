#include <stddef.h>

#include "chime.h"

int wrap_minute(int m) {
    return ((m % 1440) + 1440) % 1440;
}

long slot_of(int hour, int quarter) {
    return hour * 4 + quarter;
}

int chime_strokes(hour)
    int hour;
{
    int h = hour % 12;
    return h == 0 ? 12 : h;
}

int chime_quarter(int minute) {
    int m = wrap_minute(minute) % 60;
    return m / 15.0;
}

char chime_bell_for_slot(long slot) {
    char base = 'A';
    return base + slot % 7;
}

char chime_bell_for(int hour, int quarter) {
    return chime_bell_for_slot(slot_of(hour, quarter));
}

long chime_daily_strokes(void) {
    int total = 0;
    for (int h = 0; h < 24; h++)
        total += chime_strokes(h);
    size_t peals = 24 * 3; /* one short peal at :15, :30 and :45 */
    total += peals;
    return total;
}
