package verification

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"

	"vcfarch"
	"vcfarch/internal/jsonschema"
)

const (
	installerSpecSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
	estateSHA256        = "04e2923893cc43acbf8c4eaebcc973de051c3d093143f015a642fa9e5ec61ed9"
	snapshotSHA256      = "33d717848e9a653c04d362e375b7af24cff521666c5f5d81284ed950101153eb"
	planSchemaSHA256    = "9848f958326e0ff94ef78602f520408614b36c367cacbd0a5f4ab67832fad468"
)

type rawEnvelope struct {
	TargetSddcSpec json.RawMessage `json:"targetSddcSpec"`
}

type estate struct {
	EstateID          string `json:"estateId"`
	FoundationVersion string `json:"foundationVersion"`
	Components        []struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"components"`
}

type snapshot struct {
	TargetVersion           string                  `json:"targetVersion"`
	InstallerSpec           installerSpec           `json:"installerSpec"`
	SupportedFoundationHops []vcfarch.FoundationHop `json:"supportedFoundationHops"`
	ExpectedSteps           []vcfarch.MigrationStep `json:"expectedSteps"`
	RequiredPlacements      []vcfarch.Placement     `json:"requiredPlacements"`
	TargetSpecExpectations  targetSpecExpectations  `json:"targetSpecExpectations"`
}

type installerSpec struct {
	Tag    string `json:"tag"`
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type targetSpecExpectations struct {
	SddcID                    string   `json:"sddcId"`
	WorkflowType              string   `json:"workflowType"`
	Version                   string   `json:"version"`
	VcenterHostname           string   `json:"vcenterHostname"`
	OperationsSize            string   `json:"operationsSize"`
	OperationsNodes           []string `json:"operationsNodes"`
	OperationsLoadBalancer    string   `json:"operationsLoadBalancer"`
	AutomationHostname        string   `json:"automationHostname"`
	AutomationSize            string   `json:"automationSize"`
	AutomationIPPool          []string `json:"automationIpPool"`
	ManagementServicesNetwork string   `json:"managementServicesNetwork"`
	LicenseServerHostname     string   `json:"licenseServerHostname"`
}

type targetSpecProjection struct {
	SddcID       string `json:"sddcId"`
	WorkflowType string `json:"workflowType"`
	Version      string `json:"version"`
	VcenterSpec  struct {
		Hostname              string `json:"vcenterHostname"`
		RootPassword          string `json:"rootVcenterPassword"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"vcenterSpec"`
	Operations struct {
		Nodes []struct {
			Hostname string `json:"hostname"`
		} `json:"nodes"`
		Size                  string `json:"applianceSize"`
		LoadBalancerFQDN      string `json:"loadBalancerFqdn"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"vcfOperationsSpec"`
	Automation struct {
		Hostname              string   `json:"hostname"`
		Size                  string   `json:"size"`
		IPPool                []string `json:"ipPool"`
		UseExistingDeployment bool     `json:"useExistingDeployment"`
	} `json:"vcfAutomationSpec"`
	ManagementInfrastructure struct {
		LocalRegionNetwork struct {
			NetworkName string `json:"networkName"`
		} `json:"localRegionNetwork"`
	} `json:"vcfManagementComponentsInfrastructureSpec"`
	LicenseServer struct {
		Hostname string `json:"hostname"`
	} `json:"licenseServerSpec"`
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate verifier source")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func requireSHA256(t *testing.T, data []byte, want, label string) {
	t.Helper()
	digest := sha256.Sum256(data)
	if got := hex.EncodeToString(digest[:]); got != want {
		t.Fatalf("%s digest mismatch: got %s", label, got)
	}
}

func decodeStrict[T any](t *testing.T, data []byte, label string) T {
	t.Helper()
	var value T
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", label, err)
	}
	if decoder.More() {
		t.Fatalf("decode %s: trailing JSON value", label)
	}
	return value
}

func decodeJSON[T any](t *testing.T, data []byte, label string) T {
	t.Helper()
	var value T
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatalf("decode %s: %v", label, err)
	}
	return value
}

func validateSchema(t *testing.T, schemaBytes []byte, schemaURI string, fragment string, instanceBytes []byte) {
	t.Helper()
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource(schemaURI, bytes.NewReader(schemaBytes)); err != nil {
		t.Fatalf("load schema %s: %v", schemaURI, err)
	}
	compiled, err := compiler.Compile(schemaURI + fragment)
	if err != nil {
		t.Fatalf("compile schema %s%s: %v", schemaURI, fragment, err)
	}
	var instance any
	if err := json.Unmarshal(instanceBytes, &instance); err != nil {
		t.Fatalf("decode schema instance: %v", err)
	}
	if err := compiled.Validate(instance); err != nil {
		t.Fatalf("schema validation failed: %v", err)
	}
}

// TestArchitectureContract intentionally performs the upstream SddcSpec schema
// validation before loading the migration schema, fixture, or compatibility
// snapshot. The separately-authored research record is checked below without
// making verification depend on live network access.
func TestArchitectureContract(t *testing.T) {
	root := repositoryRoot(t)
	planPath := filepath.Join(root, "architecture", "plan.json")
	planBytes := readFile(t, planPath)

	var envelope rawEnvelope
	if err := json.Unmarshal(planBytes, &envelope); err != nil {
		t.Fatalf("decode plan envelope: %v", err)
	}
	if len(envelope.TargetSddcSpec) == 0 {
		t.Fatal("plan has no targetSddcSpec")
	}

	// This is the first artifact validation. It uses the actual SddcSpec in the
	// pinned upstream OpenAPI document, not a rewritten approximation.
	installerBytes := readFile(t, filepath.Join(root, "specifications", "vcf-installer", "vcf-installer-openapi.json"))
	validateSchema(t, installerBytes, "file:///vcf-installer-openapi.json", "#/components/schemas/SddcSpec", envelope.TargetSddcSpec)

	planSchema := readFile(t, filepath.Join(root, "fixtures", "migration-plan.schema.json"))
	validateSchema(t, planSchema, "file:///migration-plan.schema.json", "", planBytes)
	requireSHA256(t, planSchema, planSchemaSHA256, "migration plan schema")

	estateBytes := readFile(t, filepath.Join(root, "fixtures", "estate.json"))
	snapshotBytes := readFile(t, filepath.Join(root, "fixtures", "compatibility-snapshot.json"))
	requireSHA256(t, estateBytes, estateSHA256, "estate fixture")
	requireSHA256(t, snapshotBytes, snapshotSHA256, "compatibility snapshot")
	inv := decodeJSON[estate](t, estateBytes, "estate")
	snap := decodeJSON[snapshot](t, snapshotBytes, "compatibility snapshot")
	plan := decodeStrict[vcfarch.Architecture](t, planBytes, "plan")

	digest := sha256.Sum256(installerBytes)
	actualDigest := hex.EncodeToString(digest[:])
	if actualDigest != installerSpecSHA256 || actualDigest != snap.InstallerSpec.SHA256 {
		t.Fatalf("installer schema digest mismatch: got %s", actualDigest)
	}
	if snap.InstallerSpec.Tag != "9.1.0.0" || snap.InstallerSpec.Path != "specifications/vcf-installer/vcf-installer-openapi.json" {
		t.Fatal("snapshot does not identify the pinned 9.1.0.0 installer schema")
	}

	checks := []struct {
		name string
		run  func(t *testing.T)
	}{
		{"document identity", func(t *testing.T) {
			if plan.SchemaVersion != "migration-plan.v1" || plan.EstateID != inv.EstateID || plan.TargetVersion != snap.TargetVersion {
				t.Fatalf("wrong document identity: schema=%q estate=%q target=%q", plan.SchemaVersion, plan.EstateID, plan.TargetVersion)
			}
		}},
		{"supported foundation route", func(t *testing.T) {
			if len(plan.FoundationHops) == 0 || plan.FoundationHops[0].From != inv.FoundationVersion {
				t.Fatalf("foundation route does not start at inventory version %q", inv.FoundationVersion)
			}
			if !reflect.DeepEqual(plan.FoundationHops, snap.SupportedFoundationHops) {
				t.Fatalf("unsupported or incomplete foundation hops\ngot:  %#v\nwant: %#v", plan.FoundationHops, snap.SupportedFoundationHops)
			}
		}},
		{"component coverage and routes", func(t *testing.T) {
			verifySteps(t, inv, plan.Steps, snap.ExpectedSteps)
		}},
		{"service placement and sizing", func(t *testing.T) {
			if !reflect.DeepEqual(plan.Placements, snap.RequiredPlacements) {
				t.Fatalf("placement/sizing differs from pinned demand result\ngot:  %#v\nwant: %#v", plan.Placements, snap.RequiredPlacements)
			}
		}},
		{"target spec projection", func(t *testing.T) {
			verifyTargetSpec(t, envelope.TargetSddcSpec, snap.TargetSpecExpectations)
		}},
	}
	for _, check := range checks {
		check := check
		t.Run(check.name, check.run)
	}

	loaded, err := vcfarch.Load(planPath)
	if err != nil {
		t.Fatalf("vcfarch.Load(valid plan): %v", err)
	}
	if err := loaded.ValidateBasic(); err != nil {
		t.Fatalf("ValidateBasic(valid plan): %v", err)
	}
	if !reflect.DeepEqual(*loaded, plan) {
		t.Fatal("package Load result differs from the artifact")
	}
}

func TestResearchRecord(t *testing.T) {
	root := repositoryRoot(t)
	record := string(readFile(t, filepath.Join(root, "architecture", "research.md")))
	if strings.TrimSpace(record) == "" {
		t.Fatal("research record is empty")
	}
	if !regexp.MustCompile(`\b20[0-9]{2}\b`).MatchString(record) {
		t.Fatal("research record has no recognizable access year")
	}

	urlPattern := regexp.MustCompile(`https://[^\s<>()]+`)
	seenBroadcom := make(map[string]bool)
	for _, line := range strings.Split(record, "\n") {
		for _, location := range urlPattern.FindAllStringIndex(line, -1) {
			rawURL := strings.TrimRight(line[location[0]:location[1]], ".,;:")
			parsed, err := url.Parse(rawURL)
			if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
				continue
			}
			host := strings.ToLower(parsed.Hostname())
			if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
				continue
			}
			seenBroadcom[rawURL] = true
		}
	}
	if len(seenBroadcom) < 2 {
		t.Fatalf("research record has %d distinct Broadcom HTTPS sources, want at least 2", len(seenBroadcom))
	}

	lower := strings.ToLower(record)
	for _, topic := range []string{"compatib", "interop", "siz", "upgrad"} {
		if !strings.Contains(lower, topic) {
			t.Errorf("research record does not address %q", topic)
		}
	}
}

