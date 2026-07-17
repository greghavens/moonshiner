package lifegrid_test

import (
	"os"
	"testing"

	life "go-lifegrid"
)

func fixture(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatalf("reading fixture %s: %v", name, err)
	}
	return string(data)
}

func mustParse(t *testing.T, data string, mode life.Mode) *life.Grid {
	t.Helper()
	g, err := life.Parse(data, mode)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	return g
}

func wantRender(t *testing.T, g *life.Grid, want, context string) {
	t.Helper()
	if got := g.Render(); got != want {
		t.Errorf("%s:\ngot:\n%s\nwant:\n%s", context, got, want)
	}
}

func TestParseAcceptsCommentsAndBlankLines(t *testing.T) {
	g := mustParse(t, "! a comment\n\n.#.\n\n!another\n#.#\n", life.Bounded)
	if g.Width() != 3 || g.Height() != 2 {
		t.Fatalf("got %dx%d, want 3x2", g.Width(), g.Height())
	}
	wantRender(t, g, ".#.\n#.#", "parsed grid")
	if g.Population() != 3 {
		t.Errorf("Population() = %d, want 3", g.Population())
	}
}

func TestParseRejectsBadInput(t *testing.T) {
	cases := map[string]string{
		"ragged rows":   ".#.\n.#\n",
		"bad character": ".#.\n.x.\n",
		"empty input":   "",
		"comments only": "!nothing here\n!at all\n",
	}
	for name, data := range cases {
		if _, err := life.Parse(data, life.Bounded); err == nil {
			t.Errorf("%s: Parse accepted %q, want error", name, data)
		}
	}
}

func TestParseFixturesRoundTrip(t *testing.T) {
	for name, want := range map[string]string{
		"blinker.cells": ".....\n.....\n.###.\n.....\n.....",
		"block.cells":   "....\n.##.\n.##.\n....",
	} {
		g := mustParse(t, fixture(t, name), life.Bounded)
		wantRender(t, g, want, name+" render after load")
	}
}

func TestBlinkerOscillates(t *testing.T) {
	for modeName, mode := range map[string]life.Mode{"bounded": life.Bounded, "toroidal": life.Toroidal} {
		g := mustParse(t, fixture(t, "blinker.cells"), mode)
		initial := g.Render()
		g.Step()
		wantRender(t, g, ".....\n..#..\n..#..\n..#..\n.....", modeName+" blinker after 1 step")
		g.Step()
		wantRender(t, g, initial, modeName+" blinker after 2 steps")
		if g.Population() != 3 {
			t.Errorf("%s blinker population = %d, want 3", modeName, g.Population())
		}
	}
}

func TestToadOscillates(t *testing.T) {
	g := mustParse(t, fixture(t, "toad.cells"), life.Bounded)
	initial := g.Render()
	g.Step()
	wantRender(t, g, "......\n...#..\n.#..#.\n.#..#.\n..#...\n......", "toad after 1 step")
	g.Step()
	wantRender(t, g, initial, "toad after 2 steps")
}

func TestStillLifesStayFixed(t *testing.T) {
	for _, name := range []string{"block.cells", "beehive.cells"} {
		for modeName, mode := range map[string]life.Mode{"bounded": life.Bounded, "toroidal": life.Toroidal} {
			g := mustParse(t, fixture(t, name), mode)
			initial := g.Render()
			pop := g.Population()
			g.StepN(4)
			wantRender(t, g, initial, name+" ("+modeName+") after 4 steps")
			if g.Population() != pop {
				t.Errorf("%s (%s) population changed: %d -> %d", name, modeName, pop, g.Population())
			}
		}
	}
}

