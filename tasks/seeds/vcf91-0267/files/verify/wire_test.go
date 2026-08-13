// Package verify holds the protected wire-shape verification for the
// reportclient package. It asserts the exact bytes and URLs the client puts on
// the wire against docs/contract.json, using only the in-process mock in
// internal/mockops. No live VMware endpoint is contacted.
//
// Do not edit this package. It is replaced wholesale during grading.
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"vcfops.local/opsreport/internal/contract"
	"vcfops.local/opsreport/internal/mockops"
	"vcfops.local/opsreport/reportclient"
)

const (
	reportDefinitionID = "8b1c3a12-4f6d-4a1e-9c33-7d5e2b0a91f4"
	resourceID         = "2a7d5e90-1c4b-4e33-8f21-6b9a0d3c5e18"
)

func boolPtr(b bool) *bool { return &b }

func loadContract(t *testing.T) *contract.Contract {
	t.Helper()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	return c
}

// assertOnlyContractOperations proves the client never touched a path outside
// the contract. The mock records unmatched requests with an empty OperationID.
func assertOnlyContractOperations(t *testing.T, reqs []mockops.Request) {
	t.Helper()
	for _, r := range reqs {
		if r.OperationID == "" {
			t.Errorf("request %d %s %s matched no contract operation", r.Index, r.Method, r.Path)
		}
	}
}

func operationSequence(reqs []mockops.Request) []string {
	out := make([]string, 0, len(reqs))
	for _, r := range reqs {
		if r.OperationID == "" {
			out = append(out, fmt.Sprintf("<unmatched %s %s>", r.Method, r.Path))
			continue
		}
		out = append(out, r.OperationID)
	}
	return out
}

func filter(reqs []mockops.Request, operationID string) []mockops.Request {
	var out []mockops.Request
	for _, r := range reqs {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

func bodyKeys(t *testing.T, r mockops.Request) []string {
	t.Helper()
	keys, err := r.BodyKeys()
	if err != nil {
		t.Fatalf("request %d (%s): %v", r.Index, r.OperationID, err)
	}
	return keys
}

func TestNewValidatesConfigWithoutIO(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		cfg  reportclient.Config
	}{
		{"missing BaseURL", reportclient.Config{Username: "user", Password: "pass"}},
		{"BaseURL without scheme", reportclient.Config{BaseURL: "appliance.example", Username: "user", Password: "pass"}},
		{"missing Username", reportclient.Config{BaseURL: "https://appliance.example", Password: "pass"}},
		{"missing Password", reportclient.Config{BaseURL: "https://appliance.example", Username: "user"}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			client, err := reportclient.New(tc.cfg)
			if err == nil || client != nil {
				t.Fatalf("New(%+v) = (%v, %v), want nil client and an error", tc.cfg, client, err)
			}
			if !errors.Is(err, reportclient.ErrInvalidRequest) {
				t.Errorf("New error = %v, want errors.Is(err, ErrInvalidRequest)", err)
			}
		})
	}

	srv := mockops.Start(t, mockops.Scenario{})
	sc := srv.Scenario()
	client, err := reportclient.New(reportclient.Config{
		BaseURL:    srv.URL(),
		HTTPClient: srv.Client(),
		Username:   sc.Username,
		Password:   sc.Password,
	})
	if err != nil || client == nil {
		t.Fatalf("New(valid config) = (%v, %v), want a client and no error", client, err)
	}
	if got := len(srv.Requests()); got != 0 {
		t.Errorf("New made %d HTTP requests, want none", got)
	}
}

