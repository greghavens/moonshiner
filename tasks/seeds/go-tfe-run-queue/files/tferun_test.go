// Acceptance harness for the tferun package: a loopback fake Terraform
// Enterprise API plus a separate fake archivist host, exercising the wire
// contract pinned in docs/contract.json. No real TFE, no real credentials.
// Protected — do not modify. Run: go test -race -timeout 30s ./...
package tferun_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	tferun "go-tfe-run-queue"
)

const (
	token = "test-token-abc123" // dummy; must never reach the archivist host
	wsID  = "ws-VgtLbCPKb4qJ5t9c"
	cvID  = "cv-ntv3HbhJqvFzamy7"
	runID = "run-CZcmD7eagjhyX0vN"
)

var archive = []byte("\x1f\x8b fake tar.gz payload for ws-VgtLbCPKb4qJ5t9c")

type recorded struct {
	Method      string
	Path        string
	Auth        string
	HasAuth     bool
	ContentType string
	Body        []byte
}

type runPoll struct {
	Status      string
	Confirmable bool
}

type fixture struct {
	mu        sync.Mutex
	reqs      []recorded // requests to the TFE API host
	uploads   []recorded // requests to the archivist host
	uploadURL string

	cvStates        []string  // queue for GET configuration-versions/:id (last repeats)
	runStates       []runPoll // queue for GET runs/:id (last repeats)
	runCreateStatus int       // 0 = 201 created; otherwise error status
	runCreateBody   string

	tfe *httptest.Server
	arc *httptest.Server
}

func (f *fixture) record(dst *[]recorded, r *http.Request) recorded {
	body, _ := io.ReadAll(r.Body)
	_, hasAuth := r.Header["Authorization"]
	rec := recorded{
		Method:      r.Method,
		Path:        r.URL.Path,
		Auth:        r.Header.Get("Authorization"),
		HasAuth:     hasAuth,
		ContentType: r.Header.Get("Content-Type"),
		Body:        body,
	}
	f.mu.Lock()
	*dst = append(*dst, rec)
	f.mu.Unlock()
	return rec
}

func (f *fixture) nextCV() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	st := f.cvStates[0]
	if len(f.cvStates) > 1 {
		f.cvStates = f.cvStates[1:]
	}
	return st
}

func (f *fixture) nextRun() runPoll {
	f.mu.Lock()
	defer f.mu.Unlock()
	p := f.runStates[0]
	if len(f.runStates) > 1 {
		f.runStates = f.runStates[1:]
	}
	return p
}

func (f *fixture) apiRequests() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recorded, len(f.reqs))
	copy(out, f.reqs)
	return out
}

func (f *fixture) uploadRequests() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recorded, len(f.uploads))
	copy(out, f.uploads)
	return out
}

func jsonAPI(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "application/vnd.api+json")
	w.WriteHeader(status)
	io.WriteString(w, body)
}

const notFoundDoc = `{"errors":[{"status":"404","title":"not found","detail":"the requested resource could not be found, or user unauthorized to perform action"}]}`

func newFixture(t *testing.T) (*fixture, *tferun.Client) {
	t.Helper()
	f := &fixture{
		cvStates:  []string{"uploaded"},
		runStates: []runPoll{{Status: "pending"}},
	}

	f.arc = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.record(&f.uploads, r)
		if r.Method == http.MethodPut && r.URL.Path == "/v1/object/obj-7f9a" {
			w.WriteHeader(http.StatusOK)
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	t.Cleanup(f.arc.Close)
	f.uploadURL = f.arc.URL + "/v1/object/obj-7f9a"

	f.tfe = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.record(&f.reqs, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v2/workspaces/"+wsID+"/configuration-versions":
			jsonAPI(w, 201, `{"data":{"id":"`+cvID+`","type":"configuration-versions","attributes":{"status":"pending","upload-url":"`+f.uploadURL+`"}}}`)
		case r.Method == http.MethodGet && r.URL.Path == "/api/v2/configuration-versions/"+cvID:
			jsonAPI(w, 200, `{"data":{"id":"`+cvID+`","type":"configuration-versions","attributes":{"status":"`+f.nextCV()+`","upload-url":"`+f.uploadURL+`"}}}`)
		case r.Method == http.MethodPost && r.URL.Path == "/api/v2/runs":
			if f.runCreateStatus != 0 {
				jsonAPI(w, f.runCreateStatus, f.runCreateBody)
				return
			}
			jsonAPI(w, 201, `{"data":{"id":"`+runID+`","type":"runs","attributes":{"status":"pending","actions":{"is-confirmable":false}}}}`)
		case r.Method == http.MethodGet && r.URL.Path == "/api/v2/runs/"+runID:
			p := f.nextRun()
			conf := "false"
			if p.Confirmable {
				conf = "true"
			}
			jsonAPI(w, 200, `{"data":{"id":"`+runID+`","type":"runs","attributes":{"status":"`+p.Status+`","actions":{"is-confirmable":`+conf+`}}}}`)
		case r.Method == http.MethodPost && r.URL.Path == "/api/v2/runs/"+runID+"/actions/apply":
			w.WriteHeader(http.StatusAccepted)
		default:
			jsonAPI(w, 404, notFoundDoc)
		}
	}))
	t.Cleanup(f.tfe.Close)

	c := tferun.NewClient(f.tfe.URL, token, f.tfe.Client())
	return f, c
}

