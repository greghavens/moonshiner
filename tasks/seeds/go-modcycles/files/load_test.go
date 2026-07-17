package modcycles

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeTree lays out .mod files (and anything else) under a fresh temp root.
func writeTree(t *testing.T, files map[string]string) string {
	t.Helper()
	root := t.TempDir()
	for rel, content := range files {
		path := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestLoadWalksTreeAndParses(t *testing.T) {
	root := writeTree(t, map[string]string{
		"frontend.mod": "# ui layer\nmodule frontend\n\nimport api\nimport auth\n",
		"svc/api.mod":  "module api\nimport store\n",
		"svc/deep/store.mod": "  module store  \n" +
			"# storage has no imports\n",
		"auth/auth.mod": "module auth\nimport   store\n", // extra spaces are fine
		"README.txt":    "not a module file, ignore me\n",
		"svc/notes.md":  "also ignored\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := g.Modules(); !equal(got, []string{"api", "auth", "frontend", "store"}) {
		t.Errorf("Modules() = %v, want sorted [api auth frontend store]", got)
	}
	imports, ok := g.Imports("frontend")
	if !ok || !equal(imports, []string{"api", "auth"}) {
		t.Errorf("Imports(frontend) = %v, %v; want [api auth] in declaration order", imports, ok)
	}
	imports, ok = g.Imports("store")
	if !ok || len(imports) != 0 {
		t.Errorf("Imports(store) = %v, %v; want empty, true", imports, ok)
	}
	if _, ok := g.Imports("nope"); ok {
		t.Error("Imports(nope) reported ok for an unknown module")
	}
	if missing := g.Missing(); len(missing) != 0 {
		t.Errorf("Missing() = %v, want none", missing)
	}
}

func TestLoadEmptyTree(t *testing.T) {
	g, err := Load(t.TempDir())
	if err != nil {
		t.Fatalf("Load on empty tree: %v", err)
	}
	if len(g.Modules()) != 0 {
		t.Errorf("Modules() = %v, want empty", g.Modules())
	}
	if cycles := g.Cycles(); len(cycles) != 0 {
		t.Errorf("Cycles() = %v, want none", cycles)
	}
	order, err := g.Order()
	if err != nil || len(order) != 0 {
		t.Errorf("Order() = %v, %v; want empty, nil", order, err)
	}
}

func TestMissingImportsAreReportedSortedAndDeduped(t *testing.T) {
	root := writeTree(t, map[string]string{
		"a.mod": "module a\nimport zeta\nimport beta\n",
		"b.mod": "module b\nimport beta\nimport a\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got := g.Missing(); !equal(got, []string{"beta", "zeta"}) {
		t.Errorf("Missing() = %v, want [beta zeta]", got)
	}
}

func TestLoadRejectsMalformedFiles(t *testing.T) {
	cases := []struct {
		name    string
		files   map[string]string
		errWant string // substring the error must carry
	}{
		{"no module line", map[string]string{
			"broken.mod": "import a\n"}, "broken.mod"},
		{"empty file", map[string]string{
			"empty.mod": "# only a comment\n"}, "empty.mod"},
		{"second module line", map[string]string{
			"twice.mod": "module one\nmodule two\n"}, "twice.mod"},
		{"module missing its name", map[string]string{
			"anon.mod": "module\n"}, "anon.mod"},
		{"junk directive", map[string]string{
			"junk.mod": "module j\nrequires k\n"}, "junk.mod"},
		{"module name with spaces", map[string]string{
			"spaced.mod": "module a b\n"}, "spaced.mod"},
		{"bad character in name", map[string]string{
			"odd.mod": "module core\nimport uh/oh\n"}, "odd.mod"},
		{"duplicate import in one file", map[string]string{
			"dup.mod": "module d\nimport x\nimport x\n"}, "dup.mod"},
		{"same module declared twice", map[string]string{
			"one/pay.mod": "module payments\n",
			"two/pay.mod": "module payments\n"}, "payments"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			root := writeTree(t, tc.files)
			_, err := Load(root)
			if err == nil {
				t.Fatalf("Load accepted %v", tc.files)
			}
			if !strings.Contains(err.Error(), tc.errWant) {
				t.Errorf("error %q should mention %q", err.Error(), tc.errWant)
			}
		})
	}
}
