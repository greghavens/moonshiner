package verify

import (
	"context"
	"io"
	"net/http"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"

	"vcfops.local/opssync/opsapi"
	"vcfops.local/opssync/vcfops"
)

// TestARequestIsReplayedAtMostOnce uses an in-process transport that rejects
// the original request and its replay, then would accept an improper third
// attempt. It protects the one-replay limit without relying on timing or on a
// token-expiry race, and a broken unbounded retry loop terminates promptly.
func TestARequestIsReplayedAtMostOnce(t *testing.T) {
	transport := &twiceUnauthorizedTransport{}
	client, err := vcfops.New(opsapi.Config{
		BaseURL:    "https://vcf-operations.test",
		Username:   "svc-opssync",
		Password:   "Pa55w0rd!",
		HTTPClient: &http.Client{Transport: transport},
	})
	if err != nil {
		t.Fatalf("vcfops.New: %v", err)
	}

	_, err = client.ListAllResources(context.Background(), opsapi.ResourceFilter{PageSize: 25})
	if err == nil {
		t.Fatal("ListAllResources succeeded although both the request and its one replay were rejected")
	}

	transport.mu.Lock()
	gotAcquisitions := transport.acquisitions
	gotAttempts := append([]recordedAttempt(nil), transport.attempts...)
	transport.mu.Unlock()

	if gotAcquisitions != 2 {
		t.Errorf("acquired %d tokens, want 2 (initial plus one refresh)", gotAcquisitions)
	}
	wantAttempts := []recordedAttempt{
		{Method: http.MethodGet, Path: "/suite-api/api/resources", RawQuery: "page=0&pageSize=25", Authorization: "OpsToken reject-token-1"},
		{Method: http.MethodGet, Path: "/suite-api/api/resources", RawQuery: "page=0&pageSize=25", Authorization: "OpsToken reject-token-2"},
	}
	if !reflect.DeepEqual(gotAttempts, wantAttempts) {
		t.Errorf("authenticated attempts = %+v, want exactly one original and one unchanged replay: %+v", gotAttempts, wantAttempts)
	}
	if got := client.Stats(); got != (opsapi.Stats{TokensAcquired: 2}) {
		t.Errorf("Stats() = %+v, want only the two successful token acquisitions counted", got)
	}
}

type recordedAttempt struct {
	Method        string
	Path          string
	RawQuery      string
	Authorization string
}

type twiceUnauthorizedTransport struct {
	mu           sync.Mutex
	acquisitions int
	attempts     []recordedAttempt
}

func (t *twiceUnauthorizedTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	t.mu.Lock()
	defer t.mu.Unlock()

	status := http.StatusUnauthorized
	body := ""
	if req.Method == http.MethodPost && req.URL.Path == "/suite-api/api/auth/token/acquire" {
		t.acquisitions++
		status = http.StatusOK
		body = `{"token":"reject-token-` + strconv.Itoa(t.acquisitions) + `","validity":1767247200000}`
	} else {
		t.attempts = append(t.attempts, recordedAttempt{
			Method:        req.Method,
			Path:          req.URL.Path,
			RawQuery:      req.URL.RawQuery,
			Authorization: req.Header.Get("Authorization"),
		})
		if len(t.attempts) > 2 {
			status = http.StatusOK
			body = `{}`
		}
	}

	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
		Request:    req,
	}, nil
}
