// Package verify holds the protected wire-shape verification for the
// hostcommission package. It asserts the exact bytes, headers and URLs the
// client puts on the wire against docs/contract.json, driving it only against
// the loopback mock in internal/smmock. No live VMware endpoint is contacted
// and no network access is required.
//
// Do not edit this package. It is replaced wholesale during grading.
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"vcf.local/sddchosts/hostcommission"
	"vcf.local/sddchosts/internal/contract"
	"vcf.local/sddchosts/internal/smmock"
)

const (
	adminUser     = "administrator@vsphere.local"
	adminPassword = "VMw@re1!VMw@re1!"
	networkPoolID = "3f8a1b26-9c04-4d7e-8b53-1a6e2d90c4f7"
)

// requiredOnlyHost sets exactly the five properties HostCommissionSpec marks
// required and leaves every optional one unset.
func requiredOnlyHost() hostcommission.HostSpec {
	return hostcommission.HostSpec{
		FQDN:          "esxi-07.vrack.vsphere.local",
		Username:      "root",
		Password:      "EsxiP@ssw0rd!",
		StorageType:   "VSAN_ESA",
		NetworkPoolID: networkPoolID,
	}
}

func requiredOnlyWire() map[string]any {
	return map[string]any{
		"fqdn":          "esxi-07.vrack.vsphere.local",
		"username":      "root",
		"password":      "EsxiP@ssw0rd!",
		"storageType":   "VSAN_ESA",
		"networkPoolId": networkPoolID,
	}
}

// fullyPopulatedHost sets every property the schema allows. vVol storage is the
// one configuration in which vvolStorageProtocolType applies.
func fullyPopulatedHost() hostcommission.HostSpec {
	return hostcommission.HostSpec{
		FQDN:                    "esxi-08.vrack.vsphere.local",
		Username:                "root",
		Password:                "EsxiP@ssw0rd!",
		StorageType:             "VVOL",
		NetworkPoolID:           networkPoolID,
		NetworkPoolName:         "np-mgmt-01",
		VVolStorageProtocolType: "ISCSI",
		SSHThumbprint:           "SHA256:0GHRe4XDbBpQ0lUmA7t7cCmuVo9FQCa4JQMd/9BJcCw",
		SSLThumbprint:           "3A:4C:9E:11:7B:2D:88:05:F6:1C:E0:44:97:B3:2A:6D:5F:8C:01:E9",
	}
}

func fullyPopulatedWire() map[string]any {
	return map[string]any{
		"fqdn":                    "esxi-08.vrack.vsphere.local",
		"username":                "root",
		"password":                "EsxiP@ssw0rd!",
		"storageType":             "VVOL",
		"networkPoolId":           networkPoolID,
		"networkPoolName":         "np-mgmt-01",
		"vvolStorageProtocolType": "ISCSI",
		"sshThumbprint":           "SHA256:0GHRe4XDbBpQ0lUmA7t7cCmuVo9FQCa4JQMd/9BJcCw",
		"sslThumbprint":           "3A:4C:9E:11:7B:2D:88:05:F6:1C:E0:44:97:B3:2A:6D:5F:8C:01:E9",
	}
}

func loadContract(t *testing.T) *contract.Contract {
	t.Helper()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load docs/contract.json: %v", err)
	}
	return c
}

// harness starts a mock and a client wired to it.
type harness struct {
	contract *contract.Contract
	mock     *smmock.Server
	client   *hostcommission.Client
}

func newHarness(t *testing.T, opts smmock.Options, tune func(*hostcommission.Config)) *harness {
	t.Helper()
	c := loadContract(t)
	mock, err := smmock.New(c, opts)
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(mock.Close)
	assertLoopback(t, mock.URL())

	cfg := hostcommission.Config{
		BaseURL:         mock.URL(),
		Username:        adminUser,
		Password:        adminPassword,
		PollInterval:    time.Millisecond,
		MaxPollAttempts: 10,
	}
	if tune != nil {
		tune(&cfg)
	}
	client, err := hostcommission.New(cfg)
	if err != nil {
		t.Fatalf("hostcommission.New: %v", err)
	}
	return &harness{contract: c, mock: mock, client: client}
}

// assertLoopback proves the client under test is only ever pointed at 127.0.0.1.
func assertLoopback(t *testing.T, rawURL string) {
	t.Helper()
	u, err := url.Parse(rawURL)
	if err != nil {
		t.Fatalf("parse mock URL %q: %v", rawURL, err)
	}
	ip := net.ParseIP(u.Hostname())
	if ip == nil || !ip.IsLoopback() {
		t.Fatalf("mock URL %q is not a loopback address", rawURL)
	}
}

func (h *harness) run(t *testing.T, hosts ...hostcommission.HostSpec) (*hostcommission.Result, error) {
	t.Helper()
	return h.client.CommissionHosts(context.Background(), hosts)
}

func (h *harness) requests() []smmock.Request { return h.mock.Requests() }

func (h *harness) sequence() []string { return h.mock.OperationSequence() }

func (h *harness) countOf(operationID string) int {
	return len(h.mock.RequestsFor(operationID))
}

// cancelAtEOFTransport cancels a context only after the selected response body
// has been completely delivered to the client. This lets cancellation tests
// exercise the wait between polls without a deadline that might fire during
// the setup requests on a slow machine.
type cancelAtEOFTransport struct {
	base   http.RoundTripper
	path   string
	cancel context.CancelFunc
	once   sync.Once
}

