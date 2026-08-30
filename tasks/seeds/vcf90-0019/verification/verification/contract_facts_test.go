package verification

import (
	"encoding/hex"
	"net/url"
	"sort"
	"strings"
	"testing"
)

// Digests of the normalised facts that docs/contract.json must carry. They are
// pinned rather than spelled out so that the contract has to be derived from
// the specification document itself.
const (
	wantSpecRefDigest       = "db9acf6d29f2cbe05c0c37ca5f9392e9fda715203bb1eccfb3f855fa525458e1"
	wantSpecInfoDigest      = "4ebe3a7d107a5005daaf019d61a5bc2527988f260165c63af8904c86abe918be"
	wantOperationsDigest    = "7318f64f75e461bf879dfe90a2b2f2ef42aa5d73e9369cea08c89c5f0ba8a1ac"
	wantSummariesDigest     = "bd2de734ca41f9cb2b0b9bbdf0b880c836a462151536f147324bdaf3482deb3b"
	wantHostsQueryDigest    = "79d53adf265267efd8ff8f354d5a3740dd19c69026483c3991a14d25d48a4e1e"
	wantRequestBodiesDigest = "27f3ec4009435d01915d64b3d138a71efcf25120645f45bb9bf86b411c59834e"
	wantPaginationDigest    = "9a28b880d16b3dff125658fbcb30bcefe790f079fd58840dce4bb03b119fe295"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
)

func loadContractT(t *testing.T) *Contract {
	t.Helper()
	c, err := LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load %s: %v", contractPath, err)
	}
	return c
}

func TestContractFactsMatchSpecification(t *testing.T) {
	c := loadContractT(t)

	cases := []struct {
		name    string
		label   string
		payload string
		want    string
	}{
		{"spec reference", "spec-ref", c.SpecRefPayload(), wantSpecRefDigest},
		{"spec identity", "spec-info", c.SpecInfoPayload(), wantSpecInfoDigest},
		{"operations", "operations", c.OperationsPayload(), wantOperationsDigest},
		{"operation summaries", "summaries", c.SummariesPayload(), wantSummariesDigest},
		{"getHosts query parameters", "get-hosts-query", c.QueryPayload("getHosts"), wantHostsQueryDigest},
		{"request bodies", "request-bodies", c.RequestBodiesPayload(), wantRequestBodiesDigest},
		{"pagination", "pagination", c.PaginationPayload(), wantPaginationDigest},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Digest(tc.label, tc.payload)
			if got != tc.want {
				t.Errorf("%s does not match the pinned specification facts\ndigest of contract.json: %s\nnormalised value derived from contract.json:\n%s",
					tc.name, got, tc.payload)
			}
		})
	}
}

func TestContractNamesExactlyTheTwoOperations(t *testing.T) {
	c := loadContractT(t)

	if len(c.Operations) != 2 {
		t.Fatalf("contract must name exactly 2 operations, got %d", len(c.Operations))
	}
	got := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		got = append(got, op.OperationID)
	}
	sort.Strings(got)
	want := []string{"createToken", "getHosts"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("contract operations = %v, want %v", got, want)
		}
	}

	// The paged operation is the only one carrying query parameters, and the
	// token operation is the only one carrying a request body.
	for _, op := range c.Operations {
		if op.Method == "" || op.Method != strings.ToUpper(op.Method) {
			t.Errorf("%s method = %q, want upper case", op.OperationID, op.Method)
		}
		for i := 1; i < len(op.ErrorStatuses); i++ {
			if op.ErrorStatuses[i-1] >= op.ErrorStatuses[i] {
				t.Errorf("%s errorStatuses = %v, want strictly ascending values", op.OperationID, op.ErrorStatuses)
				break
			}
		}
		switch op.OperationID {
		case "getHosts":
			if len(op.QueryParameters) == 0 {
				t.Errorf("getHosts must list its query parameters")
			}
			if op.RequestBody != nil {
				t.Errorf("getHosts has no request body in the specification")
			}
		default:
			if op.RequestBody == nil {
				t.Errorf("%s must describe its request body", op.OperationID)
			}
			if len(op.QueryParameters) != 0 {
				t.Errorf("%s has no query parameters in the specification", op.OperationID)
			}
		}
		if strings.TrimSpace(op.Summary) == "" {
			t.Errorf("%s must carry the summary the document gives it", op.OperationID)
		}
		if len(op.ErrorStatuses) == 0 {
			t.Errorf("%s must list the non-2xx responses the document declares", op.OperationID)
		}
		for _, s := range op.ErrorStatuses {
			if s < 400 {
				t.Errorf("%s errorStatuses = %v, want only the non-2xx responses", op.OperationID, op.ErrorStatuses)
				break
			}
		}
	}
}

func TestCommitIsAFullLowerCaseSHA(t *testing.T) {
	c := loadContractT(t)
	if len(c.Spec.Commit) != 40 || c.Spec.Commit != strings.ToLower(c.Spec.Commit) {
		t.Fatalf("spec commit = %q, want a 40-character lower-case commit sha", c.Spec.Commit)
	}
	if _, err := hex.DecodeString(c.Spec.Commit); err != nil {
		t.Fatalf("spec commit = %q, want hexadecimal: %v", c.Spec.Commit, err)
	}
}

