package verifier

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
	"regexp"
	"strings"
	"testing"
	"time"

	vcfarchitecture "vcfseed/architecture"
)

const (
	planPath            = "../migration-plan.json"
	inventoryPath       = "../fixtures/estate.json"
	compatibilityPath   = "../grader/compatibility-snapshot.json"
	migrationSchemaPath = "../schemas/migration-plan.schema.json"
	installerSpecPath   = "../specifications/vcf-installer/vcf-installer-openapi.json"
	researchPath        = "../research.md"
)

func TestTableDrivenPackageTests(t *testing.T) {
	entries, err := os.ReadDir("../architecture")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		path := filepath.Join("../architecture", entry.Name())
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		hasRange := false
		hasBuildPlanCall := false
		ast.Inspect(file, func(node ast.Node) bool {
			if _, ok := node.(*ast.RangeStmt); ok {
				hasRange = true
			}
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			switch function := call.Fun.(type) {
			case *ast.Ident:
				hasBuildPlanCall = hasBuildPlanCall || function.Name == "BuildPlan"
			case *ast.SelectorExpr:
				hasBuildPlanCall = hasBuildPlanCall || function.Sel.Name == "BuildPlan"
			}
			return true
		})
		if hasRange && hasBuildPlanCall {
			return
		}
	}
	t.Fatal("architecture package needs a table-driven BuildPlan test with a success and representative rejection cases")
}

func TestResearchArtifact(t *testing.T) {
	raw, err := os.ReadFile(researchPath)
	if err != nil {
		t.Fatalf("read %s: %v", researchPath, err)
	}
	text := string(raw)
	accessDatePattern := regexp.MustCompile(`(?mi)\b(?:accessed|access date)\s*:?\s*(\d{4}-\d{2}-\d{2})\b`)
	dateMatch := accessDatePattern.FindStringSubmatch(text)
	if dateMatch == nil {
		t.Fatal("research.md must record an access date in YYYY-MM-DD format")
	}
	if _, err := time.Parse("2006-01-02", dateMatch[1]); err != nil {
		t.Fatalf("research.md has an invalid access date: %v", err)
	}

	entryPattern := regexp.MustCompile(`(?m)^([^\n]*)(?:\[[^\]\n]+\]\((https://[^)\s]+)\)|(https://[^\s|)>]+))([^\n]*)$`)
	entries := entryPattern.FindAllStringSubmatch(text, -1)
	if len(entries) < 2 {
		t.Fatalf("research.md has %d documented HTTPS sources, want at least 2", len(entries))
	}
	seen := map[string]bool{}
	primaryBroadcomSources := 0
	notes := make([]string, 0, len(entries))
	for _, entry := range entries {
		sourceURL := entry[2]
		if sourceURL == "" {
			sourceURL = entry[3]
		}
		parsed, err := url.Parse(sourceURL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("research.md contains an invalid source URL %q", sourceURL)
		}
		host := strings.ToLower(parsed.Hostname())
		path := strings.ToLower(parsed.Path)
		isBroadcom := host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") || host == "blogs.vmware.com"
		isPinnedAPI := host == "github.com" && strings.HasPrefix(path, "/vmware/vcf-api-specs/")
		if !isBroadcom && !isPinnedAPI {
			t.Fatalf("research.md source %q is not primary Broadcom material or the pinned VMware API repository", sourceURL)
		}
		if strings.Contains(path, "/search") {
			t.Fatalf("research.md source %q appears to be a search-result URL", sourceURL)
		}
		if seen[sourceURL] {
			t.Fatalf("research.md repeats source URL %q", sourceURL)
		}
		seen[sourceURL] = true
		if isBroadcom {
			primaryBroadcomSources++
		}
		note := strings.Trim(strings.TrimSpace(entry[1]+" "+entry[4]), "|:—–- \t")
		if len(strings.Fields(note)) < 3 {
			t.Fatalf("research.md source %q needs a short substantive consultation note", sourceURL)
		}
		notes = append(notes, note)
	}
	if primaryBroadcomSources < 2 {
		t.Fatalf("research.md has %d primary Broadcom sources, want at least 2", primaryBroadcomSources)
	}

	combinedNotes := strings.ToLower(strings.Join(notes, " "))
	coverage := []struct {
		name    string
		pattern string
	}{
		{"source component combination", `source|bill of materials|\bbom\b|component (version|combination)|constituent`},
		{"allowed upgrade path", `upgrade path|supported path|eligible|move to 9\.1|5\.2[^.]*9\.1`},
		{"component ordering", `sequence|ordering|\border\b|\bbefore\b|\bafter\b|\bthen\b|\bfirst\b|\bnext\b`},
		{"four-host minimum", `(four|4)[^.!?]{0,30}host|host[^.!?]{0,30}(four|4)|minimum[^.!?]{0,40}(four|4)`},
	}
	for _, requirement := range coverage {
		if !regexp.MustCompile(requirement.pattern).MatchString(combinedNotes) {
			t.Errorf("research.md notes do not document consultation about %s", requirement.name)
		}
	}
}