func mustJSON(t *testing.T, raw []byte, into any) {
	t.Helper()
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("request body is not valid JSON: %v\nbody: %s", err, raw)
	}
}

type resourceBody struct {
	Data struct {
		Type          string         `json:"type"`
		Attributes    map[string]any `json:"attributes"`
		Relationships map[string]struct {
			Data struct {
				Type string `json:"type"`
				ID   string `json:"id"`
			} `json:"data"`
		} `json:"relationships"`
	} `json:"data"`
}

func TestCreateConfigVersionContract(t *testing.T) {
	f, c := newFixture(t)
	cv, err := c.CreateConfigVersion(context.Background(), wsID)
	if err != nil {
		t.Fatalf("CreateConfigVersion: %v", err)
	}
	if cv.ID != cvID {
		t.Errorf("configuration version id = %q, want %q", cv.ID, cvID)
	}
	if cv.Status != "pending" {
		t.Errorf("initial status = %q, want pending", cv.Status)
	}
	if cv.UploadURL != f.uploadURL {
		t.Errorf("upload url = %q, want %q", cv.UploadURL, f.uploadURL)
	}

	reqs := f.apiRequests()
	if len(reqs) != 1 {
		t.Fatalf("expected exactly 1 API request, got %d", len(reqs))
	}
	r := reqs[0]
	if r.Method != http.MethodPost {
		t.Errorf("method = %s, want POST", r.Method)
	}
	if r.Path != "/api/v2/workspaces/"+wsID+"/configuration-versions" {
		t.Errorf("path = %q", r.Path)
	}
	if r.Auth != "Bearer "+token {
		t.Errorf("Authorization = %q, want %q", r.Auth, "Bearer "+token)
	}
	if r.ContentType != "application/vnd.api+json" {
		t.Errorf("Content-Type = %q, want application/vnd.api+json", r.ContentType)
	}
	var body resourceBody
	mustJSON(t, r.Body, &body)
	if body.Data.Type != "configuration-versions" {
		t.Errorf("data.type = %q, want configuration-versions", body.Data.Type)
	}
	aqr, present := body.Data.Attributes["auto-queue-runs"]
	if !present {
		t.Fatalf("attributes must pin auto-queue-runs explicitly (it defaults to true server-side)")
	}
	if aqr != false {
		t.Errorf("auto-queue-runs = %v, want false", aqr)
	}
}

func TestUploadArchiveIsPreSignedAndUnauthenticated(t *testing.T) {
	f, c := newFixture(t)
	if err := c.UploadConfig(context.Background(), f.uploadURL, archive); err != nil {
		t.Fatalf("UploadConfig: %v", err)
	}
	ups := f.uploadRequests()
	if len(ups) != 1 {
		t.Fatalf("expected exactly 1 upload request, got %d", len(ups))
	}
	u := ups[0]
	if u.Method != http.MethodPut {
		t.Errorf("upload method = %s, want PUT", u.Method)
	}
	if u.Path != "/v1/object/obj-7f9a" {
		t.Errorf("upload path = %q", u.Path)
	}
	if u.ContentType != "application/octet-stream" {
		t.Errorf("upload Content-Type = %q, want application/octet-stream", u.ContentType)
	}
	if !bytes.Equal(u.Body, archive) {
		t.Errorf("uploaded bytes differ from the archive (%d vs %d bytes)", len(u.Body), len(archive))
	}
	if u.HasAuth {
		t.Errorf("bearer token leaked to the pre-signed upload host: Authorization = %q", u.Auth)
	}
	if len(f.apiRequests()) != 0 {
		t.Errorf("upload must go straight to the upload URL, not through /api/v2")
	}
}