func TestPaginationIsDrivenByTheOperationsOwnParameters(t *testing.T) {
	c := loadContractT(t)

	if c.Pagination.OperationID != "getHosts" {
		t.Fatalf("pagination operationId = %q, want getHosts", c.Pagination.OperationID)
	}
	op, ok := c.Find("getHosts")
	if !ok {
		t.Fatal("contract does not name getHosts")
	}
	named := make(map[string]bool, len(op.QueryParameters))
	for _, p := range op.QueryParameters {
		named[p.Name] = true
	}
	for _, p := range []struct{ what, name string }{
		{"pageParameter", c.Pagination.PageParameter},
		{"sizeParameter", c.Pagination.SizeParameter},
	} {
		if p.name == "" {
			t.Errorf("pagination %s is empty", p.what)
			continue
		}
		if !named[p.name] {
			t.Errorf("pagination %s = %q, which getHosts does not declare as a query parameter in this revision of the specification", p.what, p.name)
		}
	}
	if c.Pagination.PageParameter == c.Pagination.SizeParameter {
		t.Errorf("pagination pageParameter and sizeParameter are both %q", c.Pagination.PageParameter)
	}
	for _, f := range []struct{ what, name string }{
		{"elementsField", c.Pagination.ElementsField},
		{"metadataField", c.Pagination.MetadataField},
		{"pageNumberField", c.Pagination.PageNumberField},
		{"pageSizeField", c.Pagination.PageSizeField},
		{"totalElementsField", c.Pagination.TotalElementsField},
		{"totalPagesField", c.Pagination.TotalPagesField},
	} {
		if f.name == "" {
			t.Errorf("pagination %s is empty", f.what)
		}
	}
}

func TestOfficialSourcesRecordTheSpecification(t *testing.T) {
	c := loadContractT(t)

	s, err := LoadSources(sourcesPath)
	if err != nil {
		t.Fatalf("load %s: %v", sourcesPath, err)
	}
	if len(s.Sources) != 1 {
		t.Fatalf("expected exactly one recorded source, got %d", len(s.Sources))
	}
	src := s.Sources[0]

	if src.Repository != c.Spec.Repository {
		t.Errorf("source repository = %q, contract repository = %q", src.Repository, c.Spec.Repository)
	}
	if src.Path != c.Spec.Path {
		t.Errorf("source path = %q, contract path = %q", src.Path, c.Spec.Path)
	}
	if src.Tag != c.Spec.Tag {
		t.Errorf("source tag = %q, contract tag = %q", src.Tag, c.Spec.Tag)
	}
	if !strings.EqualFold(src.Commit, c.Spec.Commit) {
		t.Errorf("source commit = %q, contract commit = %q", src.Commit, c.Spec.Commit)
	}
	if src.License != "Apache-2.0" {
		t.Errorf("source license = %q, want %q", src.License, "Apache-2.0")
	}
	if strings.TrimSpace(src.Title) == "" {
		t.Errorf("source title must not be empty")
	}

	u, err := url.Parse(src.URL)
	if err != nil {
		t.Fatalf("source url %q: %v", src.URL, err)
	}
	if u.Scheme != "https" {
		t.Errorf("source url scheme = %q, want https", u.Scheme)
	}
	switch u.Host {
	case "github.com", "raw.githubusercontent.com":
	default:
		t.Errorf("source url host = %q, want the repository host", u.Host)
	}
	if !strings.Contains(u.Path, strings.ToLower(c.Spec.Commit)) {
		t.Errorf("source url %q must pin the commit, not a moving branch or tag", src.URL)
	}
	if !strings.HasSuffix(u.Path, c.Spec.Path) {
		t.Errorf("source url %q must point at %q", src.URL, c.Spec.Path)
	}
	if u.RawQuery != "" || u.Fragment != "" {
		t.Errorf("source url %q must end with the document path without a query or fragment", src.URL)
	}
	if src.Commit != c.Spec.Commit {
		t.Errorf("source commit = %q, want the exact lower-case contract commit %q", src.Commit, c.Spec.Commit)
	}
	if !strings.HasPrefix(u.Path, "/"+c.Spec.Repository+"/") {
		t.Errorf("source url = %q, want a link into %s", src.URL, c.Spec.Repository)
	}

	gotOps := append([]string(nil), src.OperationIDs...)
	sort.Strings(gotOps)
	wantOps := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		wantOps = append(wantOps, op.OperationID)
	}
	sort.Strings(wantOps)
	if len(gotOps) != len(wantOps) {
		t.Fatalf("source operationIds = %v, want %v", gotOps, wantOps)
	}
	for i := range wantOps {
		if gotOps[i] != wantOps[i] {
			t.Fatalf("source operationIds = %v, want %v", gotOps, wantOps)
		}
	}
}
