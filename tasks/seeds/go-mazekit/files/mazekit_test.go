package mazekit_test

import (
	"strings"
	"testing"

	mk "go-mazekit"
)

func generate(t *testing.T, w, h int, seed int64) *mk.Maze {
	t.Helper()
	m, err := mk.Generate(w, h, seed)
	if err != nil {
		t.Fatalf("Generate(%d,%d,%d): %v", w, h, seed, err)
	}
	return m
}

func lines(ls ...string) string { return strings.Join(ls, "\n") }

func TestGenerateRejectsBadArguments(t *testing.T) {
	for _, c := range []struct {
		w, h int
		seed int64
	}{{0, 3, 1}, {3, 0, 1}, {-2, 3, 1}, {3, 3, -1}} {
		if _, err := mk.Generate(c.w, c.h, c.seed); err == nil {
			t.Errorf("Generate(%d,%d,%d) succeeded, want error", c.w, c.h, c.seed)
		}
	}
}

func TestPinnedMazes(t *testing.T) {
	cases := []struct {
		w, h   int
		seed   int64
		render string
		solve  string
	}{
		{4, 3, 1, lines(
			"#########",
			"#     # #",
			"##### # #",
			"#   # # #",
			"# ### # #",
			"#       #",
			"#########",
		), "EESSE"},
		{6, 5, 42, lines(
			"#############",
			"# #   #     #",
			"# # # # ### #",
			"# # # #   # #",
			"# # # ### # #",
			"# # #     # #",
			"# # ####### #",
			"# #   #     #",
			"# ##### ### #",
			"#       #   #",
			"#############",
		), "SSSSEEENEES"},
		{5, 5, 7, lines(
			"###########",
			"#         #",
			"######### #",
			"#     #   #",
			"# ##### ###",
			"#       # #",
			"# ####### #",
			"# #     # #",
			"# # ### # #",
			"#   #     #",
			"###########",
		), "EEEESWSWWWSSENEESE"},
		{8, 2, 2026, lines(
			"#################",
			"#     #     #   #",
			"##### # ### # # #",
			"#       #     # #",
			"#################",
		), "EESENEESENES"},
	}
	for _, c := range cases {
		m := generate(t, c.w, c.h, c.seed)
		if got := m.Render(); got != c.render {
			t.Errorf("Render(%dx%d seed %d):\ngot:\n%s\nwant:\n%s", c.w, c.h, c.seed, got, c.render)
		}
		if got := m.Solve(); got != c.solve {
			t.Errorf("Solve(%dx%d seed %d) = %q, want %q", c.w, c.h, c.seed, got, c.solve)
		}
	}
}

func TestSameSeedSameMazeDifferentSeedDifferentMaze(t *testing.T) {
	a := generate(t, 6, 5, 42)
	b := generate(t, 6, 5, 42)
	if a.Render() != b.Render() {
		t.Error("same seed produced different mazes")
	}
	c := generate(t, 6, 5, 43)
	if c.Render() == a.Render() {
		t.Error("seeds 42 and 43 produced identical 6x5 mazes")
	}
}

func TestRenderGeometry(t *testing.T) {
	for _, c := range []struct{ w, h int }{{1, 1}, {2, 7}, {9, 4}} {
		m := generate(t, c.w, c.h, 3)
		rows := strings.Split(m.Render(), "\n")
		if len(rows) != 2*c.h+1 {
			t.Errorf("%dx%d render has %d lines, want %d", c.w, c.h, len(rows), 2*c.h+1)
		}
		for i, r := range rows {
			if len(r) != 2*c.w+1 {
				t.Errorf("%dx%d render line %d is %d chars, want %d", c.w, c.h, i, len(r), 2*c.w+1)
			}
		}
	}
	if got := generate(t, 1, 1, 0).Render(); got != "###\n# #\n###" {
		t.Errorf("1x1 render = %q", got)
	}
}