func TestMigrationPlanArtifact(t *testing.T) {
	// The first substantive artifact check is deliberately the official
	// installer SddcSpec validation. Research files are never opened here.
	planDocument := loadJSONValue(t, planPath)
	installerDocument := loadJSONValue(t, installerSpecPath)
	planObject, _ := planDocument.(map[string]any)
	installerObject, ok := installerDocument.(map[string]any)
	if !ok {
		t.Fatal("pinned installer specification is not a JSON object")
	}
	components, ok := installerObject["components"].(map[string]any)
	if !ok {
		t.Fatal("pinned installer specification has no components object")
	}
	schemas, ok := components["schemas"].(map[string]any)
	if !ok {
		t.Fatal("pinned installer specification has no schemas object")
	}
	sddcSchema, ok := schemas["SddcSpec"]
	if !ok {
		t.Fatal("pinned installer specification has no SddcSpec schema")
	}
	if err := validateJSONSchema(installerDocument, sddcSchema, planObject["targetSddcSpec"], "$.targetSddcSpec"); err != nil {
		t.Fatalf("targetSddcSpec does not validate against installer SddcSpec: %v", err)
	}

	// Only after SddcSpec validation do we check the migration-plan schema.
	migrationSchema := loadJSONValue(t, migrationSchemaPath)
	if err := validateJSONSchema(migrationSchema, migrationSchema, planDocument, "$"); err != nil {
		t.Fatalf("migration-plan schema validation failed: %v", err)
	}

	var plan vcfarchitecture.MigrationPlan
	decodeJSONFile(t, planPath, &plan)
	var inventory vcfarchitecture.Inventory
	decodeJSONFile(t, inventoryPath, &inventory)
	var compatibility vcfarchitecture.CompatibilitySnapshot
	decodeJSONFile(t, compatibilityPath, &compatibility)

	checks := []struct {
		name  string
		check func() error
	}{
		{"identity and topology", func() error { return checkIdentityAndTopology(plan, inventory, compatibility) }},
		{"supported release path", func() error { return checkReleasePath(plan, inventory, compatibility) }},
		{"gate definitions", func() error { return checkGateDefinitions(plan, compatibility) }},
		{"ordered component stages", func() error { return checkStages(plan, inventory, compatibility) }},
		{"retained target design", func() error { return checkTargetDesign(plan.TargetSddcSpec, inventory) }},
	}
	for _, test := range checks {
		t.Run(test.name, func(t *testing.T) {
			if err := test.check(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestBuildPlan(t *testing.T) {
	var baseInventory vcfarchitecture.Inventory
	decodeJSONFile(t, inventoryPath, &baseInventory)
	var baseCompatibility vcfarchitecture.CompatibilitySnapshot
	decodeJSONFile(t, compatibilityPath, &baseCompatibility)
	var artifact vcfarchitecture.MigrationPlan
	decodeJSONFile(t, planPath, &artifact)

	tests := []struct {
		name    string
		mutate  func(*vcfarchitecture.Inventory, *vcfarchitecture.CompatibilitySnapshot)
		wantErr bool
	}{
		{name: "fixture", wantErr: false},
		{name: "below minimum hosts", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Hosts = inventory.Hosts[:3]
		}},
		{name: "multiple sites", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Site.SiteCount = 2
		}},
		{name: "not consolidated", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Site.DesignModel = "standard"
		}},
		{name: "unsupported release hop", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.TargetRelease = "9.0.1.0"
		}},
		{name: "component version drift", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Components[2].CurrentVersion = "5.2.1.2-24690695"
		}},
		{name: "component state drift", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Components[2].CurrentState = "absent"
		}},
		{name: "unknown gate", wantErr: true, mutate: func(_ *vcfarchitecture.Inventory, compatibility *vcfarchitecture.CompatibilitySnapshot) {
			compatibility.Stages[0].Changes[0].Gates = append(compatibility.Stages[0].Changes[0].Gates, "unknown-gate")
		}},
		{name: "missing component", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Components = inventory.Components[:len(inventory.Components)-1]
		}},
		{name: "duplicate component", wantErr: true, mutate: func(inventory *vcfarchitecture.Inventory, _ *vcfarchitecture.CompatibilitySnapshot) {
			inventory.Components = append(inventory.Components, inventory.Components[0])
		}},
		{name: "non-contiguous stages", wantErr: true, mutate: func(_ *vcfarchitecture.Inventory, compatibility *vcfarchitecture.CompatibilitySnapshot) {
			compatibility.Stages[1].Sequence = 3
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			inventory := cloneJSON(t, baseInventory)
			compatibility := cloneJSON(t, baseCompatibility)
			if test.mutate != nil {
				test.mutate(&inventory, &compatibility)
			}
			got, err := vcfarchitecture.BuildPlan(inventory, compatibility)
			if (err != nil) != test.wantErr {
				t.Fatalf("BuildPlan() error = %v, wantErr %v", err, test.wantErr)
			}
			if !test.wantErr && !jsonEquivalent(t, got, artifact) {
				t.Fatal("BuildPlan() does not reproduce migration-plan.json")
			}
		})
	}
}

