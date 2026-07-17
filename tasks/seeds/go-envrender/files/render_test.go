package envrender

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Environment always comes from an injected map — nothing in the package may
// read the real process environment, so every test here is hermetic.

func write(t *testing.T, dir, rel, content string) string {
	t.Helper()
	path := filepath.Join(dir, rel)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func mustExpand(t *testing.T, src string, env map[string]string, opts Options) string {
	t.Helper()
	out, err := Expand(src, env, opts)
	if err != nil {
		t.Fatalf("Expand(%q): %v", src, err)
	}
	return out
}

func wantContains(t *testing.T, err error, fragments ...string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected an error containing %q, got nil", fragments)
	}
	for _, frag := range fragments {
		if !strings.Contains(err.Error(), frag) {
			t.Fatalf("error %q does not contain %q", err.Error(), frag)
		}
	}
}

func TestExpandBasicSubstitution(t *testing.T) {
	env := map[string]string{"HOST": "db.internal", "PORT": "5432"}
	got := mustExpand(t, "host=${HOST} port=${PORT} again=${HOST}", env, Options{Strict: true})
	want := "host=db.internal port=5432 again=db.internal"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestExpandDefaultForms(t *testing.T) {
	// ${VAR:-def} falls back when VAR is unset OR empty;
	// ${VAR-def} falls back only when VAR is unset.
	env := map[string]string{"EMPTY": "", "SET": "real"}
	src := "a=${MISSING:-fallback}\n" +
		"b=${EMPTY:-fallback}\n" +
		"c=${SET:-fallback}\n" +
		"d=${EMPTY-fallback}\n" +
		"e=${MISSING-fallback}\n"
	want := "a=fallback\nb=fallback\nc=real\nd=\ne=fallback\n"
	if got := mustExpand(t, src, env, Options{Strict: true}); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestExpandDollarEscapes(t *testing.T) {
	// $$ always yields one literal $. A $ not followed by { passes through
	// untouched, so shell-isms like $9 and $(pwd) survive rendering.
	src := "cost is $$5, raw $${NOT_A_VAR}, price $9, shell $(pwd), end $"
	want := "cost is $5, raw ${NOT_A_VAR}, price $9, shell $(pwd), end $"
	if got := mustExpand(t, src, nil, Options{Strict: true}); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestExpandDefaultTextIsLiteral(t *testing.T) {
	// The default is taken verbatim: no substitution happens inside it.
	env := map[string]string{"Y": "should-not-be-used"}
	got := mustExpand(t, "x=${X:-$Y} p=${P:-}", env, Options{Strict: true})
	if got != "x=$Y p=" {
		t.Fatalf("got %q, want %q", got, "x=$Y p=")
	}
}

func TestExpandStrictModeRejectsUndefinedVariable(t *testing.T) {
	src := "# endpoint\nname = api\nurl = ${SCHEME}://${HOST}\n"
	_, err := Expand(src, map[string]string{"HOST": "db1"}, Options{Strict: true})
	// Position is line:col (1-based) of the $ that started the reference.
	wantContains(t, err, "3:7", `undefined variable "SCHEME"`)
}

func TestExpandLenientModeSubstitutesEmpty(t *testing.T) {
	src := "# endpoint\nname = api\nurl = ${SCHEME}://${HOST}\n"
	got := mustExpand(t, src, map[string]string{"HOST": "db1"}, Options{})
	want := "# endpoint\nname = api\nurl = ://db1\n"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestExpandPresentButEmptyIsNotUndefined(t *testing.T) {
	// Strict mode complains about ABSENT keys only; empty string is a value.
	got := mustExpand(t, "v=[${EMPTY}]", map[string]string{"EMPTY": ""}, Options{Strict: true})
	if got != "v=[]" {
		t.Fatalf("got %q, want %q", got, "v=[]")
	}
}

func TestExpandSyntaxErrors(t *testing.T) {
	cases := []struct {
		src       string
		fragments []string
	}{
		{"cfg = ${UNCLOSED", []string{"1:7", "unterminated"}},
		{"x = ${}", []string{"1:5", "invalid variable name"}},
		{"x = ${9BAD}", []string{"1:5", "invalid variable name"}},
		{"x = ${A B}", []string{"1:5", "invalid variable reference"}},
	}
	for _, tc := range cases {
		// Syntax errors are errors in BOTH modes; lenient only forgives
		// well-formed references to unknown variables.
		for _, opts := range []Options{{Strict: true}, {Strict: false}} {
			_, err := Expand(tc.src, nil, opts)
			wantContains(t, err, tc.fragments...)
		}
	}
}

func TestExpandDoesNotProcessIncludes(t *testing.T) {
	// Include directives are a file-rendering feature; Expand leaves them be.
	src := "@include \"other.conf\"\nport=${PORT}\n"
	got := mustExpand(t, src, map[string]string{"PORT": "8080"}, Options{Strict: true})
	want := "@include \"other.conf\"\nport=8080\n"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestRenderFileResolvesIncludesRelativeToIncluder(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "service ${APP}\n@include \"inc/db.conf\"\ntail=1\n")
	// db.conf includes creds.conf by a path relative to inc/, not to a.conf.
	write(t, dir, "inc/db.conf", "db_host = ${DB_HOST:-localhost}\n@include \"creds.conf\"\n")
	// No trailing newline here: the renderer adds one so tail=1 stays on its own line.
	write(t, dir, "inc/creds.conf", "db_user = ${DB_USER}")

	env := map[string]string{"APP": "billing", "DB_USER": "svc"}
	got, err := RenderFile(root, env, Options{Strict: true})
	if err != nil {
		t.Fatalf("RenderFile: %v", err)
	}
	want := "service billing\ndb_host = localhost\ndb_user = svc\ntail=1\n"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestIncludeDirectiveMayBeIndented(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "top\n  @include \"b.conf\"\nbottom\n")
	write(t, dir, "b.conf", "mid\n")
	got, err := RenderFile(root, nil, Options{})
	if err != nil {
		t.Fatalf("RenderFile: %v", err)
	}
	// The whole directive line is replaced; its indentation is not preserved.
	if want := "top\nmid\nbottom\n"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestEmptyIncludeContributesNothing(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "before\n@include \"empty.conf\"\nafter\n")
	write(t, dir, "empty.conf", "")
	got, err := RenderFile(root, nil, Options{})
	if err != nil {
		t.Fatalf("RenderFile: %v", err)
	}
	if want := "before\nafter\n"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestIncludePathItselfIsSubstituted(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "@include \"${PROFILE}.conf\"\n")
	write(t, dir, "prod.conf", "mode=prod\n")
	got, err := RenderFile(root, map[string]string{"PROFILE": "prod"}, Options{Strict: true})
	if err != nil {
		t.Fatalf("RenderFile: %v", err)
	}
	if want := "mode=prod\n"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestIncludeCycleIsReportedWithItsPath(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "@include \"b.conf\"\n")
	write(t, dir, "b.conf", "@include \"a.conf\"\n")
	_, err := RenderFile(root, nil, Options{})
	// File names in errors are shown relative to the root file's directory.
	wantContains(t, err, "include cycle", "a.conf -> b.conf -> a.conf")
}

func TestSelfIncludeIsACycle(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "s.conf", "@include \"s.conf\"\n")
	_, err := RenderFile(root, nil, Options{})
	wantContains(t, err, "include cycle", "s.conf -> s.conf")
}

func TestDiamondIncludesAreAllowed(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "@include \"left.conf\"\n@include \"right.conf\"\n")
	write(t, dir, "left.conf", "@include \"common.conf\"\n")
	write(t, dir, "right.conf", "@include \"common.conf\"\n")
	write(t, dir, "common.conf", "shared=${N:-1}\n")
	got, err := RenderFile(root, nil, Options{Strict: true})
	if err != nil {
		t.Fatalf("diamond include should not be a cycle, got %v", err)
	}
	if want := "shared=1\nshared=1\n"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestMissingIncludeReportsIncluderPosition(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "ok=1\n@include \"nope.conf\"\n")
	_, err := RenderFile(root, nil, Options{})
	wantContains(t, err, "include not found", "nope.conf", "a.conf:2:1")
}

func TestMalformedIncludeDirectives(t *testing.T) {
	dir := t.TempDir()
	for _, bad := range []string{
		"@include nope.conf\n",       // path must be double-quoted
		"@include \"x.conf\" junk\n", // nothing after the closing quote
	} {
		root := write(t, dir, "bad.conf", bad)
		_, err := RenderFile(root, nil, Options{})
		wantContains(t, err, "malformed @include", "bad.conf:1:1")
	}
}

func TestMidLineIncludeIsJustText(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "docs: see @include \"x.conf\" for syntax\n")
	got, err := RenderFile(root, nil, Options{})
	if err != nil {
		t.Fatalf("RenderFile: %v", err)
	}
	if want := "docs: see @include \"x.conf\" for syntax\n"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestStrictErrorInsideIncludeNamesThatFile(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "@include \"inc/db.conf\"\n")
	write(t, dir, "inc/db.conf", "host=${MISSING_DB}\n")
	_, err := RenderFile(root, nil, Options{Strict: true})
	wantContains(t, err, "inc/db.conf:1:6", `undefined variable "MISSING_DB"`)
}

func TestStrictErrorInRootNamesRootFile(t *testing.T) {
	dir := t.TempDir()
	root := write(t, dir, "a.conf", "x=${GONE}\n")
	_, err := RenderFile(root, nil, Options{Strict: true})
	wantContains(t, err, "a.conf:1:3", `undefined variable "GONE"`)
}

func TestRenderFileMissingRootFile(t *testing.T) {
	dir := t.TempDir()
	_, err := RenderFile(filepath.Join(dir, "root.conf"), nil, Options{})
	wantContains(t, err, "root.conf")
}