// TestRequiredOnlyRequestOmitsEveryUnsetOptionalField is the core wire-shape
// assertion: a request built from required fields alone must serialize to
// exactly the required properties, and the optional download format must be
// absent from the URL rather than sent empty.
func TestRequiredOnlyRequestOmitsEveryUnsetOptionalField(t *testing.T) {
	t.Parallel()
	c := loadContract(t)
	srv := mockops.Start(t, mockops.Scenario{
		PollStatuses: []string{"QUEUED", "RUNNING", "COMPLETED"},
	})
	sc := srv.Scenario()

	client, err := reportclient.New(reportclient.Config{
		BaseURL:         srv.URL(),
		HTTPClient:      srv.Client(),
		Username:        sc.Username,
		Password:        sc.Password,
		PollInterval:    time.Millisecond,
		MaxPollAttempts: 10,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	res, err := client.GenerateReport(context.Background(), reportclient.ReportRequest{
		ReportDefinitionID: reportDefinitionID,
		ResourceID:         resourceID,
	}, "")
	if err != nil {
		t.Fatalf("GenerateReport: %v", err)
	}

	reqs := srv.Requests()
	assertOnlyContractOperations(t, reqs)

	wantSeq := []string{"acquireToken", "createReport", "getReport", "getReport", "getReport", "downloadReport"}
	if got := operationSequence(reqs); !reflect.DeepEqual(got, wantSeq) {
		t.Fatalf("operation sequence = %v, want %v", got, wantSeq)
	}

	// acquireToken: authSource is unset, so it must not appear at all.
	acquire := filter(reqs, "acquireToken")[0]
	if got, want := bodyKeys(t, acquire), []string{"password", "username"}; !reflect.DeepEqual(got, want) {
		t.Errorf("acquireToken body keys = %v, want %v (an unset authSource must be omitted, not sent empty)", got, want)
	}
	if got := acquire.Header.Get(c.Authorization.HeaderName); got != "" {
		t.Errorf("acquireToken carried %s = %q, want no authorization header", c.Authorization.HeaderName, got)
	}
	if got := acquire.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("acquireToken Content-Type = %q, want application/json", got)
	}
	if got := acquire.Header.Get("Accept"); got != "application/json" {
		t.Errorf("acquireToken Accept = %q, want application/json (the operation also offers application/xml)", got)
	}
	if want := c.API.BasePath + c.Operations["acquireToken"].Path; acquire.Path != want {
		t.Errorf("acquireToken path = %q, want %q", acquire.Path, want)
	}

	// createReport: every optional property is unset and must be absent.
	create := filter(reqs, "createReport")[0]
	if got, want := bodyKeys(t, create), []string{"reportDefinitionId", "resourceId"}; !reflect.DeepEqual(got, want) {
		t.Errorf("createReport body keys = %v, want %v (unset optional properties must be omitted)", got, want)
	}
	body, err := create.BodyMap()
	if err != nil {
		t.Fatalf("createReport body: %v", err)
	}
	if body["reportDefinitionId"] != reportDefinitionID {
		t.Errorf("createReport reportDefinitionId = %v, want %q", body["reportDefinitionId"], reportDefinitionID)
	}
	if body["resourceId"] != resourceID {
		t.Errorf("createReport resourceId = %v, want %q", body["resourceId"], resourceID)
	}
	if got := create.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("createReport Content-Type = %q, want application/json", got)
	}
	if got := create.Header.Get("Accept"); got != "application/json" {
		t.Errorf("createReport Accept = %q, want application/json", got)
	}
	assertAuthorized(t, c, sc.Token, create)

	// getReport: the identifier goes in the path, and there is no query string.
	polls := filter(reqs, "getReport")
	wantPollPath := c.API.BasePath + "/api/reports/" + sc.ReportID
	for _, p := range polls {
		if p.Path != wantPollPath {
			t.Errorf("getReport %d path = %q, want %q", p.Index, p.Path, wantPollPath)
		}
		if p.RawQuery != "" {
			t.Errorf("getReport %d query = %q, want no query string", p.Index, p.RawQuery)
		}
		if len(p.Body) != 0 {
			t.Errorf("getReport %d sent a body of %d bytes, want none", p.Index, len(p.Body))
		}
		if got := p.Header.Get("Accept"); got != "application/json" {
			t.Errorf("getReport %d Accept = %q, want application/json", p.Index, got)
		}
		assertAuthorized(t, c, sc.Token, p)
	}

	// downloadReport: format was not requested, so the URL must carry no query
	// string whatsoever - not "?format=" and not "?format".
	dl := filter(reqs, "downloadReport")[0]
	if want := c.API.BasePath + "/api/reports/" + sc.ReportID + "/download"; dl.Path != want {
		t.Errorf("downloadReport path = %q, want %q", dl.Path, want)
	}
	if dl.RawQuery != "" {
		t.Errorf("downloadReport query = %q, want no query string (an unset format must be omitted)", dl.RawQuery)
	}
	if len(dl.Body) != 0 {
		t.Errorf("downloadReport sent a body of %d bytes, want none", len(dl.Body))
	}
	assertAuthorized(t, c, sc.Token, dl)

	// The result must reflect the terminal poll, not the createReport response.
	if res.Report.Status != c.ReportStatus.Successful {
		t.Errorf("Result.Report.Status = %q, want %q", res.Report.Status, c.ReportStatus.Successful)
	}
	if res.Report.ID != sc.ReportID {
		t.Errorf("Result.Report.ID = %q, want %q", res.Report.ID, sc.ReportID)
	}
	if res.Report.ReportDefinitionID != reportDefinitionID {
		t.Errorf("Result.Report.ReportDefinitionID = %q, want %q", res.Report.ReportDefinitionID, reportDefinitionID)
	}
	if res.Report.ResourceID != resourceID {
		t.Errorf("Result.Report.ResourceID = %q, want %q", res.Report.ResourceID, resourceID)
	}
	if res.Report.Owner != sc.Username {
		t.Errorf("Result.Report.Owner = %q, want %q", res.Report.Owner, sc.Username)
	}
	if res.Report.CompletionTime == "" {
		t.Error("Result.Report.CompletionTime is empty, want the terminal report's completion time")
	}
	if res.PollCount != 3 {
		t.Errorf("Result.PollCount = %d, want 3", res.PollCount)
	}
	if string(res.Content) != string(sc.DownloadBody) {
		t.Errorf("Result.Content = %q, want %q", res.Content, sc.DownloadBody)
	}
	if res.ContentType != sc.DownloadContentType {
		t.Errorf("Result.ContentType = %q, want %q", res.ContentType, sc.DownloadContentType)
	}
}