func (t *cancelAtEOFTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := t.base.RoundTrip(req)
	if err == nil && req.URL.Path == t.path {
		resp.Body = &cancelAtEOFBody{ReadCloser: resp.Body, cancel: func() {
			t.once.Do(t.cancel)
		}}
	}
	return resp, err
}

type cancelAtEOFBody struct {
	io.ReadCloser
	cancel func()
}

func (b *cancelAtEOFBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	if errors.Is(err, io.EOF) {
		b.cancel()
	}
	return n, err
}

func (b *cancelAtEOFBody) Close() error {
	b.cancel()
	return b.ReadCloser.Close()
}

// only returns the single recorded request for an operation.
func (h *harness) only(t *testing.T, operationID string) smmock.Request {
	t.Helper()
	got := h.mock.RequestsFor(operationID)
	if len(got) != 1 {
		t.Fatalf("%s was called %d times, want exactly 1 (sequence: %v)",
			operationID, len(got), h.sequence())
	}
	return got[0]
}

// assertOnlyContractOperations proves the client never touched a path outside
// the contract. The mock records an unmatched request with an empty
// OperationID.
func (h *harness) assertOnlyContractOperations(t *testing.T) {
	t.Helper()
	for _, r := range h.requests() {
		if r.OperationID == "" {
			t.Errorf("request %d %s %s matched no operation in docs/contract.json",
				r.Index, r.Method, r.Path)
		}
	}
}

// assertRequestConventions applies the contract's shared header and URL rules
// to every recorded request.
func (h *harness) assertRequestConventions(t *testing.T) {
	t.Helper()
	conv := h.contract.RequestConventions
	auth := h.contract.Authorization
	wantAuth := auth.HeaderValue(h.mock.AccessToken())

	for _, r := range h.requests() {
		if r.OperationID == "" {
			continue
		}
		op := h.contract.MustOperation(r.OperationID)

		if got := r.Header.Get("Accept"); got != conv.AcceptHeader {
			t.Errorf("%s: Accept %q, want %q", r.OperationID, got, conv.AcceptHeader)
		}

		switch {
		case op.RequestBody != nil:
			if !r.HasBody() {
				t.Errorf("%s: sent no body, but the contract declares a required %s body",
					r.OperationID, op.RequestBody.ContentType)
			}
			ct := r.Header.Get("Content-Type")
			if mediaType(ct) != op.RequestBody.ContentType {
				t.Errorf("%s: Content-Type %q, want %q", r.OperationID, ct, op.RequestBody.ContentType)
			}
		default:
			if conv.GetRequestsHaveNoBody && r.HasBody() {
				t.Errorf("%s: %s carried a body %q; the contract declares no request body",
					r.OperationID, r.Method, r.Body)
			}
			if conv.ContentTypeOnBodiedRequestsOnly && r.Header.Get("Content-Type") != "" {
				t.Errorf("%s: bodyless request carried Content-Type %q",
					r.OperationID, r.Header.Get("Content-Type"))
			}
		}

		if conv.GetRequestsHaveNoQueryString && r.RawQuery != "" {
			t.Errorf("%s: URL carried a query string %q; the contract declares no query parameters",
				r.OperationID, r.RawQuery)
		}

		got := r.Header.Get(auth.HeaderName)
		switch {
		case auth.RequiresAuthorization(r.OperationID):
			if got != wantAuth {
				t.Errorf("%s: %s header %q, want %q", r.OperationID, auth.HeaderName, got, wantAuth)
			}
		case auth.IsExempt(r.OperationID):
			if got != "" {
				t.Errorf("%s: is exempt from authorization but sent %s: %q",
					r.OperationID, auth.HeaderName, got)
			}
		}
	}
}

func mediaType(header string) string {
	return strings.TrimSpace(strings.Split(header, ";")[0])
}

// assertNoNullValues proves the client expressed "unset" by omitting a property
// rather than by sending a null.
func assertNoNullValues(t *testing.T, r smmock.Request) {
	t.Helper()
	var walk func(prefix string, v any)
	walk = func(prefix string, v any) {
		switch typed := v.(type) {
		case nil:
			t.Errorf("%s: %s is null; an unset optional property must be omitted",
				r.OperationID, prefix)
		case map[string]any:
			for k, child := range typed {
				walk(prefix+"."+k, child)
			}
		case []any:
			for i, child := range typed {
				walk(fmt.Sprintf("%s[%d]", prefix, i), child)
			}
		}
	}
	var decoded any
	if err := json.Unmarshal(r.Body, &decoded); err != nil {
		t.Fatalf("%s: body is not JSON: %v", r.OperationID, err)
	}
	walk("body", decoded)
}

// assertHostSpecArray checks a recorded request body against the exact JSON the
// contract calls for: an array of objects carrying precisely the expected keys
// and values, with no extra empty-valued optional property.
func assertHostSpecArray(t *testing.T, r smmock.Request, want []map[string]any) {
	t.Helper()
	got, err := r.DecodeArray()
	if err != nil {
		t.Fatalf("%v", err)
	}
	if len(got) != len(want) {
		t.Fatalf("%s: body carried %d HostCommissionSpecs, want %d: %s",
			r.OperationID, len(got), len(want), r.Body)
	}
	for i := range want {
		if !reflect.DeepEqual(got[i], want[i]) {
			t.Errorf("%s: HostCommissionSpec[%d] wire shape mismatch\n got %s\nwant %s\n(unset optional properties must be absent, not empty)",
				r.OperationID, i, mustJSON(got[i]), mustJSON(want[i]))
		}
	}
	assertNoNullValues(t, r)
}

func mustJSON(v any) string {
	out, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("<unencodable: %v>", err)
	}
	return string(out)
}

