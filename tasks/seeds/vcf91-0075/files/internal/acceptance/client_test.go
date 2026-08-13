package acceptance_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"example.com/vcf91nsx/internal/contractmock"
	"example.com/vcf91nsx/nsxpolicy"
)

const (
	specCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

type recordedContract struct {
	Source struct {
		Commit string `json:"commit"`
		Path   string `json:"path"`
	} `json:"source"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

func TestContractProvenanceAndMockRoutes(t *testing.T) {
	t.Parallel()
	data, err := os.ReadFile(filepath.Join("..", "..", "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var contract recordedContract
	if err := json.Unmarshal(data, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.Source.Commit != specCommit || contract.Source.Path != specPath {
		t.Fatalf("contract source = %s %s", contract.Source.Commit, contract.Source.Path)
	}

	want := []contractmock.Route{
		{OperationID: "ListGroupForDomain", Method: http.MethodGet, Path: "/policy/api/v1/infra/domains/{domain-id}/groups"},
		{OperationID: "UpdateGroupForDomain", Method: http.MethodPut, Path: "/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}"},
	}
	got := contractmock.ContractRoutes()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("mock routes = %#v, want %#v", got, want)
	}
	if len(contract.Operations) != len(want) {
		t.Fatalf("contract operations = %d, want %d", len(contract.Operations), len(want))
	}
	for i, operation := range contract.Operations {
		if operation.OperationID != want[i].OperationID || operation.Method != want[i].Method || operation.Path != want[i].Path {
			t.Fatalf("contract operation %d = %#v, want %#v", i, operation, want[i])
		}
	}

	sourceData, err := os.ReadFile(filepath.Join("..", "..", "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		Commit     string `json:"repository_commit_sha"`
		Path       string `json:"spec_path"`
		Operations []struct {
			OperationID string `json:"operationId"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(sourceData, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Commit != specCommit || sources.Path != specPath || len(sources.Operations) != 2 {
		t.Fatalf("official source record is not pinned to the contract: %#v", sources)
	}
	for i := range sources.Operations {
		if sources.Operations[i].OperationID != want[i].OperationID {
			t.Fatalf("source operation %d = %q, want %q", i, sources.Operations[i].OperationID, want[i].OperationID)
		}
	}
}

func TestRequestWireShape(t *testing.T) {
	t.Parallel()

	str := func(value string) *string { return &value }
	boolean := func(value bool) *bool { return &value }
	integer := func(value int) *int { return &value }
	revision := 0

	tests := []struct {
		name          string
		invoke        func(*nsxpolicy.Client) error
		wantOperation string
		wantMethod    string
		wantURI       string
		wantBody      string
		wantType      string
	}{
		{
			name: "unset list options are omitted",
			invoke: func(client *nsxpolicy.Client) error {
				_, err := client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{})
				return err
			},
			wantOperation: "ListGroupForDomain",
			wantMethod:    http.MethodGet,
			wantURI:       "/policy/api/v1/infra/domains/default/groups",
		},
		{
			name: "explicit zero values and escaped path",
			invoke: func(client *nsxpolicy.Client) error {
				_, err := client.ListGroups(context.Background(), "prod west", nsxpolicy.ListOptions{
					Cursor:                      str(""),
					IncludeMarkForDeleteObjects: boolean(false),
					IncludedFields:              str("id,display_name"),
					MemberTypes:                 str("VirtualMachine,IPSet"),
					PageSize:                    integer(0),
					SortAscending:               boolean(false),
					SortBy:                      str("display_name"),
				})
				return err
			},
			wantOperation: "ListGroupForDomain",
			wantMethod:    http.MethodGet,
			wantURI:       "/policy/api/v1/infra/domains/prod%20west/groups?cursor=&include_mark_for_delete_objects=false&included_fields=id%2Cdisplay_name&member_types=VirtualMachine%2CIPSet&page_size=0&sort_ascending=false&sort_by=display_name",
		},
		{
			name: "unset optional group fields are omitted",
			invoke: func(client *nsxpolicy.Client) error {
				_, err := client.UpdateGroup(context.Background(), "default", "payments/api", nsxpolicy.Group{DisplayName: str("Payments")})
				return err
			},
			wantOperation: "UpdateGroupForDomain",
			wantMethod:    http.MethodPut,
			wantURI:       "/policy/api/v1/infra/domains/default/groups/payments%2Fapi",
			wantBody:      `{"display_name":"Payments"}`,
			wantType:      "application/json",
		},
		{
			name: "explicit empty and zero group fields are retained",
			invoke: func(client *nsxpolicy.Client) error {
				_, err := client.UpdateGroup(context.Background(), "prod", "edge", nsxpolicy.Group{
					Revision:    &revision,
					Description: str(""),
					DisplayName: str("Edge"),
					Expression: []nsxpolicy.Expression{{
						ResourceType: "Condition",
						MemberType:   "VirtualMachine",
						Key:          "Tag",
						Operator:     "EQUALS",
						Value:        "web",
						Paths:        []string{"/infra/segments/web"},
						IPAddresses:  []string{"192.0.2.10"},
					}},
					GroupType:    []string{"IPBasedMembership"},
					ID:           "edge",
					ResourceType: "Group",
					Tags:         []nsxpolicy.Tag{{Scope: "environment", Tag: "production"}},
				})
				return err
			},
			wantOperation: "UpdateGroupForDomain",
			wantMethod:    http.MethodPut,
			wantURI:       "/policy/api/v1/infra/domains/prod/groups/edge",
			wantBody:      `{"_revision":0,"description":"","display_name":"Edge","expression":[{"resource_type":"Condition","member_type":"VirtualMachine","key":"Tag","operator":"EQUALS","value":"web","paths":["/infra/segments/web"],"ip_addresses":["192.0.2.10"]}],"group_type":["IPBasedMembership"],"id":"edge","resource_type":"Group","tags":[{"scope":"environment","tag":"production"}]}`,
			wantType:      "application/json",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := contractmock.New(nil)
			defer server.Close()
			client := newClient(t, server, nsxpolicy.Credentials{Username: "rotation-bot", Password: "first-secret"})
			if err := tt.invoke(client); err != nil {
				t.Fatal(err)
			}
			requests := server.Requests()
			if len(requests) != 1 {
				t.Fatalf("requests = %d, want 1", len(requests))
			}
			request := requests[0]
			if request.OperationID != tt.wantOperation || request.Method != tt.wantMethod || request.RequestURI != tt.wantURI {
				t.Fatalf("request = %s %s (%s), want %s %s (%s)", request.Method, request.RequestURI, request.OperationID, tt.wantMethod, tt.wantURI, tt.wantOperation)
			}
			if string(request.Body) != tt.wantBody {
				t.Fatalf("body = %q, want %q", request.Body, tt.wantBody)
			}
			if got := request.Header.Get("Accept"); got != "application/json" {
				t.Fatalf("Accept = %q", got)
			}
			if got := request.Header.Get("Content-Type"); got != tt.wantType {
				t.Fatalf("Content-Type = %q, want %q", got, tt.wantType)
			}
			if got := basicCredentials(request.Header); got != (nsxpolicy.Credentials{Username: "rotation-bot", Password: "first-secret"}) {
				t.Fatalf("Basic credentials = %#v", got)
			}
		})
	}
}

