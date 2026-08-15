package vcfdesign_test

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"math"
	"net/netip"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"vcfdesign"
	"vcfdesign/internal/seedverify"
)

type requirementsFixture struct {
	TargetRelease string `json:"targetRelease"`
	SddcID        string `json:"sddcId"`
	DNS           struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
		NTPServers  []string `json:"ntpServers"`
	} `json:"dns"`
	DataSites []struct {
		ID        string `json:"id"`
		Role      string `json:"role"`
		HostCount int    `json:"hostCount"`
	} `json:"dataSites"`
	HostProfile     vcfdesign.HostProfile `json:"hostProfile"`
	FailureEnvelope struct {
		ReservePercent   int                `json:"reservePercent"`
		RequiredCapacity vcfdesign.Capacity `json:"requiredCapacity"`
	} `json:"failureEnvelope"`
	Availability struct {
		ManagementDomainStretched bool   `json:"managementDomainStretched"`
		RPOSeconds                int    `json:"rpoSeconds"`
		SiteDisasterTolerance     string `json:"siteDisasterTolerance"`
		FailuresToTolerate        int    `json:"failuresToTolerate"`
		Witness                   struct {
			Site          string `json:"site"`
			FailureDomain string `json:"failureDomain"`
			Hostname      string `json:"hostname"`
			Kind          string `json:"kind"`
		} `json:"witness"`
	} `json:"availability"`
	Network struct {
		MTU      int `json:"mtu"`
		Networks []struct {
			NetworkType string `json:"networkType"`
			VlanID      int    `json:"vlanId"`
			Subnet      string `json:"subnet"`
			Gateway     string `json:"gateway"`
			RangeStart  string `json:"rangeStart"`
			RangeEnd    string `json:"rangeEnd"`
		} `json:"networks"`
	} `json:"network"`
	ManagementServices vcfdesign.ManagementServices `json:"managementServices"`
}

