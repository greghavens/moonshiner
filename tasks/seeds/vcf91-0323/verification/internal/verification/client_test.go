package verification

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"example.com/vcf-automation-day2-action/internal/contractmock"
	"example.com/vcf-automation-day2-action/vcfautomation"
)

const (
	contractPath = "../../docs/contract.json"
	sourcesPath  = "../../docs/official_sources.json"

	testToken        = "eyJhbGciOiJSUzI1NiJ9.vcfa-9-1-provider-access-token.signature"
	testDeploymentID = "6d0a1f2c-9d1e-4a7b-8f31-2b7c5d9e0143"
	testRequestID    = "b1c9f4e8-3a52-4d16-9c07-58ee1a2b7d64"

	opActions = "getDeploymentActions"
	opSubmit  = "submitDeploymentActionRequest"
	opGet     = "getRequest"

	powerOffActionID = "Deployment.PowerOff"
	resizeActionID   = "Deployment.Resize"
)

// ---------------------------------------------------------------------------
// contract ground truth, transcribed from the reference pages recorded in
// docs/official_sources.json
// ---------------------------------------------------------------------------

type wantOperation struct {
	method       string
	pathTemplate string
}

var wantOperations = map[string]wantOperation{
	opActions: {"get", "/deployment/api/deployments/{deploymentId}/actions"},
	opSubmit:  {"post", "/deployment/api/deployments/{deploymentId}/requests"},
	opGet:     {"get", "/deployment/api/requests/{requestId}"},
}

// The reference documents all three ResourceActionRequest members as optional.
var wantRequestBodyMembers = map[string]bool{
	"actionId": false,
	"inputs":   false,
	"reason":   false,
}

var wantStatusEnum = []string{
	"CREATED", "PENDING", "INITIALIZATION", "CHECKING_APPROVAL",
	"APPROVAL_PENDING", "USER_INTERACTION_PENDING", "INPROGRESS",
	"COMPLETION", "APPROVAL_REJECTED", "ABORTED", "SUCCESSFUL", "FAILED",
}

var wantTerminalStatuses = []string{"ABORTED", "APPROVAL_REJECTED", "FAILED", "SUCCESSFUL"}

// ---------------------------------------------------------------------------
// scenario: a scripted, contract-shaped backend for the mock
// ---------------------------------------------------------------------------

