package contracttest

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// TestNoTestOnlyDependencies checks the run and the command line tool stand on
// their own. The mock and the contract test are the harness the work is checked
// with, not part of it, and a run that reached into them would be checking
// itself.
func TestNoTestOnlyDependencies(t *testing.T) {
	t.Parallel()
	forbidden := []string{
		"example.com/vcf/fleetlcm/internal/mocklcm",
		"example.com/vcf/fleetlcm/contracttest",
	}
	for _, dir := range []string{"../fleetrun", "../cmd"} {
		dir := dir
		t.Run(strings.TrimPrefix(dir, "../"), func(t *testing.T) {
			t.Parallel()
			for _, imp := range importsUnder(t, dir) {
				for _, bad := range forbidden {
					if imp.path == bad || strings.HasPrefix(imp.path, bad+"/") {
						t.Errorf("%s imports %s", imp.file, imp.path)
					}
				}
			}
		})
	}
}

// TestStandardLibraryOnly checks nothing was vendored in. Everything this tool
// needs is in the Go standard library.
func TestStandardLibraryOnly(t *testing.T) {
	t.Parallel()

	raw, err := os.ReadFile("../go.mod")
	if err != nil {
		t.Fatalf("read go.mod: %v", err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "require") {
			t.Errorf("go.mod requires a module: %q; the standard library is enough", line)
		}
	}
	if _, err := os.Stat("../vendor"); err == nil {
		t.Errorf("../vendor exists; nothing needs vendoring")
	}
	if _, err := os.Stat("../go.sum"); err == nil {
		t.Errorf("../go.sum exists; nothing outside the standard library is used")
	}

	// The run itself must not reach outside the standard library either.
	for _, imp := range importsUnder(t, "../fleetrun") {
		if strings.Contains(strings.SplitN(imp.path, "/", 2)[0], ".") &&
			!strings.HasPrefix(imp.path, "example.com/vcf/fleetlcm/") {
			t.Errorf("%s imports %s, which is outside the standard library", imp.file, imp.path)
		}
	}
}

type importRef struct {
	file string
	path string
}

// importsUnder collects the imports of every non-test Go file under a directory.
func importsUnder(t *testing.T, dir string) []importRef {
	t.Helper()
	var out []importRef
	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, nil, parser.ImportsOnly)
		if err != nil {
			return err
		}
		for _, spec := range f.Imports {
			value, err := strconv.Unquote(spec.Path.Value)
			if err != nil {
				continue
			}
			out = append(out, importRef{file: path, path: value})
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s: %v", dir, err)
	}
	if len(out) == 0 {
		t.Fatalf("no Go source found under %s", dir)
	}
	return out
}