func TestResponseDecoding(t *testing.T) {
	t.Parallel()
	description := "managed by rotation test"
	displayName := "Web"
	revision := 7
	tests := []struct {
		name   string
		body   string
		invoke func(*nsxpolicy.Client) (any, error)
		want   any
	}{
		{
			name: "list",
			body: `{"cursor":"next-page","result_count":1,"results":[{"_revision":7,"description":"managed by rotation test","display_name":"Web","expression":[{"resource_type":"PathExpression","paths":["/infra/segments/web"]}],"group_type":["IPBasedMembership"],"id":"web","resource_type":"Group","tags":[{"scope":"environment","tag":"production"}]}],"sort_ascending":true,"sort_by":"display_name"}`,
			invoke: func(client *nsxpolicy.Client) (any, error) {
				return client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{})
			},
			want: nsxpolicy.GroupListResult{
				Cursor:      "next-page",
				ResultCount: 1,
				Results: []nsxpolicy.Group{{
					Revision:     &revision,
					Description:  &description,
					DisplayName:  &displayName,
					Expression:   []nsxpolicy.Expression{{ResourceType: "PathExpression", Paths: []string{"/infra/segments/web"}}},
					GroupType:    []string{"IPBasedMembership"},
					ID:           "web",
					ResourceType: "Group",
					Tags:         []nsxpolicy.Tag{{Scope: "environment", Tag: "production"}},
				}},
				SortAscending: true,
				SortBy:        "display_name",
			},
		},
		{
			name: "update",
			body: `{"id":"web","description":"managed by rotation test"}`,
			invoke: func(client *nsxpolicy.Client) (any, error) {
				return client.UpdateGroup(context.Background(), "default", "web", nsxpolicy.Group{Description: &description})
			},
			want: nsxpolicy.Group{ID: "web", Description: &description},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := contractmock.New(func(contractmock.Request) contractmock.Reply {
				return contractmock.Reply{Status: http.StatusOK, Body: tt.body}
			})
			defer server.Close()
			got, err := tt.invoke(newClient(t, server, nsxpolicy.Credentials{Username: "svc", Password: "secret"}))
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("response = %#v, want %#v", got, tt.want)
			}
		})
	}
}