func verifySteps(t *testing.T, inv estate, got, want []vcfarch.MigrationStep) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("migration steps differ from pinned compatibility snapshot")
	}
	inventory := make(map[string]struct {
		name, version string
	}, len(inv.Components))
	for _, component := range inv.Components {
		inventory[component.ID] = struct{ name, version string }{component.Name, component.Version}
	}
	seen := make(map[string]bool, len(got))
	lastOrder := 0
	for _, step := range got {
		component, ok := inventory[step.ComponentID]
		if !ok {
			t.Errorf("step names non-inventory component %q", step.ComponentID)
			continue
		}
		if seen[step.ComponentID] {
			t.Errorf("component %q appears more than once", step.ComponentID)
		}
		seen[step.ComponentID] = true
		if step.ComponentName != component.name || step.SourceVersion != component.version {
			t.Errorf("step %q does not preserve inventory name/version", step.ComponentID)
		}
		if step.Order <= lastOrder {
			t.Errorf("step %q is not strictly ordered", step.ComponentID)
		}
		lastOrder = step.Order
		if len(step.Gates) == 0 {
			t.Errorf("step %q has no technical gates", step.ComponentID)
		}
		if len(step.Route) < 2 || step.Route[len(step.Route)-1].Version != step.TargetVersion && step.Route[len(step.Route)-1].Version != "retired" {
			t.Errorf("step %q has incomplete route", step.ComponentID)
		}
	}
	for id := range inventory {
		if !seen[id] {
			t.Errorf("inventory component %q is missing from the plan", id)
		}
	}
}

