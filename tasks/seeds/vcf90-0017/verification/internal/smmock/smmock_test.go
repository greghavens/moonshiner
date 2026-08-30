package smmock_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"testing"

	"vcf.local/sddchosts/internal/contract"
	"vcf.local/sddchosts/internal/smmock"
)

func start(t *testing.T, opts smmock.Options) (*smmock.Server, *contract.Contract) {
	t.Helper()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	s, err := smmock.New(c, opts)
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(s.Close)
	return s, c
}

func do(t *testing.T, s *smmock.Server, method, path, token string, body any) (int, map[string]any) {
	t.Helper()
	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("encode body: %v", err)
		}
		payload = bytes.NewReader(encoded)
	}
	req, err := http.NewRequest(method, s.URL()+path, payload)
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	out := map[string]any{}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatalf("%s %s: decode %q: %v", method, path, raw, err)
		}
	}
	return resp.StatusCode, out
}

func requiredOnlyHost() map[string]any {
	return map[string]any{
		"fqdn":          "esxi-07.vrack.vsphere.local",
		"username":      "root",
		"password":      "VMw@re123!",
		"storageType":   "VSAN_ESA",
		"networkPoolId": "3f8a1b26-9c04-4d7e-8b53-1a6e2d90c4f7",
	}
}

func TestServesOnlyContractOperations(t *testing.T) {
	s, c := start(t, smmock.Options{})

	if got := len(c.Operations); got != 5 {
		t.Fatalf("contract names %d operations, want 5", got)
	}

	// A path the contract does not name is unmatched and 404s.
	code, _ := do(t, s, http.MethodGet, "/v1/domains", s.AccessToken(), nil)
	if code != http.StatusNotFound {
		t.Errorf("GET /v1/domains: status %d, want 404", code)
	}
	// The right path with the wrong method is also unmatched.
	code, _ = do(t, s, http.MethodDelete, "/v1/hosts", s.AccessToken(), nil)
	if code != http.StatusNotFound {
		t.Errorf("DELETE /v1/hosts: status %d, want 404", code)
	}

	for _, r := range s.Requests() {
		if r.OperationID != "" {
			t.Errorf("request %d matched %q, want unmatched", r.Index, r.OperationID)
		}
	}
	want := []string{"<unmatched GET /v1/domains>", "<unmatched DELETE /v1/hosts>"}
	if got := s.OperationSequence(); !equalStrings(got, want) {
		t.Errorf("operation sequence %v, want %v", got, want)
	}
}