func TestPerfectMazeStructure(t *testing.T) {
	const w, h = 7, 6
	m := generate(t, w, h, 5)

	// Reciprocity of passages, seen from both sides.
	for y := 0; y < h; y++ {
		for x := 0; x < w-1; x++ {
			if m.Open(x, y, 'E') != m.Open(x+1, y, 'W') {
				t.Fatalf("passage (%d,%d)E disagrees with (%d,%d)W", x, y, x+1, y)
			}
		}
	}
	for y := 0; y < h-1; y++ {
		for x := 0; x < w; x++ {
			if m.Open(x, y, 'S') != m.Open(x, y+1, 'N') {
				t.Fatalf("passage (%d,%d)S disagrees with (%d,%d)N", x, y, x, y+1)
			}
		}
	}

	// The outer border is solid.
	for x := 0; x < w; x++ {
		if m.Open(x, 0, 'N') || m.Open(x, h-1, 'S') {
			t.Fatalf("border passage escapes at column %d", x)
		}
	}
	for y := 0; y < h; y++ {
		if m.Open(0, y, 'W') || m.Open(w-1, y, 'E') {
			t.Fatalf("border passage escapes at row %d", y)
		}
	}

	// Out-of-range cells and unknown directions are closed.
	if m.Open(-1, 0, 'E') || m.Open(0, -1, 'S') || m.Open(w, 0, 'W') || m.Open(0, h, 'N') {
		t.Error("out-of-range Open returned true")
	}
	if m.Open(0, 0, 'Q') || m.Open(0, 0, 0) {
		t.Error("unknown direction reported open")
	}

	// A perfect maze: every cell reachable, exactly w*h-1 carved passages.
	passages := 0
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			if m.Open(x, y, 'E') {
				passages++
			}
			if m.Open(x, y, 'S') {
				passages++
			}
		}
	}
	if passages != w*h-1 {
		t.Errorf("maze has %d passages, want %d (spanning tree)", passages, w*h-1)
	}
	reached := map[[2]int]bool{{0, 0}: true}
	queue := [][2]int{{0, 0}}
	deltas := map[byte][2]int{'N': {0, -1}, 'E': {1, 0}, 'S': {0, 1}, 'W': {-1, 0}}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		for d, dd := range deltas {
			if m.Open(cur[0], cur[1], d) {
				nxt := [2]int{cur[0] + dd[0], cur[1] + dd[1]}
				if !reached[nxt] {
					reached[nxt] = true
					queue = append(queue, nxt)
				}
			}
		}
	}
	if len(reached) != w*h {
		t.Errorf("only %d of %d cells reachable from the entrance", len(reached), w*h)
	}
}

func TestSolveWalksLegallyToExit(t *testing.T) {
	deltas := map[byte][2]int{'N': {0, -1}, 'E': {1, 0}, 'S': {0, 1}, 'W': {-1, 0}}
	for _, seed := range []int64{5, 99, 1234} {
		m := generate(t, 7, 6, seed)
		x, y := 0, 0
		for i, mv := range []byte(m.Solve()) {
			if !m.Open(x, y, mv) {
				t.Fatalf("seed %d: move %d (%c) walks through a wall at (%d,%d)", seed, i, mv, x, y)
			}
			d := deltas[mv]
			x, y = x+d[0], y+d[1]
		}
		if x != 6 || y != 5 {
			t.Errorf("seed %d: solution ends at (%d,%d), want (6,5)", seed, x, y)
		}
	}
	if got := generate(t, 1, 1, 9).Solve(); got != "" {
		t.Errorf("1x1 Solve() = %q, want empty", got)
	}
}

func TestRenderWithPath(t *testing.T) {
	want := lines(
		"#############",
		"#.#   #     #",
		"#.# # # ### #",
		"#.# # #   # #",
		"#.# # ### # #",
		"#.# #     # #",
		"#.# ####### #",
		"#.#   #.....#",
		"#.#####.###.#",
		"#.......#  .#",
		"#############",
	)
	if got := generate(t, 6, 5, 42).RenderWithPath(); got != want {
		t.Errorf("RenderWithPath(6x5 seed 42):\ngot:\n%s\nwant:\n%s", got, want)
	}
	if got := generate(t, 1, 1, 0).RenderWithPath(); got != "###\n#.#\n###" {
		t.Errorf("1x1 RenderWithPath() = %q", got)
	}
}