func verifyTargetSpec(t *testing.T, raw json.RawMessage, want targetSpecExpectations) {
	t.Helper()
	var got targetSpecProjection
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("decode target SddcSpec projection: %v", err)
	}
	nodes := make([]string, 0, len(got.Operations.Nodes))
	for _, node := range got.Operations.Nodes {
		nodes = append(nodes, node.Hostname)
	}
	checks := []struct {
		name string
		got  any
		want any
	}{
		{"sddcId", got.SddcID, want.SddcID},
		{"workflowType", got.WorkflowType, want.WorkflowType},
		{"version", got.Version, want.Version},
		{"vCenter hostname", got.VcenterSpec.Hostname, want.VcenterHostname},
		{"Operations size", got.Operations.Size, want.OperationsSize},
		{"Operations nodes", nodes, want.OperationsNodes},
		{"Operations load balancer", got.Operations.LoadBalancerFQDN, want.OperationsLoadBalancer},
		{"Automation hostname", got.Automation.Hostname, want.AutomationHostname},
		{"Automation size", got.Automation.Size, want.AutomationSize},
		{"Automation IP pool", got.Automation.IPPool, want.AutomationIPPool},
		{"management services network", got.ManagementInfrastructure.LocalRegionNetwork.NetworkName, want.ManagementServicesNetwork},
		{"license server hostname", got.LicenseServer.Hostname, want.LicenseServerHostname},
	}
	for _, check := range checks {
		if !reflect.DeepEqual(check.got, check.want) {
			t.Errorf("%s mismatch: got %#v want %#v", check.name, check.got, check.want)
		}
	}
	if !got.VcenterSpec.UseExistingDeployment || !got.Operations.UseExistingDeployment || !got.Automation.UseExistingDeployment {
		t.Error("brownfield target spec must mark vCenter, Operations, and Automation as existing deployments")
	}
}