// TestOptionalFieldsAreSentWhenSet is the other half of the omit-empty rule: a
// field the caller did set must appear, including a boolean deliberately set to
// false and a nested object whose own optional fields stay omitted.
func TestOptionalFieldsAreSentWhenSet(t *testing.T) {
	t.Parallel()
	c := loadContract(t)

	tests := []struct {
		name            string
		authSource      string
		req             reportclient.ReportRequest
		format          string
		wantAcquireKey  []string
		wantCreateKeys  []string
		wantQuery       string
		wantContentType string
		check           func(t *testing.T, body map[string]any)
	}{
		{
			name:       "every optional field populated",
			authSource: "vIDMAuthSource",
			req: reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
				Name:               "Cluster Capacity - Weekly",
				Description:        "Weekly capacity rollup",
				Subject:            []string{"ClusterComputeResource", "HostSystem"},
				Publish:            boolPtr(true),
				TraversalSpec: &reportclient.TraversalSpec{
					Name:                       "vSphere Hosts and Clusters",
					Description:                "Traverse the full cluster hierarchy",
					RootAdapterKindKey:         "VMWARE",
					RootResourceKindKey:        "ClusterComputeResource",
					AdapterInstanceAssociation: boolPtr(false),
				},
			},
			format:          "csv",
			wantAcquireKey:  []string{"authSource", "password", "username"},
			wantCreateKeys:  []string{"description", "name", "publish", "reportDefinitionId", "resourceId", "subject", "traversalSpec"},
			wantQuery:       "format=csv",
			wantContentType: "text/csv",
			check: func(t *testing.T, body map[string]any) {
				if body["name"] != "Cluster Capacity - Weekly" || body["description"] != "Weekly capacity rollup" {
					t.Errorf("outer optional strings = (%v, %v), want the caller values", body["name"], body["description"])
				}
				if body["publish"] != true {
					t.Errorf("publish = %v, want true", body["publish"])
				}
				ts, ok := body["traversalSpec"].(map[string]any)
				if !ok {
					t.Fatalf("traversalSpec = %T, want a JSON object", body["traversalSpec"])
				}
				want := []string{"adapterInstanceAssociation", "description", "name", "rootAdapterKindKey", "rootResourceKindKey"}
				if got := sortedKeys(ts); !reflect.DeepEqual(got, want) {
					t.Errorf("traversalSpec keys = %v, want %v", got, want)
				}
				if ts["adapterInstanceAssociation"] != false || ts["description"] != "Traverse the full cluster hierarchy" {
					t.Errorf("traversalSpec optional values = %v, want explicit false and caller description", ts)
				}
				subject, ok := body["subject"].([]any)
				if !ok || len(subject) != 2 || subject[0] != "ClusterComputeResource" || subject[1] != "HostSystem" {
					t.Errorf("subject = %v, want [ClusterComputeResource HostSystem]", body["subject"])
				}
			},
		},
		{
			name:       "publish explicitly false is still sent",
			authSource: "",
			req: reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
				Publish:            boolPtr(false),
			},
			format:          "pdf",
			wantAcquireKey:  []string{"password", "username"},
			wantCreateKeys:  []string{"publish", "reportDefinitionId", "resourceId"},
			wantQuery:       "format=pdf",
			wantContentType: "application/pdf",
			check: func(t *testing.T, body map[string]any) {
				if body["publish"] != false {
					t.Errorf("publish = %v, want false; a caller-set false must be sent, not dropped as empty", body["publish"])
				}
			},
		},
		{
			name:       "nested spec with only its required field",
			authSource: "",
			req: reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
				TraversalSpec:      &reportclient.TraversalSpec{Name: "vSphere Networking"},
			},
			format:          "",
			wantAcquireKey:  []string{"password", "username"},
			wantCreateKeys:  []string{"reportDefinitionId", "resourceId", "traversalSpec"},
			wantQuery:       "",
			wantContentType: "text/csv",
			check: func(t *testing.T, body map[string]any) {
				ts, ok := body["traversalSpec"].(map[string]any)
				if !ok {
					t.Fatalf("traversalSpec = %T, want a JSON object", body["traversalSpec"])
				}
				if got := sortedKeys(ts); !reflect.DeepEqual(got, []string{"name"}) {
					t.Errorf("traversalSpec keys = %v, want [name]", got)
				}
			},
		},
		{
			name:       "empty slice is not an empty array on the wire",
			authSource: "",
			req: reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
				Subject:            []string{},
			},
			format:          "",
			wantAcquireKey:  []string{"password", "username"},
			wantCreateKeys:  []string{"reportDefinitionId", "resourceId"},
			wantQuery:       "",
			wantContentType: "text/csv",
			check:           func(t *testing.T, body map[string]any) {},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockops.Start(t, mockops.Scenario{
				AuthSource:   tc.authSource,
				PollStatuses: []string{"RUNNING", "COMPLETED"},
			})
			sc := srv.Scenario()

			client, err := reportclient.New(reportclient.Config{
				BaseURL:         srv.URL(),
				HTTPClient:      srv.Client(),
				Username:        sc.Username,
				Password:        sc.Password,
				AuthSource:      tc.authSource,
				PollInterval:    time.Millisecond,
				MaxPollAttempts: 10,
			})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			res, err := client.GenerateReport(context.Background(), tc.req, tc.format)
			if err != nil {
				t.Fatalf("GenerateReport: %v", err)
			}
			if res.ContentType != tc.wantContentType {
				t.Errorf("Result.ContentType = %q, want %q", res.ContentType, tc.wantContentType)
			}

			reqs := srv.Requests()
			assertOnlyContractOperations(t, reqs)

			acquire := filter(reqs, "acquireToken")
			if len(acquire) != 1 {
				t.Fatalf("acquireToken called %d times, want 1", len(acquire))
			}
			if got := bodyKeys(t, acquire[0]); !reflect.DeepEqual(got, tc.wantAcquireKey) {
				t.Errorf("acquireToken body keys = %v, want %v", got, tc.wantAcquireKey)
			}

			create := filter(reqs, "createReport")
			if len(create) != 1 {
				t.Fatalf("createReport called %d times, want 1", len(create))
			}
			if got := bodyKeys(t, create[0]); !reflect.DeepEqual(got, tc.wantCreateKeys) {
				t.Errorf("createReport body keys = %v, want %v", got, tc.wantCreateKeys)
			}
			body, err := create[0].BodyMap()
			if err != nil {
				t.Fatalf("createReport body: %v", err)
			}
			tc.check(t, body)
			assertAuthorized(t, c, sc.Token, create[0])

			dl := filter(reqs, "downloadReport")
			if len(dl) != 1 {
				t.Fatalf("downloadReport called %d times, want 1", len(dl))
			}
			if dl[0].RawQuery != tc.wantQuery {
				t.Errorf("downloadReport query = %q, want %q", dl[0].RawQuery, tc.wantQuery)
			}
		})
	}
}

