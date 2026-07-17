package cmdline

import (
	"reflect"
	"strings"
	"testing"
)

// Acceptance contract for the shell-safety features: Command.Quoted,
// Command.Expand (${VAR} from a map, strict/lenient), and
// Command.Fill ({placeholder} templates with validation).

func TestQuotedLeavesSafeArgsBare(t *testing.T) {
	c := New("rsync", "-az", "--delete", "src/", "user@host:/srv/app", "x=1", "a,b", "%20", "+v")
	want := "rsync -az --delete src/ user@host:/srv/app x=1 a,b %20 +v"
	if got := c.Quoted(); got != want {
		t.Fatalf("Quoted() = %q, want %q", got, want)
	}
}

func TestQuotedWrapsAnythingElseInSingleQuotes(t *testing.T) {
	cases := []struct {
		arg  string
		want string
	}{
		{"hello world", "'hello world'"},
		{"", "''"},
		{"$HOME", "'$HOME'"},
		{"a;rm -rf /tmp/x", "'a;rm -rf /tmp/x'"},
		{"*", "'*'"},
		{"two\nlines", "'two\nlines'"},
		{"tab\there", "'tab\there'"},
		{"back`tick`", "'back`tick`'"},
		{"quote\"me\"", "'quote\"me\"'"},
	}
	for _, tc := range cases {
		got := New("echo", tc.arg).Quoted()
		if want := "echo " + tc.want; got != want {
			t.Fatalf("Quoted() with arg %q = %q, want %q", tc.arg, got, want)
		}
	}
}

func TestQuotedEscapesEmbeddedSingleQuotes(t *testing.T) {
	got := New("echo", "it's here").Quoted()
	want := `echo 'it'\''s here'`
	if got != want {
		t.Fatalf("Quoted() = %q, want %q", got, want)
	}
}

func TestQuotedQuotesTheExecutableToo(t *testing.T) {
	got := New("/opt/my tools/run", "--ok").Quoted()
	want := `'/opt/my tools/run' --ok`
	if got != want {
		t.Fatalf("Quoted() = %q, want %q", got, want)
	}
}

func TestExpandSubstitutesBracedVariables(t *testing.T) {
	c := New("pg_dump", "--host=${DB_HOST}", "--port=${DB_PORT}", "${DB_NAME}")
	out, err := c.Expand(map[string]string{
		"DB_HOST": "db.internal",
		"DB_PORT": "5432",
		"DB_NAME": "orders",
	}, Strict)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"pg_dump", "--host=db.internal", "--port=5432", "orders"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Expand Args() = %v, want %v", got, want)
	}
}

func TestExpandTouchesTheExecutableAndAdjacentRefs(t *testing.T) {
	c := New("${BIN_DIR}/deploy", "${ENV}${REGION}")
	out, err := c.Expand(map[string]string{"BIN_DIR": "/opt/bin", "ENV": "prod-", "REGION": "eu1"}, Strict)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"/opt/bin/deploy", "prod-eu1"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Expand Args() = %v, want %v", got, want)
	}
}

func TestExpandStrictFailsOnMissingVariableByName(t *testing.T) {
	c := New("run", "--token=${API_TOKEN}")
	if _, err := c.Expand(map[string]string{}, Strict); err == nil {
		t.Fatal("Strict Expand with missing variable succeeded, want error")
	} else if !strings.Contains(err.Error(), "API_TOKEN") {
		t.Fatalf("error %q does not name the missing variable", err)
	}
}

func TestExpandLenientSubstitutesEmptyForMissing(t *testing.T) {
	c := New("run", "--token=${API_TOKEN}", "--env=${ENV}")
	out, err := c.Expand(map[string]string{"ENV": "staging"}, Lenient)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"run", "--token=", "--env=staging"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Lenient Expand Args() = %v, want %v", got, want)
	}
}

func TestExpandRejectsUnterminatedReferenceInBothModes(t *testing.T) {
	for _, mode := range []ExpandMode{Strict, Lenient} {
		c := New("run", "--path=${OOPS")
		if _, err := c.Expand(map[string]string{"OOPS": "x"}, mode); err == nil {
			t.Fatalf("Expand(mode %v) with unterminated ${ succeeded, want error", mode)
		}
	}
}

func TestExpandLeavesBareDollarsAndPlainTextAlone(t *testing.T) {
	c := New("awk", "$1 > 10", "cost$", "50%")
	out, err := c.Expand(map[string]string{"1": "nope"}, Lenient)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"awk", "$1 > 10", "cost$", "50%"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Expand Args() = %v, want %v (only ${...} is a reference)", got, want)
	}
}

func TestExpandReturnsANewCommand(t *testing.T) {
	c := New("run", "${A}")
	out, err := c.Expand(map[string]string{"A": "expanded"}, Strict)
	if err != nil {
		t.Fatal(err)
	}
	if got := c.Args()[1]; got != "${A}" {
		t.Fatalf("original mutated: Args()[1] = %q, want %q", got, "${A}")
	}
	if got := out.Args()[1]; got != "expanded" {
		t.Fatalf("expanded copy Args()[1] = %q", got)
	}
}

func TestFillSubstitutesPlaceholders(t *testing.T) {
	c := New("ffmpeg", "-i", "{input}", "--out={output}")
	out, err := c.Fill(map[string]string{"input": "raw.mov", "output": "final.mp4"})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"ffmpeg", "-i", "raw.mov", "--out=final.mp4"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Fill Args() = %v, want %v", got, want)
	}
	// Original untouched.
	if got := c.Args()[2]; got != "{input}" {
		t.Fatalf("original mutated: Args()[2] = %q", got)
	}
}

func TestFillFailsOnUnboundPlaceholder(t *testing.T) {
	c := New("convert", "{input}", "{output}")
	_, err := c.Fill(map[string]string{"input": "a.png"})
	if err == nil {
		t.Fatal("Fill with unbound {output} succeeded, want error")
	}
	if !strings.Contains(err.Error(), "output") {
		t.Fatalf("error %q does not name the unbound placeholder", err)
	}
}

func TestFillFailsOnUnusedBinding(t *testing.T) {
	c := New("convert", "{input}")
	_, err := c.Fill(map[string]string{"input": "a.png", "outptu": "b.png"})
	if err == nil {
		t.Fatal("Fill with unused binding succeeded, want error (catches template typos)")
	}
	if !strings.Contains(err.Error(), "outptu") {
		t.Fatalf("error %q does not name the unused binding", err)
	}
}

func TestFillRepeatedPlaceholderCountsAsUsed(t *testing.T) {
	c := New("cp", "{input}", "{input}.bak")
	out, err := c.Fill(map[string]string{"input": "cfg.yml"})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"cp", "cfg.yml", "cfg.yml.bak"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Fill Args() = %v, want %v", got, want)
	}
}

func TestFillIgnoresNonPlaceholderBraces(t *testing.T) {
	// Only {lowercase_ident} is a placeholder; anything else stays put.
	c := New("awk", "{print $2}", "{FOO}", "{2x}", "un{closed")
	out, err := c.Fill(map[string]string{})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"awk", "{print $2}", "{FOO}", "{2x}", "un{closed"}
	if got := out.Args(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Fill Args() = %v, want %v", got, want)
	}
}