type scenario struct {
	mu sync.Mutex

	deploymentID  string
	actions       []map[string]any
	actionsStatus int

	requestID       string
	submitStatus    int
	submitBodyRaw   []byte
	submitReqStatus string

	getStatus  int
	getBodyRaw []byte
	getWait    bool

	// pollStatuses are returned by successive getRequest calls; the last entry
	// repeats forever.
	pollStatuses []string
	pollDetails  string
	pollOutputs  map[string]any

	polls int
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

func defaultActions() []map[string]any {
	return []map[string]any{
		{
			"id": powerOffActionID, "name": "PowerOff", "displayName": "Power Off",
			"description": "Power off the deployment", "actionType": "RESOURCE_ACTION", "valid": true,
		},
		{
			"id": resizeActionID, "name": "Resize", "displayName": "Resize",
			"description": "Change deployment capacity", "actionType": "RESOURCE_ACTION", "valid": true,
		},
		{
			"id": "Deployment.Delete", "name": "Delete", "displayName": "Delete",
			"description": "Delete the deployment", "actionType": "RESOURCE_ACTION", "valid": false,
		},
	}
}

func newScenario(pollStatuses ...string) *scenario {
	if len(pollStatuses) == 0 {
		pollStatuses = []string{"SUCCESSFUL"}
	}
	return &scenario{
		deploymentID:    testDeploymentID,
		actions:         defaultActions(),
		requestID:       testRequestID,
		submitReqStatus: "PENDING",
		pollStatuses:    pollStatuses,
	}
}

func (s *scenario) requestBody(status string) map[string]any {
	body := map[string]any{
		"id":             s.requestID,
		"name":           "Power Off myDeployment",
		"status":         status,
		"actionId":       powerOffActionID,
		"deploymentId":   s.deploymentID,
		"requestedBy":    "provider-admin@vcf.local",
		"cancelable":     status == "PENDING" || status == "INPROGRESS",
		"completedTasks": 2,
		"totalTasks":     5,
		"createdAt":      "2026-04-17T09:14:22.481Z",
	}
	if s.pollDetails != "" {
		body["details"] = s.pollDetails
	}
	if s.pollOutputs != nil {
		body["outputs"] = s.pollOutputs
	}
	return body
}

func (s *scenario) responder(r contractmock.Request) contractmock.Response {
	s.mu.Lock()
	defer s.mu.Unlock()

	switch r.OperationID {
	case opActions:
		if r.PathParams["deploymentId"] != s.deploymentID {
			return apiErrorResponse(http.StatusNotFound, "DEPLOYMENT_NOT_FOUND", "deployment not found")
		}
		if s.actionsStatus != 0 && s.actionsStatus != http.StatusOK {
			return apiErrorResponse(s.actionsStatus, "ACTIONS_DENIED", "cannot list deployment actions")
		}
		return contractmock.JSON(http.StatusOK, s.actions)

	case opSubmit:
		if r.PathParams["deploymentId"] != s.deploymentID {
			return apiErrorResponse(http.StatusNotFound, "DEPLOYMENT_NOT_FOUND", "deployment not found")
		}
		if s.submitStatus != 0 && s.submitStatus != http.StatusOK {
			return apiErrorResponse(s.submitStatus, "ACTION_REJECTED", "the deployment action was rejected")
		}
		if s.submitBodyRaw != nil {
			return contractmock.Response{Status: http.StatusOK, ContentType: "application/json", Body: s.submitBodyRaw}
		}
		return contractmock.JSON(http.StatusOK, s.requestBody(s.submitReqStatus))

	case opGet:
		if r.PathParams["requestId"] != s.requestID {
			return apiErrorResponse(http.StatusNotFound, "REQUEST_NOT_FOUND", "request not found")
		}
		if s.getStatus != 0 && s.getStatus != http.StatusOK {
			return apiErrorResponse(s.getStatus, "REQUEST_DENIED", "cannot read the request")
		}
		if s.getWait {
			return contractmock.Response{WaitForRequestContext: true}
		}
		n := s.polls
		s.polls++
		if s.getBodyRaw != nil {
			return contractmock.Response{Status: http.StatusOK, ContentType: "application/json", Body: s.getBodyRaw}
		}
		if n >= len(s.pollStatuses) {
			n = len(s.pollStatuses) - 1
		}
		return contractmock.JSON(http.StatusOK, s.requestBody(s.pollStatuses[n]))
	}
	return apiErrorResponse(http.StatusInternalServerError, "UNREACHABLE", "unhandled contract operation "+r.OperationID)
}

func apiErrorResponse(status int, code, message string) contractmock.Response {
	return contractmock.JSON(status, map[string]any{"errorCode": code, "message": message})
}

func startMock(t *testing.T, s *scenario) *contractmock.Server {
	t.Helper()
	return contractmock.New(t, contractPath, s.responder)
}

func newClient(t *testing.T, srv *contractmock.Server) *vcfautomation.Client {
	t.Helper()
	client, err := vcfautomation.NewClient(srv.URL(), testToken, nil)
	if err != nil {
		t.Fatalf("NewClient: unexpected error: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	client.PollInterval = time.Millisecond
	client.PollTimeout = 20 * time.Second
	return client
}

func stringPtr(s string) *string { return &s }

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func find(t *testing.T, srv *contractmock.Server, operationID string) contractmock.Request {
	t.Helper()
	for _, r := range srv.Requests() {
		if r.OperationID == operationID {
			return r
		}
	}
	t.Fatalf("no logged request matched contract operation %q; log was %v", operationID, srv.Operations())
	return contractmock.Request{}
}

// ---------------------------------------------------------------------------
// contract and provenance
// ---------------------------------------------------------------------------

func readJSON(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return doc
}

func TestContractDeclaresReferenceDerivedProvenance(t *testing.T) {
	contract := readJSON(t, contractPath)

	source, ok := contract["x-contract-source"].(map[string]any)
	if !ok {
		t.Fatal("docs/contract.json must carry an x-contract-source object")
	}

	if got := source["kind"]; got != "reference-documentation" {
		t.Errorf("x-contract-source.kind = %v, want \"reference-documentation\"", got)
	}
	published, ok := source["publishedSpecificationAvailable"].(bool)
	if !ok || published {
		t.Errorf("x-contract-source.publishedSpecificationAvailable = %v, want false", source["publishedSpecificationAvailable"])
	}

	statement, _ := source["statement"].(string)
	if len(statement) < 120 {
		t.Fatalf("x-contract-source.statement must plainly describe the source; got %d characters", len(statement))
	}
	lower := strings.ToLower(statement)
	for _, phrase := range []string{"reference documentation", "not a published specification"} {
		if !strings.Contains(lower, phrase) {
			t.Errorf("x-contract-source.statement must contain %q; got %q", phrase, statement)
		}
	}
}

func TestOfficialSourcesRecordEveryReferencePage(t *testing.T) {
	sources := readJSON(t, sourcesPath)

	if published, ok := sources["publishedSpecificationAvailable"].(bool); !ok || published {
		t.Errorf("official_sources.publishedSpecificationAvailable = %v, want false", sources["publishedSpecificationAvailable"])
	}

	pages, ok := sources["pages"].([]any)
	if !ok || len(pages) == 0 {
		t.Fatal("docs/official_sources.json must carry a non-empty pages array")
	}

	covered := map[string]bool{}
	for i, entry := range pages {
		page, ok := entry.(map[string]any)
		if !ok {
			t.Fatalf("pages[%d] is not an object", i)
		}

		url, _ := page["url"].(string)
		if !strings.HasPrefix(url, "https://developer.broadcom.com/xapis/") {
			t.Errorf("pages[%d].url = %q, want an https developer.broadcom.com xAPIs reference page", i, url)
		}
		for _, banned := range []string{".invalid", "example.com", "localhost"} {
			if strings.Contains(url, banned) {
				t.Errorf("pages[%d].url = %q must be a real reachable page", i, url)
			}
		}

		if title, _ := page["title"].(string); strings.TrimSpace(title) == "" {
			t.Errorf("pages[%d].title is empty", i)
		}
		documents, _ := page["documents"].(string)
		if len(strings.TrimSpace(documents)) < 20 {
			t.Errorf("pages[%d].documents must say what the page documents; got %q", i, documents)
		}

		fetched, _ := page["dateFetched"].(string)
		parsed, err := time.Parse("2006-01-02", fetched)
		if err != nil {
			t.Errorf("pages[%d].dateFetched = %q, want a YYYY-MM-DD date: %v", i, fetched, err)
		} else if parsed.Before(time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)) {
			t.Errorf("pages[%d].dateFetched = %q predates the VCF 9 reference", i, fetched)
		}

		if op, ok := page["operationId"].(string); ok {
			covered[op] = true
		}
	}

	for operationID := range wantOperations {
		if !covered[operationID] {
			t.Errorf("no page in docs/official_sources.json records operation %q", operationID)
		}
	}
}

func TestContractProjectsTheReferencedOperations(t *testing.T) {
	contract := readJSON(t, contractPath)

	paths, ok := contract["paths"].(map[string]any)
	if !ok {
		t.Fatal("docs/contract.json must carry a paths object")
	}

	found := map[string]wantOperation{}
	for template, entry := range paths {
		item, ok := entry.(map[string]any)
		if !ok {
			t.Fatalf("paths[%q] is not an object", template)
		}
		for method, raw := range item {
			operation, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			operationID, _ := operation["operationId"].(string)
			if operationID == "" {
				t.Errorf("paths[%q].%s has no operationId", template, method)
				continue
			}
			if _, dup := found[operationID]; dup {
				t.Errorf("operationId %q appears more than once", operationID)
			}
			found[operationID] = wantOperation{strings.ToLower(method), template}
		}
	}

	if len(found) != len(wantOperations) {
		t.Errorf("contract names %d operations (%v), want exactly %d", len(found), sortedOperationIDs(found), len(wantOperations))
	}
	for operationID, want := range wantOperations {
		got, ok := found[operationID]
		if !ok {
			t.Errorf("contract does not name operation %q", operationID)
			continue
		}
		if got != want {
			t.Errorf("operation %q = %s %s, want %s %s", operationID, strings.ToUpper(got.method), got.pathTemplate, strings.ToUpper(want.method), want.pathTemplate)
		}
	}
}

func sortedOperationIDs(m map[string]wantOperation) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func TestContractSchemasMatchTheReference(t *testing.T) {
	contract := readJSON(t, contractPath)
	components, _ := contract["components"].(map[string]any)
	if components == nil {
		t.Fatal("docs/contract.json must carry a components object")
	}
	schemas, _ := components["schemas"].(map[string]any)
	if schemas == nil {
		t.Fatal("docs/contract.json must carry components.schemas")
	}

	t.Run("ResourceActionRequest members are all optional", func(t *testing.T) {
		body, _ := schemas["ResourceActionRequest"].(map[string]any)
		if body == nil {
			t.Fatal("components.schemas.ResourceActionRequest is missing")
		}
		if required, ok := body["required"]; ok {
			if list, _ := required.([]any); len(list) != 0 {
				t.Errorf("ResourceActionRequest.required = %v, want no required members", required)
			}
		}
		properties, _ := body["properties"].(map[string]any)
		got := map[string]bool{}
		for name := range properties {
			got[name] = false
		}
		if !reflect.DeepEqual(got, wantRequestBodyMembers) {
			t.Errorf("ResourceActionRequest members = %v, want %v", sortedKeys(properties), []string{"actionId", "inputs", "reason"})
		}
	})

	t.Run("Request status enum", func(t *testing.T) {
		request, _ := schemas["Request"].(map[string]any)
		if request == nil {
			t.Fatal("components.schemas.Request is missing")
		}
		properties, _ := request["properties"].(map[string]any)
		status, _ := properties["status"].(map[string]any)
		if status == nil {
			t.Fatal("components.schemas.Request.properties.status is missing")
		}
		raw, _ := status["enum"].([]any)
		got := make([]string, 0, len(raw))
		for _, v := range raw {
			s, _ := v.(string)
			got = append(got, s)
		}
		sortedGot := append([]string(nil), got...)
		sortedWant := append([]string(nil), wantStatusEnum...)
		sort.Strings(sortedGot)
		sort.Strings(sortedWant)
		if !reflect.DeepEqual(sortedGot, sortedWant) {
			t.Errorf("Request.status enum = %v, want the twelve documented values %v", got, wantStatusEnum)
		}
	})

	t.Run("terminal status classification", func(t *testing.T) {
		lifecycle, _ := components["x-status-lifecycle"].(map[string]any)
		if lifecycle == nil {
			t.Fatal("components.x-status-lifecycle is missing")
		}
		terminal, _ := lifecycle["terminal"].(map[string]any)
		if terminal == nil {
			t.Fatal("x-status-lifecycle.terminal is missing")
		}
		var got []string
		for _, key := range []string{"successful", "unsuccessful"} {
			list, _ := terminal[key].([]any)
			for _, v := range list {
				s, _ := v.(string)
				got = append(got, s)
			}
		}
		sort.Strings(got)
		if !reflect.DeepEqual(got, wantTerminalStatuses) {
			t.Errorf("terminal statuses = %v, want %v", got, wantTerminalStatuses)
		}
	})
}

// ---------------------------------------------------------------------------
// asynchronous polling
// ---------------------------------------------------------------------------

func TestRunDeploymentActionPollsToATerminalStatus(t *testing.T) {
	cases := []struct {
		name         string
		pollStatuses []string
		wantStatus   string
		wantFailure  bool
	}{
		{
			name:         "full non-terminal lifecycle before success",
			pollStatuses: []string{"CREATED", "PENDING", "INITIALIZATION", "INPROGRESS", "COMPLETION", "SUCCESSFUL"},
			wantStatus:   "SUCCESSFUL",
		},
		{
			name:         "COMPLETION is not terminal",
			pollStatuses: []string{"COMPLETION", "COMPLETION", "SUCCESSFUL"},
			wantStatus:   "SUCCESSFUL",
		},
		{
			name:         "USER_INTERACTION_PENDING is not terminal",
			pollStatuses: []string{"USER_INTERACTION_PENDING", "INPROGRESS", "SUCCESSFUL"},
			wantStatus:   "SUCCESSFUL",
		},
		{
			name:         "approval path settles successfully",
			pollStatuses: []string{"CHECKING_APPROVAL", "APPROVAL_PENDING", "INPROGRESS", "SUCCESSFUL"},
			wantStatus:   "SUCCESSFUL",
		},
		{
			name:         "terminal on the first poll",
			pollStatuses: []string{"SUCCESSFUL"},
			wantStatus:   "SUCCESSFUL",
		},
		{
			name:         "FAILED is terminal",
			pollStatuses: []string{"INPROGRESS", "FAILED"},
			wantStatus:   "FAILED",
			wantFailure:  true,
		},
		{
			name:         "ABORTED is terminal",
			pollStatuses: []string{"INPROGRESS", "INPROGRESS", "ABORTED"},
			wantStatus:   "ABORTED",
			wantFailure:  true,
		},
		{
			name:         "APPROVAL_REJECTED is terminal",
			pollStatuses: []string{"CHECKING_APPROVAL", "APPROVAL_PENDING", "APPROVAL_REJECTED"},
			wantStatus:   "APPROVAL_REJECTED",
			wantFailure:  true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := newScenario(tc.pollStatuses...)
			s.pollDetails = "Deployment action reached " + tc.wantStatus
			s.pollOutputs = map[string]any{"powerState": "OFF"}
			srv := startMock(t, s)
			client := newClient(t, srv)

			got, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})

			if tc.wantFailure {
				var failed *vcfautomation.RequestFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("RunDeploymentAction error = %v, want *RequestFailedError", err)
				}
				if failed.Status != tc.wantStatus {
					t.Errorf("RequestFailedError.Status = %q, want %q", failed.Status, tc.wantStatus)
				}
				if failed.RequestID != testRequestID {
					t.Errorf("RequestFailedError.RequestID = %q, want %q", failed.RequestID, testRequestID)
				}
				if failed.Details != s.pollDetails {
					t.Errorf("RequestFailedError.Details = %q, want %q", failed.Details, s.pollDetails)
				}
			} else if err != nil {
				t.Fatalf("RunDeploymentAction: unexpected error: %v", err)
			}

			if got == nil {
				t.Fatal("RunDeploymentAction returned a nil Request; the terminal request must be returned in both outcomes")
			}
			if got.Status != tc.wantStatus {
				t.Errorf("Request.Status = %q, want %q", got.Status, tc.wantStatus)
			}
			if got.ID != testRequestID {
				t.Errorf("Request.ID = %q, want %q", got.ID, testRequestID)
			}

			// The poll count is exact: the client must stop at the first
			// terminal status and must not re-read it.
			if n := srv.Count(opGet); n != len(tc.pollStatuses) {
				t.Errorf("getRequest was called %d times, want exactly %d (one per scripted status)", n, len(tc.pollStatuses))
			}
			if n := srv.Count(opSubmit); n != 1 {
				t.Errorf("submitDeploymentActionRequest was called %d times, want exactly 1", n)
			}

			wantOrder := []string{opActions, opSubmit}
			for range tc.pollStatuses {
				wantOrder = append(wantOrder, opGet)
			}
			if got := srv.Operations(); !reflect.DeepEqual(got, wantOrder) {
				t.Errorf("operation order = %v, want %v", got, wantOrder)
			}
		})
	}
}