type estateFixture struct {
	EstateID   string `json:"estateId"`
	VCFRelease string `json:"vcfRelease"`
	Components []struct {
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"components"`
}

type snapshotFixture struct {
	TargetRelease         string `json:"targetRelease"`
	SupportedReleasePaths []struct {
		From string `json:"from"`
		To   string `json:"to"`
	} `json:"supportedReleasePaths"`
	GreenfieldBillOfMaterials map[string]string `json:"greenfieldBillOfMaterials"`
	ComponentPolicies         []struct {
		Component     string   `json:"component"`
		SupportedFrom []string `json:"supportedFrom"`
		Target        string   `json:"target"`
		Action        string   `json:"action"`
		Order         int      `json:"order"`
		RequiredGates []string `json:"requiredGates"`
	} `json:"componentPolicies"`
	OrderingConstraints []struct {
		Before string `json:"before"`
		After  string `json:"after"`
	} `json:"orderingConstraints"`
}

type checkedSddcSpec struct {
	SddcID       string `json:"sddcId"`
	WorkflowType string `json:"workflowType"`
	Version      string `json:"version"`
	HostSpecs    []struct {
		Hostname string `json:"hostname"`
	} `json:"hostSpecs"`
	VcenterSpec struct {
		Hostname string `json:"vcenterHostname"`
		Version  string `json:"version"`
	} `json:"vcenterSpec"`
	NsxtSpec struct {
		Version string `json:"version"`
	} `json:"nsxtSpec"`
	SddcManagerSpec struct {
		Version string `json:"version"`
	} `json:"sddcManagerSpec"`
	NetworkSpecs []struct {
		NetworkType            string `json:"networkType"`
		VlanID                 int    `json:"vlanId"`
		Subnet                 string `json:"subnet"`
		Gateway                string `json:"gateway"`
		MTU                    int    `json:"mtu"`
		IncludeIPAddressRanges []struct {
			Start string `json:"startIpAddress"`
			End   string `json:"endIpAddress"`
		} `json:"includeIpAddressRanges"`
	} `json:"networkSpecs"`
	DNS struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
	} `json:"dnsSpec"`
	NTPServers []string `json:"ntpServers"`
}

// Test00ProtectedAcceptance deliberately validates the installer SddcSpec before
// loading a fixture, snapshot, migration schema, or checking any design rule.
func Test00ProtectedAcceptance(t *testing.T) {
	artifactBytes := mustRead(t, "architecture.json")
	installerBytes := mustRead(t, "specifications/vcf-installer/vcf-installer-openapi.json")

	artifactRaw := decodeMap(t, artifactBytes)
	installerRaw := decodeMap(t, installerBytes)
	greenfieldRaw := mustMap(t, artifactRaw["greenfield"], "greenfield")
	sddcRaw := mustMap(t, greenfieldRaw["sddcSpec"], "greenfield.sddcSpec")
	rootSchema := map[string]any{"$ref": "#/components/schemas/SddcSpec"}
	if err := seedverify.Validate(installerRaw, rootSchema, sddcRaw); err != nil {
		t.Fatalf("greenfield.sddcSpec does not validate against installer SddcSpec: %v", err)
	}

	var artifact vcfdesign.Artifact
	decodeInto(t, artifactBytes, &artifact)
	if artifact.SchemaVersion != "1.0" {
		t.Fatalf("artifact schemaVersion = %q, want 1.0", artifact.SchemaVersion)
	}
	var req requirementsFixture
	decodeFile(t, "testdata/design-requirements.json", &req)
	var estate estateFixture
	decodeFile(t, "testdata/estate.json", &estate)
	var snapshot snapshotFixture
	decodeFile(t, "testdata/compatibility-snapshot.json", &snapshot)

	t.Run("greenfield topology and capacity", func(t *testing.T) {
		checkGreenfield(t, artifact.Greenfield, req, snapshot)
	})

	migrationSchema := decodeMap(t, mustRead(t, "specifications/migration-plan.schema.json"))
	migrationRaw := mustMap(t, artifactRaw["migrationPlan"], "migrationPlan")
	if err := seedverify.Validate(migrationSchema, migrationSchema, migrationRaw); err != nil {
		t.Fatalf("migrationPlan does not validate against migration schema: %v", err)
	}

	t.Run("migration inventory compatibility and order", func(t *testing.T) {
		checkMigration(t, artifact.MigrationPlan, estate, snapshot)
	})

	t.Run("package rebuild is deterministic", func(t *testing.T) {
		rebuilt, err := vcfdesign.Build(
			"testdata/design-requirements.json",
			"testdata/estate.json",
			"testdata/compatibility-snapshot.json",
		)
		if err != nil {
			t.Fatalf("Build: %v", err)
		}
		got, err := json.Marshal(rebuilt)
		if err != nil {
			t.Fatal(err)
		}
		want, err := json.Marshal(artifact)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(got, want) {
			t.Fatalf("Build result differs from architecture.json\n got: %s\nwant: %s", got, want)
		}
	})
}

func Test01ResearchProvenance(t *testing.T) {
	content := string(mustRead(t, "research.md"))
	if strings.TrimSpace(content) == "" {
		t.Fatal("research.md is empty")
	}
	if !regexp.MustCompile(`\b20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])\b`).MatchString(content) {
		t.Fatal("research.md does not record an ISO access date")
	}
	urls := regexp.MustCompile(`https://[^\s|)>]+`).FindAllString(content, -1)
	if len(urls) == 0 {
		t.Fatal("research.md contains no HTTPS research sources")
	}
	for _, raw := range urls {
		parsed, err := url.Parse(strings.TrimRight(raw, ".,;"))
		if err != nil || parsed.Scheme != "https" || parsed.User != nil {
			t.Fatalf("research source is not a valid HTTPS URL: %q", raw)
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "" || host == "localhost" || strings.HasSuffix(host, ".localhost") ||
			strings.HasSuffix(host, ".invalid") || strings.HasSuffix(host, ".test") ||
			strings.HasSuffix(host, ".example") {
			t.Fatalf("research source is not a reachable public host: %q", raw)
		}
		if address, err := netip.ParseAddr(host); err == nil && (!address.IsGlobalUnicast() || address.IsPrivate()) {
			t.Fatalf("research source uses a non-public address: %q", raw)
		}
	}
	lower := strings.ToLower(content)
	if !strings.Contains(lower, "broadcom") {
		t.Error("research.md does not identify Broadcom as the source publisher")
	}
	for _, subject := range []string{"compatib", "interoperab", "upgrade", "9.1"} {
		if !strings.Contains(lower, subject) {
			t.Errorf("research.md does not identify the %q research context", subject)
		}
	}
	if !strings.Contains(lower, "decision") && !strings.Contains(lower, "informed") {
		t.Error("research.md does not explain which design or migration decision the research informed")
	}
}

