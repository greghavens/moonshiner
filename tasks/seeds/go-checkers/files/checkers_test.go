package checkers_test

import (
	"os"
	"slices"
	"strings"
	"testing"

	ck "go-checkers"
)

func fixture(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatalf("reading fixture %s: %v", name, err)
	}
	return string(data)
}

func load(t *testing.T, fx string) *ck.Game {
	t.Helper()
	g, err := ck.Load(fx)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	return g
}

func apply(t *testing.T, g *ck.Game, move string) {
	t.Helper()
	if err := g.Apply(move); err != nil {
		t.Fatalf("Apply(%q): %v", move, err)
	}
}

func board(lines ...string) string { return strings.Join(lines, "\n") }

const (
	kingingPos = "turn: w\n........\n..b.b...\n.w......\n........\n........\n........\n........\n........\n"
	kingCapPos = "turn: w\n........\n........\n........\n..b.....\n...W....\n....b...\n........\n........\n"
	kingQuiet  = "turn: w\n.......b\n........\n........\n........\n...W....\n........\n........\n........\n"
	blockedPos = "turn: b\n........\n........\n........\n........\n........\nb.......\n.w......\n..w.....\n"
)

func TestLoadAndRenderRoundTrip(t *testing.T) {
	g := load(t, fixture(t, "initial.brd"))
	if g.Turn() != 'b' {
		t.Errorf("Turn() = %c, want b", g.Turn())
	}
	want := board(
		".b.b.b.b",
		"b.b.b.b.",
		".b.b.b.b",
		"........",
		"........",
		"w.w.w.w.",
		".w.w.w.w",
		"w.w.w.w.",
	)
	if got := g.Render(); got != want {
		t.Errorf("Render():\ngot:\n%s\nwant:\n%s", got, want)
	}
	if _, over := g.Winner(); over {
		t.Error("Winner() reports the opening position as finished")
	}
}

func TestLoadRejectsBadFixtures(t *testing.T) {
	cases := map[string]string{
		"piece on light square": "turn: b\nb.......\n........\n........\n........\n........\n........\n........\n........\n",
		"bad square character":  "turn: b\n.x......\n........\n........\n........\n........\n........\n........\n........\n",
		"missing board line":    "turn: b\n.b......\n........\n........\n........\n........\n........\n........\n",
		"bad turn line":         "turn: x\n.b......\n........\n........\n........\n........\n........\n........\n........\n",
		"line too long":         "turn: b\n.b.......\n........\n........\n........\n........\n........\n........\n........\n",
		"white man on rank 8":   "turn: b\n.w......\n........\n........\n........\n........\n........\n........\n........\n",
		"black man on rank 1":   "turn: b\n........\n........\n........\n........\n........\n........\n........\nb.......\n",
	}
	for name, fx := range cases {
		if _, err := ck.Load(fx); err == nil {
			t.Errorf("%s: Load accepted the fixture", name)
		}
	}
}

func TestOpeningMoveEnumeration(t *testing.T) {
	g := load(t, fixture(t, "initial.brd"))
	wantBlack := []string{"b6-a5", "b6-c5", "d6-c5", "d6-e5", "f6-e5", "f6-g5", "h6-g5"}
	if got := g.LegalMoves(); !slices.Equal(got, wantBlack) {
		t.Fatalf("black opening moves = %v, want %v", got, wantBlack)
	}
	apply(t, g, "b6-c5")
	if g.Turn() != 'w' {
		t.Fatalf("after black's move Turn() = %c, want w", g.Turn())
	}
	wantWhite := []string{"a3-b4", "c3-b4", "c3-d4", "e3-d4", "e3-f4", "g3-f4", "g3-h4"}
	if got := g.LegalMoves(); !slices.Equal(got, wantWhite) {
		t.Fatalf("white reply moves = %v, want %v", got, wantWhite)
	}
}

func TestCapturesAreForced(t *testing.T) {
	g := load(t, fixture(t, "forced.brd"))
	want := []string{"b6xd4"}
	if got := g.LegalMoves(); !slices.Equal(got, want) {
		t.Fatalf("LegalMoves() = %v, want %v (quiet moves must vanish)", got, want)
	}
	if err := g.Apply("f6-e5"); err == nil {
		t.Error("Apply accepted a quiet move while a capture was available")
	}
	apply(t, g, "b6xd4")
	wantBoard := board(
		"........",
		"........",
		".....b..",
		"........",
		"...b....",
		"........",
		"........",
		"........",
	)
	if got := g.Render(); got != wantBoard {
		t.Errorf("after b6xd4:\ngot:\n%s\nwant:\n%s", got, wantBoard)
	}
}

func TestMultiJumpChains(t *testing.T) {
	g := load(t, fixture(t, "multijump.brd"))
	want := []string{"c3xe5xc7", "c3xe5xg7"}
	if got := g.LegalMoves(); !slices.Equal(got, want) {
		t.Fatalf("LegalMoves() = %v, want %v", got, want)
	}
	// A chain must be taken whole: stopping after the first hop is illegal.
	if err := g.Apply("c3xe5"); err == nil {
		t.Error("Apply accepted a capture chain cut short mid-jump")
	}
	apply(t, g, "c3xe5xg7")
	want2 := board(
		"........",
		"......w.",
		"...b....",
		"........",
		"........",
		"........",
		"........",
		"w.......",
	)
	if got := g.Render(); got != want2 {
		t.Errorf("after c3xe5xg7 (both jumped men gone, d6 alive):\ngot:\n%s\nwant:\n%s", got, want2)
	}
	if g.Turn() != 'b' {
		t.Errorf("Turn() = %c, want b", g.Turn())
	}
}