func canonicalJSON(t *testing.T, raw []byte) string {
	t.Helper()
	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("body is not JSON: %v", err)
	}
	return mustJSON(decoded)
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

func canonicalSet(values []string) []string {
	out := make([]string, 0, len(values))
	seen := map[string]bool{}
	for _, v := range values {
		c := contract.Canonical(v)
		if seen[c] {
			continue
		}
		seen[c] = true
		out = append(out, c)
	}
	sort.Strings(out)
	return out
}

// ---------------------------------------------------------------------------
// The contract itself
// ---------------------------------------------------------------------------

// TestContractProvenance pins the contract to the 9.0.0.0 revision of the SDDC
// Manager specification and to the five operations this flow uses.
func TestContractProvenance(t *testing.T) {
	c := loadContract(t)

	if got, want := c.API.Version, "9.0.0.0"; got != want {
		t.Errorf("contract api.version %q, want %q", got, want)
	}
	if got, want := c.API.SpecPath, "specifications/sddc-manager/sddc-manager-openapi.json"; got != want {
		t.Errorf("contract api.specPath %q, want %q", got, want)
	}
	if got, want := c.API.RepositoryCommitSha, "85151f6b1bb58f13b6ac0304bfec53904bea085f"; got != want {
		t.Errorf("contract api.repositoryCommitSha %q, want %q", got, want)
	}

	wantOps := []string{
		"commissionHosts", "createToken", "getHostCommissionValidationByID",
		"getTask", "validateHostCommissionSpec",
	}
	if got := c.OperationIDs(); !equalStrings(got, wantOps) {
		t.Errorf("contract operations %v, want %v", got, wantOps)
	}

	wantPaths := map[string]string{
		"createToken":                     "POST /v1/tokens",
		"validateHostCommissionSpec":      "POST /v1/hosts/validations",
		"getHostCommissionValidationByID": "GET /v1/hosts/validations/{id}",
		"commissionHosts":                 "POST /v1/hosts",
		"getTask":                         "GET /v1/tasks/{id}",
	}
	for id, want := range wantPaths {
		op := c.MustOperation(id)
		if got := op.Method + " " + op.Path; got != want {
			t.Errorf("%s: %q, want %q", id, got, want)
		}
	}

	spec, ok := c.Schemas["HostCommissionSpec"]
	if !ok {
		t.Fatal("contract does not describe HostCommissionSpec")
	}
	wantRequired := []string{"fqdn", "networkPoolId", "password", "storageType", "username"}
	if got := append([]string(nil), spec.RequiredProperties...); !equalStrings(sorted(got), wantRequired) {
		t.Errorf("HostCommissionSpec required %v, want %v", got, wantRequired)
	}
	wantOptional := []string{"networkPoolName", "sshThumbprint", "sslThumbprint", "vvolStorageProtocolType"}
	if got := sorted(append([]string(nil), spec.OptionalProperties...)); !equalStrings(got, wantOptional) {
		t.Errorf("HostCommissionSpec optional %v, want %v", got, wantOptional)
	}
}

// TestContractStatusVocabulariesAre90 guards against a contract reconciled
// against the 9.1.0.0 revision of the same file, which widens Task.status with
// QUEUED and TIMED_OUT.
func TestContractStatusVocabulariesAre90(t *testing.T) {
	c := loadContract(t)

	task, err := c.Vocabulary("task")
	if err != nil {
		t.Fatalf("%v", err)
	}
	wantTask := []string{
		"CANCELLED", "COMPLETED_WITH_WARNING", "FAILED", "IN_PROGRESS",
		"PENDING", "SKIPPED", "SUCCESSFUL",
	}
	if got := canonicalSet(task.RawValues); !equalStrings(got, wantTask) {
		t.Errorf("Task.status vocabulary %v, want the 9.0.0.0 set %v", got, wantTask)
	}
	for _, forbidden := range []string{"QUEUED", "TIMED_OUT"} {
		for _, v := range canonicalSet(task.RawValues) {
			if v == forbidden {
				t.Errorf("Task.status vocabulary contains %q, which the 9.0.0.0 revision does not define", forbidden)
			}
		}
	}
	if got := sorted(append([]string(nil), task.Terminal...)); !equalStrings(got,
		[]string{"CANCELLED", "COMPLETED_WITH_WARNING", "FAILED", "SKIPPED", "SUCCESSFUL"}) {
		t.Errorf("Task.status terminal set %v", got)
	}

	exec, err := c.Vocabulary("validationExecution")
	if err != nil {
		t.Fatalf("%v", err)
	}
	wantExec := []string{
		"CANCELLATION_IN_PROGRESS", "CANCELLED", "COMPLETED", "FAILED",
		"IN_PROGRESS", "SKIPPED", "UNKNOWN",
	}
	if got := canonicalSet(exec.RawValues); !equalStrings(got, wantExec) {
		t.Errorf("Validation.executionStatus vocabulary %v, want %v", got, wantExec)
	}
	if exec.IsTerminal("CANCELLATION_IN_PROGRESS") {
		t.Error("CANCELLATION_IN_PROGRESS must be non-terminal")
	}
}

func sorted(s []string) []string {
	sort.Strings(s)
	return s
}

// ---------------------------------------------------------------------------
// The flow
// ---------------------------------------------------------------------------

