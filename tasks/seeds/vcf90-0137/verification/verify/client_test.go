// This file is part of the protected verifier. Do not edit it.
package verify

import (
	"context"
	"encoding/json"
	"fmt"
	"mime"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"sync"
	"testing"
	"time"

	"vcfopsnetincidents/incidents"
	"vcfopsnetincidents/internal/contractmock"
)

func TestListAllContract(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		credentials incidents.Credentials
		opts        incidents.ListOptions
		count       int
		expire      bool
		wantPages   int
		wantCreates int
	}{
		{
			name: "unset optionals are omitted across an expired token",
			credentials: incidents.Credentials{
				Username: "operator@local",
				Password: "correct horse battery staple",
			},
			count:       23,
			expire:      true,
			wantPages:   3,
			wantCreates: 2,
		},
		{
			name: "all optional query and LDAP domain values are present",
			credentials: incidents.Credentials{
				Username:    "network-reader",
				Password:    "fixture-password",
				DomainType:  incidents.DomainLDAP,
				DomainValue: "corp.example",
			},
			opts: incidents.ListOptions{
				PageSize:      3,
				StartEntityID: "18230:1:000100",
			},
			count:       7,
			wantPages:   3,
			wantCreates: 1,
		},
		{
			name: "LOCAL domain omits its unset value property",
			credentials: incidents.Credentials{
				Username:   "local-reader",
				Password:   "fixture-password",
				DomainType: incidents.DomainLocal,
			},
			count:       1,
			wantPages:   1,
			wantCreates: 1,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			srv := contractmock.New(contractmock.Options{
				Incidents:        test.count,
				ExpireFirstToken: test.expire,
			})
			defer srv.Close()

			client, err := incidents.New(incidents.Config{
				BaseURL:     srv.URL(),
				Credentials: test.credentials,
				HTTPClient:  srvHTTPClient(srv),
			})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			if got := len(srv.Log()); got != 0 {
				t.Fatalf("New made %d network requests; want none", got)
			}
			if got := srv.TokensIssued(); got != 0 {
				t.Fatalf("New obtained %d tokens; want none", got)
			}

			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			result, err := client.ListAll(ctx, test.opts)
			if err != nil {
				t.Fatalf("ListAll: %v", err)
			}

			if result.Pages != test.wantPages {
				t.Errorf("Pages = %d, want %d", result.Pages, test.wantPages)
			}
			if result.TotalCount != test.count {
				t.Errorf("TotalCount = %d, want %d", result.TotalCount, test.count)
			}
			if want := fixtureIncidents(test.count); !reflect.DeepEqual(result.Incidents, want) {
				t.Errorf("incident order/content mismatch\n got: %#v\nwant: %#v", result.Incidents, want)
			}
			if got := client.TokenCreates(); got != test.wantCreates {
				t.Errorf("TokenCreates = %d, want %d", got, test.wantCreates)
			}
			if got := srv.TokensIssued(); got != test.wantCreates {
				t.Errorf("mock issued %d tokens, want %d", got, test.wantCreates)
			}

			assertWireShape(t, srv, test.credentials, test.opts)
			if test.expire {
				assertResumeAtFailedCursor(t, srv.Log())
			}
		})
	}
}

func fixtureIncidents(count int) []incidents.Incident {
	statuses := []string{"OPEN", "IN_PROGRESS", "RESOLVED"}
	result := make([]incidents.Incident, count)
	for i := range result {
		result[i] = incidents.Incident{
			EntityID:      fmt.Sprintf("18230:999:%06d", i+1),
			StartEntityID: fmt.Sprintf("18230:1:%06d", 100+i),
			Name:          fmt.Sprintf("incident-%02d", i+1),
			Status:        statuses[i%len(statuses)],
		}
	}
	return result
}

type createGateTransport struct {
	base        http.RoundTripper
	enabled     bool
	entered     chan struct{}
	release     chan struct{}
	enterOnce   sync.Once
	releaseOnce sync.Once
}

func newCreateGateTransport() *createGateTransport {
	return &createGateTransport{
		base:    http.DefaultTransport,
		entered: make(chan struct{}),
		release: make(chan struct{}),
	}
}

func (g *createGateTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if g.enabled && request.URL.Path == contractmock.BasePath+"/auth/token" {
		g.enterOnce.Do(func() { close(g.entered) })
		<-g.release
	}
	return g.base.RoundTrip(request)
}

func (g *createGateTransport) unblock() {
	g.releaseOnce.Do(func() { close(g.release) })
}