func TestWaitForUploadPollsUntilUploaded(t *testing.T) {
	f, c := newFixture(t)
	f.cvStates = []string{"pending", "fetching", "uploaded"}
	var paces int
	c.Pace = func(ctx context.Context) error { paces++; return nil }

	cv, err := c.WaitForUpload(context.Background(), cvID)
	if err != nil {
		t.Fatalf("WaitForUpload: %v", err)
	}
	if cv.Status != "uploaded" {
		t.Errorf("final status = %q, want uploaded", cv.Status)
	}
	var gets int
	for _, r := range f.apiRequests() {
		if r.Method == http.MethodGet && r.Path == "/api/v2/configuration-versions/"+cvID {
			gets++
		}
	}
	if gets != 3 {
		t.Errorf("show requests = %d, want 3 (pending, fetching, uploaded)", gets)
	}
	if paces != 2 {
		t.Errorf("pace calls = %d, want 2 (between successive polls only)", paces)
	}
}

func TestWaitForUploadErroredIsTerminal(t *testing.T) {
	f, c := newFixture(t)
	f.cvStates = []string{"pending", "errored"}
	c.Pace = func(ctx context.Context) error { return nil }
	_, err := c.WaitForUpload(context.Background(), cvID)
	if err == nil {
		t.Fatalf("WaitForUpload must fail when the configuration version status is errored")
	}
	if !strings.Contains(err.Error(), "errored") {
		t.Errorf("error should name the terminal status errored, got: %v", err)
	}
}

func TestCreateRunRelationships(t *testing.T) {
	f, c := newFixture(t)
	run, err := c.CreateRun(context.Background(), wsID, cvID, "deploy build 4821")
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if run.ID != runID {
		t.Errorf("run id = %q, want %q", run.ID, runID)
	}
	if run.Status != "pending" {
		t.Errorf("run status = %q, want pending", run.Status)
	}

	reqs := f.apiRequests()
	if len(reqs) != 1 {
		t.Fatalf("expected exactly 1 API request, got %d", len(reqs))
	}
	r := reqs[0]
	if r.Method != http.MethodPost || r.Path != "/api/v2/runs" {
		t.Errorf("request = %s %s, want POST /api/v2/runs", r.Method, r.Path)
	}
	if r.ContentType != "application/vnd.api+json" {
		t.Errorf("Content-Type = %q, want application/vnd.api+json", r.ContentType)
	}
	var body resourceBody
	mustJSON(t, r.Body, &body)
	if body.Data.Type != "runs" {
		t.Errorf("data.type = %q, want runs", body.Data.Type)
	}
	if got := body.Data.Attributes["message"]; got != "deploy build 4821" {
		t.Errorf("attributes.message = %v, want the launch message", got)
	}
	ws, ok := body.Data.Relationships["workspace"]
	if !ok {
		t.Fatalf("relationships.workspace missing")
	}
	if ws.Data.Type != "workspaces" || ws.Data.ID != wsID {
		t.Errorf("workspace relationship = %+v, want type workspaces id %s", ws.Data, wsID)
	}
	cv, ok := body.Data.Relationships["configuration-version"]
	if !ok {
		t.Fatalf("relationships.configuration-version missing")
	}
	if cv.Data.Type != "configuration-versions" || cv.Data.ID != cvID {
		t.Errorf("configuration-version relationship = %+v, want type configuration-versions id %s", cv.Data, cvID)
	}
}

func launch(t *testing.T, f *fixture, c *tferun.Client, auto bool) (*tferun.LaunchResult, error) {
	t.Helper()
	return tferun.Launch(context.Background(), c, wsID, archive, tferun.LaunchOptions{
		Message:   "deploy build 4821",
		AutoApply: auto,
	})
}

func applyCalls(f *fixture) []recorded {
	var out []recorded
	for _, r := range f.apiRequests() {
		if r.Method == http.MethodPost && r.Path == "/api/v2/runs/"+runID+"/actions/apply" {
			out = append(out, r)
		}
	}
	return out
}