func Test02PackageIncludesDesignTests(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_test.go") || entry.Name() == "acceptance_test.go" {
			continue
		}
		content := string(mustRead(t, entry.Name()))
		if regexp.MustCompile(`func\s+Test[A-Za-z0-9_]*\s*\(`).MatchString(content) &&
			regexp.MustCompile(`(?s)\bfor\b.*\brange\b`).MatchString(content) {
			return
		}
	}
	t.Fatal("add a table-driven package test for the design logic")
}

func checkGreenfield(t *testing.T, got vcfdesign.Greenfield, req requirementsFixture, snapshot snapshotFixture) {
	t.Helper()
	if got.TargetRelease != req.TargetRelease || got.TargetRelease != snapshot.TargetRelease {
		t.Fatalf("target release = %q, want %q", got.TargetRelease, req.TargetRelease)
	}
	if !reflect.DeepEqual(got.BillOfMaterials, snapshot.GreenfieldBillOfMaterials) {
		t.Fatalf("bill of materials does not match pinned compatible combination")
	}
	if !reflect.DeepEqual(got.Topology.HostProfile, req.HostProfile) {
		t.Fatalf("host profile = %+v, want %+v", got.Topology.HostProfile, req.HostProfile)
	}

	if len(got.Topology.Sites) != len(req.DataSites) {
		t.Fatalf("data-site count = %d, want %d", len(got.Topology.Sites), len(req.DataSites))
	}
	dataSites := map[string]struct{}{}
	hosts := map[string]struct{}{}
	minSiteHosts := math.MaxInt
	for _, wantSite := range req.DataSites {
		dataSites[wantSite.ID] = struct{}{}
		var found *vcfdesign.SitePlacement
		for i := range got.Topology.Sites {
			if got.Topology.Sites[i].ID == wantSite.ID {
				found = &got.Topology.Sites[i]
				break
			}
		}
		if found == nil || found.Role != wantSite.Role || len(found.Hostnames) != wantSite.HostCount {
			t.Fatalf("site %s placement does not match role/host count", wantSite.ID)
		}
		minSiteHosts = min(minSiteHosts, len(found.Hostnames))
		for _, hostname := range found.Hostnames {
			if _, duplicate := hosts[hostname]; duplicate {
				t.Fatalf("host %q appears in more than one site", hostname)
			}
			hosts[hostname] = struct{}{}
		}
	}

	stretched := got.Topology.StretchedManagementDomain
	if !stretched.Enabled || !req.Availability.ManagementDomainStretched {
		t.Fatal("management domain is not stretched")
	}
	if stretched.RPOSeconds != req.Availability.RPOSeconds || stretched.RPOSeconds != 0 {
		t.Fatalf("RPO seconds = %d, want zero", stretched.RPOSeconds)
	}
	if stretched.VsanPolicy.FailuresToTolerate != req.Availability.FailuresToTolerate ||
		stretched.VsanPolicy.SiteDisasterTolerance != req.Availability.SiteDisasterTolerance ||
		stretched.VsanPolicy.PreferredSite != req.DataSites[0].ID {
		t.Fatalf("vSAN stretched policy does not match availability requirements: %+v", stretched.VsanPolicy)
	}
	w := stretched.Witness
	if w.Hostname != req.Availability.Witness.Hostname || w.Site != req.Availability.Witness.Site ||
		w.FailureDomain != req.Availability.Witness.FailureDomain || w.Kind != req.Availability.Witness.Kind {
		t.Fatalf("witness placement = %+v, want third-location fixture", w)
	}
	if _, isDataSite := dataSites[w.Site]; isDataSite || w.RunsOnManagementDomain {
		t.Fatalf("witness must be outside both data sites and its witnessed management domain: %+v", w)
	}

	reserveFactor := 1 - float64(req.FailureEnvelope.ReservePercent)/100
	wantUsable := vcfdesign.Capacity{
		CPUCores:   float64(minSiteHosts*req.HostProfile.CoresPerHost) * reserveFactor,
		MemoryGiB:  float64(minSiteHosts*req.HostProfile.MemoryGiBPerHost) * reserveFactor,
		StorageTiB: float64(minSiteHosts) * req.HostProfile.RawStorageTiBPerHost * reserveFactor,
	}
	if got.Capacity.ReservePercent != req.FailureEnvelope.ReservePercent ||
		got.Capacity.SiteFailureSurvivingHosts != minSiteHosts ||
		!capacityNear(got.Capacity.UsableAfterReserve, wantUsable) ||
		!capacityNear(got.Capacity.Required, req.FailureEnvelope.RequiredCapacity) {
		t.Fatalf("site-failure capacity decision is wrong: got %+v, usable want %+v", got.Capacity, wantUsable)
	}
	if wantUsable.CPUCores < got.Capacity.Required.CPUCores ||
		wantUsable.MemoryGiB < got.Capacity.Required.MemoryGiB ||
		wantUsable.StorageTiB < got.Capacity.Required.StorageTiB {
		t.Fatal("surviving site cannot carry requirements with reserve")
	}

	var spec checkedSddcSpec
	b, _ := json.Marshal(got.SddcSpec)
	decodeInto(t, b, &spec)
	if spec.SddcID != req.SddcID || spec.WorkflowType != "VCF" || spec.Version != req.TargetRelease {
		t.Fatalf("installer identity/workflow/version is wrong: %+v", spec)
	}
	if spec.VcenterSpec.Version != snapshot.GreenfieldBillOfMaterials["vcenter"] ||
		spec.NsxtSpec.Version != snapshot.GreenfieldBillOfMaterials["nsx"] ||
		spec.SddcManagerSpec.Version != snapshot.GreenfieldBillOfMaterials["sddc-manager"] {
		t.Fatal("installer component versions do not match pinned bill of materials")
	}
	specHosts := map[string]struct{}{}
	for _, host := range spec.HostSpecs {
		if _, duplicate := specHosts[host.Hostname]; duplicate {
			t.Fatalf("SddcSpec host %q is duplicated", host.Hostname)
		}
		specHosts[host.Hostname] = struct{}{}
	}
	if len(spec.HostSpecs) != len(hosts) || !reflect.DeepEqual(specHosts, hosts) {
		t.Fatalf("SddcSpec hosts do not exactly match site placement")
	}
	if spec.DNS.Subdomain != req.DNS.Subdomain || !reflect.DeepEqual(spec.DNS.Nameservers, req.DNS.Nameservers) ||
		!reflect.DeepEqual(spec.NTPServers, req.DNS.NTPServers) {
		t.Fatal("DNS/NTP design does not match requirements")
	}

	networks := map[string]struct {
		VlanID, MTU                 int
		Subnet, Gateway, Start, End string
	}{}
	if len(spec.NetworkSpecs) != len(req.Network.Networks) {
		t.Fatalf("SddcSpec network count = %d, want %d", len(spec.NetworkSpecs), len(req.Network.Networks))
	}
	for _, network := range spec.NetworkSpecs {
		if _, duplicate := networks[network.NetworkType]; duplicate {
			t.Fatalf("SddcSpec network type %q is duplicated", network.NetworkType)
		}
		entry := struct {
			VlanID, MTU                 int
			Subnet, Gateway, Start, End string
		}{VlanID: network.VlanID, MTU: network.MTU, Subnet: network.Subnet, Gateway: network.Gateway}
		if len(network.IncludeIPAddressRanges) == 1 {
			entry.Start = network.IncludeIPAddressRanges[0].Start
			entry.End = network.IncludeIPAddressRanges[0].End
		}
		networks[network.NetworkType] = entry
	}
	for _, want := range req.Network.Networks { // table-driven network contract
		gotNet, ok := networks[want.NetworkType]
		if !ok || gotNet.VlanID != want.VlanID || gotNet.MTU != req.Network.MTU ||
			gotNet.Subnet != want.Subnet || gotNet.Gateway != want.Gateway ||
			gotNet.Start != want.RangeStart || gotNet.End != want.RangeEnd {
			t.Errorf("network %s = %+v, does not match requirement", want.NetworkType, gotNet)
		}
	}
	checkManagementPool(t, got.ManagementServices, req.ManagementServices, req.Network.Networks)
}

