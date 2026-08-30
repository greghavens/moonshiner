package acceptance_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"example.com/vcf-diagnostics/diagnostics"
	"example.com/vcf-diagnostics/internal/vcfmock"
)

const (
	contractPath = "../../docs/contract.json"
	fixturePath  = "testdata/search_responses.json"
)

var incident = diagnostics.Incident{
	DeploymentID: "deploy-7f3a",
	StartedAt:    time.Date(2026, 7, 14, 9, 30, 0, 0, time.UTC),
	EndedAt:      time.Date(2026, 7, 14, 9, 40, 0, 0, time.UTC),
}

func TestDiagnoseFailureWireContract(t *testing.T) {
	server := newMock(t, loadFixtureResponses(t))
	client, err := diagnostics.NewClient(diagnostics.Config{
		BaseURL:    server.URL(),
		Token:      "fixture-token",
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}

	diagnosis, err := client.DiagnoseFailure(context.Background(), incident)
	if err != nil {
		t.Fatalf("DiagnoseFailure() error = %v", err)
	}
	wantDiagnosis := diagnostics.Diagnosis{
		RootCause:     diagnostics.CertificateRotationRootCause,
		CorrelationID: "corr-41",
		Endpoint:      "vc01.mgmt.example.com",
		Evidence: []diagnostics.Evidence{
			{
				Kind:      "log",
				Timestamp: 1784021580000,
				Text:      "peer certificate fingerprint no longer matches cached trust for vc01",
			},
			{
				Kind:      "event",
				Timestamp: 1784021520000,
				Text:      "vCenter certificate replaced; dependent trust caches require refresh",
			},
		},
	}
	if !reflect.DeepEqual(diagnosis, wantDiagnosis) {
		t.Fatalf("DiagnoseFailure() = %#v, want %#v", diagnosis, wantDiagnosis)
	}

	requests := server.Requests()
	wireCases := []struct {
		name string
		body string
	}{
		{
			name: "deployment logs",
			body: `{
                "indices":["logs"],
                "query":{"bool":{"filter":[
                    {"match_phrase":{"deployment_id":"deploy-7f3a"}},
                    {"range":{"timestamp":{"gte":"2026-07-14T09:30:00Z","lte":"2026-07-14T09:40:00Z"}}}
                ]}},
                "size":100,
                "sort":[{"timestamp":{"order":"asc"}}],
                "trackTotalHits":true
            }`,
		},
		{
			name: "correlated events",
			body: `{
                "indices":["events"],
                "query":{"bool":{"filter":[
                    {"match_phrase":{"correlation_id":"corr-41"}},
                    {"range":{"timestamp":{"gte":"2026-07-14T09:30:00Z","lte":"2026-07-14T09:40:00Z"}}}
                ]}},
                "size":100,
                "sort":[{"timestamp":{"order":"asc"}}],
                "trackTotalHits":true
            }`,
		},
	}
	if len(requests) != len(wireCases) {
		t.Fatalf("request count = %d, want %d", len(requests), len(wireCases))
	}
	for index, test := range wireCases {
		t.Run(test.name, func(t *testing.T) {
			assertRequestWire(t, requests[index], test.body)
		})
	}
}

func TestDiagnoseFailureRequiresCorrelatedEvidence(t *testing.T) {
	validLog := json.RawMessage(`{
        "events":{"hits":[{"msgContent":{
            "fields":[
                {"internalName":"deployment_id","value":"deploy-7f3a"},
                {"internalName":"severity","value":"ERROR"},
                {"internalName":"error_class","value":"TLS_HANDSHAKE"},
                {"internalName":"correlation_id","value":"corr-41"},
                {"internalName":"endpoint","value":"vc01.mgmt.example.com"}
            ],
            "logTimestamp":1784021580000,
            "originalText":"certificate mismatch"
        }}],"total":1},"timedOut":false
    }`)

	tests := []struct {
		name         string
		responses    []json.RawMessage
		wantRequests int
	}{
		{
			name: "generic alert without a qualifying log",
			responses: []json.RawMessage{json.RawMessage(`{
                "events":{"hits":[{"msgContent":{"fields":[
                    {"internalName":"severity","value":"INFO"},
                    {"internalName":"correlation_id","value":"corr-41"}
                ],"originalText":"upstream handshake failed"}}],"total":1}
            }`)},
			wantRequests: 1,
		},
		{
			name: "qualifying log without an event",
			responses: []json.RawMessage{
				validLog,
				json.RawMessage(`{"events":{"hits":[],"total":0},"timedOut":false}`),
			},
			wantRequests: 2,
		},
		{
			name: "event has a different correlation id",
			responses: []json.RawMessage{
				validLog,
				json.RawMessage(`{"events":{"hits":[{"msgContent":{"fields":[
                    {"internalName":"correlation_id","value":"corr-other"},
                    {"internalName":"event_type","value":"VCENTER_CERTIFICATE_REPLACED"},
                    {"internalName":"endpoint","value":"vc01.mgmt.example.com"}
                ]}}],"total":1},"timedOut":false}`),
			},
			wantRequests: 2,
		},
		{
			name: "correlated event is not certificate replacement",
			responses: []json.RawMessage{
				validLog,
				json.RawMessage(`{"events":{"hits":[{"msgContent":{"fields":[
                    {"internalName":"correlation_id","value":"corr-41"},
                    {"internalName":"event_type","value":"VCENTER_SESSION_EXPIRED"},
                    {"internalName":"endpoint","value":"vc01.mgmt.example.com"}
                ]}}],"total":1},"timedOut":false}`),
			},
			wantRequests: 2,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t, test.responses)
			client, err := diagnostics.NewClient(diagnostics.Config{
				BaseURL:    server.URL(),
				Token:      "fixture-token",
				HTTPClient: server.Client(),
			})
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}

			got, err := client.DiagnoseFailure(context.Background(), incident)
			if !errors.Is(err, diagnostics.ErrInsufficientEvidence) {
				t.Fatalf("DiagnoseFailure() error = %v, want ErrInsufficientEvidence", err)
			}
			if !reflect.DeepEqual(got, diagnostics.Diagnosis{}) {
				t.Fatalf("DiagnoseFailure() returned unsupported diagnosis: %#v", got)
			}
			if gotRequests := len(server.Requests()); gotRequests != test.wantRequests {
				t.Fatalf("request count = %d, want %d", gotRequests, test.wantRequests)
			}
		})
	}
}

