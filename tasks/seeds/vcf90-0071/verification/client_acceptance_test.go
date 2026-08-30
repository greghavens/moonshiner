package vcfops_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	vcfops "example.com/vcfops"
	"example.com/vcfops/internal/vcfmock"
)

func boolPointer(value bool) *bool { return &value }

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type closeSignalBody struct {
	io.ReadCloser
	closed chan struct{}
	once   *sync.Once
}

func (body *closeSignalBody) Close() error {
	err := body.ReadCloser.Close()
	body.once.Do(func() { close(body.closed) })
	return err
}

func TestImportAndWaitWireShape(t *testing.T) {
	tests := []struct {
		name             string
		opts             vcfops.ImportOptions
		wantQuery        string
		wantEncryption   string
		wantEncryptionOK bool
	}{
		{
			name:      "unset optional fields are omitted",
			wantQuery: "",
		},
		{
			name: "explicit false and password are represented",
			opts: vcfops.ImportOptions{
				Force:              boolPointer(false),
				EncryptionPassword: "correct horse battery staple",
			},
			wantQuery:        "force=false",
			wantEncryption:   "correct horse battery staple",
			wantEncryptionOK: true,
		},
		{
			name: "explicit true is represented",
			opts: vcfops.ImportOptions{
				Force: boolPointer(true),
			},
			wantQuery: "force=true",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			mock := vcfmock.New("NOT_INITIALIZED", "INITIALIZED", "RUNNING", "FINISHED")
			defer mock.Close()

			client, err := vcfops.NewClient(mock.URL(), "vRealizeOpsToken fixture-token", mock.Client(), time.Microsecond)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			result, err := client.ImportAndWait(context.Background(), "bundle.zip", strings.NewReader("fixture-content"), test.opts)
			if err != nil {
				t.Fatalf("ImportAndWait: %v", err)
			}
			if result.Accepted.ID != "bb1f4c64-b1e9-4a3f-a051-790d926342d2" || result.Operation.State != vcfops.StateFinished {
				t.Fatalf("unexpected result: %+v", result)
			}

			requests := mock.Requests()
			if len(requests) != 5 {
				t.Fatalf("request count = %d, want one POST and four polls", len(requests))
			}
			post := requests[0]
			if post.Method != "POST" || post.Path != vcfmock.ImportPath || post.RawQuery != test.wantQuery {
				t.Fatalf("POST target = %s %s?%s", post.Method, post.Path, post.RawQuery)
			}
			assertCommonHeaders(t, post)
			values, present := post.Header["Encryptionpassword"]
			if present != test.wantEncryptionOK {
				t.Fatalf("EncryptionPassword presence = %v, want %v (values %q)", present, test.wantEncryptionOK, values)
			}
			if test.wantEncryptionOK && !reflect.DeepEqual(values, []string{test.wantEncryption}) {
				t.Fatalf("EncryptionPassword = %q, want exactly %q", values, test.wantEncryption)
			}
			assertMultipartBody(t, post)

			for index, poll := range requests[1:] {
				if poll.Method != "GET" || poll.RequestURI != vcfmock.ImportPath || poll.RawQuery != "" {
					t.Fatalf("poll %d target = %s %s", index+1, poll.Method, poll.RequestURI)
				}
				assertCommonHeaders(t, poll)
				if len(poll.Body) != 0 {
					t.Fatalf("poll %d sent %d body bytes", index+1, len(poll.Body))
				}
				if _, ok := poll.Header["Encryptionpassword"]; ok {
					t.Fatalf("poll %d leaked EncryptionPassword", index+1)
				}
				if got := poll.Header.Get("Content-Type"); got != "" {
					t.Fatalf("poll %d Content-Type = %q, want omitted", index+1, got)
				}
			}
		})
	}
}

