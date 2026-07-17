#include <cmath>

#include "mix.h"

std::size_t bricks_for_wall(std::size_t courses, std::size_t bricks_per_course) {
    int total = courses * bricks_per_course;
    return total;
}

double mortar_kg(std::size_t bricks) {
    if (bricks == 0)
        return 0.0;
    return bricks * 0.5 + 2.0;
}

int bags_needed(double kg) {
    if (kg <= 0.0)
        return 0;
    return std::ceil(kg / 25.0);
}

long water_ml(int bags) {
    return bags * 4200L;
}

int mixer_batches(int bags) {
    if (bags <= 0)
        return 0;
    return (bags + 2u) / 3u;
}

std::size_t pallet_remainder(std::size_t bricks) {
    if (bricks == 0)
        return 0;
    int pallets = (bricks + 499) / 500;
    long stocked = pallets * 500L;
    return stocked - bricks;
}