func TestSubmitResponseIsNotTreatedAsTerminal(t *testing.T) {
	// The submit response reports SUCCESSFUL, but the reference documents the
	// operation as asynchronous: the request must still be polled.
	s := newScenario("INPROGRESS", "FAILED")
	s.submitReqStatus = "SUCCESSFUL"
	srv := startMock(t, s)
	client := newClient(t, srv)

	got, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})

	var failed *vcfautomation.RequestFailedError
	if !errors.As(err, &failed) {
		t.Fatalf("error = %v, want *RequestFailedError; the submit response status must not short-circuit polling", err)
	}
	if got == nil || got.Status != "FAILED" {
		t.Fatalf("Request = %+v, want the polled terminal status FAILED", got)
	}
	if n := srv.Count(opGet); n != 2 {
		t.Errorf("getRequest was called %d times, want 2", n)
	}
}

func TestPollTimeout(t *testing.T) {
	s := newScenario("INPROGRESS")
	srv := startMock(t, s)
	client := newClient(t, srv)
	client.PollInterval = time.Millisecond
	client.PollTimeout = 150 * time.Millisecond

	_, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
	if !errors.Is(err, vcfautomation.ErrPollTimeout) {
		t.Fatalf("error = %v, want an error satisfying errors.Is(err, ErrPollTimeout)", err)
	}
}