func checkMigration(t *testing.T, got vcfdesign.MigrationPlan, estate estateFixture, snapshot snapshotFixture) {
	t.Helper()
	if got.EstateID != estate.EstateID || got.TargetRelease != snapshot.TargetRelease || got.SchemaVersion != "1.0" {
		t.Fatalf("migration plan identity/target is wrong: %+v", got)
	}
	pathSupported := false
	for _, path := range snapshot.SupportedReleasePaths {
		pathSupported = pathSupported || (path.From == estate.VCFRelease && path.To == got.TargetRelease)
	}
	if !pathSupported {
		t.Fatal("estate release has no pinned supported path to target")
	}
	if len(got.Steps) != len(estate.Components) {
		t.Fatalf("migration steps = %d, inventory components = %d", len(got.Steps), len(estate.Components))
	}

	current := map[string]string{}
	for _, component := range estate.Components {
		current[component.Name] = component.Version
	}
	steps := map[string]vcfdesign.MigrationStep{}
	orders := map[int]struct{}{}
	for index, step := range got.Steps {
		if step.Order != index+1 {
			t.Fatalf("migration step %q is at array position %d but declares order %d", step.Component, index+1, step.Order)
		}
		if _, duplicate := steps[step.Component]; duplicate {
			t.Fatalf("duplicate migration component %q", step.Component)
		}
		if _, duplicate := orders[step.Order]; duplicate {
			t.Fatalf("duplicate migration order %d", step.Order)
		}
		steps[step.Component] = step
		orders[step.Order] = struct{}{}
	}
	for _, policy := range snapshot.ComponentPolicies { // table-driven compatibility contract
		step, ok := steps[policy.Component]
		if !ok {
			t.Errorf("component %q is missing", policy.Component)
			continue
		}
		if step.CurrentVersion != current[policy.Component] || step.Target != policy.Target ||
			step.Action != policy.Action || step.Order != policy.Order ||
			!sameStrings(step.Gates, policy.RequiredGates) {
			t.Errorf("component %q step = %+v, policy target/action/order/gates differ", policy.Component, step)
		}
		if !contains(policy.SupportedFrom, step.CurrentVersion) {
			t.Errorf("component %q source %q is unsupported", policy.Component, step.CurrentVersion)
		}
	}
	for name := range current {
		if _, ok := steps[name]; !ok {
			t.Errorf("inventory component %q has no migration step", name)
		}
	}
	for _, edge := range snapshot.OrderingConstraints {
		if steps[edge.Before].Order >= steps[edge.After].Order {
			t.Errorf("ordering constraint violated: %s must precede %s", edge.Before, edge.After)
		}
	}
}