func TestCredentialRotationDoesNotStrandInflightRequest(t *testing.T) {
	firstArrived := make(chan struct{}, 1)
	releaseFirst := make(chan struct{})
	var releaseOnce sync.Once
	releaseFirstRequest := func() { releaseOnce.Do(func() { close(releaseFirst) }) }
	server := contractmock.New(func(request contractmock.Request) contractmock.Reply {
		if basicPassword(request.Header) == "old-secret" {
			firstArrived <- struct{}{}
			<-releaseFirst
			return contractmock.Reply{Status: http.StatusUnauthorized, Body: `{"error":"expired"}`}
		}
		return contractmock.Reply{Status: http.StatusOK, Body: string(request.Body)}
	})
	defer server.Close()
	defer releaseFirstRequest()
	client := newClient(t, server, nsxpolicy.Credentials{Username: "old-svc", Password: "old-secret"})

	done := make(chan error, 1)
	go func() {
		_, err := client.UpdateGroup(context.Background(), "default", "payments/api", nsxpolicy.Group{
			DisplayName: stringPointer("Payments"),
		})
		done <- err
	}()

	select {
	case <-firstArrived:
	case err := <-done:
		t.Fatalf("request returned before credential rotation: %v", err)
	}
	if err := client.RotateCredentials(nsxpolicy.Credentials{Username: "new-svc", Password: "new-secret"}); err != nil {
		t.Fatal(err)
	}
	releaseFirstRequest()
	if err := <-done; err != nil {
		t.Fatalf("in-flight request was stranded: %v", err)
	}

	requests := server.Requests()
	if len(requests) != 2 {
		t.Fatalf("attempts = %d, want 2", len(requests))
	}
	if got := []nsxpolicy.Credentials{basicCredentials(requests[0].Header), basicCredentials(requests[1].Header)}; !reflect.DeepEqual(got, []nsxpolicy.Credentials{{Username: "old-svc", Password: "old-secret"}, {Username: "new-svc", Password: "new-secret"}}) {
		t.Fatalf("attempt credentials = %#v", got)
	}
	for _, field := range []string{"OperationID", "Method", "RequestURI", "Header", "Body"} {
		if !sameRequestField(requests[0], requests[1], field) {
			t.Fatalf("retry changed %s: %#v then %#v", field, requests[0], requests[1])
		}
	}
}

func TestRotationAffectsFutureAttempts(t *testing.T) {
	t.Parallel()
	server := contractmock.New(nil)
	defer server.Close()
	client := newClient(t, server, nsxpolicy.Credentials{Username: "old-svc", Password: "old-secret"})
	if err := client.RotateCredentials(nsxpolicy.Credentials{Username: "new-svc", Password: "new-secret"}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{}); err != nil {
		t.Fatal(err)
	}
	requests := server.Requests()
	if len(requests) != 1 {
		t.Fatalf("requests = %d, want 1", len(requests))
	}
	if got := basicCredentials(requests[0].Header); got != (nsxpolicy.Credentials{Username: "new-svc", Password: "new-secret"}) {
		t.Fatalf("Basic credentials = %#v, want new-svc/new-secret", got)
	}
}

func TestConcurrentRotationAndRequestsAreRaceFree(t *testing.T) {
	server := contractmock.New(func(contractmock.Request) contractmock.Reply {
		return contractmock.Reply{Status: http.StatusOK, Body: `{"results":[]}`}
	})
	defer server.Close()
	client := newClient(t, server, nsxpolicy.Credentials{Username: "svc", Password: "secret-0"})

	start := make(chan struct{})
	var wg sync.WaitGroup
	var errorMu sync.Mutex
	var firstError error
	var errorCount int
	recordError := func(err error) {
		errorMu.Lock()
		defer errorMu.Unlock()
		errorCount++
		if firstError == nil {
			firstError = err
		}
	}
	for worker := 0; worker < 8; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			for request := 0; request < 32; request++ {
				if _, err := client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{}); err != nil {
					recordError(err)
				}
			}
		}()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		<-start
		for rotation := 1; rotation <= 128; rotation++ {
			if err := client.RotateCredentials(nsxpolicy.Credentials{Username: "svc", Password: "secret-rotated"}); err != nil {
				recordError(err)
			}
		}
	}()
	close(start)
	wg.Wait()
	if firstError != nil {
		t.Errorf("%d concurrent operations failed; first error: %v", errorCount, firstError)
	}
	if got := len(server.Requests()); got != 8*32 {
		t.Fatalf("logged requests = %d, want %d", got, 8*32)
	}
}