func TestPollIntervalSeparatesSuccessivePolls(t *testing.T) {
	s := newScenario("INPROGRESS", "SUCCESSFUL")
	srv := startMock(t, s)
	client := newClient(t, srv)
	client.PollInterval = 25 * time.Millisecond

	if _, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"}); err != nil {
		t.Fatalf("RunDeploymentAction: unexpected error: %v", err)
	}
	var polls []contractmock.Request
	for _, request := range srv.Requests() {
		if request.OperationID == opGet {
			polls = append(polls, request)
		}
	}
	if len(polls) != 2 {
		t.Fatalf("getRequest was called %d times, want 2", len(polls))
	}
	if gap := polls[1].ReceivedAt.Sub(polls[0].ReceivedAt); gap < client.PollInterval {
		t.Errorf("successive getRequest calls were %s apart, want at least PollInterval %s", gap, client.PollInterval)
	}
}

func TestPollTimeoutBoundsAnInFlightPoll(t *testing.T) {
	s := newScenario("INPROGRESS")
	s.getWait = true
	srv := startMock(t, s)
	client := newClient(t, srv)
	client.PollInterval = time.Millisecond
	client.PollTimeout = 25 * time.Millisecond

	// The caller deadline is only a watchdog. PollTimeout must cancel the
	// in-flight getRequest first and map that cancellation to ErrPollTimeout.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	got, err := client.RunDeploymentAction(ctx, testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
	if got != nil {
		t.Errorf("Request = %+v, want nil on a poll timeout", got)
	}
	if !errors.Is(err, vcfautomation.ErrPollTimeout) {
		t.Fatalf("error = %v, want an error satisfying errors.Is(err, ErrPollTimeout)", err)
	}
}

