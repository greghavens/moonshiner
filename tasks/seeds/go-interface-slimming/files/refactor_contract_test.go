package relay

import (
	"bytes"
	"context"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"unicode"

	"example.com/go-interface-slimming/internal/dispatch"
	"example.com/go-interface-slimming/internal/httpapi"
	"example.com/go-interface-slimming/internal/worker"
)

type concreteServiceContract interface {
	GetRun(context.Context, string) (dispatch.Run, error)
	CancelRun(context.Context, string) error
	ListReady(context.Context, int) ([]dispatch.Run, error)
	MarkDispatched(context.Context, string) error
	RotateSigningKey(context.Context, string) error
	PurgeTenant(context.Context, string) error
}

var _ concreteServiceContract = (*dispatch.MemoryService)(nil)

type parsedPackage struct {
	fset  *token.FileSet
	files []*ast.File
}

type methodShape struct {
	params  []string
	results []string
}

func parseProductionPackage(t *testing.T, directory string) parsedPackage {
	t.Helper()
	fset := token.NewFileSet()
	entries, err := os.ReadDir(directory)
	if err != nil {
		t.Fatalf("read %s: %v", directory, err)
	}
	var files []*ast.File
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		path := filepath.Join(directory, entry.Name())
		file, err := parser.ParseFile(fset, path, nil, parser.AllErrors)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		files = append(files, file)
	}
	return parsedPackage{fset: fset, files: files}
}

func compactNode(t *testing.T, fset *token.FileSet, node ast.Node) string {
	t.Helper()
	var output bytes.Buffer
	if err := format.Node(&output, fset, node); err != nil {
		t.Fatalf("format AST node: %v", err)
	}
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return r
	}, output.String())
}

func expandedFieldTypes(t *testing.T, fset *token.FileSet, fields *ast.FieldList) []string {
	t.Helper()
	if fields == nil {
		return nil
	}
	var result []string
	for _, field := range fields.List {
		count := len(field.Names)
		if count == 0 {
			count = 1
		}
		for i := 0; i < count; i++ {
			result = append(result, compactNode(t, fset, field.Type))
		}
	}
	return result
}

func requireInterface(t *testing.T, pkg parsedPackage, name string, expected map[string]methodShape) {
	t.Helper()
	var found *ast.InterfaceType
	for _, file := range pkg.files {
		for _, declaration := range file.Decls {
			generic, ok := declaration.(*ast.GenDecl)
			if !ok || generic.Tok != token.TYPE {
				continue
			}
			for _, rawSpec := range generic.Specs {
				spec := rawSpec.(*ast.TypeSpec)
				if spec.Name.Name != name {
					continue
				}
				var ok bool
				found, ok = spec.Type.(*ast.InterfaceType)
				if !ok || spec.Assign.IsValid() {
					t.Fatalf("%s must be a declared local interface, not an alias", name)
				}
			}
		}
	}
	if found == nil {
		t.Fatalf("missing consumer-owned interface %s", name)
	}
	if len(found.Methods.List) != len(expected) {
		t.Fatalf("%s has %d entries, want exactly %d methods", name, len(found.Methods.List), len(expected))
	}
	for _, field := range found.Methods.List {
		if len(field.Names) != 1 {
			t.Fatalf("%s must not embed another interface", name)
		}
		method := field.Names[0].Name
		want, ok := expected[method]
		if !ok {
			t.Fatalf("%s has unrelated method %s", name, method)
		}
		function, ok := field.Type.(*ast.FuncType)
		if !ok {
			t.Fatalf("%s.%s is not a method signature", name, method)
		}
		got := methodShape{
			params:  expandedFieldTypes(t, pkg.fset, function.Params),
			results: expandedFieldTypes(t, pkg.fset, function.Results),
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("%s.%s shape = %#v, want %#v", name, method, got, want)
		}
	}
}

func requireStructFieldType(t *testing.T, pkg parsedPackage, structName, fieldName, typeName string) {
	t.Helper()
	for _, file := range pkg.files {
		for _, declaration := range file.Decls {
			generic, ok := declaration.(*ast.GenDecl)
			if !ok || generic.Tok != token.TYPE {
				continue
			}
			for _, rawSpec := range generic.Specs {
				spec := rawSpec.(*ast.TypeSpec)
				if spec.Name.Name != structName {
					continue
				}
				structure, ok := spec.Type.(*ast.StructType)
				if !ok {
					t.Fatalf("%s is not a struct", structName)
				}
				for _, field := range structure.Fields.List {
					if len(field.Names) == 1 && field.Names[0].Name == fieldName {
						if got := compactNode(t, pkg.fset, field.Type); got != typeName {
							t.Fatalf("%s.%s type = %s, want %s", structName, fieldName, got, typeName)
						}
						return
					}
				}
			}
		}
	}
	t.Fatalf("missing %s.%s", structName, fieldName)
}

func requireConstructorInput(t *testing.T, pkg parsedPackage, functionName, typeName string) {
	t.Helper()
	for _, file := range pkg.files {
		for _, declaration := range file.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Recv != nil || function.Name.Name != functionName {
				continue
			}
			inputs := expandedFieldTypes(t, pkg.fset, function.Type.Params)
			if len(inputs) == 0 || inputs[0] != typeName {
				t.Fatalf("%s first input = %v, want %s", functionName, inputs, typeName)
			}
			return
		}
	}
	t.Fatalf("missing constructor %s", functionName)
}

