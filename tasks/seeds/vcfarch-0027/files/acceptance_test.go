package architecture

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

type estateFixture struct {
	EstateID   string `json:"estateId"`
	Components []struct {
		ID      string `json:"id"`
		Product string `json:"product"`
		Version string `json:"version"`
	} `json:"components"`
}

type compatibilitySnapshot struct {
	TargetVCFVersion string `json:"targetVcfVersion"`
	Greenfield       struct {
		SelectedArchitecture string `json:"selectedArchitecture"`
		StorageOptions       []struct {
			Architecture              string `json:"architecture"`
			Supported                 bool   `json:"supported"`
			UsableTiBPerAvailableHost int    `json:"usableTiBPerAvailableHost"`
			HostCountForRequirement   int    `json:"hostCountForRequirement"`
			PhysicalNicsPerHost       int    `json:"physicalNicsPerHost"`
			LinkSpeedGbps             int    `json:"linkSpeedGbps"`
			DedicatedVsanNicCount     int    `json:"dedicatedVsanNicCount"`
		} `json:"storageOptions"`
	} `json:"greenfield"`
	MigrationTransitions []struct {
		ComponentID    string   `json:"componentId"`
		TargetVersion  string   `json:"targetVersion"`
		Action         string   `json:"action"`
		Gates          []string `json:"gates"`
		PostConditions []string `json:"postConditions"`
	} `json:"migrationTransitions"`
}

type migrationPlan struct {
	SchemaVersion    string `json:"schemaVersion"`
	EstateID         string `json:"estateId"`
	TargetVCFVersion string `json:"targetVcfVersion"`
	Steps            []struct {
		Order          int      `json:"order"`
		ComponentID    string   `json:"componentId"`
		Product        string   `json:"product"`
		FromVersion    string   `json:"fromVersion"`
		TargetVersion  string   `json:"targetVersion"`
		Action         string   `json:"action"`
		Gates          []string `json:"gates"`
		PostConditions []string `json:"postConditions"`
	} `json:"steps"`
}

// TestArchitectureAcceptance is intentionally one sequential verifier. The
// upstream installer schema validation is its first check; only after that
// succeeds are the seed's fixture and pinned compatibility authority opened.
func TestArchitectureAcceptance(t *testing.T) {
	specBytes := mustRead(t, "artifacts/sddc-spec.json")
	spec := decodeAny(t, specBytes)
	installerSchema := decodePinnedInstallerOpenAPI(t)
	components := object(t, installerSchema["components"], "installer schema components")
	schemas := object(t, components["schemas"], "installer schema schemas")
	sddcSchema := object(t, schemas["SddcSpec"], "installer SddcSpec schema")
	if errs := validateSchema(spec, sddcSchema, installerSchema, "$"); len(errs) != 0 {
		t.Fatalf("SddcSpec does not validate against the pinned projection of the 9.1.0.0 installer specification:\n%s", strings.Join(errs, "\n"))
	}

	// All seed-specific checks start only after the upstream-schema gate above.
	extensionSchema := decodeObject(t, mustReadPinned(t, "schemas/architecture-extension.schema.json", "5de095a2202249a96e3583c42bcf520feee77285ddb359879bc37df710a4d87a"), "architecture extension schema")
	specObject := object(t, spec, "SddcSpec")
	extension := specObject["x-architecture"]
	if errs := validateSchema(extension, extensionSchema, extensionSchema, "$.x-architecture"); len(errs) != 0 {
		t.Fatalf("x-architecture does not validate:\n%s", strings.Join(errs, "\n"))
	}

	planBytes := mustRead(t, "artifacts/migration-plan.json")
	planValue := decodeAny(t, planBytes)
	planSchema := decodeObject(t, mustReadPinned(t, "schemas/migration-plan.schema.json", "c31b8c0c70afa957072a8ce51f20128435a9b6d2abbb1fa51addd4e836b7caa4"), "migration plan schema")
	if errs := validateSchema(planValue, planSchema, planSchema, "$"); len(errs) != 0 {
		t.Fatalf("migration plan does not validate:\n%s", strings.Join(errs, "\n"))
	}

	var fixture estateFixture
	decodeInto(t, mustReadPinned(t, "fixtures/estate.json", "bfe32ed776e94ae3377edb184c47534cdd61c55f4777798bd8dd876bbcd40cc2"), &fixture)
	var snapshot compatibilitySnapshot
	decodeInto(t, mustReadPinned(t, "testdata/compatibility-snapshot.json", "654cfed8bbccce9961444f1acf058b625e0c4c51d0f8b961296863b5cf938515"), &snapshot)

	checkGreenfield(t, specObject, object(t, extension, "x-architecture"), snapshot)
	checkMigration(t, planBytes, fixture, snapshot)
	checkResearch(t)

	built, err := Build()
	if err != nil {
		t.Fatalf("Build(): %v", err)
	}
	assertJSONEqual(t, built.SddcSpec, specBytes, "Build().SddcSpec")
	assertJSONEqual(t, built.MigrationPlan, planBytes, "Build().MigrationPlan")
}