func TestGliderTranslatesAcrossTorus(t *testing.T) {
	g := mustParse(t, fixture(t, "glider.cells"), life.Toroidal)
	initial := g.Render()

	g.StepN(4)
	wantRender(t, g, "......\n..#...\n...#..\n.###..\n......\n......", "glider after 4 toroidal steps")
	g.StepN(4)
	wantRender(t, g, "......\n......\n...#..\n....#.\n..###.\n......", "glider after 8 toroidal steps")
	if g.Population() != 5 {
		t.Errorf("glider population = %d, want 5", g.Population())
	}

	g.StepN(16)
	wantRender(t, g, initial, "glider after 24 toroidal steps (full torus lap)")
}

func TestGliderDiesAgainstBoundedCorner(t *testing.T) {
	g := mustParse(t, fixture(t, "glider.cells"), life.Bounded)
	g.StepN(12)
	wantRender(t, g, "......\n......\n......\n....#.\n.....#\n...###", "glider after 12 bounded steps")
	g.StepN(3)
	wantRender(t, g, "......\n......\n......\n......\n....##\n....##", "glider after 15 bounded steps")
	// The wreckage is a still life from here on.
	settled := g.Render()
	g.StepN(5)
	wantRender(t, g, settled, "settled block after 5 more steps")
	if g.Population() != 4 {
		t.Errorf("settled population = %d, want 4", g.Population())
	}
}

func TestEdgePatternDivergesByMode(t *testing.T) {
	bounded := mustParse(t, fixture(t, "edge_blinker.cells"), life.Bounded)
	toroidal := mustParse(t, fixture(t, "edge_blinker.cells"), life.Toroidal)
	initial := toroidal.Render()

	bounded.Step()
	wantRender(t, bounded, "..#..\n..#..\n.....\n.....", "edge blinker bounded after 1 step")
	bounded.Step()
	wantRender(t, bounded, ".....\n.....\n.....\n.....", "edge blinker bounded after 2 steps")
	if bounded.Population() != 0 {
		t.Errorf("bounded edge blinker population = %d, want 0", bounded.Population())
	}

	toroidal.Step()
	wantRender(t, toroidal, "..#..\n..#..\n.....\n..#..", "edge blinker toroidal after 1 step")
	toroidal.Step()
	wantRender(t, toroidal, initial, "edge blinker toroidal after 2 steps")
}

func TestAliveCoordinateSemantics(t *testing.T) {
	g := mustParse(t, "#..\n...\n..#\n", life.Toroidal)
	checks := []struct {
		x, y int
		want bool
	}{
		{0, 0, true}, {2, 2, true}, {1, 1, false},
		{-3, -3, true}, // wraps to (0,0)
		{3, 3, true},   // wraps to (0,0)
		{-1, -1, true}, // wraps to (2,2)
		{5, 5, true},   // wraps to (2,2)
		{-1, 0, false}, // wraps to (2,0)
	}
	for _, c := range checks {
		if got := g.Alive(c.x, c.y); got != c.want {
			t.Errorf("toroidal Alive(%d,%d) = %v, want %v", c.x, c.y, got, c.want)
		}
	}

	b := mustParse(t, "#..\n...\n..#\n", life.Bounded)
	for _, xy := range [][2]int{{-1, 0}, {0, -1}, {3, 0}, {0, 3}, {-3, -3}, {5, 5}} {
		if b.Alive(xy[0], xy[1]) {
			t.Errorf("bounded Alive(%d,%d) = true, want false (out of range)", xy[0], xy[1])
		}
	}
	if !b.Alive(0, 0) || !b.Alive(2, 2) {
		t.Error("bounded Alive misses in-range live cells")
	}
}

func TestStepNMatchesRepeatedStep(t *testing.T) {
	a := mustParse(t, fixture(t, "toad.cells"), life.Toroidal)
	b := mustParse(t, fixture(t, "toad.cells"), life.Toroidal)
	a.StepN(7)
	for i := 0; i < 7; i++ {
		b.Step()
	}
	if a.Render() != b.Render() {
		t.Error("StepN(7) differs from 7 sequential Step() calls")
	}

	before := a.Render()
	a.StepN(0)
	wantRender(t, a, before, "StepN(0)")
	a.StepN(-3)
	wantRender(t, a, before, "StepN(-3)")
}
