#ifndef ROUTE_H
#define ROUTE_H

/* One leg of a gritting route. */
struct leg {
    int km;       /* lane-kilometres */
    int severity; /* forecast band, 0..3 */
};

/* Total lane-kilometres across the route. */
int route_leg_total(const struct leg *legs, int n);

/* Kilograms of grit the whole route will spread. */
int route_plan_kg(const struct leg *legs, int n);

/* What is left of the truck's season allocation after this route. */
int route_reserve_after(const struct leg *legs, int n);

#endif /* ROUTE_H */
