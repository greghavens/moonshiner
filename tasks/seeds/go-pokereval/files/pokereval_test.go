package pokereval_test

import (
	"os"
	"slices"
	"strings"
	"testing"

	pe "go-pokereval"
)

func cards(t *testing.T, s string) []pe.Card {
	t.Helper()
	cs, err := pe.ParseCards(s)
	if err != nil {
		t.Fatalf("ParseCards(%q): %v", s, err)
	}
	return cs
}

func eval7(t *testing.T, s string) pe.Hand {
	t.Helper()
	h, err := pe.Eval7(cards(t, s))
	if err != nil {
		t.Fatalf("Eval7(%q): %v", s, err)
	}
	return h
}

func TestParseCard(t *testing.T) {
	good := map[string]pe.Card{
		"As": {Rank: 14, Suit: 's'},
		"Kd": {Rank: 13, Suit: 'd'},
		"Qh": {Rank: 12, Suit: 'h'},
		"Jc": {Rank: 11, Suit: 'c'},
		"Td": {Rank: 10, Suit: 'd'},
		"9h": {Rank: 9, Suit: 'h'},
		"2c": {Rank: 2, Suit: 'c'},
	}
	for s, want := range good {
		got, err := pe.ParseCard(s)
		if err != nil {
			t.Errorf("ParseCard(%q): %v", s, err)
			continue
		}
		if got != want {
			t.Errorf("ParseCard(%q) = %+v, want %+v", s, got, want)
		}
		if got.String() != s {
			t.Errorf("ParseCard(%q).String() = %q", s, got.String())
		}
	}
	for _, bad := range []string{"", "A", "10c", "1s", "Ax", "as", "AS", "Zc", "T ", " s"} {
		if _, err := pe.ParseCard(bad); err == nil {
			t.Errorf("ParseCard(%q) succeeded, want error", bad)
		}
	}
	if _, err := pe.ParseCards("Ah Kd notacard"); err == nil {
		t.Error("ParseCards with a bad token succeeded, want error")
	}
}

func TestCategoryNamesAndOrder(t *testing.T) {
	names := map[pe.Category]string{
		pe.HighCard:      "high-card",
		pe.OnePair:       "one-pair",
		pe.TwoPair:       "two-pair",
		pe.ThreeOfAKind:  "three-of-a-kind",
		pe.Straight:      "straight",
		pe.Flush:         "flush",
		pe.FullHouse:     "full-house",
		pe.FourOfAKind:   "four-of-a-kind",
		pe.StraightFlush: "straight-flush",
	}
	for c, want := range names {
		if c.String() != want {
			t.Errorf("Category(%d).String() = %q, want %q", int(c), c.String(), want)
		}
	}
	order := []pe.Category{pe.HighCard, pe.OnePair, pe.TwoPair, pe.ThreeOfAKind,
		pe.Straight, pe.Flush, pe.FullHouse, pe.FourOfAKind, pe.StraightFlush}
	for i := 1; i < len(order); i++ {
		if order[i-1] >= order[i] {
			t.Errorf("category %s should be weaker than %s", order[i-1], order[i])
		}
	}
}

func TestEval7Categories(t *testing.T) {
	cases := []struct {
		seven string
		cat   pe.Category
		ranks []int
	}{
		{"As Ks Qs Js Ts 2c 3d", pe.StraightFlush, []int{14}},
		{"Ah 2h 3h 4h 5h Kc Kd", pe.StraightFlush, []int{5}},
		{"9c 9d 9h 9s Kc 2d 3h", pe.FourOfAKind, []int{9, 13}},
		{"9c 9d 9h 9s Kc Kd 3h", pe.FourOfAKind, []int{9, 13}},
		{"Ac Ad Ah Kc Kd Ks 2h", pe.FullHouse, []int{14, 13}},
		{"Qc Qd Qh 9c 9d 4c 4d", pe.FullHouse, []int{12, 9}},
		{"Ah Kh 9h 6h 3h 2h Qc", pe.Flush, []int{14, 13, 9, 6, 3}},
		{"4c 5d 6h 7s 8c 9d Tc", pe.Straight, []int{10}},
		{"Ac 2d 3h 4s 5c 9d 9h", pe.Straight, []int{5}},
		{"Ac Kd Qh Js Tc 9d 9h", pe.Straight, []int{14}},
		{"8c 8d 8h Kc Qd 2s 3s", pe.ThreeOfAKind, []int{8, 13, 12}},
		{"Ac Ad Kc Kd Qh Qs 2c", pe.TwoPair, []int{14, 13, 12}},
		{"Jc Jd 8h 8s Ac 2d 3h", pe.TwoPair, []int{11, 8, 14}},
		{"Tc Td Ac Kd 8h 4s 2c", pe.OnePair, []int{10, 14, 13, 8}},
		{"Ac Kd Qh 9s 7c 4d 2h", pe.HighCard, []int{14, 13, 12, 9, 7}},
		// Both a flush and a straight are on offer, but not a straight flush:
		// the flush must win, using the 9h over the off-suit ten.
		{"Ah Kh Qh Jh 9h Tc 2d", pe.Flush, []int{14, 13, 12, 11, 9}},
	}
	for _, c := range cases {
		h := eval7(t, c.seven)
		if h.Category != c.cat {
			t.Errorf("Eval7(%q).Category = %s, want %s", c.seven, h.Category, c.cat)
			continue
		}
		if !slices.Equal(h.Ranks, c.ranks) {
			t.Errorf("Eval7(%q).Ranks = %v, want %v", c.seven, h.Ranks, c.ranks)
		}
	}
}