func TestPackageLoadErrors(t *testing.T) {
	root := repositoryRoot(t)
	cases := []struct {
		name string
		path string
	}{
		{"missing file", filepath.Join(root, "architecture", "does-not-exist.json")},
		{"directory", filepath.Join(root, "fixtures")},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if _, err := vcfarch.Load(tc.path); err == nil {
				t.Errorf("Load(%s) unexpectedly succeeded", tc.path)
			}
		})
	}
}

func TestValidateBasicTable(t *testing.T) {
	valid := func() vcfarch.Architecture {
		return vcfarch.Architecture{
			SchemaVersion:  "migration-plan.v1",
			EstateID:       "estate",
			TargetVersion:  "9.1.0.0",
			FoundationHops: []vcfarch.FoundationHop{{From: "5.2.2.0", To: "9.1.0.0", Gates: []string{"ready"}}},
			TargetSddcSpec: map[string]any{"sddcId": "estate"},
			Placements:     []vcfarch.Placement{{ComponentID: "ops", NodeCount: 1, Size: "small", IPAddresses: []string{"10.0.0.1"}}},
			Steps:          []vcfarch.MigrationStep{{Order: 1, ComponentID: "ops", Gates: []string{"ready"}, Route: []vcfarch.RouteHop{{Version: "8", Operation: "current"}, {Version: "9", Operation: "upgrade"}}}},
		}
	}
	cases := []struct {
		name   string
		mutate func(*vcfarch.Architecture)
	}{
		{"empty schema version", func(a *vcfarch.Architecture) { a.SchemaVersion = "" }},
		{"unordered steps", func(a *vcfarch.Architecture) {
			a.Steps = append(a.Steps, vcfarch.MigrationStep{Order: 1, ComponentID: "next", Gates: []string{"ready"}, Route: []vcfarch.RouteHop{{Version: "8"}, {Version: "9"}}})
		}},
		{"duplicate component", func(a *vcfarch.Architecture) {
			a.Steps = append(a.Steps, vcfarch.MigrationStep{Order: 2, ComponentID: "ops", Gates: []string{"ready"}, Route: []vcfarch.RouteHop{{Version: "8"}, {Version: "9"}}})
		}},
		{"missing gates", func(a *vcfarch.Architecture) { a.Steps[0].Gates = nil }},
		{"short route", func(a *vcfarch.Architecture) { a.Steps[0].Route = a.Steps[0].Route[:1] }},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			a := valid()
			tc.mutate(&a)
			if err := a.ValidateBasic(); err == nil {
				t.Error("ValidateBasic unexpectedly accepted invalid plan")
			}
		})
	}
	control := valid()
	if err := control.ValidateBasic(); err != nil {
		t.Fatalf("ValidateBasic rejected valid control: %v", err)
	}
}

func TestExpectedSnapshotIsInternallyOrdered(t *testing.T) {
	root := repositoryRoot(t)
	snap := decodeJSON[snapshot](t, readFile(t, filepath.Join(root, "fixtures", "compatibility-snapshot.json")), "compatibility snapshot")
	orders := make([]int, 0, len(snap.ExpectedSteps))
	for _, step := range snap.ExpectedSteps {
		orders = append(orders, step.Order)
	}
	if !sort.IntsAreSorted(orders) {
		t.Fatalf("protected snapshot has unordered steps: %v", orders)
	}
	for i := 1; i < len(orders); i++ {
		if orders[i] == orders[i-1] {
			t.Fatalf("protected snapshot has duplicate order %d", orders[i])
		}
	}
}

func ExampleArchitecture() {
	fmt.Println("migration-plan.v1")
	// Output: migration-plan.v1
}