func TestKingingEndsTheMove(t *testing.T) {
	g := load(t, kingingPos)
	// Landing on d8 crowns the man and ENDS the move, even though a fresh
	// king could seemingly continue over e7.
	want := []string{"b6xd8"}
	if got := g.LegalMoves(); !slices.Equal(got, want) {
		t.Fatalf("LegalMoves() = %v, want %v", got, want)
	}
	apply(t, g, "b6xd8")
	wantBoard := board(
		"...W....",
		"....b...",
		"........",
		"........",
		"........",
		"........",
		"........",
		"........",
	)
	if got := g.Render(); got != wantBoard {
		t.Errorf("after b6xd8:\ngot:\n%s\nwant:\n%s", got, wantBoard)
	}
	wantBlack := []string{"e7-d6", "e7-f6"}
	if got := g.LegalMoves(); !slices.Equal(got, wantBlack) {
		t.Errorf("black replies = %v, want %v", got, wantBlack)
	}
}

func TestKingMovesAllFourWays(t *testing.T) {
	g := load(t, kingCapPos)
	wantCaps := []string{"d4xb6", "d4xf2"}
	if got := g.LegalMoves(); !slices.Equal(got, wantCaps) {
		t.Fatalf("king captures = %v, want %v", got, wantCaps)
	}
	q := load(t, kingQuiet)
	wantQuiet := []string{"d4-c5", "d4-e5", "d4-c3", "d4-e3"}
	if got := q.LegalMoves(); !slices.Equal(got, wantQuiet) {
		t.Errorf("king quiet moves = %v, want %v", got, wantQuiet)
	}
}

func TestNoMovesMeansLoss(t *testing.T) {
	g := load(t, blockedPos)
	if got := g.LegalMoves(); len(got) != 0 {
		t.Fatalf("blocked side has moves: %v", got)
	}
	side, over := g.Winner()
	if !over || side != 'w' {
		t.Errorf("Winner() = (%c, %v), want (w, true)", side, over)
	}
}

func TestApplyRejectsNonsense(t *testing.T) {
	g := load(t, fixture(t, "initial.brd"))
	for _, m := range []string{"", "b6", "b6-b5", "b6xc5", "a5-b4", "zz-a1", "b6-c5-d4", "e3-d4"} {
		if err := g.Apply(m); err == nil {
			t.Errorf("Apply(%q) succeeded, want error", m)
		}
	}
	// Nothing above may have mutated the position or the turn.
	if g.Turn() != 'b' {
		t.Errorf("Turn() = %c after rejected moves, want b", g.Turn())
	}
	if got := g.Render(); !strings.HasPrefix(got, ".b.b.b.b") {
		t.Errorf("board changed after rejected moves:\n%s", got)
	}
}

func TestMidgameTranscript(t *testing.T) {
	got, err := ck.Transcript(fixture(t, "midgame.brd"),
		[]string{"c3-d4", "e5xc3", "h2-g3", "c3-b2", "g3-f4", "b2-a1"})
	if err != nil {
		t.Fatalf("Transcript: %v", err)
	}
	want := strings.Join([]string{
		"start",
		"........", "......b.", "........", "....b...", "........", "..w.....", ".......w", "........",
		"",
		"move 1 (w): c3-d4",
		"........", "......b.", "........", "....b...", "...w....", "........", ".......w", "........",
		"",
		"move 2 (b): e5xc3",
		"........", "......b.", "........", "........", "........", "..b.....", ".......w", "........",
		"",
		"move 3 (w): h2-g3",
		"........", "......b.", "........", "........", "........", "..b...w.", "........", "........",
		"",
		"move 4 (b): c3-b2",
		"........", "......b.", "........", "........", "........", "......w.", ".b......", "........",
		"",
		"move 5 (w): g3-f4",
		"........", "......b.", "........", "........", ".....w..", "........", ".b......", "........",
		"",
		"move 6 (b): b2-a1",
		"........", "......b.", "........", "........", ".....w..", "........", "........", "B.......",
		"",
		"next: w",
	}, "\n") + "\n"
	if got != want {
		t.Errorf("midgame transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestEndgameTranscriptDeclaresWinner(t *testing.T) {
	got, err := ck.Transcript(fixture(t, "endgame.brd"), []string{"c3xe5"})
	if err != nil {
		t.Fatalf("Transcript: %v", err)
	}
	want := strings.Join([]string{
		"start",
		"........", "........", "........", "........", "...b....", "b.w.....", ".w......", "..w.....",
		"",
		"move 1 (w): c3xe5",
		"........", "........", "........", "....w...", "........", "b.......", ".w......", "..w.....",
		"",
		"winner: w",
	}, "\n") + "\n"
	if got != want {
		t.Errorf("endgame transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestTranscriptErrors(t *testing.T) {
	if _, err := ck.Transcript("turn: q\n", nil); err == nil {
		t.Error("Transcript accepted a broken fixture")
	}
	if _, err := ck.Transcript(fixture(t, "midgame.brd"), []string{"c3-d4", "g7-f6"}); err == nil {
		t.Error("Transcript accepted a quiet move while a capture was forced")
	}
}