func assertCommonHeaders(t *testing.T, request vcfmock.Request) {
	t.Helper()
	if got := request.Header.Values("Authorization"); !reflect.DeepEqual(got, []string{"vRealizeOpsToken fixture-token"}) {
		t.Fatalf("Authorization = %q", got)
	}
	if got := request.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("Accept = %q", got)
	}
}

func assertMultipartBody(t *testing.T, request vcfmock.Request) {
	t.Helper()
	mediaType, params, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if err != nil {
		t.Fatalf("parse Content-Type: %v", err)
	}
	if mediaType != "multipart/form-data" || params["boundary"] == "" {
		t.Fatalf("Content-Type = %q", request.Header.Get("Content-Type"))
	}
	reader := multipart.NewReader(bytes.NewReader(request.Body), params["boundary"])
	part, err := reader.NextPart()
	if err != nil {
		t.Fatalf("read contentFile part: %v", err)
	}
	if part.FormName() != "contentFile" || part.FileName() != "bundle.zip" {
		t.Fatalf("multipart part name=%q filename=%q", part.FormName(), part.FileName())
	}
	if got := part.Header.Get("Content-Type"); got != "application/octet-stream" {
		t.Fatalf("contentFile Content-Type = %q", got)
	}
	body, err := io.ReadAll(part)
	if err != nil {
		t.Fatalf("read contentFile: %v", err)
	}
	if string(body) != "fixture-content" {
		t.Fatalf("contentFile = %q", body)
	}
	if next, err := reader.NextPart(); !errors.Is(err, io.EOF) || next != nil {
		t.Fatalf("unexpected extra multipart part: part=%v err=%v", next, err)
	}
}

func TestImportAndWaitTerminalFailures(t *testing.T) {
	tests := []struct {
		state vcfops.OperationState
	}{
		{state: vcfops.StateFailed},
		{state: vcfops.StateUnknown},
	}
	for _, test := range tests {
		t.Run(string(test.state), func(t *testing.T) {
			mock := vcfmock.New(string(test.state))
			defer mock.Close()
			client, err := vcfops.NewClient(mock.URL(), "token", mock.Client(), 0)
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.ImportAndWait(context.Background(), "bundle.zip", strings.NewReader("x"), vcfops.ImportOptions{})
			var operationError *vcfops.OperationError
			if !errors.As(err, &operationError) {
				t.Fatalf("error = %T %v, want *OperationError", err, err)
			}
			if operationError.Operation.State != test.state ||
				operationError.Operation.ID != "bb1f4c64-b1e9-4a3f-a051-790d926342d2" ||
				operationError.Operation.Type != "IMPORT" ||
				operationError.Operation.LastUpdatedTime != 1625238320326 ||
				operationError.Operation.ErrorCode != "OPERATION_FAILED" ||
				!reflect.DeepEqual(operationError.Operation.ErrorMessages, []string{"fixture import failed"}) {
				t.Fatalf("operation error = %+v", operationError.Operation)
			}
		})
	}
}

func TestImportAndWaitRequiresContractStatuses(t *testing.T) {
	tests := []struct {
		name       string
		postStatus int
		pollStatus int
		wantCalls  int
	}{
		{
			name:       "importContent requires 202",
			postStatus: http.StatusOK,
			pollStatus: http.StatusOK,
			wantCalls:  1,
		},
		{
			name:       "getLastImportOperation requires 200",
			postStatus: http.StatusAccepted,
			pollStatus: http.StatusAccepted,
			wantCalls:  2,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			mock := vcfmock.New("FINISHED")
			defer mock.Close()
			mock.SetPostStatus(test.postStatus)
			mock.SetPollStatus(test.pollStatus)
			client, err := vcfops.NewClient(mock.URL(), "token", mock.Client(), 0)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := client.ImportAndWait(context.Background(), "bundle.zip", strings.NewReader("x"), vcfops.ImportOptions{}); err == nil {
				t.Fatal("ImportAndWait succeeded for a non-contract status")
			}
			if got := len(mock.Requests()); got != test.wantCalls {
				t.Fatalf("request count = %d, want %d", got, test.wantCalls)
			}
		})
	}
}

