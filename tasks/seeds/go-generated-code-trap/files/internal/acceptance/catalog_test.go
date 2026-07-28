package acceptance_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"example.com/catalogsync/internal/catalogapi"
	"example.com/catalogsync/syncer"
)

const continuation = "after:widget-1+blue"

func TestSyncAllTraversesServicePages(t *testing.T) {
	t.Parallel()

	var queries []url.Values
	transport := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.Method != http.MethodGet || r.URL.Path != "/v1/widgets" {
			return jsonResponse(http.StatusNotFound, `{"error":"unexpected request"}`), nil
		}
		query := r.URL.Query()
		queries = append(queries, query)

		switch query.Get("page_token") {
		case "":
			return encodedResponse(http.StatusOK, map[string]any{
				"widgets":         []map[string]string{{"id": "widget-1"}},
				"next_page_token": continuation,
			}), nil
		case continuation:
			return encodedResponse(http.StatusOK, map[string]any{
				"widgets":         []map[string]string{{"id": "widget-2"}},
				"next_page_token": "",
			}), nil
		default:
			return jsonResponse(http.StatusBadRequest, `{"error":"unknown page token"}`), nil
		}
	})

	client, err := catalogapi.NewClient("https://catalog.example.test", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	got, err := syncer.New(client, 2).SyncAll(context.Background())
	if err != nil {
		t.Fatalf("SyncAll: %v", err)
	}
	want := []catalogapi.Widget{{ID: "widget-1"}, {ID: "widget-2"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("widgets = %#v, want %#v", got, want)
	}

	if len(queries) != 2 {
		t.Fatalf("request count = %d, want 2", len(queries))
	}
	if got := queries[0].Get("page_size"); got != "2" {
		t.Errorf("first page_size = %q, want 2", got)
	}
	if _, exists := queries[0]["page_token"]; exists {
		t.Errorf("empty page token must be omitted; query was %q", queries[0].Encode())
	}
	if got := queries[1]["page_token"]; !reflect.DeepEqual(got, []string{continuation}) {
		t.Errorf("second page_token values = %#v, want [%q]", got, continuation)
	}
	if _, exists := queries[1]["cursor"]; exists {
		t.Errorf("retired cursor parameter was sent: %q", queries[1].Encode())
	}
}

func TestListWidgetsKeepsIndependentOptionSemantics(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name           string
		includeRetired *bool
		wantPresent    bool
		wantValue      string
	}{
		{name: "unset"},
		{name: "false", includeRetired: boolPointer(false), wantPresent: true, wantValue: "false"},
		{name: "true", includeRetired: boolPointer(true), wantPresent: true, wantValue: "true"},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			var query url.Values
			transport := roundTripFunc(func(r *http.Request) (*http.Response, error) {
				query = r.URL.Query()
				return jsonResponse(http.StatusOK, `{"widgets":[],"next_page_token":""}`), nil
			})

			client, err := catalogapi.NewClient("https://catalog.example.test", &http.Client{Transport: transport})
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.ListWidgets(context.Background(), catalogapi.ListWidgetsOptions{
				PageSize:       17,
				PageToken:      continuation,
				IncludeRetired: tt.includeRetired,
			})
			if err != nil {
				t.Fatalf("ListWidgets: %v", err)
			}

			if got := query.Get("page_size"); got != "17" {
				t.Errorf("page_size = %q, want 17", got)
			}
			if got := query["page_token"]; !reflect.DeepEqual(got, []string{continuation}) {
				t.Errorf("page_token values = %#v, want [%q]", got, continuation)
			}
			if _, exists := query["cursor"]; exists {
				t.Errorf("retired cursor parameter was sent: %q", query.Encode())
			}
			got, present := query["include_retired"]
			if present != tt.wantPresent {
				t.Fatalf("include_retired present = %v, want %v; query %q", present, tt.wantPresent, query.Encode())
			}
			if present && !reflect.DeepEqual(got, []string{tt.wantValue}) {
				t.Errorf("include_retired values = %#v, want [%q]", got, tt.wantValue)
			}
		})
	}
}

func TestSyncAllRetainsRepeatedTokenGuard(t *testing.T) {
	t.Parallel()

	calls := 0
	transport := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if calls > 2 {
			return nil, errors.New("too many requests: pagination guard did not stop the cycle")
		}
		return encodedResponse(http.StatusOK, map[string]any{
			"widgets":         []map[string]string{},
			"next_page_token": continuation,
		}), nil
	})
	client, err := catalogapi.NewClient("https://catalog.example.test", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}

	_, err = syncer.New(client, 2).SyncAll(context.Background())
	if err == nil || err.Error() != `pagination token cycle: "after:widget-1+blue"` {
		t.Fatalf("SyncAll error = %v, want repeated-token cycle error", err)
	}
	if calls != 2 {
		t.Fatalf("request count before cycle rejection = %d, want 2", calls)
	}
}

func boolPointer(value bool) *bool {
	return &value
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

func encodedResponse(status int, value any) *http.Response {
	var body strings.Builder
	if err := json.NewEncoder(&body).Encode(value); err != nil {
		panic(err)
	}
	return jsonResponse(status, body.String())
}

func jsonResponse(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}