// TestFlowSequenceAndURLs walks the whole flow and checks the operation order,
// the exact URLs, and every shared header and URL convention.
func TestFlowSequenceAndURLs(t *testing.T) {
	h := newHarness(t, smmock.Options{
		Validations: []smmock.ValidationState{
			{ExecutionStatus: "IN_PROGRESS", ResultStatus: "UNKNOWN"},
			{ExecutionStatus: "COMPLETED", ResultStatus: "SUCCEEDED"},
		},
		Tasks: []smmock.TaskState{
			{Status: "IN_PROGRESS"},
			{Status: "SUCCESSFUL"},
		},
	}, nil)

	result, err := h.run(t, requiredOnlyHost())
	if err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}

	h.assertOnlyContractOperations(t)
	h.assertRequestConventions(t)

	want := []string{
		"createToken",
		"validateHostCommissionSpec",
		"getHostCommissionValidationByID",
		"getHostCommissionValidationByID",
		"commissionHosts",
		"getTask",
		"getTask",
	}
	if got := h.sequence(); !equalStrings(got, want) {
		t.Errorf("operation sequence\n got %v\nwant %v", got, want)
	}

	wantPaths := map[string]string{
		"createToken":                "/v1/tokens",
		"validateHostCommissionSpec": "/v1/hosts/validations",
		"commissionHosts":            "/v1/hosts",
	}
	for id, wantPath := range wantPaths {
		if got := h.only(t, id).Path; got != wantPath {
			t.Errorf("%s: path %q, want %q", id, got, wantPath)
		}
	}
	for _, r := range h.mock.RequestsFor("getHostCommissionValidationByID") {
		if want := "/v1/hosts/validations/" + h.mock.ValidationID(); r.Path != want {
			t.Errorf("getHostCommissionValidationByID: path %q, want %q", r.Path, want)
		}
	}
	for _, r := range h.mock.RequestsFor("getTask") {
		if want := "/v1/tasks/" + h.mock.TaskID(); r.Path != want {
			t.Errorf("getTask: path %q, want %q", r.Path, want)
		}
	}

	if result.ValidationPolls != 2 {
		t.Errorf("Result.ValidationPolls %d, want 2", result.ValidationPolls)
	}
	if result.TaskPolls != 2 {
		t.Errorf("Result.TaskPolls %d, want 2", result.TaskPolls)
	}
	if result.Validation.ExecutionStatus != "COMPLETED" || result.Validation.ResultStatus != "SUCCEEDED" {
		t.Errorf("Result.Validation %+v, want the terminal COMPLETED/SUCCEEDED poll", result.Validation)
	}
	if result.Task.Status != "SUCCESSFUL" || result.Task.ID != h.mock.TaskID() {
		t.Errorf("Result.Task %+v, want the terminal SUCCESSFUL poll of task %s", result.Task, h.mock.TaskID())
	}
	if result.Task.CompletionTimestamp == "" {
		t.Error("Result.Task.CompletionTimestamp is empty; the terminal poll carried one")
	}
}

