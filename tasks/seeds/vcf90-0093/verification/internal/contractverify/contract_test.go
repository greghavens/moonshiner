package contractverify_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	"example.com/vcflogs/internal/mocklogs"
	"example.com/vcflogs/vcflogs"
)

func TestPinnedOfficialContract(t *testing.T) {
	t.Parallel()

	var contract struct {
		OpenAPI string `json:"openapi"`
		Info    struct {
			Title   string `json:"title"`
			Version string `json:"version"`
		} `json:"info"`
		Servers []struct {
			URL string `json:"url"`
		} `json:"servers"`
		Paths map[string]struct {
			Get struct {
				OperationID string `json:"operationId"`
				Parameters  []struct {
					Name string `json:"name"`
					In   string `json:"in"`
				} `json:"parameters"`
			} `json:"get"`
		} `json:"paths"`
	}
	readJSON(t, "../../docs/contract.json", &contract)
	if contract.Info.Title != "VCF Operations for Logs" || contract.Info.Version != "v2" || contract.OpenAPI != "3.0.1" {
		t.Fatalf("wrong 9.0 contract identity: %#v", contract)
	}
	if len(contract.Servers) != 1 || contract.Servers[0].URL != "/api/v2" {
		t.Fatalf("wrong contract server base: %#v", contract.Servers)
	}
	if len(contract.Paths) != 1 {
		t.Fatalf("mock contract must contain exactly one path, got %d", len(contract.Paths))
	}
	pathItem, ok := contract.Paths["/events/{+path}"]
	if !ok || pathItem.Get.OperationID != mocklogs.OperationID {
		t.Fatalf("wrong operation contract: %#v", contract.Paths)
	}
	wantParams := map[string]string{
		"+path": "path", "limit": "query", "timeout": "query", "view": "query",
		"content-pack-fields": "query", "order-by-direction": "query",
	}
	gotParams := make(map[string]string, len(pathItem.Get.Parameters))
	for _, parameter := range pathItem.Get.Parameters {
		gotParams[parameter.Name] = parameter.In
	}
	if !reflect.DeepEqual(gotParams, wantParams) {
		t.Fatalf("operation parameters do not match pinned spec: got %#v want %#v", gotParams, wantParams)
	}

	var sources struct {
		Tag          string   `json:"tag"`
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operation_ids"`
	}
	readJSON(t, "../../docs/official_sources.json", &sources)
	if sources.Tag != "9.0.0.0" || sources.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" ||
		sources.SpecPath != "specifications/vcf-operations/vcf-operations-for-logs-openapi.json" ||
		!reflect.DeepEqual(sources.OperationIDs, []string{mocklogs.OperationID}) {
		t.Fatalf("official source pin is incorrect: %#v", sources)
	}
}

func TestListAllEventsContract(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		after      int64
		pageSize   int
		want       []vcflogs.Event
		requestURI []string
	}{
		{
			name:     "walks three pages from the beginning",
			after:    0,
			pageSize: 2,
			want: []vcflogs.Event{
				{Text: "alpha", Timestamp: 1700000000100, TimestampString: "2023-11-14T22:13:20.100Z", Fields: []vcflogs.Field{{Name: "source", Content: "esx-01"}}},
				{Text: "bravo", Timestamp: 1700000000200, TimestampString: "2023-11-14T22:13:20.200Z"},
				{Text: "gamma", Timestamp: 1700000000300, TimestampString: "2023-11-14T22:13:20.300Z"},
				{Text: "delta", Timestamp: 1700000000400, TimestampString: "2023-11-14T22:13:20.400Z"},
				{Text: "echo", Timestamp: 1700000000500, TimestampString: "2023-11-14T22:13:20.500Z"},
			},
			requestURI: []string{
				"/api/v2/events/timestamp/GT%200?limit=2&order-by-direction=ASC",
				"/api/v2/events/timestamp/GT%201700000000200?limit=2&order-by-direction=ASC",
				"/api/v2/events/timestamp/GT%201700000000400?limit=2&order-by-direction=ASC",
			},
		},
		{
			name:     "honors an exclusive starting boundary",
			after:    1700000000200,
			pageSize: 2,
			want: []vcflogs.Event{
				{Text: "gamma", Timestamp: 1700000000300, TimestampString: "2023-11-14T22:13:20.300Z"},
				{Text: "delta", Timestamp: 1700000000400, TimestampString: "2023-11-14T22:13:20.400Z"},
				{Text: "echo", Timestamp: 1700000000500, TimestampString: "2023-11-14T22:13:20.500Z"},
			},
			requestURI: []string{
				"/api/v2/events/timestamp/GT%201700000000200?limit=2&order-by-direction=ASC",
				"/api/v2/events/timestamp/GT%201700000000400?limit=2&order-by-direction=ASC",
			},
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			server := mocklogs.New()
			defer server.Close()

			client := vcflogs.NewClient(server.URL(), "fixture-session", server.Client())
			got, err := client.ListAllEvents(context.Background(), tt.after, tt.pageSize)
			if err != nil {
				t.Fatalf("ListAllEvents returned error: %v", err)
			}
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("events mismatch:\n got: %#v\nwant: %#v", got, tt.want)
			}

			requests := server.Requests()
			if len(requests) != len(tt.requestURI) {
				t.Fatalf("request count = %d, want %d: %#v", len(requests), len(tt.requestURI), requests)
			}
			for i, request := range requests {
				if request.Method != "GET" || request.RequestURI != tt.requestURI[i] {
					t.Errorf("request %d wire line = %s %s, want GET %s", i, request.Method, request.RequestURI, tt.requestURI[i])
				}
				if request.Header.Get("Authorization") != "Bearer fixture-session" {
					t.Errorf("request %d Authorization = %q", i, request.Header.Get("Authorization"))
				}
				if request.Header.Get("Accept") != "application/json" {
					t.Errorf("request %d Accept = %q", i, request.Header.Get("Accept"))
				}
				if request.Body != "" {
					t.Errorf("request %d body = %q, want empty", i, request.Body)
				}
				for _, optional := range []string{"timeout", "view", "content-pack-fields"} {
					if strings.Contains(request.RequestURI, optional+"=") {
						t.Errorf("request %d sent unset optional %q: %s", i, optional, request.RequestURI)
					}
				}
			}
		})
	}
}