func TestImportAndWaitHasNoIndependentAttemptLimit(t *testing.T) {
	states := make([]string, 257)
	for index := range states {
		states[index] = "RUNNING"
	}
	states = append(states, "FINISHED")
	mock := vcfmock.New(states...)
	defer mock.Close()
	client, err := vcfops.NewClient(mock.URL(), "token", mock.Client(), 0)
	if err != nil {
		t.Fatal(err)
	}
	result, err := client.ImportAndWait(context.Background(), "bundle.zip", strings.NewReader("x"), vcfops.ImportOptions{})
	if err != nil {
		t.Fatalf("ImportAndWait: %v", err)
	}
	if result.Operation.State != vcfops.StateFinished {
		t.Fatalf("operation state = %q", result.Operation.State)
	}
	if got, want := len(mock.Requests()), 1+len(states); got != want {
		t.Fatalf("request count = %d, want %d", got, want)
	}
}

func TestImportAndWaitHonorsCancellationWhilePolling(t *testing.T) {
	mock := vcfmock.New("RUNNING")
	defer mock.Close()
	pollBodyClosed := make(chan struct{})
	var pollBodyClosedOnce sync.Once
	transport := mock.Client().Transport
	if transport == nil {
		transport = http.DefaultTransport
	}
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		response, err := transport.RoundTrip(request)
		if err == nil && request.Method == http.MethodGet {
			response.Body = &closeSignalBody{ReadCloser: response.Body, closed: pollBodyClosed, once: &pollBodyClosedOnce}
		}
		return response, err
	})}
	client, err := vcfops.NewClient(mock.URL(), "token", httpClient, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := client.ImportAndWait(ctx, "bundle.zip", strings.NewReader("x"), vcfops.ImportOptions{})
		done <- err
	}()

	select {
	case <-pollBodyClosed:
	case err := <-done:
		t.Fatalf("ImportAndWait returned before waiting between polls: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("ImportAndWait did not complete the first poll")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %v, want context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ImportAndWait did not stop after cancellation")
	}
}

func TestPinnedOfficialContract(t *testing.T) {
	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	type contractDocument struct {
		BasePath   string      `json:"basePath"`
		Operations []operation `json:"operations"`
	}
	type sourcesDocument struct {
		License    string      `json:"license"`
		Tag        string      `json:"tag"`
		Commit     string      `json:"commit"`
		SpecPath   string      `json:"specPath"`
		Operations []operation `json:"operations"`
	}

	contractBytes, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract contractDocument
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatal(err)
	}
	sourcesBytes, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var sources sourcesDocument
	if err := json.Unmarshal(sourcesBytes, &sources); err != nil {
		t.Fatal(err)
	}

	wantOperations := []operation{
		{OperationID: "importContent", Method: "POST", Path: "/api/content/operations/import"},
		{OperationID: "getLastImportOperation", Method: "GET", Path: "/api/content/operations/import"},
	}
	if contract.BasePath != "/suite-api" || !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations = %+v with base %q", contract.Operations, contract.BasePath)
	}
	if sources.Tag != "9.0.0.0" || sources.Commit != "85151f6b1bb58f13b6ac0304bfec53904bea085f" || sources.SpecPath != "specifications/vcf-operations/vcf-operations-openapi.json" || sources.License != "Apache-2.0" {
		t.Fatalf("official source pin is wrong: %+v", sources)
	}
	if !reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("official source operations = %+v", sources.Operations)
	}
}

func TestMockServesOnlyContractOperations(t *testing.T) {
	mock := vcfmock.New()
	defer mock.Close()
	response, err := mock.Client().Get(mock.URL() + "/suite-api/api/tasks")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 404 {
		t.Fatalf("uncontracted route status = %d, want 404", response.StatusCode)
	}
}