func TestClientRejectsInvalidInputs(t *testing.T) {
	configTests := []struct {
		name string
		cfg  diagnostics.Config
	}{
		{name: "missing base URL", cfg: diagnostics.Config{Token: "token"}},
		{name: "relative base URL", cfg: diagnostics.Config{BaseURL: "/local", Token: "token"}},
		{name: "unsupported URL scheme", cfg: diagnostics.Config{BaseURL: "file:///tmp/socket", Token: "token"}},
		{name: "missing token", cfg: diagnostics.Config{BaseURL: "http://127.0.0.1:1"}},
	}
	for _, test := range configTests {
		t.Run(test.name, func(t *testing.T) {
			if client, err := diagnostics.NewClient(test.cfg); err == nil || client != nil {
				t.Fatalf("NewClient() = (%#v, %v), want nil, error", client, err)
			}
		})
	}

	server := newMock(t, nil)
	client, err := diagnostics.NewClient(diagnostics.Config{
		BaseURL: server.URL(), Token: "token", HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	incidentTests := []struct {
		name     string
		incident diagnostics.Incident
	}{
		{name: "missing deployment id", incident: diagnostics.Incident{StartedAt: incident.StartedAt, EndedAt: incident.EndedAt}},
		{name: "missing start", incident: diagnostics.Incident{DeploymentID: incident.DeploymentID, EndedAt: incident.EndedAt}},
		{name: "end before start", incident: diagnostics.Incident{DeploymentID: incident.DeploymentID, StartedAt: incident.EndedAt, EndedAt: incident.StartedAt}},
	}
	for _, test := range incidentTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.DiagnoseFailure(context.Background(), test.incident); err == nil {
				t.Fatal("DiagnoseFailure() error = nil, want input error")
			}
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("invalid inputs made %d HTTP requests", got)
	}
}

func TestQueryFailuresNeverReturnDiagnosis(t *testing.T) {
	tests := []struct {
		name      string
		responses []json.RawMessage
	}{
		{name: "timed out", responses: []json.RawMessage{json.RawMessage(`{"events":{"hits":[]},"timedOut":true}`)}},
		{name: "query failure", responses: []json.RawMessage{json.RawMessage(`{"failureReason":"QUERY","failureMessage":"bad filter","timedOut":false}`)}},
		{name: "non-200 response", responses: nil},
		{name: "malformed JSON", responses: []json.RawMessage{json.RawMessage(`{`)}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t, test.responses)
			client, err := diagnostics.NewClient(diagnostics.Config{
				BaseURL: server.URL(), Token: "token", HTTPClient: server.Client(),
			})
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}
			got, err := client.DiagnoseFailure(context.Background(), incident)
			if err == nil {
				t.Fatal("DiagnoseFailure() error = nil")
			}
			if !reflect.DeepEqual(got, diagnostics.Diagnosis{}) {
				t.Fatalf("DiagnoseFailure() returned diagnosis after API failure: %#v", got)
			}
		})
	}
}