func decodePinnedInstallerOpenAPI(t *testing.T) map[string]any {
	t.Helper()
	encoded := strings.Join(strings.Fields(string(mustRead(t, "testdata/vcf-installer-openapi-9.1.0.0.json.gz.b64"))), "")
	compressed, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		t.Fatalf("decode pinned installer specification: %v", err)
	}
	reader, err := gzip.NewReader(bytes.NewReader(compressed))
	if err != nil {
		t.Fatalf("open pinned installer specification: %v", err)
	}
	data, err := io.ReadAll(reader)
	if closeErr := reader.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		t.Fatalf("read pinned installer specification: %v", err)
	}
	const upstreamSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
	if got := fmt.Sprintf("%x", sha256.Sum256(data)); got != upstreamSHA256 {
		t.Fatalf("pinned installer specification hash: got %s, want %s", got, upstreamSHA256)
	}
	return object(t, decodeAny(t, data), "installer specification")
}

func checkGreenfield(t *testing.T, spec, extension map[string]any, snapshot compatibilitySnapshot) {
	t.Helper()
	equal(t, spec["sddcId"], "chi01-m01", "sddcId")
	equal(t, spec["workflowType"], "VCF", "workflowType")
	equal(t, spec["version"], "9.1.0.0", "version")
	equal(t, spec["skipEsxThumbprintValidation"], false, "skipEsxThumbprintValidation")
	equal(t, spec["skipGatewayPingValidation"], false, "skipGatewayPingValidation")

	hosts := array(t, spec["hostSpecs"], "hostSpecs")
	if len(hosts) != 6 {
		t.Fatalf("hostSpecs: got %d hosts, want 6", len(hosts))
	}
	hostSet := map[string]bool{}
	for i, raw := range hosts {
		hostname := stringField(t, object(t, raw, fmt.Sprintf("hostSpecs[%d]", i)), "hostname")
		if hostSet[hostname] {
			t.Fatalf("hostSpecs: duplicate hostname %q", hostname)
		}
		hostSet[hostname] = true
	}

	racks := array(t, extension["racks"], "x-architecture.racks")
	rackIDs := map[string]bool{}
	placed := map[string]bool{}
	for i, raw := range racks {
		rack := object(t, raw, fmt.Sprintf("racks[%d]", i))
		id := stringField(t, rack, "id")
		if rackIDs[id] {
			t.Fatalf("racks: duplicate id %q", id)
		}
		rackIDs[id] = true
		rackHosts := array(t, rack["hosts"], "rack hosts")
		if len(rackHosts) != 3 {
			t.Fatalf("rack %q: got %d hosts, want 3", id, len(rackHosts))
		}
		for _, host := range rackHosts {
			name := scalarString(t, host, "rack hostname")
			if !hostSet[name] || placed[name] {
				t.Fatalf("rack placement for %q is missing from hostSpecs or duplicated", name)
			}
			placed[name] = true
		}
	}
	if len(placed) != len(hostSet) {
		t.Fatalf("rack placement covers %d of %d hosts", len(placed), len(hostSet))
	}

	decision := object(t, extension["storageDecision"], "storageDecision")
	equal(t, decision["selectedArchitecture"], snapshot.Greenfield.SelectedArchitecture, "selectedArchitecture")
	actualOptions := array(t, decision["options"], "storageDecision.options")
	if len(actualOptions) != len(snapshot.Greenfield.StorageOptions) {
		t.Fatalf("storageDecision.options: got %d, want %d", len(actualOptions), len(snapshot.Greenfield.StorageOptions))
	}
	byArchitecture := map[string]map[string]any{}
	for i, option := range actualOptions {
		m := object(t, option, fmt.Sprintf("storage option %d", i))
		byArchitecture[stringField(t, m, "architecture")] = m
	}
	for _, want := range snapshot.Greenfield.StorageOptions {
		got, ok := byArchitecture[want.Architecture]
		if !ok {
			t.Errorf("storage option %q missing", want.Architecture)
			continue
		}
		checks := []struct {
			field string
			want  any
		}{
			{"supported", want.Supported},
			{"usableTiBPerAvailableHost", float64(want.UsableTiBPerAvailableHost)},
			{"hostCount", float64(want.HostCountForRequirement)},
			{"physicalNicsPerHost", float64(want.PhysicalNicsPerHost)},
			{"linkSpeedGbps", float64(want.LinkSpeedGbps)},
			{"dedicatedVsanNicCount", float64(want.DedicatedVsanNicCount)},
		}
		for _, check := range checks {
			if !reflect.DeepEqual(got[check.field], check.want) {
				t.Errorf("storage option %s.%s: got %v, want %v", want.Architecture, check.field, got[check.field], check.want)
			}
		}
	}

	networkCases := []struct {
		networkType string
		vlan        float64
		subnet      string
		gateway     string
		start       string
		end         string
	}{
		{"MANAGEMENT", 110, "10.110.0.0/24", "10.110.0.1", "10.110.0.51", "10.110.0.70"},
		{"VMOTION", 120, "10.120.0.0/24", "10.120.0.1", "10.120.0.51", "10.120.0.70"},
		{"VSAN", 130, "10.130.0.0/24", "10.130.0.1", "10.130.0.51", "10.130.0.70"},
	}
	networks := array(t, spec["networkSpecs"], "networkSpecs")
	if len(networks) != len(networkCases) {
		t.Fatalf("networkSpecs: got %d networks, want %d", len(networks), len(networkCases))
	}
	byType := map[string]map[string]any{}
	for i, raw := range networks {
		network := object(t, raw, fmt.Sprintf("networkSpecs[%d]", i))
		byType[stringField(t, network, "networkType")] = network
	}
	for _, tc := range networkCases {
		network := byType[tc.networkType]
		if network == nil {
			t.Errorf("network %s missing", tc.networkType)
			continue
		}
		equal(t, network["vlanId"], tc.vlan, tc.networkType+" vlanId")
		equal(t, network["subnet"], tc.subnet, tc.networkType+" subnet")
		equal(t, network["gateway"], tc.gateway, tc.networkType+" gateway")
		equal(t, network["subnetMask"], "255.255.255.0", tc.networkType+" subnetMask")
		equal(t, network["mtu"], float64(9000), tc.networkType+" mtu")
		ranges := array(t, network["includeIpAddressRanges"], tc.networkType+" ranges")
		if len(ranges) != 1 {
			t.Errorf("%s ranges: got %d, want 1", tc.networkType, len(ranges))
			continue
		}
		r := object(t, ranges[0], tc.networkType+" range")
		equal(t, r["startIpAddress"], tc.start, tc.networkType+" range start")
		equal(t, r["endIpAddress"], tc.end, tc.networkType+" range end")
	}

	dns := object(t, spec["dnsSpec"], "dnsSpec")
	equal(t, dns["subdomain"], "chi01.example.net", "dns subdomain")
	assertStringSet(t, dns["nameservers"], []string{"10.110.0.10", "10.110.0.11"}, "nameservers")
	assertStringSet(t, spec["ntpServers"], []string{"10.110.0.20", "10.110.0.21"}, "ntpServers")

	vcenter := object(t, spec["vcenterSpec"], "vcenterSpec")
	password := stringField(t, vcenter, "rootVcenterPassword")
	if !regexp.MustCompile(`^(?:\$\{[^{}\s]+\}|\{\{[^{}\s]+\}\}|<[^<>\s]+>)$`).MatchString(password) {
		t.Errorf("rootVcenterPassword must be a non-secret substitution token, got %q", password)
	}

	datastore := object(t, spec["datastoreSpec"], "datastoreSpec")
	vsan := object(t, datastore["vsanSpec"], "vsanSpec")
	equal(t, vsan["datastoreName"], "chi01-m01-vsan01", "datastoreName")
	equal(t, vsan["failuresToTolerate"], float64(1), "failuresToTolerate")
	esa := object(t, vsan["esaConfig"], "esaConfig")
	equal(t, esa["enabled"], true, "esaConfig.enabled")

	dvsSpecs := array(t, spec["dvsSpecs"], "dvsSpecs")
	if len(dvsSpecs) != 2 {
		t.Fatalf("dvsSpecs: got %d switches, want 2", len(dvsSpecs))
	}
	allNics := map[string]bool{}
	vsanNics := 0
	for i, raw := range dvsSpecs {
		dvs := object(t, raw, fmt.Sprintf("dvsSpecs[%d]", i))
		equal(t, dvs["mtu"], float64(9000), "DVS mtu")
		networkSet := stringSet(t, dvs["networks"], "DVS networks")
		mappings := array(t, dvs["vmnicsToUplinks"], "DVS vmnicsToUplinks")
		if networkSet["VSAN"] {
			if len(networkSet) != 1 || len(mappings) != 2 {
				t.Errorf("vSAN DVS must contain only VSAN and its dedicated redundant NIC pair")
			}
			vsanNics += len(mappings)
		} else if len(networkSet) != 3 || !networkSet["MANAGEMENT"] || !networkSet["VMOTION"] || !networkSet["VM_MANAGEMENT"] || len(mappings) != 2 {
			t.Errorf("shared DVS must carry MANAGEMENT, VMOTION, and VM_MANAGEMENT traffic on a redundant NIC pair")
		}
		for _, rawMapping := range mappings {
			mapping := object(t, rawMapping, "vmnic mapping")
			id := stringField(t, mapping, "id")
			if allNics[id] {
				t.Errorf("physical NIC %q is mapped more than once", id)
			}
			allNics[id] = true
			if stringField(t, mapping, "uplink") == "" {
				t.Errorf("physical NIC %q has empty uplink", id)
			}
		}
	}
	if len(allNics) != 4 || vsanNics != 2 {
		t.Errorf("ESA network design has %d unique NICs and %d dedicated vSAN NICs; want 4 and 2", len(allNics), vsanNics)
	}
}