func TestTokenCreatesConcurrentWithListAll(t *testing.T) {
	t.Parallel()
	srv := contractmock.New(contractmock.Options{Incidents: 1})
	defer srv.Close()

	transport := newCreateGateTransport()
	client, err := incidents.New(incidents.Config{
		BaseURL: srv.URL(),
		Credentials: incidents.Credentials{
			Username: "race-reader",
			Password: "fixture-password",
		},
		HTTPClient: &http.Client{Transport: transport, Timeout: 3 * time.Second},
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if got := len(srv.Log()); got != 0 {
		t.Fatalf("New made %d network requests; want none", got)
	}

	transport.enabled = true
	defer transport.unblock()
	type outcome struct {
		result *incidents.Result
		err    error
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	listDone := make(chan outcome, 1)
	go func() {
		result, err := client.ListAll(ctx, incidents.ListOptions{})
		listDone <- outcome{result: result, err: err}
	}()

	select {
	case <-transport.entered:
	case <-ctx.Done():
		t.Fatalf("create request did not start: %v", ctx.Err())
	}

	readStart := make(chan struct{})
	readDone := make(chan int, 1)
	go func() {
		<-readStart
		readDone <- client.TokenCreates()
	}()
	transport.unblock()
	close(readStart)

	var got outcome
	select {
	case got = <-listDone:
	case <-ctx.Done():
		t.Fatalf("ListAll did not finish: %v", ctx.Err())
	}
	select {
	case <-readDone:
	case <-ctx.Done():
		t.Fatalf("concurrent TokenCreates did not finish: %v", ctx.Err())
	}
	if got.err != nil {
		t.Fatalf("ListAll: %v", got.err)
	}
	if !reflect.DeepEqual(got.result.Incidents, fixtureIncidents(1)) {
		t.Fatalf("ListAll incidents = %#v, want fixture incident", got.result.Incidents)
	}
	if gotCreates := client.TokenCreates(); gotCreates != 1 {
		t.Fatalf("TokenCreates = %d, want 1", gotCreates)
	}
}

func srvHTTPClient(_ *contractmock.Server) *http.Client {
	return &http.Client{Timeout: 3 * time.Second}
}

func assertWireShape(t *testing.T, srv *contractmock.Server, credentials incidents.Credentials, opts incidents.ListOptions) {
	t.Helper()
	log := srv.Log()
	if len(log) == 0 {
		t.Fatal("client made no requests")
	}
	for _, entry := range log {
		if entry.OperationID == "" {
			t.Errorf("request #%d %s %s matched no contract operation (status %d)", entry.Seq, entry.Method, entry.Path, entry.Status)
		}
		switch entry.Status {
		case http.StatusBadRequest, http.StatusNotFound, http.StatusMethodNotAllowed:
			t.Errorf("request #%d was rejected by the contract mock with %d", entry.Seq, entry.Status)
		}
	}
	assertCreateWire(t, contractmock.EntriesFor(log, "create"), credentials)
	assertListWire(t, srv, contractmock.EntriesFor(log, "listTroubleshootingIncidents"), opts)
}

func assertCreateWire(t *testing.T, entries []contractmock.Entry, credentials incidents.Credentials) {
	t.Helper()
	if len(entries) == 0 {
		t.Fatal("operationId create was not called")
	}
	for _, entry := range entries {
		where := fmt.Sprintf("create request #%d", entry.Seq)
		if entry.Method != http.MethodPost {
			t.Errorf("%s method = %s, want POST", where, entry.Method)
		}
		if entry.Path != contractmock.BasePath+"/auth/token" {
			t.Errorf("%s path = %q", where, entry.Path)
		}
		if entry.Authorization != "" {
			t.Errorf("%s sent Authorization %q; create is unauthenticated", where, entry.Authorization)
		}
		mediaType, _, err := mime.ParseMediaType(entry.ContentType)
		if err != nil || mediaType != "application/json" {
			t.Errorf("%s Content-Type = %q, want application/json", where, entry.ContentType)
		}
		assertKeys(t, where+" query", entry.QueryKeys(), nil)

		var body map[string]json.RawMessage
		if err := json.Unmarshal(entry.Body, &body); err != nil {
			t.Errorf("%s body is not a JSON object: %v", where, err)
			continue
		}
		wantKeys := []string{"password", "username"}
		if credentials.DomainType != "" {
			wantKeys = append(wantKeys, "domain")
		}
		assertKeys(t, where+" body", sortedMapKeys(body), wantKeys)
		assertJSONString(t, where, body, "username", credentials.Username)
		assertJSONString(t, where, body, "password", credentials.Password)

		if raw, ok := body["domain"]; ok {
			var domain map[string]json.RawMessage
			if err := json.Unmarshal(raw, &domain); err != nil {
				t.Errorf("%s domain is not an object: %v", where, err)
				continue
			}
			wantDomainKeys := []string{"domain_type"}
			if credentials.DomainValue != "" {
				wantDomainKeys = append(wantDomainKeys, "value")
			}
			assertKeys(t, where+" domain", sortedMapKeys(domain), wantDomainKeys)
			assertJSONString(t, where+" domain", domain, "domain_type", credentials.DomainType)
			if credentials.DomainValue != "" {
				assertJSONString(t, where+" domain", domain, "value", credentials.DomainValue)
			}
		}
	}
}

func assertListWire(t *testing.T, srv *contractmock.Server, entries []contractmock.Entry, opts incidents.ListOptions) {
	t.Helper()
	if len(entries) == 0 {
		t.Fatal("operationId listTroubleshootingIncidents was not called")
	}
	for i, entry := range entries {
		where := fmt.Sprintf("list request #%d", entry.Seq)
		if entry.Method != http.MethodGet {
			t.Errorf("%s method = %s, want GET", where, entry.Method)
		}
		if entry.Path != contractmock.BasePath+"/gnt/troubleshoot/incidents" {
			t.Errorf("%s path = %q", where, entry.Path)
		}
		if len(entry.Body) != 0 {
			t.Errorf("%s sent a %d-byte body; the operation has no body", where, len(entry.Body))
		}
		if entry.TokenIndex == 0 {
			t.Errorf("%s Authorization %q is not a token issued by the mock", where, entry.Authorization)
		} else if want := contractmock.AuthPrefix + srv.TokenValue(entry.TokenIndex); entry.Authorization != want {
			t.Errorf("%s Authorization = %q, want %q", where, entry.Authorization, want)
		}
		for key, values := range entry.Query {
			if len(values) != 1 {
				t.Errorf("%s query %q has %d values, want exactly one", where, key, len(values))
			}
		}

		wantKeys := []string{}
		if opts.PageSize > 0 {
			wantKeys = append(wantKeys, "size")
			if entry.Query.Get("size") != strconv.Itoa(opts.PageSize) {
				t.Errorf("%s size = %q, want %d", where, entry.Query.Get("size"), opts.PageSize)
			}
		}
		if opts.StartEntityID != "" {
			wantKeys = append(wantKeys, "start_entity_id")
			if entry.Query.Get("start_entity_id") != opts.StartEntityID {
				t.Errorf("%s start_entity_id = %q, want %q", where, entry.Query.Get("start_entity_id"), opts.StartEntityID)
			}
		}
		if entry.Query.Has("cursor") {
			wantKeys = append(wantKeys, "cursor")
			if entry.Query.Get("cursor") == "" {
				t.Errorf("%s sent an empty cursor", where)
			}
		}
		assertKeys(t, where+" query", entry.QueryKeys(), wantKeys)
		if i == 0 && entry.Query.Has("cursor") {
			t.Errorf("%s sent cursor on the first page", where)
		}
	}
}

func assertResumeAtFailedCursor(t *testing.T, log []contractmock.Entry) {
	t.Helper()
	entries := contractmock.EntriesFor(log, "listTroubleshootingIncidents")
	var firstPage, unauthorized int
	var failedCursor string
	for _, entry := range entries {
		if !entry.Query.Has("cursor") && entry.Status == http.StatusOK {
			firstPage++
		}
		if entry.Status == http.StatusUnauthorized {
			unauthorized++
			failedCursor = entry.Query.Get("cursor")
		}
	}
	if firstPage != 1 {
		t.Errorf("successful first page was requested %d times, want exactly once", firstPage)
	}
	if unauthorized != 1 || failedCursor == "" {
		t.Errorf("401 observations = %d with cursor %q, want one failed non-first page", unauthorized, failedCursor)
		return
	}
	var attempts int
	var successAfterFailure bool
	seenFailure := false
	for _, entry := range entries {
		if entry.Query.Get("cursor") != failedCursor {
			continue
		}
		attempts++
		if entry.Status == http.StatusUnauthorized {
			seenFailure = true
		} else if seenFailure && entry.Status == http.StatusOK {
			successAfterFailure = true
		}
	}
	if attempts != 2 || !successAfterFailure {
		t.Errorf("failed cursor %q attempts = %d, success-after-refresh = %v; want one 401 then one successful retry", failedCursor, attempts, successAfterFailure)
	}
}

func assertKeys(t *testing.T, where string, got, want []string) {
	t.Helper()
	got = append([]string(nil), got...)
	want = append([]string(nil), want...)
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s keys = %v, want %v", where, got, want)
	}
}

func sortedMapKeys(values map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func assertJSONString(t *testing.T, where string, body map[string]json.RawMessage, key, want string) {
	t.Helper()
	raw, ok := body[key]
	if !ok {
		return
	}
	var got string
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Errorf("%s %s is not a string: %v", where, key, err)
		return
	}
	if got != want {
		t.Errorf("%s %s = %q, want %q", where, key, got, want)
	}
}
