package verify

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"testing"
)

// TestSubmissionIncludesRequestedClientTests verifies the test artifact the
// task asks for, while the behavioral tests in this package verify the client
// itself. It intentionally checks structural traits rather than test names.
func TestSubmissionIncludesRequestedClientTests(t *testing.T) {
	paths, err := filepath.Glob("../vcfops/*_test.go")
	if err != nil {
		t.Fatalf("find vcfops tests: %v", err)
	}
	if len(paths) == 0 {
		t.Fatal("vcfops has no *_test.go files; add the requested table-driven client tests")
	}

	var hasTest, hasTable, hasSubtest, importsMock, startsMock, readsRequestLog bool
	mockNames := map[string]bool{}
	files := token.NewFileSet()
	for _, path := range paths {
		file, err := parser.ParseFile(files, path, nil, 0)
		if err != nil {
			t.Errorf("parse %s: %v", filepath.Clean(path), err)
			continue
		}
		for _, imported := range file.Imports {
			importPath, err := strconv.Unquote(imported.Path.Value)
			if err == nil && importPath == "vcfops.local/opssync/mock" {
				importsMock = true
				name := "mock"
				if imported.Name != nil {
					name = imported.Name.Name
				}
				mockNames[name] = true
			}
		}
		ast.Inspect(file, func(node ast.Node) bool {
			switch n := node.(type) {
			case *ast.FuncDecl:
				if n.Recv == nil && n.Name.IsExported() && len(n.Name.Name) > 4 && n.Name.Name[:4] == "Test" {
					hasTest = true
				}
			case *ast.RangeStmt:
				hasTable = true
			case *ast.SelectorExpr:
				if n.Sel.Name == "Run" {
					hasSubtest = true
				}
				if ident, ok := n.X.(*ast.Ident); ok && mockNames[ident.Name] && n.Sel.Name == "Start" {
					startsMock = true
				}
				switch n.Sel.Name {
				case "Requests", "RequestsFor", "AcceptedBatches":
					readsRequestLog = true
				}
			case *ast.CallExpr:
				if ident, ok := n.Fun.(*ast.Ident); ok && mockNames["."] && ident.Name == "Start" {
					startsMock = true
				}
			}
			return true
		})
	}

	if !hasTest {
		t.Error("vcfops test files define no Test functions")
	}
	if !hasTable || !hasSubtest {
		t.Error("vcfops tests are not table-driven: want a ranged case table with subtests")
	}
	if !importsMock || !startsMock || !readsRequestLog {
		t.Error("vcfops tests must drive the loopback mock and assert against its request log")
	}
}