func checkMigration(t *testing.T, planBytes []byte, fixture estateFixture, snapshot compatibilitySnapshot) {
	t.Helper()
	var plan migrationPlan
	decodeInto(t, planBytes, &plan)
	equal(t, plan.SchemaVersion, "1.0", "migration schemaVersion")
	equal(t, plan.EstateID, fixture.EstateID, "migration estateId")
	equal(t, plan.TargetVCFVersion, snapshot.TargetVCFVersion, "migration targetVcfVersion")
	if len(plan.Steps) != len(fixture.Components) || len(plan.Steps) != len(snapshot.MigrationTransitions) {
		t.Fatalf("migration steps: got %d, want one for each of %d components", len(plan.Steps), len(fixture.Components))
	}
	for i := range plan.Steps {
		got := plan.Steps[i]
		component := fixture.Components[i]
		transition := snapshot.MigrationTransitions[i]
		if got.Order != i+1 || got.ComponentID != component.ID || got.ComponentID != transition.ComponentID ||
			got.Product != component.Product || got.FromVersion != component.Version ||
			got.TargetVersion != transition.TargetVersion || got.Action != transition.Action ||
			!reflect.DeepEqual(got.Gates, transition.Gates) || !reflect.DeepEqual(got.PostConditions, transition.PostConditions) {
			t.Errorf("migration step %d does not match fixture/snapshot\n got: %+v\nwant component=%+v transition=%+v", i+1, got, component, transition)
		}
	}
}

