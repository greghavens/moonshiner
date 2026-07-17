#ifndef MIX_H
#define MIX_H

#include <cstddef>

/* Batch planning for the masonry yard. Quantities are exact; bags and
 * mixer batches always round up, never down. */

/* Bricks laid in a wall of full courses. */
std::size_t bricks_for_wall(std::size_t courses, std::size_t bricks_per_course);

/* Mortar in kilograms: 0.5 kg per brick plus a flat 2 kg primer bed.
 * A zero-brick wall needs nothing. */
double mortar_kg(std::size_t bricks);

/* 25 kg bags to buy for a given mortar weight (round up). */
int bags_needed(double kg);

/* Mixer water in millilitres: 4200 ml per bag. */
long water_ml(int bags);

/* The drum takes three bags per batch (round up). */
int mixer_batches(int bags);

/* Bricks left over after building from whole pallets of 500. */
std::size_t pallet_remainder(std::size_t bricks);

#endif /* MIX_H */