func checkIdentityAndTopology(plan vcfarchitecture.MigrationPlan, inventory vcfarchitecture.Inventory, compatibility vcfarchitecture.CompatibilitySnapshot) error {
	if plan.SchemaVersion != "1.0.0" || plan.EstateID != inventory.EstateID || plan.SourceRelease != inventory.SourceRelease || plan.TargetRelease != inventory.TargetRelease {
		return fmt.Errorf("plan identity/releases do not match the inventory")
	}
	want := vcfarchitecture.PlanTopology{
		SiteCount:            inventory.Site.SiteCount,
		AvailabilityZoneMode: inventory.Site.AvailabilityZoneMode,
		DesignModel:          inventory.Site.DesignModel,
		StorageType:          inventory.Cluster.StorageType,
		HostCount:            len(inventory.Hosts),
	}
	if !reflect.DeepEqual(plan.Topology, want) {
		return fmt.Errorf("topology = %+v, want %+v", plan.Topology, want)
	}
	minimum, found := matchingMinimum(inventory, compatibility)
	if !found {
		return fmt.Errorf("snapshot has no host constraint for inventory topology")
	}
	if len(inventory.Hosts) != minimum || plan.Topology.HostCount != minimum {
		return fmt.Errorf("consolidated design must remain at the pinned minimum of %d hosts", minimum)
	}
	return nil
}

func checkReleasePath(plan vcfarchitecture.MigrationPlan, inventory vcfarchitecture.Inventory, compatibility vcfarchitecture.CompatibilitySnapshot) error {
	if len(plan.ReleaseHops) == 0 {
		return fmt.Errorf("release path is empty")
	}
	cursor := inventory.SourceRelease
	for index, hop := range plan.ReleaseHops {
		if !hop.Supported || hop.From != cursor {
			return fmt.Errorf("release hop %d does not continue the supported path from %s", index+1, cursor)
		}
		matched := false
		for _, allowed := range compatibility.ReleaseHops {
			if allowed.Supported && reflect.DeepEqual(hop, allowed) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("release hop %s -> %s is not a supported snapshot edge", hop.From, hop.To)
		}
		cursor = hop.To
	}
	if cursor != inventory.TargetRelease {
		return fmt.Errorf("release path ends at %s, want %s", cursor, inventory.TargetRelease)
	}
	return nil
}

func checkGateDefinitions(plan vcfarchitecture.MigrationPlan, compatibility vcfarchitecture.CompatibilitySnapshot) error {
	catalog := make(map[string]string, len(compatibility.GateCatalog))
	for _, gate := range compatibility.GateCatalog {
		if _, duplicate := catalog[gate.ID]; duplicate {
			return fmt.Errorf("snapshot gate %q is defined more than once", gate.ID)
		}
		catalog[gate.ID] = gate.Condition
	}
	defined := make(map[string]bool, len(plan.GateDefinitions))
	for _, gate := range plan.GateDefinitions {
		if defined[gate.ID] {
			return fmt.Errorf("gate %q is defined more than once", gate.ID)
		}
		condition, exists := catalog[gate.ID]
		if !exists || condition != gate.Condition {
			return fmt.Errorf("gate %q does not match the compatibility snapshot", gate.ID)
		}
		defined[gate.ID] = true
	}
	for _, stage := range plan.Stages {
		for _, change := range stage.Changes {
			seen := map[string]bool{}
			for _, gate := range change.Gates {
				if !defined[gate] {
					return fmt.Errorf("component %s references undefined gate %q", change.ComponentID, gate)
				}
				if seen[gate] {
					return fmt.Errorf("component %s repeats gate %q", change.ComponentID, gate)
				}
				seen[gate] = true
			}
		}
	}
	return nil
}

