package grader

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"vcfarchitecture/architecture"
)

type estateFixture struct {
	EstateID string `json:"estateId"`
	Fleet    struct {
		ManagementDomain struct {
			ID            string `json:"id"`
			Site          string `json:"site"`
			VCFVersion    string `json:"vcfVersion"`
			ChangeAllowed bool   `json:"changeAllowed"`
		} `json:"managementDomain"`
	} `json:"fleet"`
	Workload struct {
		Site                     string                 `json:"site"`
		DataResidencySite        string                 `json:"dataResidencySite"`
		Stretched                bool                   `json:"stretched"`
		LatencyToManagementMS    float64                `json:"latencyToManagementMs"`
		MaxLatencyToManagementMS float64                `json:"maxLatencyToManagementMs"`
		HostCount                int                    `json:"hostCount"`
		RackCount                int                    `json:"rackCount"`
		HostsPerRack             int                    `json:"hostsPerRack"`
		MaxRackFailures          int                    `json:"maxRackFailures"`
		ReservedHostFailures     int                    `json:"reservedHostFailures"`
		RequiredUsableStorageTiB float64                `json:"requiredUsableStorageTiB"`
		UsableStorageRatio       float64                `json:"usableStorageRatio"`
		VsanFailuresToTolerate   int                    `json:"vsanFailuresToTolerate"`
		PerHost                  architecture.Resources `json:"perHost"`
		RequiredAfterFailures    architecture.Resources `json:"requiredAfterFailures"`
	} `json:"workload"`
	Naming struct {
		SddcID           string `json:"sddcId"`
		DNSSubdomain     string `json:"dnsSubdomain"`
		VcenterHostname  string `json:"vcenterHostname"`
		DatacenterName   string `json:"datacenterName"`
		ClusterName      string `json:"clusterName"`
		DVSName          string `json:"dvsName"`
		NSXVIPFQDN       string `json:"nsxVipFqdn"`
		HostPrefix       string `json:"hostPrefix"`
		NSXManagerPrefix string `json:"nsxManagerPrefix"`
	} `json:"naming"`
	Infrastructure struct {
		DNSServers []string `json:"dnsServers"`
		NTPServers []string `json:"ntpServers"`
		Networks   []struct {
			Type    string `json:"type"`
			VLANID  int    `json:"vlanId"`
			Subnet  string `json:"subnet"`
			Gateway string `json:"gateway"`
			MTU     int    `json:"mtu"`
			StartIP string `json:"startIp"`
			EndIP   string `json:"endIp"`
		} `json:"networks"`
	} `json:"infrastructure"`
	Components []struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"components"`
}

type compatibilitySnapshot struct {
	VCFRelease           string `json:"vcfRelease"`
	SupportedTransitions []struct {
		ComponentID   string   `json:"componentId"`
		SourceName    string   `json:"sourceName"`
		SourceVersion string   `json:"sourceVersion"`
		TargetName    string   `json:"targetName"`
		TargetVersion string   `json:"targetVersion"`
		Action        string   `json:"action"`
		RequiredGates []string `json:"requiredGates"`
		MustFollow    []string `json:"mustFollow"`
	} `json:"supportedTransitions"`
}

// TestArchitecture intentionally performs the installer-schema validation
// before migration, capacity, fixture, compatibility, or research assertions.
func TestArchitecture(t *testing.T) {
	artifactBytes := mustRead(t, "../architecture/design.json")
	var artifact map[string]any
	mustJSON(t, artifactBytes, &artifact)

	// FIRST CHECK: validate sddcSpec against the SddcSpec schema loaded from
	// the pinned installer OpenAPI document itself.
	openAPIBytes := mustRead(t, "../specifications/vcf-installer/vcf-installer-openapi.json")
	var openAPI map[string]any
	mustJSON(t, openAPIBytes, &openAPI)
	sddcSpec, present := artifact["sddcSpec"]
	if !present {
		t.Fatal("artifact has no sddcSpec")
	}
	sddcSchema, err := schemaValidator{document: openAPI}.resolve("#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("resolve installer SddcSpec: %v", err)
	}
	if err := (schemaValidator{document: openAPI}).validate(sddcSchema, sddcSpec, "$.sddcSpec"); err != nil {
		t.Fatalf("installer SddcSpec schema validation failed: %v", err)
	}

	// All remaining verification is deliberately after installer validation.
	migrationSchemaBytes := mustRead(t, "../schemas/migration-plan.schema.json")
	var migrationSchema map[string]any
	mustJSON(t, migrationSchemaBytes, &migrationSchema)
	migration, present := artifact["migrationPlan"]
	if !present {
		t.Fatal("artifact has no migrationPlan")
	}
	if err := (schemaValidator{document: migrationSchema}).validate(migrationSchema, migration, "$.migrationPlan"); err != nil {
		t.Fatalf("migration-plan schema validation failed: %v", err)
	}

	var fixture estateFixture
	mustJSON(t, mustRead(t, "../testdata/estate.json"), &fixture)
	var snapshot compatibilitySnapshot
	mustJSON(t, mustRead(t, "../testdata/compatibility-9.1.0.json"), &snapshot)
	design, err := architecture.Build()
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	builtBytes, err := json.Marshal(design)
	if err != nil {
		t.Fatalf("marshal Build result: %v", err)
	}
	if !jsonEqual(artifactBytes, builtBytes) {
		t.Fatal("Build result differs from architecture/design.json")
	}

	t.Run("greenfield-installer-shape", func(t *testing.T) {
		checkSddcShape(t, sddcSpec.(map[string]any), fixture)
	})
	t.Run("capacity-and-site", func(t *testing.T) {
		checkCapacityAndSite(t, design, fixture)
	})
	t.Run("migration-from-pinned-authorities", func(t *testing.T) {
		checkMigration(t, design.Migration, fixture, snapshot)
	})
	t.Run("live-research-record", func(t *testing.T) {
		checkResearch(t, design.Research)
	})
}

func checkSddcShape(t *testing.T, spec map[string]any, fixture estateFixture) {
	t.Helper()
	wantScalar := map[string]any{
		"sddcId":       fixture.Naming.SddcID,
		"workflowType": "VCF_EXTEND",
		"version":      "9.1.0.0",
	}
	for field, want := range wantScalar {
		if got := spec[field]; got != want {
			t.Errorf("sddcSpec.%s = %v, want %v", field, got, want)
		}
	}
	hosts := objectArray(t, spec["hostSpecs"], "hostSpecs")
	if len(hosts) != fixture.Workload.HostCount {
		t.Fatalf("hostSpecs count = %d, want %d", len(hosts), fixture.Workload.HostCount)
	}
	seenHosts := map[string]bool{}
	for _, host := range hosts {
		hostname, _ := host["hostname"].(string)
		if !strings.HasPrefix(hostname, fixture.Naming.HostPrefix) || seenHosts[hostname] {
			t.Errorf("invalid or duplicate host hostname %q", hostname)
		}
		seenHosts[hostname] = true
	}
	vcenter := object(t, spec["vcenterSpec"], "vcenterSpec")
	if vcenter["vcenterHostname"] != fixture.Naming.VcenterHostname || vcenter["useExistingDeployment"] != false {
		t.Errorf("vcenterSpec must describe new %s", fixture.Naming.VcenterHostname)
	}
	if password, _ := vcenter["rootVcenterPassword"].(string); !strings.Contains(strings.ToUpper(password), "REPLACE") {
		t.Error("rootVcenterPassword must be a conspicuous replacement string")
	}
	cluster := object(t, spec["clusterSpec"], "clusterSpec")
	if cluster["datacenterName"] != fixture.Naming.DatacenterName || cluster["clusterName"] != fixture.Naming.ClusterName {
		t.Error("clusterSpec names do not match fixture")
	}
	nsx := object(t, spec["nsxtSpec"], "nsxtSpec")
	if nsx["vipFqdn"] != fixture.Naming.NSXVIPFQDN || nsx["useExistingDeployment"] != false {
		t.Error("nsxtSpec must describe the fixture's new NSX cluster")
	}
	managers := objectArray(t, nsx["nsxtManagers"], "nsxtManagers")
	if len(managers) != 3 {
		t.Fatalf("nsxtManagers count = %d, want 3", len(managers))
	}
	seenManagers := map[string]bool{}
	for _, manager := range managers {
		hostname, _ := manager["hostname"].(string)
		if !strings.HasPrefix(hostname, fixture.Naming.NSXManagerPrefix) || seenManagers[hostname] {
			t.Errorf("invalid or duplicate NSX manager hostname %q", hostname)
		}
		seenManagers[hostname] = true
	}
	datastore := object(t, spec["datastoreSpec"], "datastoreSpec")
	vsan := object(t, datastore["vsanSpec"], "vsanSpec")
	if got := intValue(t, vsan["failuresToTolerate"], "failuresToTolerate"); got != fixture.Workload.VsanFailuresToTolerate {
		t.Errorf("failuresToTolerate = %d, want %d", got, fixture.Workload.VsanFailuresToTolerate)
	}

	networks := objectArray(t, spec["networkSpecs"], "networkSpecs")
	if len(networks) != len(fixture.Infrastructure.Networks) {
		t.Fatalf("networkSpecs count = %d, want %d", len(networks), len(fixture.Infrastructure.Networks))
	}
	byType := map[string]map[string]any{}
	for _, network := range networks {
		name, _ := network["networkType"].(string)
		if _, duplicate := byType[name]; duplicate {
			t.Fatalf("duplicate network type %q", name)
		}
		byType[name] = network
	}
	for _, want := range fixture.Infrastructure.Networks {
		got, ok := byType[want.Type]
		if !ok {
			t.Errorf("missing network %s", want.Type)
			continue
		}
		if intValue(t, got["vlanId"], want.Type+" vlan") != want.VLANID ||
			got["subnet"] != want.Subnet || got["gateway"] != want.Gateway ||
			intValue(t, got["mtu"], want.Type+" mtu") != want.MTU {
			t.Errorf("network %s does not match fixture", want.Type)
		}
		ranges := objectArray(t, got["includeIpAddressRanges"], want.Type+" ranges")
		if len(ranges) != 1 || ranges[0]["startIpAddress"] != want.StartIP || ranges[0]["endIpAddress"] != want.EndIP {
			t.Errorf("network %s IP range does not match fixture", want.Type)
		}
	}
	dns := object(t, spec["dnsSpec"], "dnsSpec")
	if dns["subdomain"] != fixture.Naming.DNSSubdomain || !reflect.DeepEqual(stringSlice(dns["nameservers"]), fixture.Infrastructure.DNSServers) {
		t.Error("dnsSpec does not match fixture")
	}
	if !reflect.DeepEqual(stringSlice(spec["ntpServers"]), fixture.Infrastructure.NTPServers) {
		t.Error("ntpServers do not match fixture")
	}

	dvsSpecs := objectArray(t, spec["dvsSpecs"], "dvsSpecs")
	if len(dvsSpecs) != 1 {
		t.Fatalf("dvsSpecs count = %d, want 1", len(dvsSpecs))
	}
	if dvsSpecs[0]["dvsName"] != fixture.Naming.DVSName {
		t.Errorf("dvsName = %v, want %s", dvsSpecs[0]["dvsName"], fixture.Naming.DVSName)
	}
	wantDVSNetworks := make([]string, 0, len(fixture.Infrastructure.Networks))
	for _, network := range fixture.Infrastructure.Networks {
		wantDVSNetworks = append(wantDVSNetworks, network.Type)
	}
	if !sameStrings(stringSlice(dvsSpecs[0]["networks"]), wantDVSNetworks) {
		t.Errorf("DVS networks = %v, want the four separate fixture networks", dvsSpecs[0]["networks"])
	}
}

func checkCapacityAndSite(t *testing.T, design architecture.Design, fixture estateFixture) {
	t.Helper()
	capacity := design.Capacity
	workload := fixture.Workload
	if capacity.HostCount != workload.HostCount || capacity.PerHost != workload.PerHost ||
		capacity.ReservedHostFailures != workload.ReservedHostFailures || capacity.UsableStorageRatio != workload.UsableStorageRatio {
		t.Error("capacity inputs do not match fixture")
	}
	wantAfter := architecture.Resources{
		PhysicalCores: (workload.HostCount - workload.ReservedHostFailures) * workload.PerHost.PhysicalCores,
		MemoryGiB:     (workload.HostCount - workload.ReservedHostFailures) * workload.PerHost.MemoryGiB,
	}
	if capacity.ProvidedAfterHostFailures.PhysicalCores != wantAfter.PhysicalCores ||
		capacity.ProvidedAfterHostFailures.MemoryGiB != wantAfter.MemoryGiB {
		t.Errorf("post-failure resources = %+v, want %+v", capacity.ProvidedAfterHostFailures, wantAfter)
	}
	if wantAfter.PhysicalCores < workload.RequiredAfterFailures.PhysicalCores || wantAfter.MemoryGiB < workload.RequiredAfterFailures.MemoryGiB {
		t.Fatal("fixture host design cannot meet post-failure requirement")
	}
	wantUsable := float64(workload.HostCount*workload.PerHost.RawStorageTiB) * workload.UsableStorageRatio
	if capacity.ProvidedUsableStorageTiB != wantUsable || wantUsable < workload.RequiredUsableStorageTiB {
		t.Errorf("usable storage = %v, want %v and >= %v", capacity.ProvidedUsableStorageTiB, wantUsable, workload.RequiredUsableStorageTiB)
	}
	if capacity.Required.PhysicalCores != workload.RequiredAfterFailures.PhysicalCores ||
		capacity.Required.MemoryGiB != workload.RequiredAfterFailures.MemoryGiB ||
		capacity.Required.UsableStorageTiB != workload.RequiredUsableStorageTiB {
		t.Error("declared required capacity does not match fixture")
	}
	site := design.Site
	if site.WorkloadSite != workload.Site || site.ManagementSite != fixture.Fleet.ManagementDomain.Site ||
		site.RackCount != workload.RackCount || site.HostsPerRack != workload.HostsPerRack ||
		site.MaxRackFailures != workload.MaxRackFailures || site.DataResidency != workload.DataResidencySite ||
		site.Stretched != workload.Stretched || site.LatencyToManagement != workload.LatencyToManagementMS ||
		site.MaxAllowedLatency != workload.MaxLatencyToManagementMS || site.LatencyToManagement > site.MaxAllowedLatency {
		t.Error("site plan does not satisfy fixture")
	}
	if site.RackCount*site.HostsPerRack != capacity.HostCount || site.HostsPerRack != capacity.ReservedHostFailures {
		t.Error("rack placement does not implement the two-host failure reserve")
	}
}

func checkMigration(t *testing.T, plan architecture.MigrationPlan, fixture estateFixture, snapshot compatibilitySnapshot) {
	t.Helper()
	if fixture.Fleet.ManagementDomain.ChangeAllowed || plan.ManagementDomainChange {
		t.Fatal("management domain no-change boundary was not preserved")
	}
	if plan.EstateID != fixture.EstateID || plan.TargetVCFVersion != snapshot.VCFRelease || len(plan.Steps) != len(fixture.Components) {
		t.Fatal("migration plan identity, target release, or component count is wrong")
	}
	inventory := map[string]struct{ name, version string }{}
	for _, component := range fixture.Components {
		inventory[component.ID] = struct{ name, version string }{component.Name, component.Version}
	}
	transitions := map[string]struct {
		sourceName, sourceVersion, targetName, targetVersion, action string
		gates, follows                                               []string
	}{}
	for _, transition := range snapshot.SupportedTransitions {
		transitions[transition.ComponentID] = struct {
			sourceName, sourceVersion, targetName, targetVersion, action string
			gates, follows                                               []string
		}{transition.SourceName, transition.SourceVersion, transition.TargetName, transition.TargetVersion, transition.Action, transition.RequiredGates, transition.MustFollow}
	}
	positions := map[string]int{}
	seen := map[string]bool{}
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			t.Errorf("step %d has order %d", index, step.Order)
		}
		if seen[step.ComponentID] {
			t.Fatalf("duplicate migration component %q", step.ComponentID)
		}
		seen[step.ComponentID] = true
		positions[step.ComponentID] = step.Order
		item, inInventory := inventory[step.ComponentID]
		transition, supported := transitions[step.ComponentID]
		if !inInventory || !supported {
			t.Errorf("component %q absent from inventory or snapshot", step.ComponentID)
			continue
		}
		if step.Component != item.name || step.CurrentVersion != item.version ||
			step.Component != transition.sourceName || step.CurrentVersion != transition.sourceVersion ||
			step.Target.Component != transition.targetName || step.Target.Version != transition.targetVersion ||
			step.Action != transition.action || !containsAll(step.Gates, transition.gates) {
			t.Errorf("step for %s does not match fixture and pinned transition", step.ComponentID)
		}
	}
	for id, transition := range transitions {
		position, exists := positions[id]
		if !exists {
			t.Errorf("missing migration step for %s", id)
			continue
		}
		for _, predecessor := range transition.follows {
			if positions[predecessor] == 0 || positions[predecessor] >= position {
				t.Errorf("%s must follow %s", id, predecessor)
			}
		}
	}
}

func checkResearch(t *testing.T, entries []architecture.ResearchEntry) {
	t.Helper()
	if len(entries) < 2 {
		t.Fatalf("research has %d source(s), want at least two Broadcom publications", len(entries))
	}
	seenURLs := map[string]bool{}
	for index, entry := range entries {
		if strings.TrimSpace(entry.Title) == "" || !strings.Contains(strings.ToLower(entry.Publisher), "broadcom") {
			t.Errorf("research[%d] must identify a titled Broadcom publication", index)
		}
		parsed, err := url.Parse(entry.URL)
		host := ""
		if err == nil {
			host = strings.ToLower(parsed.Hostname())
		}
		if parsed == nil || parsed.Scheme != "https" || (host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com")) {
			t.Errorf("research[%d].url %q is not an HTTPS Broadcom source", index, entry.URL)
		}
		if seenURLs[entry.URL] {
			t.Errorf("research[%d].url duplicates an earlier source", index)
		}
		seenURLs[entry.URL] = true
		if len(entry.Facts) == 0 {
			t.Errorf("research[%d] records no relevant facts", index)
		}
		for factIndex, fact := range entry.Facts {
			if strings.TrimSpace(fact) == "" {
				t.Errorf("research[%d].facts[%d] is empty", index, factIndex)
			}
		}
	}
}

func TestArchitecturePackageIncludesTableDrivenTests(t *testing.T) {
	paths, err := filepath.Glob("../architecture/*_test.go")
	if err != nil {
		t.Fatalf("find architecture tests: %v", err)
	}
	files := token.NewFileSet()
	for _, path := range paths {
		parsed, err := parser.ParseFile(files, path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil || !strings.HasPrefix(function.Name.Name, "Test") {
				continue
			}
			hasRange := false
			hasSubtest := false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch typed := node.(type) {
				case *ast.RangeStmt:
					hasRange = true
				case *ast.CallExpr:
					selector, ok := typed.Fun.(*ast.SelectorExpr)
					hasSubtest = hasSubtest || (ok && selector.Sel.Name == "Run")
				}
				return true
			})
			if hasRange && hasSubtest {
				return
			}
		}
	}
	t.Fatal("architecture package must include a table-driven Go test with subtests")
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}

func mustJSON(t *testing.T, data []byte, target any) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
}

func jsonEqual(left, right []byte) bool {
	var a, b map[string]any
	if json.Unmarshal(left, &a) != nil || json.Unmarshal(right, &b) != nil {
		return false
	}
	return reflect.DeepEqual(a, b)
}

func object(t *testing.T, value any, name string) map[string]any {
	t.Helper()
	result, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("%s is %T, want object", name, value)
	}
	return result
}

func objectArray(t *testing.T, value any, name string) []map[string]any {
	t.Helper()
	items, ok := value.([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", name, value)
	}
	result := make([]map[string]any, 0, len(items))
	for index, item := range items {
		result = append(result, object(t, item, fmt.Sprintf("%s[%d]", name, index)))
	}
	return result
}

func intValue(t *testing.T, value any, name string) int {
	t.Helper()
	n, ok := number(value)
	if !ok || float64(int(n)) != n {
		t.Fatalf("%s is %v, want integer", name, value)
	}
	return int(n)
}

func stringSlice(value any) []string {
	items, _ := value.([]any)
	result := make([]string, 0, len(items))
	for _, item := range items {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}

func sameStrings(left, right []string) bool {
	a := append([]string(nil), left...)
	b := append([]string(nil), right...)
	sort.Strings(a)
	sort.Strings(b)
	return reflect.DeepEqual(a, b)
}

func containsAll(values, required []string) bool {
	available := make(map[string]bool, len(values))
	for _, value := range values {
		available[value] = true
	}
	for _, requirement := range required {
		if !available[requirement] {
			return false
		}
	}
	return true
}