func TestPollHonoursContextCancellation(t *testing.T) {
	s := newScenario("INPROGRESS")
	srv := startMock(t, s)
	client := newClient(t, srv)
	client.PollInterval = 5 * time.Millisecond
	client.PollTimeout = time.Minute

	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()

	done := make(chan error, 1)
	go func() {
		_, err := client.RunDeploymentAction(ctx, testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
		done <- err
	}()

	select {
	case err := <-done:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("error = %v, want an error satisfying errors.Is(err, context.DeadlineExceeded)", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("RunDeploymentAction ignored the cancelled context")
	}
}

// ---------------------------------------------------------------------------
// exact request wire shape
// ---------------------------------------------------------------------------

func TestSubmitBodyOmitsUnsetOptionalMembers(t *testing.T) {
	cases := []struct {
		name     string
		action   vcfautomation.ActionRequest
		wantKeys []string
		wantBody map[string]any
	}{
		{
			name:     "unset optional members are omitted entirely",
			action:   vcfautomation.ActionRequest{ActionName: "PowerOff"},
			wantKeys: []string{"actionId"},
			wantBody: map[string]any{"actionId": powerOffActionID},
		},
		{
			name:     "explicit empty reason is preserved, inputs stays omitted",
			action:   vcfautomation.ActionRequest{ActionName: "PowerOff", Reason: stringPtr("")},
			wantKeys: []string{"actionId", "reason"},
			wantBody: map[string]any{"actionId": powerOffActionID, "reason": ""},
		},
		{
			name:     "explicit empty inputs is preserved, reason stays omitted",
			action:   vcfautomation.ActionRequest{ActionName: "PowerOff", Inputs: map[string]any{}},
			wantKeys: []string{"actionId", "inputs"},
			wantBody: map[string]any{"actionId": powerOffActionID, "inputs": map[string]any{}},
		},
		{
			name:     "reason only",
			action:   vcfautomation.ActionRequest{ActionName: "PowerOff", Reason: stringPtr("Scheduled maintenance window")},
			wantKeys: []string{"actionId", "reason"},
			wantBody: map[string]any{"actionId": powerOffActionID, "reason": "Scheduled maintenance window"},
		},
		{
			name:     "inputs only",
			action:   vcfautomation.ActionRequest{ActionName: "Resize", Inputs: map[string]any{"cpuCount": 4}},
			wantKeys: []string{"actionId", "inputs"},
			wantBody: map[string]any{"actionId": resizeActionID, "inputs": map[string]any{"cpuCount": float64(4)}},
		},
		{
			name: "every member set",
			action: vcfautomation.ActionRequest{
				ActionName: "Resize",
				Inputs:     map[string]any{"cpuCount": 8, "memoryGB": 32},
				Reason:     stringPtr("Q3 capacity change"),
			},
			wantKeys: []string{"actionId", "inputs", "reason"},
			wantBody: map[string]any{
				"actionId": resizeActionID,
				"inputs":   map[string]any{"cpuCount": float64(8), "memoryGB": float64(32)},
				"reason":   "Q3 capacity change",
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, newScenario("SUCCESSFUL"))
			client := newClient(t, srv)

			if _, err := client.RunDeploymentAction(context.Background(), testDeploymentID, tc.action); err != nil {
				t.Fatalf("RunDeploymentAction: unexpected error: %v", err)
			}

			submit := find(t, srv, opSubmit)
			var got map[string]any
			if err := json.Unmarshal(submit.Body, &got); err != nil {
				t.Fatalf("submit body %q is not a JSON object: %v", submit.Body, err)
			}

			if keys := sortedKeys(got); !reflect.DeepEqual(keys, tc.wantKeys) {
				t.Errorf("submit body members = %v, want exactly %v (raw body %s)", keys, tc.wantKeys, submit.Body)
			}
			if !reflect.DeepEqual(got, tc.wantBody) {
				t.Errorf("submit body = %#v, want %#v (raw body %s)", got, tc.wantBody, submit.Body)
			}
			if strings.Contains(string(submit.Body), "null") {
				t.Errorf("submit body %s serialises an unset member as null", submit.Body)
			}
		})
	}
}

func TestRequestWireShape(t *testing.T) {
	srv := startMock(t, newScenario("INPROGRESS", "SUCCESSFUL"))
	client := newClient(t, srv)

	reason := "Scheduled maintenance window"
	if _, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{
		ActionName: "PowerOff",
		Reason:     &reason,
	}); err != nil {
		t.Fatalf("RunDeploymentAction: unexpected error: %v", err)
	}

	requests := srv.Requests()
	if len(requests) != 4 {
		t.Fatalf("logged %d requests (%v), want 4", len(requests), srv.Operations())
	}

	cases := []struct {
		name     string
		req      contractmock.Request
		wantMeth string
		wantPath string
		wantBody bool
		params   map[string]string
	}{
		{
			name: "getDeploymentActions", req: requests[0],
			wantMeth: http.MethodGet,
			wantPath: "/deployment/api/deployments/" + testDeploymentID + "/actions",
			params:   map[string]string{"deploymentId": testDeploymentID},
		},
		{
			name: "submitDeploymentActionRequest", req: requests[1],
			wantMeth: http.MethodPost,
			wantPath: "/deployment/api/deployments/" + testDeploymentID + "/requests",
			wantBody: true,
			params:   map[string]string{"deploymentId": testDeploymentID},
		},
		{
			name: "getRequest poll one", req: requests[2],
			wantMeth: http.MethodGet,
			wantPath: "/deployment/api/requests/" + testRequestID,
			params:   map[string]string{"requestId": testRequestID},
		},
		{
			name: "getRequest poll two", req: requests[3],
			wantMeth: http.MethodGet,
			wantPath: "/deployment/api/requests/" + testRequestID,
			params:   map[string]string{"requestId": testRequestID},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := tc.req

			if r.Method != tc.wantMeth {
				t.Errorf("method = %s, want %s", r.Method, tc.wantMeth)
			}
			if r.Path != tc.wantPath {
				t.Errorf("path = %s, want %s", r.Path, tc.wantPath)
			}
			if !reflect.DeepEqual(r.PathParams, tc.params) {
				t.Errorf("path parameters = %v, want %v", r.PathParams, tc.params)
			}
			if r.RawQuery != "" {
				t.Errorf("raw query = %q, want empty: the contract defines no query parameter", r.RawQuery)
			}
			if strings.Contains(r.RequestURI, "?") {
				t.Errorf("request target %q contains a bare or populated query separator", r.RequestURI)
			}

			if got := r.Header.Values("Authorization"); len(got) != 1 || got[0] != "Bearer "+testToken {
				t.Errorf("Authorization = %v, want exactly one \"Bearer <token>\" header", got)
			}
			accept := r.Header.Values("Accept")
			if len(accept) != 1 {
				t.Fatalf("Accept = %v, want exactly one header", accept)
			}
			if media, _, err := mime.ParseMediaType(accept[0]); err != nil || media != "application/json" {
				t.Errorf("Accept = %q, want a JSON media type", accept[0])
			}

			if len(r.TransferEncoding) != 0 {
				t.Errorf("transfer encoding = %v, want none", r.TransferEncoding)
			}

			contentType := r.Header.Values("Content-Type")
			if !tc.wantBody {
				if len(r.Body) != 0 {
					t.Errorf("bodyless request carried a %d byte body: %s", len(r.Body), r.Body)
				}
				if r.ContentLength > 0 {
					t.Errorf("content length = %d, want none on a bodyless request", r.ContentLength)
				}
				if len(contentType) != 0 {
					t.Errorf("Content-Type = %v, want none on a bodyless request", contentType)
				}
				return
			}

			if len(r.Body) == 0 {
				t.Fatal("submit carried no body")
			}
			if r.ContentLength != int64(len(r.Body)) {
				t.Errorf("content length = %d, want %d", r.ContentLength, len(r.Body))
			}
			if len(contentType) != 1 {
				t.Fatalf("Content-Type = %v, want exactly one header", contentType)
			}
			if media, _, err := mime.ParseMediaType(contentType[0]); err != nil || media != "application/json" {
				t.Errorf("Content-Type = %q, want a JSON media type", contentType[0])
			}
		})
	}
}