// TestBaseURLWithTrailingSlash checks that the appliance root is joined to the
// contract paths without doubling the separator.
func TestBaseURLWithTrailingSlash(t *testing.T) {
	h := newHarness(t, smmock.Options{}, func(cfg *hostcommission.Config) {
		cfg.BaseURL += "/"
	})
	if _, err := h.run(t, requiredOnlyHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	h.assertOnlyContractOperations(t)
	if got := h.only(t, "commissionHosts").Path; got != "/v1/hosts" {
		t.Errorf("commissionHosts path %q, want /v1/hosts", got)
	}
}

// ---------------------------------------------------------------------------
// Wire shape
// ---------------------------------------------------------------------------

// TestUnsetOptionalPropertiesAreOmitted is the core wire assertion: a host
// carrying only its required properties must serialize to exactly those five
// keys, in both operations that take a HostCommissionSpec array.
func TestUnsetOptionalPropertiesAreOmitted(t *testing.T) {
	h := newHarness(t, smmock.Options{}, nil)
	if _, err := h.run(t, requiredOnlyHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	h.assertOnlyContractOperations(t)
	h.assertRequestConventions(t)

	want := []map[string]any{requiredOnlyWire()}
	for _, id := range []string{"validateHostCommissionSpec", "commissionHosts"} {
		assertHostSpecArray(t, h.only(t, id), want)
	}

	tokenReq := h.only(t, "createToken")
	gotToken, err := tokenReq.DecodeObject()
	if err != nil {
		t.Fatalf("%v", err)
	}
	wantToken := map[string]any{"username": adminUser, "password": adminPassword}
	if !reflect.DeepEqual(gotToken, wantToken) {
		t.Errorf("createToken body\n got %s\nwant %s\n(apiKey and idToken are unused by this flow and must be absent)",
			mustJSON(gotToken), mustJSON(wantToken))
	}
	assertNoNullValues(t, tokenReq)
}

// TestPopulatedOptionalPropertiesAreSent is the other half: a property the
// caller did set has to reach the wire.
func TestPopulatedOptionalPropertiesAreSent(t *testing.T) {
	h := newHarness(t, smmock.Options{}, nil)
	if _, err := h.run(t, fullyPopulatedHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	h.assertOnlyContractOperations(t)
	h.assertRequestConventions(t)

	want := []map[string]any{fullyPopulatedWire()}
	for _, id := range []string{"validateHostCommissionSpec", "commissionHosts"} {
		assertHostSpecArray(t, h.only(t, id), want)
	}
}

// TestMultipleHostsPreserveOrderAndShape checks the array body with a mix of
// sparse and fully populated specs.
func TestMultipleHostsPreserveOrderAndShape(t *testing.T) {
	sparse := requiredOnlyHost()
	partial := requiredOnlyHost()
	partial.FQDN = "esxi-09.vrack.vsphere.local"
	partial.SSLThumbprint = "AA:BB:CC:DD"

	h := newHarness(t, smmock.Options{}, nil)
	if _, err := h.run(t, sparse, partial, fullyPopulatedHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	h.assertOnlyContractOperations(t)

	partialWire := requiredOnlyWire()
	partialWire["fqdn"] = "esxi-09.vrack.vsphere.local"
	partialWire["sslThumbprint"] = "AA:BB:CC:DD"

	want := []map[string]any{requiredOnlyWire(), partialWire, fullyPopulatedWire()}
	for _, id := range []string{"validateHostCommissionSpec", "commissionHosts"} {
		assertHostSpecArray(t, h.only(t, id), want)
	}
}

// TestValidationAndCommissionSendTheSameBody proves the two operations describe
// the same hosts; the specification gives them the same request schema.
func TestValidationAndCommissionSendTheSameBody(t *testing.T) {
	h := newHarness(t, smmock.Options{}, nil)
	if _, err := h.run(t, requiredOnlyHost(), fullyPopulatedHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	validated := canonicalJSON(t, h.only(t, "validateHostCommissionSpec").Body)
	commissioned := canonicalJSON(t, h.only(t, "commissionHosts").Body)
	if validated != commissioned {
		t.Errorf("validateHostCommissionSpec and commissionHosts sent different bodies\n validated:    %s\n commissioned: %s",
			validated, commissioned)
	}
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

// TestAcceptedResponseIsNotTheOutcome proves the client polls even when the 202
// answers already look finished. Both POSTs report a terminal state here; the
// client must still confirm it with a GET.
func TestAcceptedResponseIsNotTheOutcome(t *testing.T) {
	h := newHarness(t, smmock.Options{
		PostValidation: smmock.ValidationState{ExecutionStatus: "COMPLETED", ResultStatus: "SUCCEEDED"},
		PostTask:       smmock.TaskState{Status: "SUCCESSFUL"},
		Validations:    []smmock.ValidationState{{ExecutionStatus: "COMPLETED", ResultStatus: "SUCCEEDED"}},
		Tasks:          []smmock.TaskState{{Status: "SUCCESSFUL"}},
	}, nil)

	result, err := h.run(t, requiredOnlyHost())
	if err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	if got := h.countOf("getHostCommissionValidationByID"); got != 1 {
		t.Errorf("getHostCommissionValidationByID called %d times, want 1: the 202 answer to validateHostCommissionSpec is not the outcome", got)
	}
	if got := h.countOf("getTask"); got != 1 {
		t.Errorf("getTask called %d times, want 1: the 202 answer to commissionHosts is not the outcome", got)
	}
	if result.ValidationPolls != 1 || result.TaskPolls != 1 {
		t.Errorf("Result poll counts %d/%d, want 1/1", result.ValidationPolls, result.TaskPolls)
	}
}

// TestDisplayFormStatusesAreRecognized covers the specification's mixed
// vocabulary: Task.status is documented as both IN_PROGRESS and "In Progress",
// SUCCESSFUL and "Successful".
func TestDisplayFormStatusesAreRecognized(t *testing.T) {
	h := newHarness(t, smmock.Options{
		Tasks: []smmock.TaskState{
			{Status: "Pending"},
			{Status: "In Progress"},
			{Status: "Successful"},
		},
	}, nil)

	result, err := h.run(t, requiredOnlyHost())
	if err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	if result.TaskPolls != 3 {
		t.Errorf("Result.TaskPolls %d, want 3", result.TaskPolls)
	}
	if result.Task.Status != "Successful" {
		t.Errorf("Result.Task.Status %q, want the raw terminal value %q", result.Task.Status, "Successful")
	}
}

// TestUnrecognizedStatusesAreNonTerminal covers values outside the 9.0.0.0
// vocabulary, including the two the 9.1.0.0 revision added. An unrecognized
// status means keep polling, never guess an outcome.
func TestUnrecognizedStatusesAreNonTerminal(t *testing.T) {
	h := newHarness(t, smmock.Options{
		Validations: []smmock.ValidationState{
			{ExecutionStatus: "CANCELLATION_IN_PROGRESS", ResultStatus: "CANCELLATION_IN_PROGRESS"},
			{ExecutionStatus: "REVALIDATING", ResultStatus: "UNKNOWN"},
			{ExecutionStatus: "COMPLETED", ResultStatus: "SUCCEEDED"},
		},
		Tasks: []smmock.TaskState{
			{Status: "QUEUED"},
			{Status: "Timed Out"},
			{Status: "COMPLETED_WITH_WARNING"},
		},
	}, nil)

	result, err := h.run(t, requiredOnlyHost())
	if err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	if result.ValidationPolls != 3 {
		t.Errorf("Result.ValidationPolls %d, want 3: CANCELLATION_IN_PROGRESS and an unrecognized status are both non-terminal",
			result.ValidationPolls)
	}
	if result.TaskPolls != 3 {
		t.Errorf("Result.TaskPolls %d, want 3: QUEUED and \"Timed Out\" are not in the 9.0.0.0 vocabulary and are non-terminal",
			result.TaskPolls)
	}
	if result.Task.Status != "COMPLETED_WITH_WARNING" {
		t.Errorf("Result.Task.Status %q, want COMPLETED_WITH_WARNING", result.Task.Status)
	}
}

// TestTerminalTaskStatuses is the table over every terminal Task.status the
// 9.0.0.0 vocabulary defines.
func TestTerminalTaskStatuses(t *testing.T) {
	cases := []struct {
		name    string
		status  string
		wantErr error
	}{
		{"successful", "SUCCESSFUL", nil},
		{"successful display form", "Successful", nil},
		{"completed with warning", "COMPLETED_WITH_WARNING", nil},
		{"failed", "FAILED", hostcommission.ErrTaskFailed},
		{"failed display form", "Failed", hostcommission.ErrTaskFailed},
		{"cancelled", "CANCELLED", hostcommission.ErrTaskFailed},
		{"skipped", "SKIPPED", hostcommission.ErrTaskFailed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, smmock.Options{
				Tasks: []smmock.TaskState{{
					Status:        tc.status,
					ErrorMessages: []string{"host esxi-07 failed the vSAN disk claim"},
				}},
			}, nil)

			result, err := h.run(t, requiredOnlyHost())
			h.assertOnlyContractOperations(t)

			if got := h.countOf("getTask"); got != 1 {
				t.Errorf("getTask called %d times, want 1", got)
			}
			if tc.wantErr == nil {
				if err != nil {
					t.Fatalf("CommissionHosts: %v", err)
				}
				if result == nil || result.Task.Status != tc.status {
					t.Fatalf("Result.Task.Status = %v, want %q", result, tc.status)
				}
				return
			}
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("CommissionHosts error %v, want one matching %v", err, tc.wantErr)
			}
			if result != nil {
				t.Errorf("Result is non-nil alongside an error: %+v", result)
			}
		})
	}
}

// TestValidationOutcomes covers the terminal Validation states and proves that
// a validation the flow cannot continue from stops it before commissionHosts.
func TestValidationOutcomes(t *testing.T) {
	cases := []struct {
		name            string
		executionStatus string
		resultStatus    string
		wantErr         error
	}{
		{"succeeded", "COMPLETED", "SUCCEEDED", nil},
		{"completed with warning", "COMPLETED", "WARNING", nil},
		{"completed but failed", "COMPLETED", "FAILED", hostcommission.ErrValidationFailed},
		{"completed but unknown", "COMPLETED", "UNKNOWN", hostcommission.ErrValidationFailed},
		{"execution failed", "FAILED", "FAILED", hostcommission.ErrValidationFailed},
		{"execution cancelled", "CANCELLED", "UNKNOWN", hostcommission.ErrValidationFailed},
		{"execution skipped", "SKIPPED", "UNKNOWN", hostcommission.ErrValidationFailed},
		{"execution unknown", "UNKNOWN", "UNKNOWN", hostcommission.ErrValidationFailed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, smmock.Options{
				Validations: []smmock.ValidationState{{
					ExecutionStatus: tc.executionStatus,
					ResultStatus:    tc.resultStatus,
					Checks: []smmock.ValidationCheck{{
						Description:  "ESXi host SSL thumbprint",
						Severity:     "ERROR",
						ResultStatus: tc.resultStatus,
					}},
				}},
			}, nil)

			result, err := h.run(t, requiredOnlyHost())
			h.assertOnlyContractOperations(t)

			if got := h.countOf("getHostCommissionValidationByID"); got != 1 {
				t.Errorf("getHostCommissionValidationByID called %d times, want 1", got)
			}
			if tc.wantErr == nil {
				if err != nil {
					t.Fatalf("CommissionHosts: %v", err)
				}
				if got := h.countOf("commissionHosts"); got != 1 {
					t.Errorf("commissionHosts called %d times, want 1", got)
				}
				if len(result.Validation.Checks) != 1 {
					t.Errorf("Result.Validation.Checks %+v, want the one check the appliance reported",
						result.Validation.Checks)
				}
				return
			}
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("CommissionHosts error %v, want one matching %v", err, tc.wantErr)
			}
			if got := h.countOf("commissionHosts"); got != 0 {
				t.Errorf("commissionHosts was called %d times after a failed validation, want 0", got)
			}
			if got := h.countOf("getTask"); got != 0 {
				t.Errorf("getTask was called %d times after a failed validation, want 0", got)
			}
		})
	}
}

