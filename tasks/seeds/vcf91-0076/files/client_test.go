package nsxpolicy_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	nsxpolicy "vcf91nsxdiag"
	"vcf91nsxdiag/internal/mocknsx"
)

const (
	testUser     = "svc-vcf"
	testPassword = "fixture-secret"
	intentPath   = "/infra/segments/app-seg"
)

func newMock(t *testing.T) *mocknsx.Server {
	t.Helper()
	server, err := mocknsx.New("docs/contract.json")
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *mocknsx.Server) *nsxpolicy.Client {
	t.Helper()
	client, err := nsxpolicy.New(nsxpolicy.Config{
		BaseURL:    server.URL(),
		Username:   testUser,
		Password:   testPassword,
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return client
}

func queue(t *testing.T, server *mocknsx.Server, operationID string, responses ...mocknsx.Response) {
	t.Helper()
	if err := server.Queue(operationID, responses...); err != nil {
		t.Fatalf("queue %s: %v", operationID, err)
	}
}

func assertCommonRequest(t *testing.T, request mocknsx.Request) {
	t.Helper()
	wantAuthorization := "Basic " + base64.StdEncoding.EncodeToString([]byte(testUser+":"+testPassword))
	if got := request.Header.Get("Authorization"); got != wantAuthorization {
		t.Errorf("Authorization = %q, want %q", got, wantAuthorization)
	}
	if got := request.Header.Get("Accept"); got != "application/json" {
		t.Errorf("Accept = %q, want application/json", got)
	}
}

func TestProtectedContractProvenanceAndMockScope(t *testing.T) {
	var provenance struct {
		RepositoryCommitSHA string `json:"repository_commit_sha"`
		SpecPath            string `json:"spec_path"`
		OperationIDs        []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operation_ids"`
	}
	raw, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &provenance); err != nil {
		t.Fatal(err)
	}
	if provenance.RepositoryCommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" {
		t.Fatalf("repository commit = %q", provenance.RepositoryCommitSHA)
	}
	if provenance.SpecPath != "specifications/nsx/openapi-2.0/nsx_policy_api.yaml" {
		t.Fatalf("spec path = %q", provenance.SpecPath)
	}
	var got []string
	for _, operation := range provenance.OperationIDs {
		got = append(got, operation.OperationID+" "+operation.Method+" "+operation.Path)
	}
	sort.Strings(got)
	want := []string{
		"ListAlarms GET /infra/realized-state/alarms",
		"ListRealizedEntities GET /infra/realized-state/realized-entities",
		"PatchInfraSegment PATCH /infra/segments/{segment-id}",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("official operation provenance = %#v, want %#v", got, want)
	}

	server := newMock(t)
	if err := server.Queue("InventedOperation", mocknsx.Response{Status: 200}); err == nil {
		t.Fatal("mock accepted an operation outside docs/contract.json")
	}
	response, err := server.Client().Get(server.URL() + "/policy/api/v1/infra/segments/app-seg/statistics")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("uncontracted path status = %d, want 404", response.StatusCode)
	}
}