// TestPollsToTerminalStateBeforeDownloading covers the asynchronous half of the
// contract: the client must keep polling until the status is terminal, must not
// download a report that failed, and must give up after MaxPollAttempts.
func TestPollsToTerminalStateBeforeDownloading(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name         string
		pollStatuses []string
		maxAttempts  int
		wantErr      error
		wantPolls    int
		wantDownload int
	}{
		{
			name:         "reaches COMPLETED after several non-terminal polls",
			pollStatuses: []string{"QUEUED", "QUEUED", "RUNNING", "RUNNING", "COMPLETED"},
			maxAttempts:  10,
			wantErr:      nil,
			wantPolls:    5,
			wantDownload: 1,
		},
		{
			name:         "terminal FAILED stops polling and skips the download",
			pollStatuses: []string{"QUEUED", "RUNNING", "FAILED"},
			maxAttempts:  10,
			wantErr:      reportclient.ErrReportFailed,
			wantPolls:    3,
			wantDownload: 0,
		},
		{
			name:         "never terminal gives up after MaxPollAttempts",
			pollStatuses: []string{"RUNNING"},
			maxAttempts:  4,
			wantErr:      reportclient.ErrPollTimeout,
			wantPolls:    4,
			wantDownload: 0,
		},
		{
			name:         "terminal on the very first poll",
			pollStatuses: []string{"COMPLETED"},
			maxAttempts:  10,
			wantErr:      nil,
			wantPolls:    1,
			wantDownload: 1,
		},
		{
			name:         "an unrecognized status is not treated as terminal",
			pollStatuses: []string{"PENDING_APPROVAL", "COMPLETED"},
			maxAttempts:  10,
			wantErr:      nil,
			wantPolls:    2,
			wantDownload: 1,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockops.Start(t, mockops.Scenario{PollStatuses: tc.pollStatuses})
			sc := srv.Scenario()

			client, err := reportclient.New(reportclient.Config{
				BaseURL:         srv.URL(),
				HTTPClient:      srv.Client(),
				Username:        sc.Username,
				Password:        sc.Password,
				PollInterval:    time.Millisecond,
				MaxPollAttempts: tc.maxAttempts,
			})
			if err != nil {
				t.Fatalf("New: %v", err)
			}

			res, err := client.GenerateReport(context.Background(), reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
			}, "")

			switch {
			case tc.wantErr == nil && err != nil:
				t.Fatalf("GenerateReport: unexpected error %v", err)
			case tc.wantErr != nil && !errors.Is(err, tc.wantErr):
				t.Fatalf("GenerateReport error = %v, want errors.Is(err, %v)", err, tc.wantErr)
			}

			reqs := srv.Requests()
			assertOnlyContractOperations(t, reqs)

			if got := len(filter(reqs, "getReport")); got != tc.wantPolls {
				t.Errorf("getReport called %d times, want %d", got, tc.wantPolls)
			}
			if got := len(filter(reqs, "downloadReport")); got != tc.wantDownload {
				t.Errorf("downloadReport called %d times, want %d", got, tc.wantDownload)
			}
			if tc.wantErr == nil {
				if res == nil {
					t.Fatalf("GenerateReport returned a nil result with no error")
				}
				if res.PollCount != tc.wantPolls {
					t.Errorf("Result.PollCount = %d, want %d", res.PollCount, tc.wantPolls)
				}
				if len(res.Content) == 0 {
					t.Errorf("Result.Content is empty, want the downloaded report bytes")
				}
			}

			// Whatever the outcome, a download may only ever follow the polls.
			for _, r := range reqs {
				if r.OperationID != "downloadReport" {
					continue
				}
				if n := len(filter(reqs[:r.Index], "getReport")); n != tc.wantPolls {
					t.Errorf("downloadReport was sent after %d polls, want %d; the report must be polled to a terminal state first", n, tc.wantPolls)
				}
			}
		})
	}
}