func checkResearch(t *testing.T) {
	t.Helper()
	research := string(mustRead(t, "research.md"))
	lower := strings.ToLower(research)

	dateMatch := regexp.MustCompile(`(?i)\baccess(?:ed)?(?:\s+on)?[:\s]+(\d{4}-\d{2}-\d{2})\b`).FindStringSubmatch(research)
	if len(dateMatch) != 2 {
		t.Error("research.md must record an access date in YYYY-MM-DD form")
	} else if _, err := time.Parse("2006-01-02", dateMatch[1]); err != nil {
		t.Errorf("research.md has an invalid access date %q", dateMatch[1])
	}

	sources := map[string]bool{}
	for _, raw := range regexp.MustCompile(`https://[^\s<>()\[\]]+`).FindAllString(research, -1) {
		source := strings.TrimRight(raw, `"'.,;:`)
		if strings.Contains(strings.ToLower(source), "broadcom.com") {
			sources[source] = true
		}
	}
	if len(sources) < 2 {
		t.Errorf("research.md must identify at least two consulted Broadcom HTTPS publications; got %d", len(sources))
	}

	for _, required := range []string{"compatibility", "upgrade", "9.1", "5.2", "esa", "osa", "storage vmotion"} {
		if !strings.Contains(lower, required) {
			t.Errorf("research.md does not record the required %q conclusion", required)
		}
	}
	if !(strings.Contains(lower, "25 gb") || strings.Contains(lower, "25gb")) ||
		!(strings.Contains(lower, "10 gb") || strings.Contains(lower, "10gb")) {
		t.Error("research.md must record the ESA and OSA network-speed implications")
	}
	if !regexp.MustCompile(`(?i)(side[- ]by[- ]side|non[- ]in[- ]place|no direct|not (?:a )?direct)`).MatchString(research) {
		t.Error("research.md must record that OSA-to-ESA is not an in-place conversion")
	}
}

