package vcmock_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"testing"

	"vcf.local/vcenterchange/internal/contract"
	"vcf.local/vcenterchange/internal/vcmock"
)

// do issues a raw request against the appliance with the session header set.
func do(t *testing.T, s *vcmock.Server, method, url string, body []byte) (*http.Response, []byte) {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rdr)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("vmware-api-session-id", s.SessionID)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	got, _ := io.ReadAll(resp.Body)
	return resp, got
}

func TestServesOnlyContractOperations(t *testing.T) {
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	s := vcmock.New(t, vcmock.Config{})

	// Every operation the contract names is routable.
	if got, want := len(c.OperationIDs()), 6; got != want {
		t.Fatalf("contract names %d operations, want %d", got, want)
	}

	// A path the contract does not name is refused and still recorded.
	resp, _ := do(t, s, http.MethodGet, s.URL+"/api/vcenter/vm/vm-101/hardware/ethernet", nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed operation: status = %d, want 404", resp.StatusCode)
	}
	reqs := s.Requests()
	if len(reqs) != 1 {
		t.Fatalf("recorded %d requests, want 1", len(reqs))
	}
	if reqs[0].OperationID != "" {
		t.Fatalf("unnamed operation recorded as %q, want an empty operationId", reqs[0].OperationID)
	}
}

func TestActionQueryParameterSeparatesPowerOperations(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})

	resp, body := do(t, s, http.MethodGet, s.URL+"/api/vcenter/vm/vm-101/power", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Power_get: status = %d body = %s", resp.StatusCode, body)
	}
	var info struct {
		State string `json:"state"`
	}
	if err := json.Unmarshal(body, &info); err != nil {
		t.Fatalf("Power_get body: %v", err)
	}
	if info.State != vcmock.PoweredOn {
		t.Fatalf("Power_get state = %q, want %q", info.State, vcmock.PoweredOn)
	}

	resp, body = do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/power?action=stop", nil)
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("Power_stop: status = %d body = %s", resp.StatusCode, body)
	}
	if got := s.PowerState(); got != vcmock.PoweredOff {
		t.Fatalf("power state = %q, want %q", got, vcmock.PoweredOff)
	}

	// A second stop is refused with the error type the specification declares
	// for that status on this operation.
	resp, body = do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/power?action=stop", nil)
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("repeat Power_stop: status = %d, want 400", resp.StatusCode)
	}
	var apiErr struct {
		ErrorType string `json:"error_type"`
	}
	if err := json.Unmarshal(body, &apiErr); err != nil {
		t.Fatalf("error body: %v", err)
	}
	if apiErr.ErrorType != "ALREADY_IN_DESIRED_STATE" {
		t.Fatalf("error_type = %q, want ALREADY_IN_DESIRED_STATE", apiErr.ErrorType)
	}

	// A POST to the shared path with no action matches no operation.
	resp, _ = do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/power", nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("POST power with no action: status = %d, want 404", resp.StatusCode)
	}

	if got, want := s.OperationIDs(), []string{
		"Vcenter.Vm.Power_get",
		"Vcenter.Vm.Power_stop",
		"Vcenter.Vm.Power_stop",
		"",
	}; !equalStrings(got, want) {
		t.Fatalf("operation log = %v, want %v", got, want)
	}
}

func TestRejectsUndeclaredAndMissingProperties(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{InitialPowerState: vcmock.PoweredOff})

	// A property the schema does not declare is refused.
	resp, body := do(t, s, http.MethodPatch, s.URL+"/api/vcenter/vm/vm-101/hardware/memory",
		[]byte(`{"size_MiB":8192}`))
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("undeclared property: status = %d body = %s, want 400", resp.StatusCode, body)
	}

	// The spec-derived spelling is accepted.
	resp, body = do(t, s, http.MethodPatch, s.URL+"/api/vcenter/vm/vm-101/hardware/memory",
		[]byte(`{"size_mib":8192}`))
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("size_mib: status = %d body = %s, want 204", resp.StatusCode, body)
	}

	// A nested object is validated too: ScsiAddressSpec requires bus.
	resp, body = do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/hardware/disk",
		[]byte(`{"type":"SCSI","scsi":{"unit":0}}`))
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("scsi without bus: status = %d body = %s, want 400", resp.StatusCode, body)
	}

	resp, body = do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/hardware/disk",
		[]byte(`{"type":"SCSI","scsi":{"bus":0},"new_vmdk":{"capacity":42949672960}}`))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("disk create: status = %d body = %s, want 201", resp.StatusCode, body)
	}
	var diskID string
	if err := json.Unmarshal(body, &diskID); err != nil {
		t.Fatalf("Disk_create returns a bare JSON string: %v (body %s)", err, body)
	}
	if diskID != "2000" {
		t.Fatalf("disk id = %q, want 2000", diskID)
	}
}

func TestRequiresSessionHeader(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	req, err := http.NewRequest(http.MethodGet, s.URL+"/api/vcenter/vm/vm-101/power", nil)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("missing session header: status = %d, want 401", resp.StatusCode)
	}
}

func TestInjectedFailure(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{
		InitialPowerState: vcmock.PoweredOff,
		Failures: []vcmock.Failure{{
			Operation:  "Vcenter.Vm.Hardware.Disk_create",
			Occurrence: 2,
			Status:     http.StatusBadRequest,
			ErrorType:  "UNABLE_TO_ALLOCATE_RESOURCE",
			Message:    "The datastore has insufficient free space for the requested disk.",
		}},
	})
	spec := []byte(`{"type":"SCSI","new_vmdk":{"capacity":1073741824}}`)

	if resp, body := do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/hardware/disk", spec); resp.StatusCode != http.StatusCreated {
		t.Fatalf("first disk: status = %d body = %s, want 201", resp.StatusCode, body)
	}
	resp, body := do(t, s, http.MethodPost, s.URL+"/api/vcenter/vm/vm-101/hardware/disk", spec)
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("second disk: status = %d, want 400", resp.StatusCode)
	}
	var apiErr struct {
		ErrorType string `json:"error_type"`
		Messages  []struct {
			DefaultMessage string `json:"default_message"`
		} `json:"messages"`
	}
	if err := json.Unmarshal(body, &apiErr); err != nil {
		t.Fatalf("error body: %v", err)
	}
	if apiErr.ErrorType != "UNABLE_TO_ALLOCATE_RESOURCE" {
		t.Fatalf("error_type = %q", apiErr.ErrorType)
	}
	if len(apiErr.Messages) != 1 || apiErr.Messages[0].DefaultMessage == "" {
		t.Fatalf("error carries no default_message: %s", body)
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