func TestPatchSegmentExactWireShape(t *testing.T) {
	down := "DOWN"
	tests := []struct {
		name     string
		patch    nsxpolicy.SegmentPatch
		wantBody string
	}{
		{
			name:     "unset optional fields are omitted",
			patch:    nsxpolicy.SegmentPatch{DisplayName: "app-seg"},
			wantBody: `{"display_name":"app-seg"}`,
		},
		{
			name: "explicit fields retain contract names",
			patch: nsxpolicy.SegmentPatch{
				DisplayName:       "app-seg",
				Description:       "application segment",
				ConnectivityPath:  "/infra/tier-1s/app-t1",
				TransportZonePath: "/infra/sites/default/enforcement-points/default/transport-zones/overlay",
				Subnets: []nsxpolicy.SegmentSubnet{{
					GatewayAddress: "10.42.0.1/24",
					DHCPRanges:     []string{"10.42.0.20-10.42.0.80"},
				}},
				VLANIDs:    []string{"120"},
				AdminState: &down,
			},
			wantBody: `{"display_name":"app-seg","description":"application segment","connectivity_path":"/infra/tier-1s/app-t1","transport_zone_path":"/infra/sites/default/enforcement-points/default/transport-zones/overlay","subnets":[{"gateway_address":"10.42.0.1/24","dhcp_ranges":["10.42.0.20-10.42.0.80"]}],"vlan_ids":["120"],"admin_state":"DOWN"}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t)
			queue(t, server, "PatchInfraSegment", mocknsx.Response{Status: http.StatusOK})
			client := newClient(t, server)

			if err := client.PatchSegment(context.Background(), "app-seg", test.patch); err != nil {
				t.Fatalf("PatchSegment: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 1 {
				t.Fatalf("request count = %d, want 1", len(requests))
			}
			request := requests[0]
			assertCommonRequest(t, request)
			if request.OperationID != "PatchInfraSegment" || request.Method != http.MethodPatch {
				t.Errorf("operation = %s %s", request.Method, request.OperationID)
			}
			if request.EscapedPath != "/policy/api/v1/infra/segments/app-seg" {
				t.Errorf("escaped path = %q", request.EscapedPath)
			}
			if request.RawQuery != "" {
				t.Errorf("raw query = %q, want empty", request.RawQuery)
			}
			if got := request.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", got)
			}
			if got := string(request.Body); got != test.wantBody {
				t.Errorf("body = %s\nwant = %s", got, test.wantBody)
			}
			if test.name == "unset optional fields are omitted" {
				for _, forbidden := range []string{
					`"description"`, `"connectivity_path"`, `"transport_zone_path"`,
					`"subnets"`, `"vlan_ids"`, `"admin_state"`,
				} {
					if bytes.Contains(request.Body, []byte(forbidden)) {
						t.Errorf("minimal body contains unset key %s", forbidden)
					}
				}
			}
		})
	}
}

func TestDiagnosePullsCorrelatedLogsAndEventsWithExactQueries(t *testing.T) {
	pageZero := int64(0)
	falseValue := false
	tests := []struct {
		name              string
		options           nsxpolicy.DiagnosticsOptions
		alarmResponses    []mocknsx.Response
		wantRealizedQuery string
		wantAlarmQueries  []string
	}{
		{
			name:    "unset options are omitted and cursor is opaque",
			options: nsxpolicy.DiagnosticsOptions{},
			alarmResponses: []mocknsx.Response{
				{
					Status: 200,
					Body:   `{"cursor":"next+page","results":[{"id":"NOISE","intent_paths":["/infra/segments/db-seg"],"severity":"ERROR","message":"unrelated"}]}`,
				},
				{
					Status: 200,
					Body:   `{"results":[{"id":"SEGMENT_REALIZATION_ERROR","intent_paths":["/infra/segments/app-seg"],"source_reference":"/infra/realized-state/enforcement-points/default/segments/app-seg","severity":"ERROR","message":"transport zone overlay is unavailable on enforcement point default"}]}`,
				},
			},
			wantRealizedQuery: "intent_path=%2Finfra%2Fsegments%2Fapp-seg",
			wantAlarmQueries:  []string{"", "cursor=next%2Bpage"},
		},
		{
			name: "explicit zero and false values are sent",
			options: nsxpolicy.DiagnosticsOptions{
				SitePath:       "/infra/sites/denver",
				IncludedFields: "id,message",
				PageSize:       &pageZero,
				SortAscending:  &falseValue,
				SortBy:         "severity",
			},
			alarmResponses: []mocknsx.Response{{
				Status: 200,
				Body:   `{"results":[{"id":"SEGMENT_REALIZATION_ERROR","intent_paths":["/infra/segments/app-seg"],"severity":"ERROR","message":"transport zone overlay is unavailable on enforcement point default"}]}`,
			}},
			wantRealizedQuery: "intent_path=%2Finfra%2Fsegments%2Fapp-seg&site_path=%2Finfra%2Fsites%2Fdenver",
			wantAlarmQueries: []string{
				"included_fields=id%2Cmessage&page_size=0&sort_ascending=false&sort_by=severity",
			},
		},
	}

	logBytes, err := os.ReadFile("fixtures/controller.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t)
			queue(t, server, "ListRealizedEntities", mocknsx.Response{
				Status: 200,
				Body:   `{"results":[{"id":"logical-switch-app-seg","intent_paths":["/infra/segments/app-seg"],"state":"ERROR","operational_status":"DOWN"}]}`,
			})
			queue(t, server, "ListAlarms", test.alarmResponses...)
			client := newClient(t, server)

			report, err := client.DiagnoseSegment(context.Background(), "app-seg", bytes.NewReader(logBytes), test.options)
			if err != nil {
				t.Fatalf("DiagnoseSegment: %v", err)
			}
			wantReport := nsxpolicy.DiagnosticReport{
				CorrelationID:     "req-7d9c2",
				IntentPath:        intentPath,
				ControllerMessage: "NSX Policy returned 500; realization outcome unknown",
				RealizedEntityID:  "logical-switch-app-seg",
				RealizationState:  "ERROR",
				AlarmID:           "SEGMENT_REALIZATION_ERROR",
				Severity:          "ERROR",
				Cause:             "transport zone overlay is unavailable on enforcement point default",
			}
			if !reflect.DeepEqual(report, wantReport) {
				t.Errorf("report = %#v\nwant   = %#v", report, wantReport)
			}

			requests := server.Requests()
			if len(requests) != 1+len(test.wantAlarmQueries) {
				t.Fatalf("request count = %d, want %d", len(requests), 1+len(test.wantAlarmQueries))
			}
			if requests[0].OperationID != "ListRealizedEntities" || requests[0].Method != http.MethodGet {
				t.Errorf("first operation = %s %s", requests[0].Method, requests[0].OperationID)
			}
			if requests[0].EscapedPath != "/policy/api/v1/infra/realized-state/realized-entities" {
				t.Errorf("realized path = %q", requests[0].EscapedPath)
			}
			if requests[0].RawQuery != test.wantRealizedQuery {
				t.Errorf("realized query = %q, want %q", requests[0].RawQuery, test.wantRealizedQuery)
			}
			for index, wantQuery := range test.wantAlarmQueries {
				request := requests[index+1]
				if request.OperationID != "ListAlarms" || request.Method != http.MethodGet {
					t.Errorf("alarm operation %d = %s %s", index, request.Method, request.OperationID)
				}
				if request.EscapedPath != "/policy/api/v1/infra/realized-state/alarms" {
					t.Errorf("alarm path %d = %q", index, request.EscapedPath)
				}
				if request.RawQuery != wantQuery {
					t.Errorf("alarm query %d = %q, want %q", index, request.RawQuery, wantQuery)
				}
			}
			for _, request := range requests {
				assertCommonRequest(t, request)
				if len(request.Body) != 0 {
					t.Errorf("%s GET sent body %q", request.OperationID, request.Body)
				}
				if got := request.Header.Get("Content-Type"); got != "" {
					t.Errorf("%s GET Content-Type = %q, want omitted", request.OperationID, got)
				}
			}
		})
	}
}

func TestDiagnoseRequiresCorrelatedEvidenceRatherThanGuessing(t *testing.T) {
	matchingLog := `{"operation_id":"PatchInfraSegment","segment_id":"app-seg","intent_path":"/infra/segments/app-seg","correlation_id":"req-1","status":"failed","message":"generic failure"}` + "\n"
	tests := []struct {
		name         string
		log          string
		realizedBody string
		alarmBody    string
		wantRequests int
	}{
		{
			name:         "no matching failed controller log",
			log:          `{"operation_id":"PatchInfraSegment","segment_id":"other","status":"failed"}` + "\n",
			wantRequests: 0,
		},
		{
			name:         "no matching realized entity",
			log:          matchingLog,
			realizedBody: `{"results":[{"id":"other","intent_paths":["/infra/segments/other"],"state":"ERROR"}]}`,
			wantRequests: 1,
		},
		{
			name:         "no matching error alarm",
			log:          matchingLog,
			realizedBody: `{"results":[{"id":"entity","intent_paths":["/infra/segments/app-seg"],"state":"ERROR"}]}`,
			alarmBody:    `{"results":[{"id":"warning","intent_paths":["/infra/segments/app-seg"],"severity":"WARNING","message":"not a root cause"}]}`,
			wantRequests: 2,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t)
			if test.realizedBody != "" {
				queue(t, server, "ListRealizedEntities", mocknsx.Response{Status: 200, Body: test.realizedBody})
			}
			if test.alarmBody != "" {
				queue(t, server, "ListAlarms", mocknsx.Response{Status: 200, Body: test.alarmBody})
			}
			client := newClient(t, server)
			_, err := client.DiagnoseSegment(context.Background(), "app-seg", strings.NewReader(test.log), nsxpolicy.DiagnosticsOptions{})
			if !errors.Is(err, nsxpolicy.ErrNoDiagnosticEvidence) {
				t.Fatalf("error = %v, want ErrNoDiagnosticEvidence", err)
			}
			if got := len(server.Requests()); got != test.wantRequests {
				t.Errorf("request count = %d, want %d", got, test.wantRequests)
			}
		})
	}
}

func TestPageSizeValidationPrecedesRequests(t *testing.T) {
	tests := []struct {
		name  string
		value int64
	}{
		{name: "below minimum", value: -1},
		{name: "above maximum", value: 1001},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t)
			client := newClient(t, server)
			log := `{"operation_id":"PatchInfraSegment","segment_id":"app-seg","intent_path":"/infra/segments/app-seg","correlation_id":"req-1","status":"failed","message":"generic failure"}` + "\n"
			_, err := client.DiagnoseSegment(context.Background(), "app-seg", strings.NewReader(log), nsxpolicy.DiagnosticsOptions{PageSize: &test.value})
			if err == nil {
				t.Fatal("expected page_size validation error")
			}
			if got := len(server.Requests()); got != 0 {
				t.Fatalf("request count = %d, want 0", got)
			}
		})
	}
}

func TestAPIErrorAndContextCancellation(t *testing.T) {
	t.Run("structured API error", func(t *testing.T) {
		server := newMock(t)
		queue(t, server, "PatchInfraSegment", mocknsx.Response{
			Status: http.StatusBadRequest,
			Body:   `{"error_code":8327,"error_message":"invalid segment","module_name":"PolicyConnectivity","details":"gateway is outside subnet"}`,
		})
		client := newClient(t, server)
		err := client.PatchSegment(context.Background(), "app-seg", nsxpolicy.SegmentPatch{DisplayName: "app-seg"})
		var apiError *nsxpolicy.APIError
		if !errors.As(err, &apiError) {
			t.Fatalf("error = %T %v, want *APIError", err, err)
		}
		want := nsxpolicy.APIError{
			StatusCode:   http.StatusBadRequest,
			ErrorCode:    8327,
			ErrorMessage: "invalid segment",
			ModuleName:   "PolicyConnectivity",
			Details:      "gateway is outside subnet",
		}
		if !reflect.DeepEqual(*apiError, want) {
			t.Errorf("APIError = %#v, want %#v", *apiError, want)
		}
		if strings.Contains(err.Error(), testPassword) {
			t.Error("error exposed password")
		}
	})

	t.Run("context cancellation remains wrapped", func(t *testing.T) {
		server := newMock(t)
		client := newClient(t, server)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		err := client.PatchSegment(ctx, "app-seg", nsxpolicy.SegmentPatch{DisplayName: "app-seg"})
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %v, want wrapped context.Canceled", err)
		}
	})
}

func TestConcurrentPatchesAndRequestLogUnderRaceDetector(t *testing.T) {
	const count = 12
	server := newMock(t)
	responses := make([]mocknsx.Response, count)
	for index := range responses {
		responses[index] = mocknsx.Response{Status: http.StatusOK}
	}
	queue(t, server, "PatchInfraSegment", responses...)
	client := newClient(t, server)

	var wait sync.WaitGroup
	errorsFound := make(chan error, count)
	for index := 0; index < count; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			errorsFound <- client.PatchSegment(context.Background(), "app-seg", nsxpolicy.SegmentPatch{DisplayName: "app-seg"})
		}()
	}
	wait.Wait()
	close(errorsFound)
	for err := range errorsFound {
		if err != nil {
			t.Errorf("PatchSegment: %v", err)
		}
	}
	if got := len(server.Requests()); got != count {
		t.Fatalf("request log count = %d, want %d", got, count)
	}
}