func checkStages(plan vcfarchitecture.MigrationPlan, inventory vcfarchitecture.Inventory, compatibility vcfarchitecture.CompatibilitySnapshot) error {
	if len(plan.Stages) != len(compatibility.Stages) {
		return fmt.Errorf("got %d stages, want %d", len(plan.Stages), len(compatibility.Stages))
	}
	seenComponents := map[string]int{}
	for index, expected := range compatibility.Stages {
		actual := plan.Stages[index]
		if actual.Sequence != index+1 || expected.Sequence != index+1 {
			return fmt.Errorf("stage order is not contiguous at index %d", index)
		}
		if actual.ID != expected.ID || actual.Mechanism != expected.Mechanism || len(actual.Changes) != len(expected.Changes) {
			return fmt.Errorf("stage %d does not match pinned id, mechanism, or change count", index+1)
		}
		for changeIndex, expectedChange := range expected.Changes {
			actualChange := actual.Changes[changeIndex]
			want := vcfarchitecture.ComponentChange{
				ComponentID: expectedChange.ComponentID, FromVersion: expectedChange.FromVersion,
				FromState: expectedChange.FromState, TargetVersion: expectedChange.TargetVersion,
				TargetState: expectedChange.TargetState, Gates: expectedChange.Gates,
			}
			if !reflect.DeepEqual(actualChange, want) {
				return fmt.Errorf("stage %d change %d does not match pinned component transition", index+1, changeIndex+1)
			}
			seenComponents[actualChange.ComponentID]++
		}
	}
	if len(seenComponents) != len(inventory.Components) {
		return fmt.Errorf("plan names %d distinct components, inventory has %d", len(seenComponents), len(inventory.Components))
	}
	for _, component := range inventory.Components {
		if seenComponents[component.ID] != 1 {
			return fmt.Errorf("inventory component %s appears %d times, want exactly once", component.ID, seenComponents[component.ID])
		}
	}
	return nil
}