func TestPathVariablesUseSegmentEscaping(t *testing.T) {
	s := newScenario("SUCCESSFUL")
	s.deploymentID = "deployment/segment ?#%"
	s.requestID = "request/segment ?#%"
	srv := startMock(t, s)
	client := newClient(t, srv)

	got, err := client.RunDeploymentAction(context.Background(), s.deploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
	if err != nil {
		t.Fatalf("RunDeploymentAction: unexpected error: %v", err)
	}
	if got == nil || got.ID != s.requestID {
		t.Fatalf("Request = %+v, want id %q", got, s.requestID)
	}

	requests := srv.Requests()
	if len(requests) != 3 {
		t.Fatalf("logged %d requests (%v), want 3", len(requests), srv.Operations())
	}
	if got := requests[0].PathParams["deploymentId"]; got != s.deploymentID {
		t.Errorf("decoded deploymentId = %q, want %q", got, s.deploymentID)
	}
	if got := requests[1].PathParams["deploymentId"]; got != s.deploymentID {
		t.Errorf("decoded deploymentId = %q, want %q", got, s.deploymentID)
	}
	if got := requests[2].PathParams["requestId"]; got != s.requestID {
		t.Errorf("decoded requestId = %q, want %q", got, s.requestID)
	}
	for _, r := range requests {
		if r.RawQuery != "" || strings.Contains(r.RequestURI, "?") {
			t.Errorf("escaped request target %q unexpectedly carries a query", r.RequestURI)
		}
	}
}

// ---------------------------------------------------------------------------
// the mock is pinned to the contract
// ---------------------------------------------------------------------------

func TestMockServesOnlyOperationsTheContractNames(t *testing.T) {
	// Trim submitDeploymentActionRequest out of a copy of the contract. The
	// mock must then refuse the route the client uses for it.
	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract map[string]any
	if err := json.Unmarshal(data, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	paths, _ := contract["paths"].(map[string]any)
	delete(paths, wantOperations[opSubmit].pathTemplate)

	trimmed, err := json.Marshal(contract)
	if err != nil {
		t.Fatalf("re-encode contract: %v", err)
	}
	trimmedPath := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(trimmedPath, trimmed, 0o600); err != nil {
		t.Fatalf("write trimmed contract: %v", err)
	}

	s := newScenario("SUCCESSFUL")
	srv := contractmock.New(t, trimmedPath, s.responder)
	client := newClient(t, srv)

	_, err = client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})

	var apiErr *vcfautomation.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v, want *APIError once the operation leaves the contract", err)
	}
	if apiErr.StatusCode != http.StatusNotFound {
		t.Errorf("APIError.StatusCode = %d, want 404", apiErr.StatusCode)
	}
	for _, op := range srv.Operations() {
		if op == opSubmit {
			t.Fatal("the mock routed an operation the contract no longer names")
		}
	}
	if srv.Count(opGet) != 0 {
		t.Error("the client polled after the submit failed")
	}
}