func TestLaunchAutoApplyHappyPath(t *testing.T) {
	f, c := newFixture(t)
	f.cvStates = []string{"pending", "uploaded"}
	f.runStates = []runPoll{
		{Status: "pending"},
		{Status: "planning"},
		{Status: "planned", Confirmable: true},
		{Status: "applying"},
		{Status: "applied"},
	}
	res, err := launch(t, f, c, true)
	if err != nil {
		t.Fatalf("Launch: %v", err)
	}
	if res.Status != "applied" {
		t.Errorf("final status = %q, want applied", res.Status)
	}
	if !res.Applied {
		t.Errorf("Applied = false, want true")
	}
	if res.NeedsConfirmation {
		t.Errorf("NeedsConfirmation = true, want false")
	}
	if res.RunID != runID {
		t.Errorf("RunID = %q, want %q", res.RunID, runID)
	}
	if res.ConfigVersionID != cvID {
		t.Errorf("ConfigVersionID = %q, want %q", res.ConfigVersionID, cvID)
	}

	if got := len(f.uploadRequests()); got != 1 {
		t.Errorf("uploads = %d, want 1", got)
	}

	applies := applyCalls(f)
	if len(applies) != 1 {
		t.Fatalf("apply actions = %d, want exactly 1", len(applies))
	}
	if applies[0].ContentType != "application/vnd.api+json" {
		t.Errorf("apply Content-Type = %q, want application/vnd.api+json", applies[0].ContentType)
	}
	var comment struct {
		Comment string `json:"comment"`
	}
	mustJSON(t, applies[0].Body, &comment)
	if comment.Comment != "deploy build 4821" {
		t.Errorf("apply comment = %q, want the launch message", comment.Comment)
	}

	// Ordering: create CV -> wait for upload -> create run -> polls -> apply.
	idx := map[string]int{}
	for i, r := range f.apiRequests() {
		key := r.Method + " " + r.Path
		if _, seen := idx[key]; !seen {
			idx[key] = i
		}
	}
	createCV := idx["POST /api/v2/workspaces/"+wsID+"/configuration-versions"]
	showCV := idx["GET /api/v2/configuration-versions/"+cvID]
	createRun := idx["POST /api/v2/runs"]
	apply := idx["POST /api/v2/runs/"+runID+"/actions/apply"]
	if !(createCV < showCV && showCV < createRun && createRun < apply) {
		t.Errorf("request order wrong: createCV=%d showCV=%d createRun=%d apply=%d", createCV, showCV, createRun, apply)
	}
}

func TestLaunchPlannedAndFinishedNeedsNoApply(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{
		{Status: "pending"},
		{Status: "planning"},
		{Status: "planned_and_finished"},
	}
	res, err := launch(t, f, c, true)
	if err != nil {
		t.Fatalf("Launch: %v", err)
	}
	if res.Status != "planned_and_finished" {
		t.Errorf("final status = %q, want planned_and_finished", res.Status)
	}
	if res.Applied {
		t.Errorf("Applied = true, want false (nothing to apply)")
	}
	if got := len(applyCalls(f)); got != 0 {
		t.Errorf("apply actions = %d, want 0", got)
	}
}

func TestLaunchWithoutAutoApplyStopsConfirmable(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{
		{Status: "planning"},
		{Status: "planned", Confirmable: true},
	}
	res, err := launch(t, f, c, false)
	if err != nil {
		t.Fatalf("Launch: %v", err)
	}
	if !res.NeedsConfirmation {
		t.Errorf("NeedsConfirmation = false, want true")
	}
	if res.Status != "planned" {
		t.Errorf("status = %q, want planned", res.Status)
	}
	if res.Applied {
		t.Errorf("Applied = true, want false")
	}
	if got := len(applyCalls(f)); got != 0 {
		t.Errorf("apply actions = %d, want 0 without AutoApply", got)
	}
}

func TestLaunchPlanFailure(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{
		{Status: "pending"},
		{Status: "planning"},
		{Status: "errored"},
	}
	res, err := launch(t, f, c, true)
	if err == nil {
		t.Fatalf("Launch must fail when the run errors")
	}
	if res != nil {
		t.Errorf("result must be nil on failure, got %+v", res)
	}
	var rf *tferun.RunFailure
	if !errors.As(err, &rf) {
		t.Fatalf("error must unwrap to *tferun.RunFailure, got %T: %v", err, err)
	}
	if rf.RunID != runID {
		t.Errorf("RunFailure.RunID = %q, want %q", rf.RunID, runID)
	}
	if rf.Status != "errored" {
		t.Errorf("RunFailure.Status = %q, want errored", rf.Status)
	}
	if rf.Stage != "plan" {
		t.Errorf("RunFailure.Stage = %q, want plan (run never confirmed)", rf.Stage)
	}
	if !strings.Contains(err.Error(), runID) || !strings.Contains(err.Error(), "errored") {
		t.Errorf("failure message should carry run id and status, got: %v", err)
	}
}