func TestHappyPathSequence(t *testing.T) {
	s, _ := start(t, smmock.Options{
		Validations: []smmock.ValidationState{
			{ExecutionStatus: "IN_PROGRESS", ResultStatus: "UNKNOWN"},
			{ExecutionStatus: "COMPLETED", ResultStatus: "SUCCEEDED"},
		},
		Tasks: []smmock.TaskState{
			{Status: "In Progress"},
			{Status: "Successful"},
		},
	})

	code, tokens := do(t, s, http.MethodPost, "/v1/tokens", "", map[string]any{
		"username": "administrator@vsphere.local", "password": "VMw@re123!",
	})
	if code != http.StatusCreated {
		t.Fatalf("createToken: status %d, want 201", code)
	}
	token, _ := tokens["accessToken"].(string)
	if token != s.AccessToken() {
		t.Fatalf("createToken handed out %q, want %q", token, s.AccessToken())
	}

	hosts := []map[string]any{requiredOnlyHost()}
	code, validation := do(t, s, http.MethodPost, "/v1/hosts/validations", token, hosts)
	if code != http.StatusAccepted {
		t.Fatalf("validateHostCommissionSpec: status %d, want 202", code)
	}
	if validation["executionStatus"] != "IN_PROGRESS" {
		t.Errorf("POST validation executionStatus %v, want IN_PROGRESS", validation["executionStatus"])
	}

	path := "/v1/hosts/validations/" + s.ValidationID()
	if _, v := do(t, s, http.MethodGet, path, token, nil); v["executionStatus"] != "IN_PROGRESS" {
		t.Errorf("poll 1 executionStatus %v, want IN_PROGRESS", v["executionStatus"])
	}
	if _, v := do(t, s, http.MethodGet, path, token, nil); v["executionStatus"] != "COMPLETED" {
		t.Errorf("poll 2 executionStatus %v, want COMPLETED", v["executionStatus"])
	}
	// The last state repeats once the sequence is exhausted.
	if _, v := do(t, s, http.MethodGet, path, token, nil); v["executionStatus"] != "COMPLETED" {
		t.Errorf("poll 3 executionStatus %v, want COMPLETED", v["executionStatus"])
	}

	code, task := do(t, s, http.MethodPost, "/v1/hosts", token, hosts)
	if code != http.StatusAccepted {
		t.Fatalf("commissionHosts: status %d, want 202", code)
	}
	if task["id"] != s.TaskID() {
		t.Errorf("commissionHosts task id %v, want %v", task["id"], s.TaskID())
	}

	taskPath := "/v1/tasks/" + s.TaskID()
	if code, tk := do(t, s, http.MethodGet, taskPath, token, nil); code != http.StatusOK || tk["status"] != "In Progress" {
		t.Errorf("getTask poll 1: status %d %v, want 200 \"In Progress\"", code, tk["status"])
	}
	if _, tk := do(t, s, http.MethodGet, taskPath, token, nil); tk["status"] != "Successful" {
		t.Errorf("getTask poll 2 status %v, want Successful", tk["status"])
	}

	want := []string{
		"createToken", "validateHostCommissionSpec",
		"getHostCommissionValidationByID", "getHostCommissionValidationByID",
		"getHostCommissionValidationByID", "commissionHosts", "getTask", "getTask",
	}
	if got := s.OperationSequence(); !equalStrings(got, want) {
		t.Errorf("operation sequence\n got %v\nwant %v", got, want)
	}
}

func TestAuthorizationEnforced(t *testing.T) {
	s, _ := start(t, smmock.Options{})

	if code, _ := do(t, s, http.MethodPost, "/v1/hosts", "", []map[string]any{requiredOnlyHost()}); code != http.StatusUnauthorized {
		t.Errorf("commissionHosts without a token: status %d, want 401", code)
	}
	if code, _ := do(t, s, http.MethodPost, "/v1/hosts", "not-the-token", []map[string]any{requiredOnlyHost()}); code != http.StatusUnauthorized {
		t.Errorf("commissionHosts with a wrong token: status %d, want 401", code)
	}
	// createToken is exempt.
	if code, _ := do(t, s, http.MethodPost, "/v1/tokens", "", map[string]any{"username": "u", "password": "p"}); code != http.StatusCreated {
		t.Errorf("createToken without a token: status %d, want 201", code)
	}
}

func TestRequestBodyValidation(t *testing.T) {
	s, _ := start(t, smmock.Options{})
	token := s.AccessToken()

	spec := requiredOnlyHost()
	delete(spec, "networkPoolId")
	if code, e := do(t, s, http.MethodPost, "/v1/hosts", token, []map[string]any{spec}); code != http.StatusBadRequest {
		t.Errorf("missing required property: status %d (%v), want 400", code, e["message"])
	}

	spec = requiredOnlyHost()
	spec["notAProperty"] = "x"
	if code, _ := do(t, s, http.MethodPost, "/v1/hosts", token, []map[string]any{spec}); code != http.StatusBadRequest {
		t.Errorf("unknown property: status %d, want 400", code)
	}

	// An object where the contract declares an array.
	if code, _ := do(t, s, http.MethodPost, "/v1/hosts", token, requiredOnlyHost()); code != http.StatusBadRequest {
		t.Errorf("object body: status %d, want 400", code)
	}

	// An empty optional property is accepted here on purpose; catching it is
	// the verifier's job, not the appliance's.
	spec = requiredOnlyHost()
	spec["sshThumbprint"] = ""
	if code, _ := do(t, s, http.MethodPost, "/v1/hosts", token, []map[string]any{spec}); code != http.StatusAccepted {
		t.Errorf("empty optional property: status %d, want 202", code)
	}
}

