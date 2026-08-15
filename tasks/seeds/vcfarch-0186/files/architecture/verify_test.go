package architecture

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func loadInputs(t *testing.T) (Inventory, CompatibilitySnapshot) {
	t.Helper()
	var inv Inventory
	if err := DecodeJSONFile("testdata/estate.json", &inv); err != nil {
		t.Fatal(err)
	}
	var snap CompatibilitySnapshot
	if err := DecodeJSONFile("testdata/compatibility_snapshot.json", &snap); err != nil {
		t.Fatal(err)
	}
	return inv, snap
}

func loadArtifact(t *testing.T) Plan {
	t.Helper()
	var plan Plan
	if err := DecodeJSONFile("migration_plan.json", &plan); err != nil {
		t.Fatal(err)
	}
	return plan
}

func TestMigrationPlanArtifact(t *testing.T) {
	inv, snap := loadInputs(t)
	plan := loadArtifact(t)
	if err := ValidatePlan(plan, inv, snap); err != nil {
		t.Fatalf("migration plan is invalid: %v", err)
	}
	want, err := BuildPlan(inv, snap, plan.Research)
	if err != nil {
		t.Fatalf("BuildPlan: %v", err)
	}
	if !reflect.DeepEqual(plan, want) {
		t.Fatal("migration_plan.json does not match BuildPlan output")
	}
}

func TestHostCountContradictsFailuresToTolerate(t *testing.T) {
	inv, snap := loadInputs(t)
	base := loadArtifact(t)
	tests := []struct {
		name      string
		hosts     int
		ftt       int
		wantError string
	}{
		{name: "ftt two needs five", hosts: 4, ftt: 2, wantError: "requires at least 5 hosts"},
		{name: "ftt one needs three", hosts: 2, ftt: 1, wantError: "requires at least 3 hosts"},
		{name: "inventory topology valid", hosts: 6, ftt: 2},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			plan := base
			plan.Placement.HostCount = tc.hosts
			plan.Placement.FailuresToTolerate = tc.ftt
			err := ValidatePlan(plan, inv, snap)
			if tc.wantError == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tc.wantError) {
				t.Fatalf("got %v, want error containing %q", err, tc.wantError)
			}
		})
	}
}

func TestVerifierRejectsCompatibilityViolations(t *testing.T) {
	inv, snap := loadInputs(t)
	base := loadArtifact(t)
	tests := []struct {
		name   string
		mutate func(*Plan)
	}{
		{
			name:   "management domain placement",
			mutate: func(p *Plan) { p.Placement.DomainID = "management-domain" },
		},
		{
			name: "operations in place upgrade",
			mutate: func(p *Plan) {
				migrationStep(p, "aria-operations-prod").Migration.Method = "in-place-upgrade"
			},
		},
		{
			name: "logs overstates transferable history",
			mutate: func(p *Plan) {
				step := migrationStep(p, "aria-logs-prod")
				for i := range step.Migration.Content {
					if step.Migration.Content[i].Category == "historical_logs_days" {
						step.Migration.Content[i].CarryQuantity = 365
						step.Migration.Content[i].AbandonQuantity = 0
					}
				}
			},
		},
		{
			name: "undersized automation",
			mutate: func(p *Plan) {
				for i := range p.Placement.Components {
					if p.Placement.Components[i].Component == "VCF Automation" {
						p.Placement.Components[i].MemoryGiBPerNode--
					}
				}
			},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			plan := clonePlan(t, base)
			tc.mutate(&plan)
			if err := ValidatePlan(plan, inv, snap); err == nil {
				t.Fatal("ValidatePlan accepted an incompatible architecture")
			}
		})
	}
}

func TestVerifierRejectsInvalidResearchMetadata(t *testing.T) {
	inv, snap := loadInputs(t)
	base := loadArtifact(t)
	tests := []struct {
		name   string
		mutate func(*Plan)
	}{
		{
			name:   "research is required",
			mutate: func(p *Plan) { p.Research = nil },
		},
		{
			name: "Broadcom source is required",
			mutate: func(p *Plan) {
				p.Research[0].URL = "https://example.com/migration"
			},
		},
		{
			name: "access date must be an ISO date",
			mutate: func(p *Plan) {
				p.Research[0].AccessedAt = "recently"
			},
		},
		{
			name: "claims cannot be blank",
			mutate: func(p *Plan) {
				p.Research[0].Claims = []string{" "}
			},
		},
		{
			name: "every exact source version needs lifecycle research",
			mutate: func(p *Plan) {
				for i := range p.Research {
					for j := range p.Research[i].Claims {
						p.Research[i].Claims[j] = strings.ReplaceAll(p.Research[i].Claims[j], "8.18.3", "8.18.x")
					}
					p.Research[i].Title = strings.ReplaceAll(p.Research[i].Title, "8.18.3", "8.18.x")
				}
			},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			plan := clonePlan(t, base)
			tc.mutate(&plan)
			if err := ValidatePlan(plan, inv, snap); err == nil {
				t.Fatal("ValidatePlan accepted invalid research metadata")
			}
		})
	}
}

func TestSubmissionAddsTableDrivenGoTests(t *testing.T) {
	paths, err := filepath.Glob("*_test.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range paths {
		if path == "verify_test.go" {
			continue
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		hasCaseTable, hasRange, exercisesEntryPoint := false, false, false
		ast.Inspect(file, func(node ast.Node) bool {
			switch typed := node.(type) {
			case *ast.CompositeLit:
				array, ok := typed.Type.(*ast.ArrayType)
				if ok {
					if _, ok := array.Elt.(*ast.StructType); ok {
						hasCaseTable = true
					}
				}
			case *ast.RangeStmt:
				hasRange = true
			case *ast.CallExpr:
				if ident, ok := typed.Fun.(*ast.Ident); ok && (ident.Name == "BuildPlan" || ident.Name == "ValidatePlan") {
					exercisesEntryPoint = true
				}
			}
			return true
		})
		if hasCaseTable && hasRange && exercisesEntryPoint {
			return
		}
	}
	t.Fatal("add a table-driven Go test for BuildPlan or ValidatePlan in a new _test.go file")
}

func TestSchemaTypesRemainUnchanged(t *testing.T) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "schema.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var declarations bytes.Buffer
	for _, declaration := range file.Decls {
		general, ok := declaration.(*ast.GenDecl)
		if !ok || general.Tok != token.TYPE {
			continue
		}
		if err := format.Node(&declarations, fset, general); err != nil {
			t.Fatal(err)
		}
		declarations.WriteByte('\n')
	}
	got := sha256.Sum256(declarations.Bytes())
	want := "56e4208aab40968e77b9440f1357179cd6f39d37d77c6fafc155ca25c72332fb"
	if gotHex := fmt.Sprintf("%x", got); gotHex != want {
		t.Fatalf("schema types or JSON tags changed: got contract hash %s", gotHex)
	}
}

func migrationStep(plan *Plan, sourceID string) *Step {
	for i := range plan.Steps {
		if plan.Steps[i].Kind == "migrate-product" && plan.Steps[i].SourceID == sourceID {
			return &plan.Steps[i]
		}
	}
	panic("migration step not found: " + sourceID)
}

func clonePlan(t *testing.T, in Plan) Plan {
	t.Helper()
	path := t.TempDir() + "/plan.json"
	if err := WritePlan(path, in); err != nil {
		t.Fatal(err)
	}
	var out Plan
	if err := DecodeJSONFile(path, &out); err != nil {
		t.Fatal(err)
	}
	return out
}
