package verifier_test

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestReferenceDerivedContractAndProvenance(t *testing.T) {
	t.Parallel()

	var contract struct {
		ContractKind string `json:"contract_kind"`
		SourceNotice string `json:"source_notice"`
		Operations   []struct {
			Name         string `json:"name"`
			Method       string `json:"method"`
			PathTemplate string `json:"path_template"`
			SourceURL    string `json:"source_url"`
		} `json:"operations"`
		RequestStatus struct {
			Terminal []string `json:"terminal"`
		} `json:"request_status"`
	}
	decodeJSONFile(t, contractPath, &contract)

	if contract.ContractKind != "reference-documentation-derived REST contract" {
		t.Errorf("contract_kind = %q", contract.ContractKind)
	}
	if !strings.Contains(contract.SourceNotice, "reference documentation") || !strings.Contains(contract.SourceNotice, "not a published specification") {
		t.Errorf("source_notice must plainly distinguish reference documentation from a published specification: %q", contract.SourceNotice)
	}

	wantOperations := []struct {
		Name, Method, Path, URL string
	}{
		{
			"Submit Deployment Action Request",
			"POST",
			"/deployment/api/deployments/{deploymentId}/requests",
			"https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/requests/post/",
		},
		{
			"Get Request",
			"GET",
			"/deployment/api/requests/{requestId}",
			"https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/requests/requestId/get/",
		},
	}
	if len(contract.Operations) != len(wantOperations) {
		t.Fatalf("contract operations = %d, want %d", len(contract.Operations), len(wantOperations))
	}
	for i, want := range wantOperations {
		got := contract.Operations[i]
		if got.Name != want.Name || got.Method != want.Method || got.PathTemplate != want.Path || got.SourceURL != want.URL {
			t.Errorf("operation %d = %+v, want %+v", i, got, want)
		}
	}
	wantTerminal := []string{"APPROVAL_REJECTED", "ABORTED", "SUCCESSFUL", "FAILED"}
	if !reflect.DeepEqual(contract.RequestStatus.Terminal, wantTerminal) {
		t.Errorf("terminal statuses = %v, want %v", contract.RequestStatus.Terminal, wantTerminal)
	}

	var provenance struct {
		Sources []struct {
			URL         string `json:"url"`
			Operation   string `json:"operation"`
			DateFetched string `json:"date_fetched"`
		} `json:"sources"`
	}
	decodeJSONFile(t, "../docs/official_sources.json", &provenance)
	if len(provenance.Sources) != len(wantOperations) {
		t.Fatalf("official sources = %d, want %d", len(provenance.Sources), len(wantOperations))
	}
	for i, want := range wantOperations {
		got := provenance.Sources[i]
		if got.URL != want.URL || got.Operation != want.Name || got.DateFetched != "2026-08-13" {
			t.Errorf("official source %d = %+v", i, got)
		}
	}
}

func decodeJSONFile(t testing.TB, path string, target any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}
