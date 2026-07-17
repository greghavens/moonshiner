/* vent.h -- ridge-vent positioning for the seedling house controller. */
#ifndef VENTPLAN_H
#define VENTPLAN_H

/* Target vent opening (0..100 percent) for the current air temperature,
 * proportional across the response band above the setpoint. */
int vent_target_percent(double temp_c, double setpoint_c, double band_c);

/* One actuator tick: move current toward target, at most max_step percent. */
int vent_step_toward(int current, int target, int max_step);

/* Fill steps[] with successive vent positions until the target is reached
 * or cap entries are written; returns how many entries were written. */
int vent_plan(double temp_c, double setpoint_c, double band_c,
              int current, int max_step, int steps[], int cap);

#endif /* VENTPLAN_H */
