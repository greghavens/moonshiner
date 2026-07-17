#ifndef QUILT_H
#define QUILT_H

#include <cstddef>
#include <utility>
#include <vector>

/* A quilt top laid out as a grid of palette codes; 0 means unpieced. */
class PatchGrid {
public:
    PatchGrid(int width, int height)
        : w(width), h(height),
          cells(static_cast<std::size_t>(width) * static_cast<std::size_t>(height), 0) {}
    ~PatchGrid() {}

    int at(int x, int y) const { return cells[idx(x, y)]; }
    void set(int x, int y, int color) { cells[idx(x, y)] = color; }
    std::pair<int, int> shape() const { return {w, h}; }

private:
    std::size_t idx(int x, int y) const {
        return static_cast<std::size_t>(y) * static_cast<std::size_t>(w)
             + static_cast<std::size_t>(x);
    }
    int w;
    int h;
    std::vector<int> cells;
};

#endif /* QUILT_H */
