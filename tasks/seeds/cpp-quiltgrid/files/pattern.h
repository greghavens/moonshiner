#ifndef PATTERN_H
#define PATTERN_H

#include <map>
#include <utility>

#include "quilt.h"

/* Left-right mirror of a quilt top; the original is untouched. */
inline PatchGrid mirrored(const PatchGrid &g) {
    auto [w, h] = g.shape();
    PatchGrid out = g;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            out.set(w - 1 - x, y, g.at(x, y));
    return std::move(out);
}

/* Palette codes in use and how many patches carry each; unpieced cells
 * (code 0) are skipped. */
inline std::map<int, int> palette_counts(const PatchGrid &g) {
    auto [w, h] = g.shape();
    std::map<int, int> counts;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            if (g.at(x, y) != 0)
                counts[g.at(x, y)]++;
    return counts;
}

/* The code covering the most patches; smallest code wins ties, 0 for an
 * unpieced top. */
inline int dominant_color(const PatchGrid &g) {
    auto [w, h] = g.shape();
    int best = 0;
    int best_n = 0;
    for (const auto &[color, n] : palette_counts(g)) {
        if (n > best_n) {
            best = color;
            best_n = n;
        }
    }
    return best;
}

#endif /* PATTERN_H */
