package verify

import (
	"encoding/json"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"example.com/vcf90/gosc/internal/mockvc"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
)

type sourcesDoc struct {
	Sources []struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		Tag          string   `json:"tag"`
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		SpecVersion  string   `json:"spec_version"`
		URL          string   `json:"url"`
		OperationIDs []string `json:"operation_ids"`
	} `json:"sources"`
}

func TestOfficialSourcesRecordTheNineZeroSpecification(t *testing.T) {
	raw, err := os.ReadFile(sourcesPath)
	if err != nil {
		t.Fatalf("read %s: %v", sourcesPath, err)
	}
	var doc sourcesDoc
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&doc); err != nil {
		t.Fatalf("%s: %v", sourcesPath, err)
	}
	if len(doc.Sources) != 1 {
		t.Fatalf("%s: want exactly 1 source, got %d", sourcesPath, len(doc.Sources))
	}
	s := doc.Sources[0]

	tests := []struct {
		field string
		got   string
		want  string
	}{
		{"repository", strings.TrimSuffix(strings.TrimSuffix(s.Repository, "/"), ".git"), specRepository},
		{"license", s.License, specLicense},
		{"tag", s.Tag, specTag},
		{"commit_sha", s.CommitSHA, specCommitSHA},
		{"spec_path", s.SpecPath, specPath},
		{"spec_version", s.SpecVersion, specVersion},
	}
	for _, tc := range tests {
		if tc.got != tc.want {
			t.Errorf("%s: %s = %q, want %q", sourcesPath, tc.field, tc.got, tc.want)
		}
	}
	if s.CommitSHA == theNineOneCommitSHA {
		t.Errorf("%s: commit_sha is the 9.1.0.0 revision of the same file", sourcesPath)
	}
	if !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(s.CommitSHA) {
		t.Errorf("%s: commit_sha %q is not a full 40-hex commit sha", sourcesPath, s.CommitSHA)
	}
	if !strings.Contains(s.URL, specCommitSHA) || !strings.Contains(s.URL, specPath) {
		t.Errorf("%s: url %q is not a permalink to %s at %s", sourcesPath, s.URL, specPath, specCommitSHA)
	}
	if want := []string{opCheck, opSet}; !reflect.DeepEqual(s.OperationIDs, want) {
		t.Errorf("%s: operation_ids = %v, want %v", sourcesPath, s.OperationIDs, want)
	}
}

func TestContractTranscribesTheSpecification(t *testing.T) {
	got := loadContract(t)
	want := expectedContract()

	if got.API != want.API {
		t.Errorf("api = %q, want %q", got.API, want.API)
	}
	if got.SpecVersion != want.SpecVersion {
		t.Errorf("spec_version = %q, want %q", got.SpecVersion, want.SpecVersion)
	}
	if got.ServerBasePath != want.ServerBasePath {
		t.Errorf("server_base_path = %q, want %q", got.ServerBasePath, want.ServerBasePath)
	}
	if !reflect.DeepEqual(got.Auth, want.Auth) {
		t.Errorf("auth = %+v, want %+v", got.Auth, want.Auth)
	}

	if len(got.Operations) != len(want.Operations) {
		t.Fatalf("operations: got %d, want %d", len(got.Operations), len(want.Operations))
	}
	for i, w := range want.Operations {
		g := got.Operations[i]
		if !reflect.DeepEqual(g, w) {
			t.Errorf("operations[%d]:\n got %s\nwant %s", i, mustIndent(g), mustIndent(w))
		}
	}

	gotNames, wantNames := schemaNames(got.Schemas), schemaNames(want.Schemas)
	if !reflect.DeepEqual(gotNames, wantNames) {
		t.Errorf("schemas: the contracted set is not the transitive closure of the two operations' bodies\n got %v\nwant %v",
			diffStrings(gotNames, wantNames), diffStrings(wantNames, gotNames))
	}

	for _, name := range wantNames {
		g, ok := got.Schemas[name]
		if !ok {
			continue
		}
		w := want.Schemas[name]
		gp, wp := propNames(g), propNames(w)
		if !reflect.DeepEqual(gp, wp) {
			t.Errorf("schemas[%q]: properties\n got %v\nwant %v", name, gp, wp)
			continue
		}
		for _, pn := range wp {
			if !reflect.DeepEqual(g.Properties[pn], w.Properties[pn]) {
				t.Errorf("schemas[%q].properties[%q]:\n got %s\nwant %s",
					name, pn, mustIndent(g.Properties[pn]), mustIndent(w.Properties[pn]))
			}
		}
	}
}

// TestContractIsNotTheNineOneRevision guards the one place where the 9.0.0.0 and
// 9.1.0.0 revisions of vcenter.yaml disagree inside this contract's closure.
func TestContractIsNotTheNineOneRevision(t *testing.T) {
	got := loadContract(t)
	ipv4, ok := got.Schemas["Vcenter.Guest.Ipv4"]
	if !ok {
		t.Fatal("contract does not carry Vcenter.Guest.Ipv4")
	}
	enum := ipv4.Properties["type"].Enum
	want := []string{"DHCP", "STATIC", "USER_INPUT_REQUIRED"}
	if !reflect.DeepEqual(enum, want) {
		t.Errorf("Vcenter.Guest.Ipv4.type enum = %v, want %v", enum, want)
	}
	for _, v := range enum {
		if v == "DISABLED" {
			t.Error("Vcenter.Guest.Ipv4.type carries DISABLED, which the 9.1.0.0 revision added and 9.0.0.0 does not have")
		}
	}
	if got.SpecVersion != "9.0.0.0" {
		t.Errorf("spec_version = %q, want 9.0.0.0", got.SpecVersion)
	}
}

func loadContract(t *testing.T) *mockvc.Contract {
	t.Helper()
	c, err := mockvc.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	return c
}

func schemaNames(m map[string]mockvc.Schema) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func propNames(s mockvc.Schema) []string {
	out := make([]string, 0, len(s.Properties))
	for k := range s.Properties {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func diffStrings(a, b []string) []string {
	in := map[string]bool{}
	for _, s := range b {
		in[s] = true
	}
	var out []string
	for _, s := range a {
		if !in[s] {
			out = append(out, s)
		}
	}
	if out == nil {
		out = []string{}
	}
	return out
}

func mustIndent(v any) string {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err.Error()
	}
	return string(b)
}