func TestListAllEventsRejectsNonPositivePageSizeWithoutRequest(t *testing.T) {
	t.Parallel()
	for _, size := range []int{0, -1} {
		size := size
		t.Run(string(rune('A'-size)), func(t *testing.T) {
			server := mocklogs.New()
			defer server.Close()
			client := vcflogs.NewClient(server.URL(), "fixture-session", server.Client())
			if _, err := client.ListAllEvents(context.Background(), 0, size); err == nil {
				t.Fatalf("page size %d unexpectedly succeeded", size)
			}
			if got := len(server.Requests()); got != 0 {
				t.Fatalf("page size %d made %d requests", size, got)
			}
		})
	}
}

func TestMockOnlyServesNamedContractOperation(t *testing.T) {
	t.Parallel()
	server := mocklogs.New()
	defer server.Close()

	tests := []struct {
		method string
		path   string
	}{
		{method: http.MethodGet, path: "/api/v2/limits"},
		{method: http.MethodPost, path: "/api/v2/events/timestamp/GT%200?limit=2&order-by-direction=ASC"},
	}
	for _, tt := range tests {
		request, err := http.NewRequest(tt.method, server.URL()+tt.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		request.Header.Set("Authorization", "Bearer fixture-session")
		request.Header.Set("Accept", "application/json")
		response, err := server.Client().Do(request)
		if err != nil {
			t.Fatal(err)
		}
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Errorf("%s %s status = %d, want 404", tt.method, tt.path, response.StatusCode)
		}
	}
}

func TestListAllEventsRejectsInvalidResponses(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		statusCode int
		status     string
		body       string
		pageSize   int
		wantError  string
	}{
		{
			name: "non success status", statusCode: http.StatusServiceUnavailable,
			status: "503 Service Unavailable", body: `{"complete":true,"events":[]}`,
			pageSize: 2, wantError: "unexpected HTTP status",
		},
		{
			name: "malformed payload", statusCode: http.StatusOK, status: "200 OK",
			body: `{`, pageSize: 2, wantError: "decode events response",
		},
		{
			name: "trailing malformed content", statusCode: http.StatusOK, status: "200 OK",
			body: `{"complete":true,"events":[]} trailing`, pageSize: 2, wantError: "decode events response",
		},
		{
			name: "incomplete response", statusCode: http.StatusOK, status: "200 OK",
			body: `{"complete":false,"events":[]}`, pageSize: 2, wantError: "response is incomplete",
		},
		{
			name: "full page does not advance", statusCode: http.StatusOK, status: "200 OK",
			body:     `{"complete":true,"events":[{"text":"a","timestamp":0},{"text":"b","timestamp":0}]}`,
			pageSize: 2, wantError: "did not advance timestamp boundary",
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: tt.statusCode,
					Status:     tt.status,
					Header:     make(http.Header),
					Body:       io.NopCloser(strings.NewReader(tt.body)),
				}, nil
			})
			client := vcflogs.NewClient("http://127.0.0.1", "fixture-session", &http.Client{Transport: transport})
			_, err := client.ListAllEvents(context.Background(), 0, tt.pageSize)
			if err == nil || !strings.Contains(err.Error(), tt.wantError) {
				t.Fatalf("error = %v, want one containing %q", err, tt.wantError)
			}
		})
	}
}

func TestListAllEventsStableOrdering(t *testing.T) {
	t.Parallel()
	body := `{"complete":true,"events":[` +
		`{"text":"zeta","timestamp":2},` +
		`{"text":"beta","timestamp":1},` +
		`{"text":"alpha","timestamp":1}]}`
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusOK,
			Status:     "200 OK",
			Header:     make(http.Header),
			Body:       io.NopCloser(strings.NewReader(body)),
		}, nil
	})
	client := vcflogs.NewClient("http://127.0.0.1", "fixture-session", &http.Client{Transport: transport})
	got, err := client.ListAllEvents(context.Background(), 0, 4)
	if err != nil {
		t.Fatal(err)
	}
	want := []vcflogs.Event{
		{Text: "alpha", Timestamp: 1},
		{Text: "beta", Timestamp: 1},
		{Text: "zeta", Timestamp: 2},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("stable event order = %#v, want %#v", got, want)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func readJSON(t *testing.T, path string, dst any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, dst); err != nil {
		t.Fatal(err)
	}
}