func checkManagementPool(t *testing.T, got, want vcfdesign.ManagementServices, networks []struct {
	NetworkType string `json:"networkType"`
	VlanID      int    `json:"vlanId"`
	Subnet      string `json:"subnet"`
	Gateway     string `json:"gateway"`
	RangeStart  string `json:"rangeStart"`
	RangeEnd    string `json:"rangeEnd"`
}) {
	t.Helper()
	if got != want {
		t.Fatalf("management-services reservation = %+v, want %+v", got, want)
	}
	start := netip.MustParseAddr(got.PoolStart)
	end := netip.MustParseAddr(got.PoolEnd)
	count := int(addressUint32(end)-addressUint32(start)) + 1
	if count < got.MinimumAddresses {
		t.Fatalf("management-services pool has %d addresses, want at least %d", count, got.MinimumAddresses)
	}
	internal := netip.MustParsePrefix(got.InternalCIDR)
	if internal.Contains(start) || internal.Contains(end) {
		t.Fatal("management-services pool overlaps its internal service CIDR")
	}
	for _, network := range networks {
		if network.NetworkType == got.NetworkType {
			prefix := netip.MustParsePrefix(network.Subnet)
			if !prefix.Contains(start) || !prefix.Contains(end) {
				t.Fatal("management-services pool is not inside selected network")
			}
			return
		}
	}
	t.Fatalf("management-services network type %q is missing", got.NetworkType)
}

func capacityNear(a, b vcfdesign.Capacity) bool {
	return math.Abs(a.CPUCores-b.CPUCores) < 0.001 && math.Abs(a.MemoryGiB-b.MemoryGiB) < 0.001 && math.Abs(a.StorageTiB-b.StorageTiB) < 0.001
}

func addressUint32(addr netip.Addr) uint32 { return binary.BigEndian.Uint32(addr.AsSlice()) }

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func sameStrings(a, b []string) bool {
	a = append([]string(nil), a...)
	b = append([]string(nil), b...)
	sort.Strings(a)
	sort.Strings(b)
	return reflect.DeepEqual(a, b)
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func decodeFile(t *testing.T, path string, target any) { decodeInto(t, mustRead(t, path), target) }

func decodeInto(t *testing.T, data []byte, target any) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		t.Fatalf("decode JSON: trailing content after document")
	}
}

func decodeMap(t *testing.T, data []byte) map[string]any {
	t.Helper()
	var result map[string]any
	decodeInto(t, data, &result)
	return result
}

func mustMap(t *testing.T, value any, name string) map[string]any {
	t.Helper()
	result, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("%s must be an object, got %T", name, value)
	}
	return result
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