func TestUnauthorizedRetryPolicy(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name      string
		rotateTo  *nsxpolicy.Credentials
		wantCalls int32
	}{
		{name: "unchanged credentials are not retried", wantCalls: 1},
		{name: "only one retry after rotation", rotateTo: &nsxpolicy.Credentials{Username: "svc", Password: "new"}, wantCalls: 2},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			arrived := make(chan struct{}, 1)
			release := make(chan struct{})
			var releaseOnce sync.Once
			releaseFirstRequest := func() { releaseOnce.Do(func() { close(release) }) }
			var calls atomic.Int32
			server := contractmock.New(func(contractmock.Request) contractmock.Reply {
				call := calls.Add(1)
				if call == 1 {
					arrived <- struct{}{}
					<-release
				}
				return contractmock.Reply{Status: http.StatusUnauthorized, Body: `{"error":"unauthorized"}`}
			})
			defer server.Close()
			defer releaseFirstRequest()
			client := newClient(t, server, nsxpolicy.Credentials{Username: "svc", Password: "old"})
			pageSize := 0
			done := make(chan error, 1)
			go func() {
				_, err := client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{PageSize: &pageSize})
				done <- err
			}()
			select {
			case <-arrived:
			case err := <-done:
				t.Fatalf("request returned before retry decision: %v", err)
			}
			if tt.rotateTo != nil {
				if err := client.RotateCredentials(*tt.rotateTo); err != nil {
					t.Fatal(err)
				}
			}
			releaseFirstRequest()
			err := <-done
			var httpErr *nsxpolicy.HTTPError
			if !errors.As(err, &httpErr) || httpErr.StatusCode != http.StatusUnauthorized || !strings.Contains(httpErr.Body, "unauthorized") {
				t.Fatalf("error = %#v, want HTTPError 401 with response body", err)
			}
			if got := calls.Load(); got != tt.wantCalls {
				t.Fatalf("attempts = %d, want %d", got, tt.wantCalls)
			}
			for i, request := range server.Requests() {
				if request.RequestURI != "/policy/api/v1/infra/domains/default/groups?page_size=0" {
					t.Fatalf("attempt %d URI = %q", i+1, request.RequestURI)
				}
			}
		})
	}
}

func TestContextCancellationIsHonored(t *testing.T) {
	t.Parallel()
	arrived := make(chan struct{}, 1)
	release := make(chan struct{})
	server := contractmock.New(func(contractmock.Request) contractmock.Reply {
		arrived <- struct{}{}
		<-release
		return contractmock.Reply{Status: http.StatusOK, Body: `{"results":[]}`}
	})
	defer server.Close()
	defer close(release)
	client := newClient(t, server, nsxpolicy.Credentials{Username: "svc", Password: "secret"})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := client.ListGroups(ctx, "default", nsxpolicy.ListOptions{})
		done <- err
	}()
	select {
	case <-arrived:
	case err := <-done:
		t.Fatalf("request returned before cancellation: %v", err)
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context canceled", err)
	}
}

func TestResponseBodiesAreClosed(t *testing.T) {
	t.Parallel()
	tests := []struct {
		status int
		body   string
	}{
		{status: http.StatusOK, body: `{"results":[]}`},
		{status: http.StatusBadGateway, body: `{"error":"upstream unavailable"}`},
	}
	for _, tt := range tests {
		t.Run(http.StatusText(tt.status), func(t *testing.T) {
			body := &trackingBody{Reader: strings.NewReader(tt.body)}
			transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: tt.status,
					Header:     make(http.Header),
					Body:       body,
				}, nil
			})
			client, err := nsxpolicy.NewClient("https://nsx.example.test", &http.Client{Transport: transport}, nsxpolicy.Credentials{Username: "svc", Password: "secret"})
			if err != nil {
				t.Fatal(err)
			}
			_, callErr := client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{})
			if !body.closed.Load() {
				t.Fatal("response body was not closed")
			}
			if tt.status == http.StatusOK {
				if callErr != nil {
					t.Fatalf("successful response returned error: %v", callErr)
				}
				return
			}
			var httpErr *nsxpolicy.HTTPError
			if !errors.As(callErr, &httpErr) || httpErr.StatusCode != tt.status || httpErr.Body != tt.body {
				t.Fatalf("error = %#v, want HTTPError %d with body %q", callErr, tt.status, tt.body)
			}
			if !strings.Contains(callErr.Error(), "upstream unavailable") {
				t.Fatalf("error text lacks response detail: %q", callErr)
			}
		})
	}
}

