package blackjack_test

import (
	"os"
	"strings"
	"testing"

	bj "go-blackjack"
)

func shoe(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatalf("reading shoe fixture %s: %v", name, err)
	}
	return string(data)
}

func transcript(lines ...string) string {
	return strings.Join(lines, "\n") + "\n"
}

func runGame(t *testing.T, shoeData, script string, rounds int) string {
	t.Helper()
	out, err := bj.Run(shoeData, script, rounds)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return out
}

func TestHandValue(t *testing.T) {
	cases := []struct {
		cards []string
		total int
		soft  bool
	}{
		{[]string{"KH", "QD"}, 20, false},
		{[]string{"AH", "6D"}, 17, true},
		{[]string{"AH", "KD"}, 21, true},
		{[]string{"AH", "AD"}, 12, true},
		{[]string{"AH", "AD", "9C"}, 21, true},
		{[]string{"AS", "AD", "AC", "TH"}, 13, false},
		{[]string{"KH", "QD", "3S"}, 23, false},
		{[]string{"5H", "6D", "TC"}, 21, false},
		{[]string{"AH", "5D", "5C"}, 21, true},
		{[]string{"9H", "7D", "AS"}, 17, false},
	}
	for _, c := range cases {
		total, soft, err := bj.HandValue(c.cards)
		if err != nil {
			t.Errorf("HandValue(%v): %v", c.cards, err)
			continue
		}
		if total != c.total || soft != c.soft {
			t.Errorf("HandValue(%v) = (%d, %v), want (%d, %v)", c.cards, total, soft, c.total, c.soft)
		}
	}
	if _, _, err := bj.HandValue([]string{"ZZ"}); err == nil {
		t.Error("HandValue accepted a bogus card")
	}
	if _, _, err := bj.HandValue([]string{"10H"}); err == nil {
		t.Error("HandValue accepted '10H' (tens are 'T')")
	}
}

func TestParseShoe(t *testing.T) {
	cards, err := bj.ParseShoe("# a comment\nAS TH # trailing\n 9D\n")
	if err != nil {
		t.Fatalf("ParseShoe: %v", err)
	}
	want := []string{"AS", "TH", "9D"}
	if len(cards) != len(want) {
		t.Fatalf("ParseShoe returned %v, want %v", cards, want)
	}
	for i := range want {
		if cards[i] != want[i] {
			t.Fatalf("ParseShoe returned %v, want %v", cards, want)
		}
	}
	if _, err := bj.ParseShoe("AS XX"); err == nil {
		t.Error("ParseShoe accepted a bogus card")
	}
}