// TestPollExhaustion bounds both loops independently.
func TestPollExhaustion(t *testing.T) {
	t.Run("validation", func(t *testing.T) {
		h := newHarness(t, smmock.Options{
			Validations: []smmock.ValidationState{{ExecutionStatus: "IN_PROGRESS", ResultStatus: "UNKNOWN"}},
		}, func(cfg *hostcommission.Config) { cfg.MaxPollAttempts = 4 })

		_, err := h.run(t, requiredOnlyHost())
		if !errors.Is(err, hostcommission.ErrPollTimeout) {
			t.Fatalf("CommissionHosts error %v, want one matching ErrPollTimeout", err)
		}
		if !strings.Contains(err.Error(), "getHostCommissionValidationByID") {
			t.Errorf("error %q does not name the operation that was polled", err)
		}
		if got := h.countOf("getHostCommissionValidationByID"); got != 4 {
			t.Errorf("getHostCommissionValidationByID called %d times, want exactly MaxPollAttempts (4)", got)
		}
		if got := h.countOf("commissionHosts"); got != 0 {
			t.Errorf("commissionHosts called %d times after the validation never settled, want 0", got)
		}
	})

	t.Run("task", func(t *testing.T) {
		h := newHarness(t, smmock.Options{
			Tasks: []smmock.TaskState{{Status: "In Progress"}},
		}, func(cfg *hostcommission.Config) { cfg.MaxPollAttempts = 3 })

		_, err := h.run(t, requiredOnlyHost())
		if !errors.Is(err, hostcommission.ErrPollTimeout) {
			t.Fatalf("CommissionHosts error %v, want one matching ErrPollTimeout", err)
		}
		if !strings.Contains(err.Error(), "getTask") {
			t.Errorf("error %q does not name the operation that was polled", err)
		}
		if got := h.countOf("getTask"); got != 3 {
			t.Errorf("getTask called %d times, want exactly MaxPollAttempts (3)", got)
		}
		// The validation loop settled on its first poll and must not have
		// consumed the task loop's budget.
		if got := h.countOf("getHostCommissionValidationByID"); got != 1 {
			t.Errorf("getHostCommissionValidationByID called %d times, want 1", got)
		}
	})
}