func TestCancellationMakesNoRequest(t *testing.T) {
	server := newMock(t, nil)
	client, err := diagnostics.NewClient(diagnostics.Config{
		BaseURL: server.URL(), Token: "token", HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	got, err := client.DiagnoseFailure(ctx, incident)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("DiagnoseFailure() error = %v, want context.Canceled", err)
	}
	if !reflect.DeepEqual(got, diagnostics.Diagnosis{}) {
		t.Fatalf("DiagnoseFailure() returned diagnosis after cancellation: %#v", got)
	}
	if gotRequests := len(server.Requests()); gotRequests != 0 {
		t.Fatalf("cancelled call made %d requests", gotRequests)
	}
}

func assertRequestWire(t *testing.T, request vcfmock.RequestRecord, wantBody string) {
	t.Helper()
	if request.Method != http.MethodPost {
		t.Errorf("method = %q, want POST", request.Method)
	}
	if request.RequestURI != "/api/v2/logs/search" {
		t.Errorf("request URI = %q, want /api/v2/logs/search", request.RequestURI)
	}
	if got := request.Header.Get("X-JWT-Token"); got != "fixture-token" {
		t.Errorf("X-JWT-Token = %q, want fixture-token", got)
	}
	if got := request.Header.Get("Authorization"); got != "" {
		t.Errorf("Authorization must be absent, got %q", got)
	}
	if got := request.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	if got := request.Header.Get("Accept"); got != "application/json" {
		t.Errorf("Accept = %q, want application/json", got)
	}

	gotJSON := decodeJSONObject(t, string(request.Body))
	wantJSON := decodeJSONObject(t, wantBody)
	if !reflect.DeepEqual(gotJSON, wantJSON) {
		t.Errorf("JSON body = %s\nwant semantic body = %s", request.Body, wantBody)
	}
	for _, optional := range []string{"aggregations", "from", "scroll", "scrollSize"} {
		if _, exists := gotJSON[optional]; exists {
			t.Errorf("unset optional field %q must be omitted", optional)
		}
	}
	assertNoEmptyJSON(t, "$", gotJSON)
}

func assertNoEmptyJSON(t *testing.T, path string, value any) {
	t.Helper()
	switch typed := value.(type) {
	case string:
		if typed == "" {
			t.Errorf("%s is an empty string; unset optionals must be omitted", path)
		}
	case []any:
		if len(typed) == 0 {
			t.Errorf("%s is an empty array; unset optionals must be omitted", path)
		}
		for index, child := range typed {
			assertNoEmptyJSON(t, path+"["+strconv.Itoa(index)+"]", child)
		}
	case map[string]any:
		if len(typed) == 0 {
			t.Errorf("%s is an empty object; unset optionals must be omitted", path)
		}
		for key, child := range typed {
			assertNoEmptyJSON(t, path+"."+key, child)
		}
	case nil:
		t.Errorf("%s is null; unset optionals must be omitted", path)
	}
}

func decodeJSONObject(t *testing.T, value string) map[string]any {
	t.Helper()
	decoder := json.NewDecoder(strings.NewReader(value))
	decoder.UseNumber()
	var result map[string]any
	if err := decoder.Decode(&result); err != nil {
		t.Fatalf("decode JSON %q: %v", value, err)
	}
	return result
}

func loadFixtureResponses(t *testing.T) []json.RawMessage {
	t.Helper()
	content, err := os.ReadFile(fixturePath)
	if err != nil {
		t.Fatalf("read response fixtures: %v", err)
	}
	var responses []json.RawMessage
	if err := json.Unmarshal(content, &responses); err != nil {
		t.Fatalf("decode response fixtures: %v", err)
	}
	return responses
}

func newMock(t *testing.T, responses []json.RawMessage) *vcfmock.Server {
	t.Helper()
	server, err := vcfmock.New(contractPath, responses)
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Errorf("close mock: %v", err)
		}
	})
	return server
}
