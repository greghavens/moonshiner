#ifndef CHIME_H
#define CHIME_H

/* Tower carillon schedule: hour strokes on the great bell, quarter peals
 * on the small bells, one bell letter (A..G) per quarter-hour slot. */

/* Strokes rung on the hour: 12 at midnight and noon, else hour mod 12. */
int chime_strokes();

/* Quarter index 0..3 within the hour. Minutes wrap: 1445 is 00:05 of the
 * next day, -10 is 23:50 of the day before. */
int chime_quarter(int minute);

/* Bell letter for a raw quarter-hour slot number (slot 0 = 00:00). */
char chime_bell_for_slot(long slot);

/* Bell letter rung at a given hour and quarter. */
char chime_bell_for(int hour, int quarter);

/* Great-bell strokes over a full day plus one short peal per quarter. */
long chime_daily_strokes(void);

#endif /* CHIME_H */