func validateSchema(value any, schema, root map[string]any, path string) []string {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolvePointer(root, ref)
		if err != nil {
			return []string{fmt.Sprintf("%s: %v", path, err)}
		}
		return validateSchema(value, resolved, root, path)
	}
	var errs []string
	if want, ok := schema["const"]; ok && !reflect.DeepEqual(value, want) {
		errs = append(errs, fmt.Sprintf("%s: got %v, want constant %v", path, value, want))
	}
	typ, _ := schema["type"].(string)
	switch typ {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return append(errs, fmt.Sprintf("%s: got %T, want object", path, value))
		}
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				key, _ := item.(string)
				if _, present := obj[key]; !present {
					errs = append(errs, fmt.Sprintf("%s: missing required property %q", path, key))
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for key, child := range obj {
			if childSchema, exists := properties[key]; exists {
				errs = append(errs, validateSchema(child, childSchema.(map[string]any), root, path+"."+key)...)
			} else if schema["additionalProperties"] == false {
				errs = append(errs, fmt.Sprintf("%s: additional property %q is not allowed", path, key))
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return append(errs, fmt.Sprintf("%s: got %T, want array", path, value))
		}
		if min, ok := number(schema["minItems"]); ok && float64(len(items)) < min {
			errs = append(errs, fmt.Sprintf("%s: has %d items, minimum is %.0f", path, len(items), min))
		}
		if max, ok := number(schema["maxItems"]); ok && float64(len(items)) > max {
			errs = append(errs, fmt.Sprintf("%s: has %d items, maximum is %.0f", path, len(items), max))
		}
		if schema["uniqueItems"] == true {
			seen := map[string]bool{}
			for _, item := range items {
				encoded, _ := json.Marshal(item)
				key := string(encoded)
				if seen[key] {
					errs = append(errs, fmt.Sprintf("%s: duplicate array item %s", path, key))
				}
				seen[key] = true
			}
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for i, item := range items {
				errs = append(errs, validateSchema(item, itemSchema, root, fmt.Sprintf("%s[%d]", path, i))...)
			}
		}
	case "string":
		s, ok := value.(string)
		if !ok {
			return append(errs, fmt.Sprintf("%s: got %T, want string", path, value))
		}
		if min, ok := number(schema["minLength"]); ok && float64(len([]rune(s))) < min {
			errs = append(errs, fmt.Sprintf("%s: string is shorter than %.0f", path, min))
		}
		if max, ok := number(schema["maxLength"]); ok && float64(len([]rune(s))) > max {
			errs = append(errs, fmt.Sprintf("%s: string is longer than %.0f", path, max))
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil || !re.MatchString(s) {
				errs = append(errs, fmt.Sprintf("%s: %q does not match %q", path, s, pattern))
			}
		}
	case "integer":
		n, ok := number(value)
		if !ok || math.Trunc(n) != n {
			return append(errs, fmt.Sprintf("%s: got %v, want integer", path, value))
		}
		errs = append(errs, validateNumber(n, schema, path)...)
	case "number":
		n, ok := number(value)
		if !ok {
			return append(errs, fmt.Sprintf("%s: got %T, want number", path, value))
		}
		errs = append(errs, validateNumber(n, schema, path)...)
	case "boolean":
		if _, ok := value.(bool); !ok {
			errs = append(errs, fmt.Sprintf("%s: got %T, want boolean", path, value))
		}
	}
	return errs
}