// ---------------------------------------------------------------------------
// error mapping
// ---------------------------------------------------------------------------

func TestAPIErrorMapping(t *testing.T) {
	cases := []struct {
		name       string
		mutate     func(*scenario)
		wantStatus int
		wantCode   string
	}{
		{"actions unauthorized", func(s *scenario) { s.actionsStatus = http.StatusUnauthorized }, 401, "ACTIONS_DENIED"},
		{"submit forbidden", func(s *scenario) { s.submitStatus = http.StatusForbidden }, 403, "ACTION_REJECTED"},
		{"submit conflict", func(s *scenario) { s.submitStatus = http.StatusConflict }, 409, "ACTION_REJECTED"},
		{"submit created is not exact 200", func(s *scenario) { s.submitStatus = http.StatusCreated }, 201, "ACTION_REJECTED"},
		{"poll unauthorized", func(s *scenario) { s.getStatus = http.StatusUnauthorized }, 401, "REQUEST_DENIED"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := newScenario("SUCCESSFUL")
			tc.mutate(s)
			srv := startMock(t, s)
			client := newClient(t, srv)

			got, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
			if got != nil {
				t.Errorf("Request = %+v, want nil on a transport failure", got)
			}
			var apiErr *vcfautomation.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %v, want *APIError", err)
			}
			if apiErr.StatusCode != tc.wantStatus {
				t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, tc.wantStatus)
			}
			if apiErr.ErrorCode != tc.wantCode {
				t.Errorf("APIError.ErrorCode = %q, want %q", apiErr.ErrorCode, tc.wantCode)
			}
			if apiErr.Message == "" {
				t.Error("APIError.Message is empty")
			}
		})
	}
}

func TestProtocolErrorMapping(t *testing.T) {
	cases := []struct {
		name   string
		action vcfautomation.ActionRequest
		mutate func(*scenario)
	}{
		{
			name:   "action name absent from the deployment",
			action: vcfautomation.ActionRequest{ActionName: "Reboot"},
		},
		{
			name:   "action present but not currently valid",
			action: vcfautomation.ActionRequest{ActionName: "Delete"},
		},
		{
			name:   "action carries no id",
			action: vcfautomation.ActionRequest{ActionName: "PowerOff"},
			mutate: func(s *scenario) {
				s.actions = []map[string]any{{"name": "PowerOff", "valid": true}}
			},
		},
		{
			name:   "submit response carries no request id",
			action: vcfautomation.ActionRequest{ActionName: "PowerOff"},
			mutate: func(s *scenario) {
				s.submitBodyRaw = []byte(`{"status":"PENDING","name":"n","requestedBy":"u","completedTasks":0,"totalTasks":3,"createdAt":"2026-04-17T09:14:22.481Z"}`)
			},
		},
		{
			name:   "poll body is not JSON",
			action: vcfautomation.ActionRequest{ActionName: "PowerOff"},
			mutate: func(s *scenario) { s.getBodyRaw = []byte("<html>gateway</html>") },
		},
		{
			name:   "poll reports a status outside the contract enum",
			action: vcfautomation.ActionRequest{ActionName: "PowerOff"},
			mutate: func(s *scenario) { s.pollStatuses = []string{"PARTIALLY_SUCCESSFUL"} },
		},
		{
			name:   "poll reports an empty status",
			action: vcfautomation.ActionRequest{ActionName: "PowerOff"},
			mutate: func(s *scenario) { s.pollStatuses = []string{""} },
		},
		{
			name:   "poll response omits a required contract member",
			action: vcfautomation.ActionRequest{ActionName: "PowerOff"},
			mutate: func(s *scenario) {
				s.getBodyRaw = []byte(`{"id":"` + testRequestID + `","status":"SUCCESSFUL","name":"n","requestedBy":"u","completedTasks":1,"createdAt":"2026-04-17T09:14:22.481Z"}`)
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := newScenario("SUCCESSFUL")
			if tc.mutate != nil {
				tc.mutate(s)
			}
			srv := startMock(t, s)
			client := newClient(t, srv)
			client.PollTimeout = 2 * time.Second

			got, err := client.RunDeploymentAction(context.Background(), testDeploymentID, tc.action)
			if got != nil {
				t.Errorf("Request = %+v, want nil", got)
			}
			var protoErr *vcfautomation.ProtocolError
			if !errors.As(err, &protoErr) {
				t.Fatalf("error = %v, want *ProtocolError", err)
			}
			if protoErr.Reason == "" {
				t.Error("ProtocolError.Reason is empty")
			}
		})
	}
}