func TestInterfacesAreNarrowAndConsumerOwned(t *testing.T) {
	api := parseProductionPackage(t, "internal/httpapi")
	requireInterface(t, api, "RunService", map[string]methodShape{
		"GetRun":    {params: []string{"context.Context", "string"}, results: []string{"dispatch.Run", "error"}},
		"CancelRun": {params: []string{"context.Context", "string"}, results: []string{"error"}},
	})
	requireStructFieldType(t, api, "Handler", "service", "RunService")
	requireConstructorInput(t, api, "New", "RunService")

	workers := parseProductionPackage(t, "internal/worker")
	requireInterface(t, workers, "DispatchQueue", map[string]methodShape{
		"ListReady":      {params: []string{"context.Context", "int"}, results: []string{"[]dispatch.Run", "error"}},
		"MarkDispatched": {params: []string{"context.Context", "string"}, results: []string{"error"}},
	})
	requireStructFieldType(t, workers, "Runner", "queue", "DispatchQueue")
	requireConstructorInput(t, workers, "New", "DispatchQueue")
}

func TestProviderOwnsNoReplacementInterface(t *testing.T) {
	provider := parseProductionPackage(t, "internal/dispatch")
	for _, file := range provider.files {
		for _, declaration := range file.Decls {
			generic, ok := declaration.(*ast.GenDecl)
			if !ok || generic.Tok != token.TYPE {
				continue
			}
			for _, rawSpec := range generic.Specs {
				spec := rawSpec.(*ast.TypeSpec)
				if spec.Name.Name == "Service" {
					t.Fatalf("dispatch.Service still exists")
				}
				if _, ok := spec.Type.(*ast.InterfaceType); ok {
					t.Fatalf("provider-owned replacement interface %s is not allowed", spec.Name.Name)
				}
			}
		}
	}
}

func TestConsumersDoNotNameProviderService(t *testing.T) {
	for _, directory := range []string{"internal/httpapi", "internal/worker"} {
		pkg := parseProductionPackage(t, directory)
		for _, file := range pkg.files {
			aliases := map[string]bool{}
			for _, imported := range file.Imports {
				if strings.Trim(imported.Path.Value, `"`) != "example.com/go-interface-slimming/internal/dispatch" {
					continue
				}
				name := "dispatch"
				if imported.Name != nil {
					name = imported.Name.Name
				}
				aliases[name] = true
			}
			ast.Inspect(file, func(node ast.Node) bool {
				selector, ok := node.(*ast.SelectorExpr)
				if !ok || selector.Sel.Name != "Service" {
					return true
				}
				identifier, ok := selector.X.(*ast.Ident)
				if ok && aliases[identifier.Name] {
					t.Errorf("%s still selects the provider-owned Service type", directory)
				}
				return true
			})
		}
	}
}

func exportedMethodNames(value any) []string {
	typ := reflect.TypeOf(value)
	names := make([]string, 0, typ.NumMethod())
	for i := 0; i < typ.NumMethod(); i++ {
		names = append(names, typ.Method(i).Name)
	}
	sort.Strings(names)
	return names
}

func compactSource(input []byte) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return r
	}, string(input))
}

func requireGeneratedFake(t *testing.T, path, assertion string, fake any, methods, forbidden []string) {
	t.Helper()
	source, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if !bytes.HasPrefix(source, []byte("// Code generated by fakegen. DO NOT EDIT.\n")) {
		t.Fatalf("%s lost its generated banner", path)
	}
	compact := compactSource(source)
	if !strings.Contains(compact, compactSource([]byte(assertion))) {
		t.Fatalf("%s does not assert the consumer-owned interface", path)
	}
	for _, name := range forbidden {
		if strings.Contains(sourceString(source), name) {
			t.Fatalf("%s retains unrelated generated member %s", path, name)
		}
	}
	got := exportedMethodNames(fake)
	sortedWant := append([]string(nil), methods...)
	sort.Strings(sortedWant)
	if !reflect.DeepEqual(got, sortedWant) {
		t.Fatalf("%T methods = %v, want exactly %v", fake, got, sortedWant)
	}
}

func sourceString(source []byte) string {
	return string(source)
}

func TestGeneratedFakesMatchTheirRoles(t *testing.T) {
	requireGeneratedFake(
		t,
		"internal/httpapi/fake_run_service.gen.go",
		"var _ RunService = (*FakeRunService)(nil)",
		&httpapi.FakeRunService{},
		[]string{"GetRun", "CancelRun"},
		[]string{"ListReady", "MarkDispatched", "RotateSigningKey", "PurgeTenant"},
	)
	requireGeneratedFake(
		t,
		"internal/worker/fake_dispatch_queue.gen.go",
		"var _ DispatchQueue = (*FakeDispatchQueue)(nil)",
		&worker.FakeDispatchQueue{},
		[]string{"ListReady", "MarkDispatched"},
		[]string{"GetRun", "CancelRun", "RotateSigningKey", "PurgeTenant"},
	)
}

func TestConcreteServicePublicMethodSetIsUnchanged(t *testing.T) {
	want := []string{
		"CancelRun",
		"GetRun",
		"ListReady",
		"MarkDispatched",
		"PurgeTenant",
		"RotateSigningKey",
	}
	if got := exportedMethodNames((*dispatch.MemoryService)(nil)); !reflect.DeepEqual(got, want) {
		t.Fatalf("*dispatch.MemoryService methods = %v, want %v", got, want)
	}
}

func TestConcreteServiceWiresDirectlyIntoBothConsumers(t *testing.T) {
	service := dispatch.NewMemoryService(nil)
	_ = httpapi.New(service)
	_ = worker.New(service, 2)
}