func TestEval7Errors(t *testing.T) {
	if _, err := pe.Eval7(cards(t, "Ac Kd Qh Js Tc 9d")); err == nil {
		t.Error("Eval7 with 6 cards succeeded, want error")
	}
	if _, err := pe.Eval7(cards(t, "Ac Kd Qh Js Tc 9d 8h 7s")); err == nil {
		t.Error("Eval7 with 8 cards succeeded, want error")
	}
	if _, err := pe.Eval7(cards(t, "Ac Ac Qh Js Tc 9d 8h")); err == nil {
		t.Error("Eval7 with a duplicate card succeeded, want error")
	}
}

func TestCompare(t *testing.T) {
	flush := eval7(t, "Ah Kh 9h 6h 3h 2c 2d")
	straight := eval7(t, "Ac Kd Qh Js Tc 2c 3d")
	if pe.Compare(flush, straight) != 1 || pe.Compare(straight, flush) != -1 {
		t.Error("flush must beat straight")
	}

	wheelSF := eval7(t, "Ah 2h 3h 4h 5h Kc Qd")
	sixSF := eval7(t, "2s 3s 4s 5s 6s Kc Qd")
	if pe.Compare(wheelSF, sixSF) != -1 {
		t.Error("steel wheel must lose to a six-high straight flush")
	}

	// Same two pair, kicker decides.
	kickA := eval7(t, "Jc Jd 8h 8s Ac 4d 3h")
	kickK := eval7(t, "Jh Js 8c 8d Kc 4s 3c")
	if pe.Compare(kickA, kickK) != 1 {
		t.Error("ace kicker must beat king kicker on identical two pair")
	}

	// Identical hands in different suits tie exactly.
	a := eval7(t, "Ac Kd Qh 9s 7c 4d 2h")
	b := eval7(t, "Ad Kh Qs 9c 7d 4h 2s")
	if pe.Compare(a, b) != 0 || pe.Compare(b, a) != 0 {
		t.Error("suit-only differences must tie")
	}
}

// loadDeals parses testdata/deals.txt: `community | hole ; hole ; ...`.
func loadDeals(t *testing.T) ([][]pe.Card, [][][]pe.Card) {
	t.Helper()
	data, err := os.ReadFile("testdata/deals.txt")
	if err != nil {
		t.Fatalf("reading deals fixture: %v", err)
	}
	var boards [][]pe.Card
	var holes [][][]pe.Card
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "|", 2)
		boards = append(boards, cards(t, parts[0]))
		var hs [][]pe.Card
		for _, hp := range strings.Split(parts[1], ";") {
			hs = append(hs, cards(t, hp))
		}
		holes = append(holes, hs)
	}
	return boards, holes
}

func TestShowdownFixtures(t *testing.T) {
	want := []struct {
		winners []int
		cats    []string
	}{
		{[]int{0}, []string{"two-pair", "two-pair"}},
		{[]int{0, 1}, []string{"one-pair", "one-pair"}},
		{[]int{0, 1}, []string{"straight-flush", "straight-flush"}},
		{[]int{1}, []string{"straight", "straight"}},
		{[]int{1}, []string{"straight", "flush"}},
		{[]int{1}, []string{"flush", "full-house"}},
		{[]int{0}, []string{"four-of-a-kind", "full-house"}},
		{[]int{1}, []string{"two-pair", "two-pair"}},
		{[]int{0}, []string{"straight-flush", "flush"}},
		{[]int{0, 1}, []string{"straight", "straight", "one-pair"}},
	}
	boards, holes := loadDeals(t)
	if len(boards) != len(want) {
		t.Fatalf("fixture has %d deals, expected %d", len(boards), len(want))
	}
	for i := range boards {
		winners, hands, err := pe.Showdown(boards[i], holes[i])
		if err != nil {
			t.Errorf("deal %d: Showdown error: %v", i, err)
			continue
		}
		if !slices.Equal(winners, want[i].winners) {
			t.Errorf("deal %d: winners = %v, want %v", i, winners, want[i].winners)
		}
		if len(hands) != len(want[i].cats) {
			t.Errorf("deal %d: got %d hands, want %d", i, len(hands), len(want[i].cats))
			continue
		}
		for p, cat := range want[i].cats {
			if hands[p].Category.String() != cat {
				t.Errorf("deal %d player %d: category = %s, want %s", i, p, hands[p].Category, cat)
			}
		}
	}
}

func TestShowdownErrors(t *testing.T) {
	board := cards(t, "Ah Kh Qh 2c 3d")
	if _, _, err := pe.Showdown(cards(t, "Ah Kh Qh 2c"), [][]pe.Card{cards(t, "As Ks")}); err == nil {
		t.Error("4-card community accepted, want error")
	}
	if _, _, err := pe.Showdown(board, nil); err == nil {
		t.Error("zero players accepted, want error")
	}
	if _, _, err := pe.Showdown(board, [][]pe.Card{cards(t, "As")}); err == nil {
		t.Error("1-card hole accepted, want error")
	}
	if _, _, err := pe.Showdown(board, [][]pe.Card{cards(t, "Ah 2s")}); err == nil {
		t.Error("hole card duplicating the board accepted, want error")
	}
	if _, _, err := pe.Showdown(board, [][]pe.Card{cards(t, "As 2s"), cards(t, "As 4d")}); err == nil {
		t.Error("hole card shared between players accepted, want error")
	}
}