// TestInvalidRequestsNeverReachTheNetwork proves required-field validation
// happens before any HTTP request is made.
func TestInvalidRequestsNeverReachTheNetwork(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		req  reportclient.ReportRequest
	}{
		{"no report definition", reportclient.ReportRequest{ResourceID: resourceID}},
		{"no resource", reportclient.ReportRequest{ReportDefinitionID: reportDefinitionID}},
		{"neither required field", reportclient.ReportRequest{}},
		{
			"traversal spec without its required name",
			reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
				TraversalSpec:      &reportclient.TraversalSpec{RootAdapterKindKey: "VMWARE"},
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockops.Start(t, mockops.Scenario{})
			sc := srv.Scenario()

			client, err := reportclient.New(reportclient.Config{
				BaseURL:         srv.URL(),
				HTTPClient:      srv.Client(),
				Username:        sc.Username,
				Password:        sc.Password,
				PollInterval:    time.Millisecond,
				MaxPollAttempts: 5,
			})
			if err != nil {
				t.Fatalf("New: %v", err)
			}

			_, err = client.GenerateReport(context.Background(), tc.req, "")
			if !errors.Is(err, reportclient.ErrInvalidRequest) {
				t.Fatalf("GenerateReport error = %v, want errors.Is(err, ErrInvalidRequest)", err)
			}
			if got := srv.Requests(); len(got) != 0 {
				t.Errorf("client sent %d requests (%v), want none; validation must precede the network",
					len(got), operationSequence(got))
			}
		})
	}
}