func validateNumber(value float64, schema map[string]any, path string) []string {
	var errs []string
	if min, ok := number(schema["minimum"]); ok && value < min {
		errs = append(errs, fmt.Sprintf("%s: %.0f is less than minimum %.0f", path, value, min))
	}
	if max, ok := number(schema["maximum"]); ok && value > max {
		errs = append(errs, fmt.Sprintf("%s: %.0f exceeds maximum %.0f", path, value, max))
	}
	return errs
}

func resolvePointer(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported schema reference %q", ref)
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		obj, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q crosses a non-object", ref)
		}
		current, ok = obj[token]
		if !ok {
			return nil, fmt.Errorf("schema reference %q is missing token %q", ref, token)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema reference %q does not resolve to an object", ref)
	}
	return resolved, nil
}

func number(value any) (float64, bool) {
	n, ok := value.(float64)
	return n, ok
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}

func decodeAny(t *testing.T, data []byte) any {
	t.Helper()
	var value any
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	return value
}

func decodeInto(t *testing.T, data []byte, target any) {
	t.Helper()
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
}

func decodeObject(t *testing.T, data []byte, label string) map[string]any {
	t.Helper()
	return object(t, decodeAny(t, data), label)
}

func mustReadPinned(t *testing.T, path, wantSHA256 string) []byte {
	t.Helper()
	data := mustRead(t, path)
	if got := fmt.Sprintf("%x", sha256.Sum256(data)); got != wantSHA256 {
		t.Fatalf("protected input %s hash: got %s, want %s", path, got, wantSHA256)
	}
	return data
}

func object(t *testing.T, value any, label string) map[string]any {
	t.Helper()
	obj, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("%s: got %T, want object", label, value)
	}
	return obj
}

func array(t *testing.T, value any, label string) []any {
	t.Helper()
	items, ok := value.([]any)
	if !ok {
		t.Fatalf("%s: got %T, want array", label, value)
	}
	return items
}

func scalarString(t *testing.T, value any, label string) string {
	t.Helper()
	s, ok := value.(string)
	if !ok {
		t.Fatalf("%s: got %T, want string", label, value)
	}
	return s
}

func stringField(t *testing.T, obj map[string]any, field string) string {
	t.Helper()
	return scalarString(t, obj[field], field)
}

func equal(t *testing.T, got, want any, label string) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s: got %v, want %v", label, got, want)
	}
}

func stringSet(t *testing.T, value any, label string) map[string]bool {
	t.Helper()
	set := map[string]bool{}
	for _, raw := range array(t, value, label) {
		set[scalarString(t, raw, label)] = true
	}
	return set
}

func assertStringSet(t *testing.T, value any, want []string, label string) {
	t.Helper()
	gotSet := stringSet(t, value, label)
	got := make([]string, 0, len(gotSet))
	for item := range gotSet {
		got = append(got, item)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s: got %v, want %v", label, got, want)
	}
}

func assertJSONEqual(t *testing.T, got, want []byte, label string) {
	t.Helper()
	gotValue := decodeAny(t, got)
	wantValue := decodeAny(t, want)
	if !reflect.DeepEqual(gotValue, wantValue) {
		t.Errorf("%s is not JSON-equivalent to checked-in artifact", label)
	}
}