func TestUnknownIdentifiers(t *testing.T) {
	s, _ := start(t, smmock.Options{})
	token := s.AccessToken()

	if code, _ := do(t, s, http.MethodGet, "/v1/tasks/00000000-0000-0000-0000-000000000000", token, nil); code != http.StatusNotFound {
		t.Errorf("getTask with an unknown id: status %d, want 404", code)
	}
	if code, _ := do(t, s, http.MethodGet, "/v1/hosts/validations/nope", token, nil); code != http.StatusBadRequest {
		t.Errorf("getHostCommissionValidationByID with an unknown id: status %d, want 400", code)
	}
}

func TestFailureInjection(t *testing.T) {
	s, _ := start(t, smmock.Options{
		Failures: map[string]smmock.Failure{
			"getTask": {StatusCode: http.StatusInternalServerError, ErrorCode: "INTERNAL_SERVER_ERROR",
				Message: "the appliance is having a day", Occurrences: 1},
		},
	})
	token := s.AccessToken()
	path := "/v1/tasks/" + s.TaskID()

	code, body := do(t, s, http.MethodGet, path, token, nil)
	if code != http.StatusInternalServerError {
		t.Fatalf("injected getTask: status %d, want 500", code)
	}
	if body["message"] != "the appliance is having a day" {
		t.Errorf("injected message %v", body["message"])
	}
	if code, _ := do(t, s, http.MethodGet, path, token, nil); code != http.StatusOK {
		t.Errorf("second getTask: status %d, want 200 (injection was capped at one occurrence)", code)
	}
}

func TestFailureInjectionRejectsUnknownOperation(t *testing.T) {
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	if _, err := smmock.New(c, smmock.Options{
		Failures: map[string]smmock.Failure{"deleteDomain": {StatusCode: 500}},
	}); err == nil {
		t.Fatal("smmock.New accepted a failure for an operation outside the contract")
	}
}

func TestRequestLogRecordsWireDetail(t *testing.T) {
	s, _ := start(t, smmock.Options{})
	hosts := []map[string]any{requiredOnlyHost()}
	do(t, s, http.MethodPost, "/v1/hosts/validations", s.AccessToken(), hosts)

	recorded := s.RequestsFor("validateHostCommissionSpec")
	if len(recorded) != 1 {
		t.Fatalf("recorded %d validateHostCommissionSpec requests, want 1", len(recorded))
	}
	r := recorded[0]
	if r.Method != http.MethodPost || r.Path != "/v1/hosts/validations" || r.RawQuery != "" {
		t.Errorf("recorded %s %s?%s", r.Method, r.Path, r.RawQuery)
	}
	if got := r.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("recorded Content-Type %q", got)
	}
	keys, err := r.ItemKeys()
	if err != nil {
		t.Fatalf("item keys: %v", err)
	}
	want := []string{"fqdn", "networkPoolId", "password", "storageType", "username"}
	if len(keys) != 1 || !equalStrings(keys[0], want) {
		t.Errorf("recorded item keys %v, want [%v]", keys, want)
	}

	// Path parameters are resolved against the contract's path template.
	do(t, s, http.MethodGet, "/v1/hosts/validations/"+s.ValidationID(), s.AccessToken(), nil)
	polls := s.RequestsFor("getHostCommissionValidationByID")
	if len(polls) != 1 {
		t.Fatalf("recorded %d polls, want 1", len(polls))
	}
	if got := polls[0].PathParams["id"]; got != s.ValidationID() {
		t.Errorf("recorded path parameter id %q, want %q", got, s.ValidationID())
	}
	if polls[0].HasBody() {
		t.Errorf("recorded a body on a GET: %q", polls[0].Body)
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