// TestDefaultMaxPollAttempts verifies the documented default without waiting on
// the default interval: a tiny explicit interval makes all 60 attempts finish
// quickly while MaxPollAttempts itself remains unset.
func TestDefaultMaxPollAttempts(t *testing.T) {
	t.Parallel()
	srv := mockops.Start(t, mockops.Scenario{PollStatuses: []string{"RUNNING"}})
	sc := srv.Scenario()

	client, err := reportclient.New(reportclient.Config{
		BaseURL:      srv.URL(),
		HTTPClient:   srv.Client(),
		Username:     sc.Username,
		Password:     sc.Password,
		PollInterval: time.Nanosecond,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	_, err = client.GenerateReport(context.Background(), reportclient.ReportRequest{
		ReportDefinitionID: reportDefinitionID,
		ResourceID:         resourceID,
	}, "")
	if !errors.Is(err, reportclient.ErrPollTimeout) {
		t.Fatalf("GenerateReport error = %v, want errors.Is(err, ErrPollTimeout)", err)
	}
	if got := len(srv.RequestsFor("getReport")); got != 60 {
		t.Errorf("default MaxPollAttempts made %d getReport calls, want 60", got)
	}
	if got := len(srv.RequestsFor("downloadReport")); got != 0 {
		t.Errorf("downloadReport called %d times after poll exhaustion, want 0", got)
	}
}

// TestPollWaitHonoursContextCancellation synchronizes on the first recorded
// poll, then cancels while the client should be waiting for the next one. This
// checks both that polls are separated by PollInterval and that the wait is
// context-aware without relying on a timing race.
func TestPollWaitHonoursContextCancellation(t *testing.T) {
	t.Parallel()
	srv := mockops.Start(t, mockops.Scenario{PollStatuses: []string{"RUNNING"}})
	sc := srv.Scenario()

	client, err := reportclient.New(reportclient.Config{
		BaseURL:         srv.URL(),
		HTTPClient:      srv.Client(),
		Username:        sc.Username,
		Password:        sc.Password,
		PollInterval:    time.Hour,
		MaxPollAttempts: 5,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		_, err := client.GenerateReport(ctx, reportclient.ReportRequest{
			ReportDefinitionID: reportDefinitionID,
			ResourceID:         resourceID,
		}, "")
		result <- err
	}()

	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	for len(srv.RequestsFor("getReport")) == 0 {
		select {
		case <-srv.RequestRecorded():
		case <-deadline.C:
			t.Fatal("GenerateReport did not make its first poll promptly")
		}
	}
	cancel()

	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("GenerateReport error = %v, want context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("GenerateReport did not abort its poll wait after cancellation")
	}
	if got := len(srv.RequestsFor("getReport")); got != 1 {
		t.Errorf("getReport called %d times, want 1 before cancellation", got)
	}
	if got := len(srv.RequestsFor("downloadReport")); got != 0 {
		t.Errorf("downloadReport called %d times after cancellation, want 0", got)
	}
}

// TestServerErrorsAreSurfaced checks every operation reports a non-2xx response
// under that operation's name and stops the flow immediately.
func TestServerErrorsAreSurfaced(t *testing.T) {
	t.Parallel()

	tests := []struct {
		operation string
		wantSeq   []string
	}{
		{"acquireToken", []string{"acquireToken"}},
		{"createReport", []string{"acquireToken", "createReport"}},
		{"getReport", []string{"acquireToken", "createReport", "getReport"}},
		{"downloadReport", []string{"acquireToken", "createReport", "getReport", "downloadReport"}},
	}

	for _, tc := range tests {
		t.Run(tc.operation, func(t *testing.T) {
			t.Parallel()
			srv := mockops.Start(t, mockops.Scenario{
				PollStatuses:      []string{"COMPLETED"},
				OperationFailures: map[string]int{tc.operation: http.StatusServiceUnavailable},
			})
			sc := srv.Scenario()
			client, err := reportclient.New(reportclient.Config{
				BaseURL:         srv.URL(),
				HTTPClient:      srv.Client(),
				Username:        sc.Username,
				Password:        sc.Password,
				PollInterval:    time.Millisecond,
				MaxPollAttempts: 5,
			})
			if err != nil {
				t.Fatalf("New: %v", err)
			}

			_, err = client.GenerateReport(context.Background(), reportclient.ReportRequest{
				ReportDefinitionID: reportDefinitionID,
				ResourceID:         resourceID,
			}, "")
			if err == nil {
				t.Fatalf("GenerateReport succeeded when %s returned non-2xx", tc.operation)
			}
			if !strings.Contains(err.Error(), tc.operation) {
				t.Errorf("error = %v, want it to name %s", err, tc.operation)
			}

			reqs := srv.Requests()
			assertOnlyContractOperations(t, reqs)
			if got := operationSequence(reqs); !reflect.DeepEqual(got, tc.wantSeq) {
				t.Errorf("operation sequence = %v, want %v", got, tc.wantSeq)
			}
			if got := reqs[len(reqs)-1].ResponseStatus; got != http.StatusServiceUnavailable {
				t.Errorf("%s status = %d, want %d", tc.operation, got, http.StatusServiceUnavailable)
			}
		})
	}
}

// TestContractProvenance keeps the shipped contract tied to the specification
// revision recorded in docs/official_sources.json.
func TestContractProvenance(t *testing.T) {
	t.Parallel()
	c := loadContract(t)

	if c.API.Version != "9.1.0.0" {
		t.Errorf("contract api.version = %q, want 9.1.0.0", c.API.Version)
	}
	if c.API.BasePath != "/suite-api" {
		t.Errorf("contract api.basePath = %q, want /suite-api", c.API.BasePath)
	}

	wantOps := []string{"acquireToken", "createReport", "downloadReport", "getReport"}
	got := make([]string, 0, len(c.Operations))
	for id := range c.Operations {
		got = append(got, id)
	}
	sort.Strings(got)
	if !reflect.DeepEqual(got, wantOps) {
		t.Errorf("contract operations = %v, want exactly %v", got, wantOps)
	}

	root, err := contract.ModuleRoot()
	if err != nil {
		t.Fatalf("module root: %v", err)
	}
	var sources struct {
		Source struct {
			SpecPath  string `json:"specPath"`
			CommitSha string `json:"commitSha"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
		} `json:"operations"`
	}
	raw, err := os.ReadFile(filepath.Join(root, "docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("read official_sources.json: %v", err)
	}
	if err := json.Unmarshal(raw, &sources); err != nil {
		t.Fatalf("decode official_sources.json: %v", err)
	}
	if sources.Source.SpecPath != "specifications/vcf-operations/vcf-operations-openapi.json" {
		t.Errorf("specPath = %q, want the VCF Operations spec (not log management)", sources.Source.SpecPath)
	}
	if len(sources.Source.CommitSha) != 40 {
		t.Errorf("commitSha = %q, want a 40-character sha", sources.Source.CommitSha)
	}
	recorded := make([]string, 0, len(sources.Operations))
	for _, o := range sources.Operations {
		recorded = append(recorded, o.OperationID)
	}
	sort.Strings(recorded)
	if !reflect.DeepEqual(recorded, wantOps) {
		t.Errorf("official_sources.json operationIds = %v, want %v", recorded, wantOps)
	}
}

func assertAuthorized(t *testing.T, c *contract.Contract, token string, r mockops.Request) {
	t.Helper()
	want := c.AuthHeaderValue(token)
	if got := r.Header.Get(c.Authorization.HeaderName); got != want {
		t.Errorf("%s (request %d) %s = %q, want %q", r.OperationID, r.Index, c.Authorization.HeaderName, got, want)
	}
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
