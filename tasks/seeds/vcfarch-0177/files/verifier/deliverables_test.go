package verifier_test

import (
	"go/ast"
	"go/parser"
	"go/token"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func TestResearchRecord(t *testing.T) {
	b, err := os.ReadFile(filepath.Join(projectRoot, "research.md"))
	if err != nil {
		t.Fatalf("read research.md: %v", err)
	}
	text := string(b)
	lower := strings.ToLower(text)
	if !regexp.MustCompile(`(?i)access(?:ed| date)\s*:?\s*20[0-9]{2}-[0-9]{2}-[0-9]{2}`).MatchString(text) {
		t.Fatal("research.md does not record an ISO access date")
	}
	for _, required := range []string{
		"Broadcom",
		"VMware Aria Operations", "8.18.6", "VCF Operations", "9.0.2",
		"VMware Aria Automation", "8.18.0", "VCF Automation",
		"VMware Aria Operations for Logs", "8.18.3", "VCF Operations for Logs", "9.0.1",
		"end of general support", "content", "sizing", "workload domain",
	} {
		if !strings.Contains(lower, strings.ToLower(required)) {
			t.Fatalf("research.md does not cover %q", required)
		}
	}
	if strings.Contains(lower, ".invalid") || strings.Contains(lower, "localhost") || strings.Contains(lower, "127.0.0.1") {
		t.Fatal("research.md contains a fixture or local URL")
	}

	urlPattern := regexp.MustCompile(`https://[^\s|)>]+`)
	seen := map[string]bool{}
	for _, raw := range urlPattern.FindAllString(text, -1) {
		raw = strings.TrimRight(raw, ".,;")
		u, err := url.Parse(raw)
		if err != nil || u.Scheme != "https" || u.Hostname() == "" {
			t.Fatalf("invalid research URL %q", raw)
		}
		host := strings.ToLower(u.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Fatalf("research URL is not Broadcom-published: %q", raw)
		}
		seen[raw] = true
	}
	if len(seen) < 6 {
		t.Fatalf("research.md records only %d distinct Broadcom URLs; want at least 6", len(seen))
	}
}

func TestPackageContainsTableDrivenTests(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join(projectRoot, "migrationplan", "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) == 0 {
		t.Fatal("migrationplan has no package test files")
	}

	hasTable, hasRange := false, false
	fileset := token.NewFileSet()
	for _, path := range paths {
		file, err := parser.ParseFile(fileset, path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range file.Decls {
			fn, ok := declaration.(*ast.FuncDecl)
			if !ok || fn.Body == nil || !strings.HasPrefix(fn.Name.Name, "Test") {
				continue
			}
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				switch n := node.(type) {
				case *ast.RangeStmt:
					hasRange = true
				case *ast.CompositeLit:
					array, ok := n.Type.(*ast.ArrayType)
					if ok && array.Len == nil {
						if _, ok := array.Elt.(*ast.StructType); ok {
							hasTable = true
						}
					}
				}
				return true
			})
		}
	}
	if !hasTable || !hasRange {
		t.Fatal("migrationplan package tests are not table-driven")
	}
}