// TestPollIntervalIsHonoured checks the client waits between polls instead of
// spinning.
func TestPollIntervalIsHonoured(t *testing.T) {
	const interval = 40 * time.Millisecond
	h := newHarness(t, smmock.Options{
		Tasks: []smmock.TaskState{
			{Status: "IN_PROGRESS"}, {Status: "IN_PROGRESS"}, {Status: "SUCCESSFUL"},
		},
	}, func(cfg *hostcommission.Config) { cfg.PollInterval = interval })

	start := time.Now()
	if _, err := h.run(t, requiredOnlyHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	// Two waits between three task polls, plus none after the terminal one.
	if elapsed := time.Since(start); elapsed < 2*interval {
		t.Errorf("the flow took %v for three task polls; it must wait PollInterval (%v) between them",
			elapsed, interval)
	}
}

// TestContextCancellationDuringPollWait proves the wait between polls is
// interruptible rather than an unconditional sleep.
func TestContextCancellationDuringPollWait(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	h := newHarness(t, smmock.Options{
		Tasks: []smmock.TaskState{{Status: "IN_PROGRESS"}},
	}, func(cfg *hostcommission.Config) {
		cfg.PollInterval = 30 * time.Second
		cfg.MaxPollAttempts = 5
		cfg.HTTPClient = &http.Client{Transport: &cancelAtEOFTransport{
			base:   http.DefaultTransport,
			path:   "/v1/tasks/" + smmock.DefaultTaskID,
			cancel: cancel,
		}}
	})

	start := time.Now()
	_, err := h.client.CommissionHosts(ctx, []hostcommission.HostSpec{requiredOnlyHost()})
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("CommissionHosts succeeded despite a cancelled context")
	}
	if !errors.Is(err, context.Canceled) {
		t.Errorf("CommissionHosts error %v, want one matching context.Canceled", err)
	}
	if elapsed > 5*time.Second {
		t.Errorf("CommissionHosts took %v; it slept through the cancellation", elapsed)
	}
	if got := h.countOf("getTask"); got != 1 {
		t.Errorf("getTask called %d times, want 1 before the context expired", got)
	}
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

// TestLocalValidationSendsNothing proves every locally detectable problem is
// caught before the first HTTP request.
func TestLocalValidationSendsNothing(t *testing.T) {
	withField := func(mutate func(*hostcommission.HostSpec)) []hostcommission.HostSpec {
		h := requiredOnlyHost()
		mutate(&h)
		return []hostcommission.HostSpec{h}
	}
	duplicate := func() []hostcommission.HostSpec {
		a := requiredOnlyHost()
		b := requiredOnlyHost()
		b.Username = "administrator"
		return []hostcommission.HostSpec{a, b}
	}

	cases := []struct {
		name  string
		hosts []hostcommission.HostSpec
	}{
		{"no hosts", nil},
		{"empty host slice", []hostcommission.HostSpec{}},
		{"missing fqdn", withField(func(h *hostcommission.HostSpec) { h.FQDN = "" })},
		{"missing username", withField(func(h *hostcommission.HostSpec) { h.Username = "" })},
		{"missing password", withField(func(h *hostcommission.HostSpec) { h.Password = "" })},
		{"missing storage type", withField(func(h *hostcommission.HostSpec) { h.StorageType = "" })},
		{"missing network pool id", withField(func(h *hostcommission.HostSpec) { h.NetworkPoolID = "" })},
		{"vvol without protocol type", withField(func(h *hostcommission.HostSpec) { h.StorageType = "VVOL" })},
		{"protocol type without vvol", withField(func(h *hostcommission.HostSpec) {
			h.VVolStorageProtocolType = "FC"
		})},
		{"duplicate fqdn", duplicate()},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, smmock.Options{}, nil)
			result, err := h.run(t, tc.hosts...)
			if !errors.Is(err, hostcommission.ErrInvalidRequest) {
				t.Fatalf("CommissionHosts error %v, want one matching ErrInvalidRequest", err)
			}
			if result != nil {
				t.Errorf("Result is non-nil alongside an error: %+v", result)
			}
			if got := h.requests(); len(got) != 0 {
				t.Errorf("%d request(s) reached the appliance before local validation rejected the input: %v",
					len(got), h.sequence())
			}
		})
	}
}