func TestRetryResponseBodiesAreClosed(t *testing.T) {
	t.Parallel()
	firstBody := &trackingBody{Reader: strings.NewReader(`{"error":"expired"}`)}
	secondBody := &trackingBody{Reader: strings.NewReader(`{"results":[]}`)}
	var client *nsxpolicy.Client
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if calls.Add(1) == 1 {
			if err := client.RotateCredentials(nsxpolicy.Credentials{Username: "new-svc", Password: "new-secret"}); err != nil {
				return nil, err
			}
			return &http.Response{StatusCode: http.StatusUnauthorized, Header: make(http.Header), Body: firstBody}, nil
		}
		if got := basicCredentials(request.Header); got != (nsxpolicy.Credentials{Username: "new-svc", Password: "new-secret"}) {
			return nil, errors.New("retry did not use rotated credentials")
		}
		return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: secondBody}, nil
	})
	var err error
	client, err = nsxpolicy.NewClient("https://nsx.example.test", &http.Client{Transport: transport}, nsxpolicy.Credentials{Username: "old-svc", Password: "old-secret"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.ListGroups(context.Background(), "default", nsxpolicy.ListOptions{}); err != nil {
		t.Fatal(err)
	}
	if !firstBody.closed.Load() || !secondBody.closed.Load() {
		t.Fatalf("closed bodies = first:%v second:%v, want both true", firstBody.closed.Load(), secondBody.closed.Load())
	}
}

func TestOptionalQueryNamesMatchContract(t *testing.T) {
	t.Parallel()
	data, err := os.ReadFile(filepath.Join("..", "..", "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Operations []struct {
			OperationID     string `json:"operationId"`
			QueryParameters []struct {
				Name string `json:"name"`
			} `json:"query_parameters"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, operation := range document.Operations {
		if operation.OperationID == "ListGroupForDomain" {
			for _, parameter := range operation.QueryParameters {
				got = append(got, parameter.Name)
			}
		}
	}
	sort.Strings(got)
	want := []string{"cursor", "include_mark_for_delete_objects", "included_fields", "member_types", "page_size", "sort_ascending", "sort_by"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("query names = %v, want %v", got, want)
	}
}

func newClient(t *testing.T, server *contractmock.Server, credentials nsxpolicy.Credentials) *nsxpolicy.Client {
	t.Helper()
	client, err := nsxpolicy.NewClient(server.URL(), server.Client(), credentials)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func stringPointer(value string) *string { return &value }

func basicPassword(header http.Header) string {
	return basicCredentials(header).Password
}

func basicCredentials(header http.Header) nsxpolicy.Credentials {
	authorization := header.Get("Authorization")
	encoded := strings.TrimPrefix(authorization, "Basic ")
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nsxpolicy.Credentials{}
	}
	username, password, ok := strings.Cut(string(decoded), ":")
	if !ok {
		return nsxpolicy.Credentials{}
	}
	return nsxpolicy.Credentials{Username: username, Password: password}
}

func sameRequestField(a, b contractmock.Request, field string) bool {
	switch field {
	case "OperationID":
		return a.OperationID == b.OperationID
	case "Method":
		return a.Method == b.Method
	case "RequestURI":
		return a.RequestURI == b.RequestURI
	case "Header":
		authA := a.Header.Get("Authorization")
		authB := b.Header.Get("Authorization")
		a.Header.Del("Authorization")
		b.Header.Del("Authorization")
		return authA != authB && reflect.DeepEqual(a.Header, b.Header)
	case "Body":
		return string(a.Body) == string(b.Body)
	default:
		return false
	}
}

type trackingBody struct {
	io.Reader
	closed atomic.Bool
}

func (body *trackingBody) Close() error {
	body.closed.Store(true)
	return nil
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (roundTrip roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return roundTrip(request)
}