func TestUnsuccessfulRequestUsesTheSubmittedRequestID(t *testing.T) {
	s := newScenario("FAILED")
	s.getBodyRaw = []byte(`{"status":"FAILED","details":"action failed","name":"n","requestedBy":"u","completedTasks":1,"totalTasks":1,"createdAt":"2026-04-17T09:14:22.481Z"}`)
	srv := startMock(t, s)
	client := newClient(t, srv)

	got, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
	var failed *vcfautomation.RequestFailedError
	if !errors.As(err, &failed) {
		t.Fatalf("error = %v, want *RequestFailedError", err)
	}
	if failed.RequestID != testRequestID {
		t.Errorf("RequestFailedError.RequestID = %q, want submitted id %q", failed.RequestID, testRequestID)
	}
	if got == nil || got.Status != "FAILED" {
		t.Fatalf("Request = %+v, want terminal FAILED request", got)
	}
}

// ---------------------------------------------------------------------------
// construction, safety and concurrency
// ---------------------------------------------------------------------------

func TestNewClientRejectsUnusableInput(t *testing.T) {
	cases := []struct {
		name    string
		baseURL string
		token   string
	}{
		{"empty base URL", "", testToken},
		{"non-HTTP scheme", "ftp://vcfa.example.net", testToken},
		{"unparseable base URL", "http://[::1", testToken},
		{"blank token", "https://vcfa.example.net", "   "},
		{"empty token", "https://vcfa.example.net", ""},
		{"header-unsafe token", "https://vcfa.example.net", "abc\ndef"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			client, err := vcfautomation.NewClient(tc.baseURL, tc.token, nil)
			if err == nil {
				t.Fatalf("NewClient(%q, ...) = %v, want an error", tc.baseURL, client)
			}
			if client != nil {
				t.Error("NewClient returned a non-nil client alongside an error")
			}
		})
	}
}

func TestRunDeploymentActionRejectsUnusableInput(t *testing.T) {
	srv := startMock(t, newScenario("SUCCESSFUL"))
	client := newClient(t, srv)

	t.Run("nil context", func(t *testing.T) {
		//nolint:staticcheck // deliberately passing a nil context
		if _, err := client.RunDeploymentAction(nil, testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"}); err == nil {
			t.Fatal("RunDeploymentAction(nil, ...) = nil error, want an error")
		}
	})
	t.Run("blank deployment id", func(t *testing.T) {
		if _, err := client.RunDeploymentAction(context.Background(), "  ", vcfautomation.ActionRequest{ActionName: "PowerOff"}); err == nil {
			t.Fatal("blank deployment id was accepted")
		}
	})
	t.Run("blank action name", func(t *testing.T) {
		if _, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{}); err == nil {
			t.Fatal("blank action name was accepted")
		}
	})

	if n := len(srv.Requests()); n != 0 {
		t.Errorf("%d requests reached the server, want none: input is rejected before any call", n)
	}
}

func TestErrorsDoNotDiscloseTheAccessToken(t *testing.T) {
	s := newScenario("SUCCESSFUL")
	s.submitStatus = http.StatusForbidden
	srv := startMock(t, s)
	client := newClient(t, srv)

	_, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
	if err == nil {
		t.Fatal("want an error")
	}
	for _, rendered := range []string{err.Error(), fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err)} {
		if strings.Contains(rendered, testToken) {
			t.Fatalf("error text discloses the access token: %s", rendered)
		}
	}

	if _, err := vcfautomation.NewClient("ftp://vcfa.example.net", testToken, nil); err != nil {
		if strings.Contains(err.Error(), testToken) {
			t.Fatalf("NewClient error text discloses the access token: %v", err)
		}
	}

	leakingTransport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		return nil, errors.New("transport rejected Authorization: Bearer " + testToken)
	})
	transportClient, err := vcfautomation.NewClient("https://vcfa.example.net", testToken, &http.Client{Transport: leakingTransport})
	if err != nil {
		t.Fatalf("NewClient with custom transport: %v", err)
	}
	_, err = transportClient.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{ActionName: "PowerOff"})
	if err == nil {
		t.Fatal("custom transport returned an error, but RunDeploymentAction returned nil")
	}
	for _, rendered := range []string{err.Error(), fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err)} {
		if strings.Contains(rendered, testToken) {
			t.Fatalf("transport error text discloses the access token: %s", rendered)
		}
	}
}

func TestClientIsSafeForConcurrentUse(t *testing.T) {
	s := newScenario("INPROGRESS", "INPROGRESS", "SUCCESSFUL")
	srv := startMock(t, s)
	client := newClient(t, srv)

	var wg sync.WaitGroup
	errs := make(chan error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got, err := client.RunDeploymentAction(context.Background(), testDeploymentID, vcfautomation.ActionRequest{
				ActionName: "PowerOff",
				Inputs:     map[string]any{"graceful": true},
			})
			if err != nil {
				errs <- err
				return
			}
			if got == nil || got.ID != testRequestID {
				errs <- fmt.Errorf("unexpected request %+v", got)
			}
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Errorf("concurrent RunDeploymentAction: %v", err)
	}
}