func TestLaunchApplyStageFailure(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{
		{Status: "planned", Confirmable: true},
		{Status: "applying"},
		{Status: "errored"},
	}
	_, err := launch(t, f, c, true)
	var rf *tferun.RunFailure
	if !errors.As(err, &rf) {
		t.Fatalf("error must unwrap to *tferun.RunFailure, got %T: %v", err, err)
	}
	if rf.Stage != "apply" {
		t.Errorf("Stage = %q, want apply (failure after confirmation)", rf.Stage)
	}
	if got := len(applyCalls(f)); got != 1 {
		t.Errorf("apply actions = %d, want exactly 1", got)
	}
}

func TestLaunchPolicySoftFailStops(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{
		{Status: "planning"},
		{Status: "policy_checking"},
		{Status: "policy_soft_failed"},
	}
	_, err := launch(t, f, c, true)
	var rf *tferun.RunFailure
	if !errors.As(err, &rf) {
		t.Fatalf("error must unwrap to *tferun.RunFailure, got %T: %v", err, err)
	}
	if rf.Status != "policy_soft_failed" || rf.Stage != "policy" {
		t.Errorf("failure = status %q stage %q, want policy_soft_failed/policy", rf.Status, rf.Stage)
	}
}

func TestLaunchPolicyOverrideIsAPolicyFailure(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{
		{Status: "policy_checking"},
		{Status: "policy_override"},
	}
	_, err := launch(t, f, c, true)
	var rf *tferun.RunFailure
	if !errors.As(err, &rf) {
		t.Fatalf("error must unwrap to *tferun.RunFailure, got %T: %v", err, err)
	}
	if rf.Status != "policy_override" || rf.Stage != "policy" {
		t.Errorf("failure = status %q stage %q, want policy_override/policy", rf.Status, rf.Stage)
	}
	if got := len(applyCalls(f)); got != 0 {
		t.Errorf("the launcher must never override or confirm a hard-stopped policy run, saw %d apply calls", got)
	}
}

func TestJSONAPIErrorDecoding(t *testing.T) {
	f, c := newFixture(t)
	f.runCreateStatus = 404
	f.runCreateBody = notFoundDoc
	_, err := c.CreateRun(context.Background(), wsID, cvID, "x")
	if err == nil {
		t.Fatalf("CreateRun must fail on a 404 response")
	}
	var ae *tferun.APIError
	if !errors.As(err, &ae) {
		t.Fatalf("error must unwrap to *tferun.APIError, got %T: %v", err, err)
	}
	if ae.StatusCode != 404 {
		t.Errorf("StatusCode = %d, want 404", ae.StatusCode)
	}
	if len(ae.Errors) != 1 {
		t.Fatalf("parsed errors = %d, want 1", len(ae.Errors))
	}
	e := ae.Errors[0]
	if e.Status != "404" || e.Title != "not found" {
		t.Errorf("error object = %+v, want status 404 / title not found", e)
	}
	if !strings.Contains(e.Detail, "user unauthorized to perform action") {
		t.Errorf("detail should preserve the authorization-masking wording, got %q", e.Detail)
	}
	if !strings.Contains(err.Error(), "user unauthorized") {
		t.Errorf("APIError.Error() should surface the detail, got %q", err.Error())
	}
}

func TestLaunchHonorsCancellation(t *testing.T) {
	f, c := newFixture(t)
	f.runStates = []runPoll{{Status: "planning"}} // never terminal
	ctx, cancel := context.WithCancel(context.Background())
	polls := 0
	c.Pace = func(ctx context.Context) error {
		polls++
		if polls >= 2 {
			cancel()
		}
		return ctx.Err()
	}
	_, err := tferun.Launch(ctx, c, wsID, archive, tferun.LaunchOptions{Message: "m", AutoApply: true})
	if err == nil {
		t.Fatalf("Launch must return an error once the context is canceled")
	}
	if !errors.Is(err, context.Canceled) {
		t.Errorf("error must satisfy errors.Is(err, context.Canceled), got: %v", err)
	}
}