// TestNewRejectsBadConfig checks New validates its input and performs no I/O.
func TestNewRejectsBadConfig(t *testing.T) {
	base := func() hostcommission.Config {
		return hostcommission.Config{
			BaseURL:  "https://sddc-manager.vrack.vsphere.local",
			Username: adminUser,
			Password: adminPassword,
		}
	}
	cases := []struct {
		name   string
		mutate func(*hostcommission.Config)
	}{
		{"no base url", func(c *hostcommission.Config) { c.BaseURL = "" }},
		{"base url without a scheme", func(c *hostcommission.Config) {
			c.BaseURL = "sddc-manager.vrack.vsphere.local"
		}},
		{"base url with a path", func(c *hostcommission.Config) {
			c.BaseURL = "https://sddc-manager.vrack.vsphere.local/v1"
		}},
		{"base url with a query", func(c *hostcommission.Config) {
			c.BaseURL = "https://sddc-manager.vrack.vsphere.local?source=test"
		}},
		{"base url with a fragment", func(c *hostcommission.Config) {
			c.BaseURL = "https://sddc-manager.vrack.vsphere.local#fragment"
		}},
		{"base url with user info", func(c *hostcommission.Config) {
			c.BaseURL = "https://embedded:credentials@sddc-manager.vrack.vsphere.local"
		}},
		{"unsupported base url scheme", func(c *hostcommission.Config) {
			c.BaseURL = "ftp://sddc-manager.vrack.vsphere.local"
		}},
		{"no username", func(c *hostcommission.Config) { c.Username = "" }},
		{"no password", func(c *hostcommission.Config) { c.Password = "" }},
		{"negative poll interval", func(c *hostcommission.Config) { c.PollInterval = -time.Second }},
		{"negative max poll attempts", func(c *hostcommission.Config) { c.MaxPollAttempts = -1 }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := base()
			tc.mutate(&cfg)
			client, err := hostcommission.New(cfg)
			if err == nil {
				t.Fatalf("New accepted %+v", cfg)
			}
			if !errors.Is(err, hostcommission.ErrInvalidRequest) {
				t.Errorf("New error %v, want one matching ErrInvalidRequest", err)
			}
			if client != nil {
				t.Error("New returned a client alongside an error")
			}
		})
	}

	// A valid config must be accepted without touching the network: the host
	// below does not resolve, and New must not care.
	if _, err := hostcommission.New(base()); err != nil {
		t.Errorf("New rejected a valid config: %v", err)
	}
}

// TestAPIErrorsAreSurfaced checks that a non-2xx answer fails the flow, names
// the operation and stops it.
func TestAPIErrorsAreSurfaced(t *testing.T) {
	cases := []struct {
		name        string
		operationID string
		failure     smmock.Failure
		wantAfter   map[string]int // operations that must not have run
	}{
		{
			name:        "createToken",
			operationID: "createToken",
			failure: smmock.Failure{StatusCode: http.StatusBadRequest, ErrorCode: "INVALID_CREDENTIALS",
				Message: "the supplied credentials are not valid"},
			wantAfter: map[string]int{"validateHostCommissionSpec": 0, "commissionHosts": 0},
		},
		{
			name:        "validateHostCommissionSpec",
			operationID: "validateHostCommissionSpec",
			failure: smmock.Failure{StatusCode: http.StatusBadRequest, ErrorCode: "BAD_REQUEST",
				Message: "network pool not found"},
			wantAfter: map[string]int{"getHostCommissionValidationByID": 0, "commissionHosts": 0},
		},
		{
			name:        "getHostCommissionValidationByID",
			operationID: "getHostCommissionValidationByID",
			failure: smmock.Failure{StatusCode: http.StatusInternalServerError, ErrorCode: "INTERNAL_SERVER_ERROR",
				Message: "validation service unavailable"},
			wantAfter: map[string]int{"commissionHosts": 0, "getTask": 0},
		},
		{
			name:        "commissionHosts",
			operationID: "commissionHosts",
			failure: smmock.Failure{StatusCode: http.StatusInternalServerError, ErrorCode: "INTERNAL_SERVER_ERROR",
				Message: "host commission service unavailable"},
			wantAfter: map[string]int{"getTask": 0},
		},
		{
			name:        "getTask",
			operationID: "getTask",
			failure: smmock.Failure{StatusCode: http.StatusNotFound, ErrorCode: "NOT_FOUND",
				Message: "task not found"},
			wantAfter: nil,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, smmock.Options{
				Failures: map[string]smmock.Failure{tc.operationID: tc.failure},
			}, nil)

			result, err := h.run(t, requiredOnlyHost())
			h.assertOnlyContractOperations(t)

			if !errors.Is(err, hostcommission.ErrAPI) {
				t.Fatalf("CommissionHosts error %v, want one matching ErrAPI", err)
			}
			if result != nil {
				t.Errorf("Result is non-nil alongside an error: %+v", result)
			}
			if !strings.Contains(err.Error(), tc.operationID) {
				t.Errorf("error %q does not name the operation that failed (%s)", err, tc.operationID)
			}
			if got := h.countOf(tc.operationID); got != 1 {
				t.Errorf("%s was called %d times, want 1: a failed call must not be retried",
					tc.operationID, got)
			}
			for id, want := range tc.wantAfter {
				if got := h.countOf(id); got != want {
					t.Errorf("%s was called %d times after %s failed, want %d",
						id, got, tc.operationID, want)
				}
			}
		})
	}
}

// TestAuthorizationHeaderIsRequiredByTheAppliance is a belt-and-braces check:
// the mock answers 401 without the contract's Authorization header, so a client
// that forgets it cannot reach a terminal state at all.
func TestAuthorizationHeaderIsRequiredByTheAppliance(t *testing.T) {
	h := newHarness(t, smmock.Options{}, nil)
	if _, err := h.run(t, requiredOnlyHost()); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	auth := h.contract.Authorization
	want := auth.HeaderValue(h.mock.AccessToken())
	for _, id := range auth.AppliesTo {
		for _, r := range h.mock.RequestsFor(id) {
			if got := r.Header.Get(auth.HeaderName); got != want {
				t.Errorf("%s: %s header %q, want %q", id, auth.HeaderName, got, want)
			}
		}
	}
	for _, id := range auth.ExemptOperations {
		for _, r := range h.mock.RequestsFor(id) {
			if got := r.Header.Get(auth.HeaderName); got != "" {
				t.Errorf("%s: is exempt but sent %s: %q", id, auth.HeaderName, got)
			}
		}
	}
}