func TestBasicRoundsTranscript(t *testing.T) {
	got := runGame(t, shoe(t, "shoe_basic.txt"), "stand hit hit stand hit double", 5)
	want := transcript(
		"== round 1 ==",
		"player: TH QD (20)",
		"dealer: AH ??",
		"stand: (20)",
		"dealer: AH 6C (soft 17)",
		"result: win +10",
		"bankroll: 110",
		"== round 2 ==",
		"player: 5H 2D (7)",
		"dealer: TS ??",
		"hit: 4S (11)",
		"hit: KD (21)",
		"dealer: TS 9C (19)",
		"result: win +10",
		"bankroll: 120",
		"== round 3 ==",
		"player: 9H 8D (17)",
		"dealer: 7C ??",
		"stand: (17)",
		"dealer: 7C TD (17)",
		"result: push +0",
		"bankroll: 120",
		"== round 4 ==",
		"player: KS 5C (15)",
		"dealer: 6D ??",
		"hit: 9S (24 bust)",
		"dealer: 6D TC (16)",
		"result: lose -10",
		"bankroll: 110",
		"== round 5 ==",
		"player: 6H 5S (11)",
		"dealer: 8H ??",
		"double: QC (21)",
		"dealer: 8H 7S (15)",
		"dealer hit: 9D (24 bust)",
		"result: win +20",
		"bankroll: 130",
	)
	if got != want {
		t.Errorf("basic transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestNaturalsTranscript(t *testing.T) {
	// Naturals settle immediately and consume no script actions at all.
	got := runGame(t, shoe(t, "shoe_naturals.txt"), "", 3)
	want := transcript(
		"== round 1 ==",
		"player: AH KD (blackjack)",
		"dealer: 5D ??",
		"dealer: 5D 8C (13)",
		"result: blackjack +15",
		"bankroll: 115",
		"== round 2 ==",
		"player: 9H 8D (17)",
		"dealer: AS ??",
		"dealer: AS KH (blackjack)",
		"result: dealer blackjack -10",
		"bankroll: 105",
		"== round 3 ==",
		"player: AC TS (blackjack)",
		"dealer: QS ??",
		"dealer: QS AD (blackjack)",
		"result: push +0",
		"bankroll: 105",
	)
	if got != want {
		t.Errorf("naturals transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestSplitsTranscript(t *testing.T) {
	got := runGame(t, shoe(t, "shoe_splits.txt"), "split double double split split stand stand", 3)
	want := transcript(
		"== round 1 ==",
		"player: 8C 8D (16)",
		"dealer: 6H ??",
		"split: 8C | 8D",
		"hand 1: 8C 3S (11)",
		"double: TS (21)",
		"hand 2: 8D 2D (10)",
		"double: 9H (19)",
		"dealer: 6H TH (16)",
		"dealer hit: 5C (21)",
		"result hand 1: push +0",
		"result hand 2: lose -20",
		"bankroll: 80",
		"== round 2 ==",
		"player: AH AD (soft 12)",
		"dealer: QS ??",
		"split: AH | AD",
		"hand 1: AH KD (soft 21)",
		"hand 2: AD 4C (soft 15)",
		"dealer: QS 7C (17)",
		"result hand 1: win +10",
		"result hand 2: lose -10",
		"bankroll: 80",
		"== round 3 ==",
		"player: KH TS (20)",
		"dealer: 5D ??",
		"split: KH | TS",
		"hand 1: KH 9C (19)",
		"stand: (19)",
		"hand 2: TS 7H (17)",
		"stand: (17)",
		"dealer: 5D 8C (13)",
		"dealer hit: 6S (19)",
		"result hand 1: push +0",
		"result hand 2: lose -10",
		"bankroll: 70",
	)
	if got != want {
		t.Errorf("splits transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestRunErrors(t *testing.T) {
	basic := shoe(t, "shoe_basic.txt")
	cases := []struct {
		name   string
		shoe   string
		script string
		rounds int
	}{
		{"zero rounds", basic, "stand", 0},
		{"negative rounds", basic, "stand", -1},
		{"bad shoe card", "XX YY", "", 1},
		{"shoe too short to deal", "5H 6D 7C", "", 1},
		{"shoe runs dry on a hit", "5H 6D 7C 8S 2C", "hit hit", 1},
		{"script exhausted", basic, "", 1},
		{"unused actions", basic, "stand stand", 1},
		{"unknown action", basic, "surrender", 1},
		{"double after hitting", "5H TS 2D 9C 4S KD", "hit double", 1},
		{"split with unequal cards", "KH 5D 9S 8C", "split", 1},
		{"re-split banned", "8C 6H 8D TH 8S 2D", "split split", 1},
		{"split of the second hand banned", "8C 6H 8D TH 3S 8S", "split stand split", 1},
	}
	for _, c := range cases {
		out, err := bj.Run(c.shoe, c.script, c.rounds)
		if err == nil {
			t.Errorf("%s: Run succeeded, want error", c.name)
		}
		if out != "" {
			t.Errorf("%s: Run returned a transcript alongside the error", c.name)
		}
	}
}

func TestDealerStandsOnSoft17(t *testing.T) {
	// Dealer shows A6 (soft 17) and must NOT draw: player's 18 wins.
	got := runGame(t, "9H AH 9S 6C", "stand", 1)
	want := transcript(
		"== round 1 ==",
		"player: 9H 9S (18)",
		"dealer: AH ??",
		"stand: (18)",
		"dealer: AH 6C (soft 17)",
		"result: win +10",
		"bankroll: 110",
	)
	if got != want {
		t.Errorf("soft-17 transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestDealerDoesNotDrawWhenEveryHandBusted(t *testing.T) {
	// Dealer sits on 12 and would have to draw — but the player busted, so
	// the hole card is revealed and no dealer card leaves the shoe.
	got := runGame(t, "KS 6D 5C 6C 9S", "hit", 1)
	want := transcript(
		"== round 1 ==",
		"player: KS 5C (15)",
		"dealer: 6D ??",
		"hit: 9S (24 bust)",
		"dealer: 6D 6C (12)",
		"result: lose -10",
		"bankroll: 90",
	)
	if got != want {
		t.Errorf("busted-hand transcript mismatch:\ngot:\n%s\nwant:\n%s", got, want)
	}
}

func TestSplitTwentyOneIsNotBlackjack(t *testing.T) {
	// An ace-ten made after splitting pays even money, not 3:2.
	got := runGame(t, "AH 7S AD 9C KD KC 2S", "split", 1)
	if !strings.Contains(got, "result hand 1: win +10") {
		t.Errorf("split 21 should win +10 (not a natural):\n%s", got)
	}
	if strings.Contains(got, "+15") {
		t.Errorf("split 21 must not pay 3:2:\n%s", got)
	}
}
