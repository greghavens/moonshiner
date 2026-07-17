package preview

import (
	"testing"
	"unicode/utf8"
)

func TestShortMessageUnchanged(t *testing.T) {
	if got := Snippet("On my way", 60); got != "On my way" {
		t.Fatalf("Snippet = %q, want %q", got, "On my way")
	}
}

func TestOnlyFirstLineShown(t *testing.T) {
	if got := Snippet("Build green \nDetails in #ci", 60); got != "Build green" {
		t.Fatalf("Snippet = %q, want %q", got, "Build green")
	}
}

func TestAsciiTruncation(t *testing.T) {
	got := Snippet("deploy failed on host build-42 with exit code 137", 20)
	want := "deploy failed on ho…"
	if got != want {
		t.Fatalf("Snippet = %q, want %q", got, want)
	}
}

func TestMessageWithinBudgetIsNotCut(t *testing.T) {
	msg := "Привет, как дела?" // 17 characters
	if got := Snippet(msg, 20); got != msg {
		t.Fatalf("Snippet = %q, want the whole message %q", got, msg)
	}
	emoji := "🎉 Release shipped 🎉" // 19 characters
	if got := Snippet(emoji, 19); got != emoji {
		t.Fatalf("Snippet = %q, want the whole message %q", got, emoji)
	}
}

func TestLongMessageTruncation(t *testing.T) {
	got := Snippet("Ключ от серверной у Димы, зайду после обеда", 10)
	want := "Ключ от с…"
	if got != want {
		t.Fatalf("Snippet = %q, want %q", got, want)
	}
}

func TestSnippetsAreAlwaysValidText(t *testing.T) {
	messages := []string{
		"Ключ от серверной у Димы, зайду после обеда",
		"日本語のテストメッセージです、よろしくお願いします",
		"🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉",
		"mixed ascii и кириллица together in one line here",
	}
	for _, msg := range messages {
		for _, max := range []int{5, 8, 12, 25} {
			got := Snippet(msg, max)
			if !utf8.ValidString(got) {
				t.Fatalf("Snippet(%q, %d) = %q — result is not valid text", msg, max, got)
			}
			if n := utf8.RuneCountInString(got); n > max {
				t.Fatalf("Snippet(%q, %d) shows %d characters, budget is %d", msg, max, n, max)
			}
		}
	}
}

func TestInitials(t *testing.T) {
	cases := []struct{ name, want string }{
		{"bob marley", "B"},
		{"åsa lindgren", "Å"},
		{"Дима К", "Д"},
		{"  padded  ", "P"},
		{"", "?"},
	}
	for _, c := range cases {
		if got := Initial(c.name); got != c.want {
			t.Fatalf("Initial(%q) = %q, want %q", c.name, got, c.want)
		}
	}
}