func checkTargetDesign(spec map[string]any, inventory vcfarchitecture.Inventory) error {
	if stringValue(spec["sddcId"]) != inventory.SDDCID || stringValue(spec["version"]) != inventory.TargetRelease || stringValue(spec["workflowType"]) != "VCF_COMPLETE" {
		return fmt.Errorf("target SddcSpec identity, workflow, or version is wrong")
	}
	hosts, ok := spec["hostSpecs"].([]any)
	if !ok || len(hosts) != len(inventory.Hosts) || len(hosts) != 4 {
		return fmt.Errorf("target SddcSpec must retain exactly the four inventory hosts")
	}
	for index, host := range hosts {
		object, _ := host.(map[string]any)
		if stringValue(object["hostname"]) != inventory.Hosts[index].ShortHostname {
			return fmt.Errorf("target host %d does not match inventory short hostname", index+1)
		}
	}

	dns, _ := spec["dnsSpec"].(map[string]any)
	if stringValue(dns["subdomain"]) != inventory.DNS.Subdomain || !stringSliceEqual(dns["nameservers"], inventory.DNS.Nameservers) {
		return fmt.Errorf("target DNS does not preserve inventory DNS")
	}
	if !stringSliceEqual(spec["ntpServers"], inventory.NTPServers) {
		return fmt.Errorf("target NTP servers do not preserve inventory NTP")
	}

	cluster, _ := spec["clusterSpec"].(map[string]any)
	if stringValue(cluster["datacenterName"]) != inventory.Cluster.DatacenterName || stringValue(cluster["clusterName"]) != inventory.Cluster.ClusterName {
		return fmt.Errorf("target cluster names do not preserve inventory")
	}
	datastore, _ := spec["datastoreSpec"].(map[string]any)
	if stringValue(datastore["existingDatastoreName"]) != inventory.Cluster.DatastoreName {
		return fmt.Errorf("target design does not retain the existing vSAN datastore")
	}

	vcenterComponent, found := inventoryComponent(inventory, "VCENTER")
	if !found || len(vcenterComponent.Endpoints) != 1 {
		return fmt.Errorf("inventory vCenter endpoint is invalid")
	}
	vcenter, _ := spec["vcenterSpec"].(map[string]any)
	if stringValue(vcenter["vcenterHostname"]) != vcenterComponent.Endpoints[0] || stringValue(vcenter["version"]) != inventory.TargetRelease || vcenter["useExistingDeployment"] != true || stringValue(vcenter["rootVcenterPassword"]) != inventory.FixtureSecrets.VCenterRootPassword {
		return fmt.Errorf("target vCenter is not the retained inventory deployment at the target version")
	}

	nsxComponent, found := inventoryComponent(inventory, "NSX_MANAGER")
	if !found {
		return fmt.Errorf("inventory NSX component is missing")
	}
	nsx, _ := spec["nsxtSpec"].(map[string]any)
	if stringValue(nsx["vipFqdn"]) != nsxComponent.VIPFQDN || stringValue(nsx["version"]) != inventory.TargetRelease || nsx["useExistingDeployment"] != true {
		return fmt.Errorf("target NSX is not the retained inventory deployment at the target version")
	}
	managers, _ := nsx["nsxtManagers"].([]any)
	if len(managers) != len(nsxComponent.Endpoints) {
		return fmt.Errorf("target NSX manager count does not match inventory")
	}
	for index, manager := range managers {
		object, _ := manager.(map[string]any)
		if stringValue(object["hostname"]) != nsxComponent.Endpoints[index] {
			return fmt.Errorf("target NSX manager %d does not match inventory", index+1)
		}
	}

	networks, _ := spec["networkSpecs"].([]any)
	if len(networks) != len(inventory.Networks) {
		return fmt.Errorf("target network count does not match inventory")
	}
	for index, network := range networks {
		object, _ := network.(map[string]any)
		want := inventory.Networks[index]
		if stringValue(object["networkType"]) != want.NetworkType || intValue(object["vlanId"]) != want.VLANID || intValue(object["mtu"]) != want.MTU || stringValue(object["subnet"]) != want.Subnet || stringValue(object["gateway"]) != want.Gateway {
			return fmt.Errorf("target network %d does not preserve inventory", index+1)
		}
		if !jsonEquivalentValue(object["includeIpAddressRanges"], want.IncludeIPAddressRanges) && len(want.IncludeIPAddressRanges) != 0 {
			return fmt.Errorf("target network %d IP ranges do not preserve inventory", index+1)
		}
	}
	return nil
}

func matchingMinimum(inventory vcfarchitecture.Inventory, compatibility vcfarchitecture.CompatibilitySnapshot) (int, bool) {
	for _, constraint := range compatibility.MinimumHostConstraints {
		if constraint.DesignModel == inventory.Site.DesignModel && constraint.SiteCount == inventory.Site.SiteCount && constraint.AvailabilityZoneMode == inventory.Site.AvailabilityZoneMode && constraint.StorageType == inventory.Cluster.StorageType {
			return constraint.MinimumHosts, true
		}
	}
	return 0, false
}

func inventoryComponent(inventory vcfarchitecture.Inventory, id string) (vcfarchitecture.InventoryComponent, bool) {
	for _, component := range inventory.Components {
		if component.ID == id {
			return component, true
		}
	}
	return vcfarchitecture.InventoryComponent{}, false
}

func decodeJSONFile(t *testing.T, path string, destination any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func cloneJSON[T any](t *testing.T, value T) T {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var clone T
	if err := json.Unmarshal(raw, &clone); err != nil {
		t.Fatal(err)
	}
	return clone
}

func jsonEquivalent(t *testing.T, left, right any) bool {
	t.Helper()
	return jsonEquivalentValue(left, right)
}

func jsonEquivalentValue(left, right any) bool {
	normalize := func(value any) any {
		raw, err := json.Marshal(value)
		if err != nil {
			return err.Error()
		}
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		var decoded any
		if err := decoder.Decode(&decoded); err != nil {
			return err.Error()
		}
		return decoded
	}
	return reflect.DeepEqual(normalize(left), normalize(right))
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}

func intValue(value any) int {
	switch number := value.(type) {
	case float64:
		return int(number)
	case json.Number:
		parsed, _ := number.Int64()
		return int(parsed)
	case int:
		return number
	default:
		return 0
	}
}

func stringSliceEqual(value any, expected []string) bool {
	items, ok := value.([]any)
	if !ok || len(items) != len(expected) {
		return false
	}
	actual := make([]string, len(items))
	for index, item := range items {
		actual[index] = stringValue(item)
	}
	return reflect.DeepEqual(actual, expected)
}
