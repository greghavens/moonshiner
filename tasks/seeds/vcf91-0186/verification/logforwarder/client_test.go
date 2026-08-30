package logforwarder_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"

	"moonshiner.local/vcf91-0186/internal/contractmock"
	"moonshiner.local/vcf91-0186/logforwarder"
)

const contractPath = "../docs/contract.json"

func pointer[T any](value T) *T {
	return &value
}

func startMock(t *testing.T, opts contractmock.Options) *contractmock.Server {
	t.Helper()
	opts.ContractPath = contractPath
	server, err := contractmock.Start(opts)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server, token string) *logforwarder.Client {
	t.Helper()
	client, err := logforwarder.NewClient(logforwarder.Config{
		BaseURL:    server.URL(),
		Token:      token,
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func TestPrecheckAndCreateExactWire(t *testing.T) {
	constraints := json.RawMessage(`{}`)
	tests := []struct {
		name     string
		input    logforwarder.LogForwarderInput
		wantBody string
	}{
		{
			name: "unset optional fields are omitted",
			input: logforwarder.LogForwarderInput{
				Host:              pointer("collector.example.test"),
				Name:              pointer("primary<&>"),
				Port:              pointer[int32](6514),
				Protocol:          pointer("SYSLOG"),
				SSLEnabled:        pointer(true),
				TransportProtocol: pointer("TCP"),
			},
			wantBody: `{"host":"collector.example.test","name":"primary<&>","port":6514,"protocol":"SYSLOG","sslEnabled":true,"transportProtocol":"TCP"}`,
		},
		{
			name: "explicit empty zero and false values are present",
			input: logforwarder.LogForwarderInput{
				Certificate:                pointer(""),
				ConnectionRefreshInterval:  pointer[int32](0),
				Constraints:                &constraints,
				Enabled:                    pointer(false),
				ForwardComplementaryFields: pointer(false),
				Host:                       pointer(""),
				Name:                       pointer(""),
				Port:                       pointer[int32](0),
				Protocol:                   pointer(""),
				SSLEnabled:                 pointer(false),
				Tags:                       pointer(map[string]string{}),
				TransportProtocol:          pointer(""),
				WorkerCount:                pointer[int32](0),
			},
			wantBody: `{"certificate":"","connectionRefreshInterval":0,"constraints":{},"enabled":false,"forwardComplementaryFields":false,"host":"","name":"","port":0,"protocol":"","sslEnabled":false,"tags":{},"transportProtocol":"","workerCount":0}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			token := "jwt-" + strings.ReplaceAll(t.Name(), "/", "-")
			createdID := "id-" + strings.ReplaceAll(t.Name(), "/", "-")
			server := startMock(t, contractmock.Options{
				ExpectedToken: token,
				CreatedID:     createdID,
			})
			client := newClient(t, server, token)

			got, err := client.PrecheckAndCreate(context.Background(), test.input)
			if err != nil {
				t.Fatalf("PrecheckAndCreate: %v", err)
			}
			if got.ID != createdID {
				t.Fatalf("created id = %q, want %q", got.ID, createdID)
			}
			if effects := server.Effects(); effects != 1 {
				t.Fatalf("mutation effects = %d, want 1", effects)
			}

			records, err := server.ReadLog()
			if err != nil {
				t.Fatalf("read request log: %v", err)
			}
			if len(records) != 2 {
				t.Fatalf("request count = %d, want 2: %#v", len(records), records)
			}
			wantOperations := []string{
				logforwarder.OperationTestLogForwarderConnection,
				logforwarder.OperationCreateLogForwarder,
			}
			wantTargets := []string{
				"/api/v2/logs/forwarders/test",
				"/api/v2/logs/forwarders",
			}
			for index, record := range records {
				if record.OperationID != wantOperations[index] {
					t.Errorf("request %d operationId = %q, want %q", index, record.OperationID, wantOperations[index])
				}
				if record.Method != http.MethodPost {
					t.Errorf("request %d method = %q, want POST", index, record.Method)
				}
				if record.RequestURI != wantTargets[index] {
					t.Errorf("request %d target = %q, want %q", index, record.RequestURI, wantTargets[index])
				}
				if record.Body != test.wantBody {
					t.Errorf("request %d body\n got: %s\nwant: %s", index, record.Body, test.wantBody)
				}
				if record.ContentLength != int64(len(test.wantBody)) {
					t.Errorf("request %d Content-Length = %d, want %d", index, record.ContentLength, len(test.wantBody))
				}
				if len(record.TransferEncoding) != 0 {
					t.Errorf("request %d Transfer-Encoding = %#v, want absent", index, record.TransferEncoding)
				}
				assertOneHeader(t, record.Headers, "Accept", "application/json")
				assertOneHeader(t, record.Headers, "Content-Type", "application/json")
				assertOneHeader(t, record.Headers, "X-JWT-Token", token)
				if strings.Contains(record.Body, `"id"`) {
					t.Errorf("request %d sent read-only id: %s", index, record.Body)
				}
			}
			if records[0].Body != records[1].Body {
				t.Errorf("precheck and create bodies differ: %q != %q", records[0].Body, records[1].Body)
			}
		})
	}
}

func TestPrecheckFailureGatesMutation(t *testing.T) {
	tests := []struct {
		name       string
		status     int
		body       []byte
		wantKind   logforwarder.ErrorKind
		wantStatus int
	}{
		{name: "bad request", status: http.StatusBadRequest, wantKind: logforwarder.KindHTTP, wantStatus: http.StatusBadRequest},
		{name: "forbidden", status: http.StatusForbidden, wantKind: logforwarder.KindHTTP, wantStatus: http.StatusForbidden},
		{name: "certificate failure", status: http.StatusBadGateway, wantKind: logforwarder.KindHTTP, wantStatus: http.StatusBadGateway},
		{name: "unexpected success body", status: http.StatusOK, body: []byte(`{}`), wantKind: logforwarder.KindProtocol},
		{name: "oversized success body", status: http.StatusOK, body: bytes.Repeat([]byte("x"), (1<<20)+1), wantKind: logforwarder.KindProtocol},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			token := "jwt-" + strings.ReplaceAll(t.Name(), "/", "-")
			server := startMock(t, contractmock.Options{
				ExpectedToken:        token,
				PrecheckStatus:       test.status,
				PrecheckResponseBody: test.body,
			})
			client := newClient(t, server, token)
			_, err := client.PrecheckAndCreate(context.Background(), logforwarder.LogForwarderInput{
				Host: pointer("collector.example.test"),
			})
			if err == nil {
				t.Fatal("PrecheckAndCreate succeeded, want precheck error")
			}
			var operationError *logforwarder.OperationError
			if !errors.As(err, &operationError) {
				t.Fatalf("error type = %T, want *OperationError", err)
			}
			if operationError.OperationID != logforwarder.OperationTestLogForwarderConnection {
				t.Errorf("operationId = %q, want %q", operationError.OperationID, logforwarder.OperationTestLogForwarderConnection)
			}
			if operationError.Kind != test.wantKind {
				t.Errorf("error kind = %q, want %q", operationError.Kind, test.wantKind)
			}
			if operationError.StatusCode != test.wantStatus {
				t.Errorf("status = %d, want %d", operationError.StatusCode, test.wantStatus)
			}
			if effects := server.Effects(); effects != 0 {
				t.Fatalf("mutation effects = %d after failed precheck, want 0", effects)
			}
			records, readErr := server.ReadLog()
			if readErr != nil {
				t.Fatalf("read request log: %v", readErr)
			}
			if len(records) != 1 {
				t.Fatalf("request count = %d, want exactly the precheck", len(records))
			}
			if records[0].OperationID != logforwarder.OperationTestLogForwarderConnection {
				t.Fatalf("only request operationId = %q, want precheck", records[0].OperationID)
			}
		})
	}
}

func TestCreateFailureOccursOnlyAfterSuccessfulPrecheck(t *testing.T) {
	token := "jwt-" + strings.ReplaceAll(t.Name(), "/", "-")
	server := startMock(t, contractmock.Options{
		ExpectedToken: token,
		CreateStatus:  http.StatusUnprocessableEntity,
	})
	client := newClient(t, server, token)

	_, err := client.PrecheckAndCreate(context.Background(), logforwarder.LogForwarderInput{
		Name: pointer("forwarder"),
	})
	if err == nil {
		t.Fatal("PrecheckAndCreate succeeded, want create error")
	}
	var operationError *logforwarder.OperationError
	if !errors.As(err, &operationError) {
		t.Fatalf("error type = %T, want *OperationError", err)
	}
	if operationError.OperationID != logforwarder.OperationCreateLogForwarder ||
		operationError.Kind != logforwarder.KindHTTP ||
		operationError.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("unexpected create error: %#v", operationError)
	}
	if effects := server.Effects(); effects != 0 {
		t.Fatalf("mutation effects = %d, want 0 for rejected create", effects)
	}
	records, readErr := server.ReadLog()
	if readErr != nil {
		t.Fatalf("read request log: %v", readErr)
	}
	if got := operationIDs(records); !reflect.DeepEqual(got, []string{
		logforwarder.OperationTestLogForwarderConnection,
		logforwarder.OperationCreateLogForwarder,
	}) {
		t.Fatalf("operation sequence = %#v", got)
	}
}

func TestCreateResponseValidation(t *testing.T) {
	tests := []struct {
		name        string
		body        []byte
		contentType string
	}{
		{name: "null", body: []byte(`null`), contentType: "application/json"},
		{name: "array", body: []byte(`[]`), contentType: "application/json"},
		{name: "trailing data", body: []byte(`{} {}`), contentType: "application/json"},
		{name: "unknown property", body: []byte(`{"id":"x","unknown":true}`), contentType: "application/json"},
		{name: "wrong media type", body: []byte(`{"id":"x"}`), contentType: "text/plain"},
		{name: "oversized body", body: bytes.Repeat([]byte("x"), (1<<20)+1), contentType: "application/json"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			token := "jwt-" + strings.ReplaceAll(t.Name(), "/", "-")
			server := startMock(t, contractmock.Options{
				ExpectedToken:      token,
				CreateResponseBody: test.body,
				CreateContentType:  test.contentType,
			})
			client := newClient(t, server, token)
			_, err := client.PrecheckAndCreate(context.Background(), logforwarder.LogForwarderInput{})
			var operationError *logforwarder.OperationError
			if !errors.As(err, &operationError) {
				t.Fatalf("error = %v (%T), want *OperationError", err, err)
			}
			if operationError.OperationID != logforwarder.OperationCreateLogForwarder ||
				operationError.Kind != logforwarder.KindProtocol {
				t.Fatalf("unexpected protocol error: %#v", operationError)
			}
			if effects := server.Effects(); effects != 1 {
				t.Fatalf("create effects = %d, want 1 because mutation response was invalid", effects)
			}
		})
	}
}

func TestValidationAndCancellationHappenBeforeTraffic(t *testing.T) {
	tests := []struct {
		name   string
		config logforwarder.Config
	}{
		{name: "empty base URL", config: logforwarder.Config{Token: "token"}},
		{name: "relative base URL", config: logforwarder.Config{BaseURL: "/relative", Token: "token"}},
		{name: "wrong scheme", config: logforwarder.Config{BaseURL: "ftp://127.0.0.1", Token: "token"}},
		{name: "user info", config: logforwarder.Config{BaseURL: "http://user@127.0.0.1", Token: "token"}},
		{name: "non-root path", config: logforwarder.Config{BaseURL: "http://127.0.0.1/api", Token: "token"}},
		{name: "query", config: logforwarder.Config{BaseURL: "http://127.0.0.1?q=1", Token: "token"}},
		{name: "fragment", config: logforwarder.Config{BaseURL: "http://127.0.0.1#x", Token: "token"}},
		{name: "blank token", config: logforwarder.Config{BaseURL: "http://127.0.0.1", Token: " \t"}},
		{name: "surrounding token whitespace", config: logforwarder.Config{BaseURL: "http://127.0.0.1", Token: " token"}},
		{name: "token newline", config: logforwarder.Config{BaseURL: "http://127.0.0.1", Token: "tok\nen"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := logforwarder.NewClient(test.config); err == nil {
				t.Fatal("NewClient succeeded for invalid configuration")
			}
		})
	}

	token := "jwt-" + strings.ReplaceAll(t.Name(), "/", "-")
	server := startMock(t, contractmock.Options{ExpectedToken: token})
	client := newClient(t, server, token)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := client.PrecheckAndCreate(ctx, logforwarder.LogForwarderInput{})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want errors.Is(context.Canceled)", err)
	}
	records, readErr := server.ReadLog()
	if readErr != nil {
		t.Fatalf("read request log: %v", readErr)
	}
	if len(records) != 0 || server.Effects() != 0 {
		t.Fatalf("canceled call made traffic or effects: records=%#v effects=%d", records, server.Effects())
	}

	if _, err := client.PrecheckAndCreate(nil, logforwarder.LogForwarderInput{}); err == nil {
		t.Fatal("nil context succeeded")
	}
}

func TestContractMockServesOnlyNamedOperations(t *testing.T) {
	server := startMock(t, contractmock.Options{ExpectedToken: "scope-token"})
	wantIDs := []string{
		logforwarder.OperationCreateLogForwarder,
		logforwarder.OperationTestLogForwarderConnection,
	}
	if got := server.OperationIDs(); !reflect.DeepEqual(got, wantIDs) {
		t.Fatalf("mock operationIds = %#v, want %#v", got, wantIDs)
	}

	request, err := http.NewRequest(http.MethodGet, server.URL()+"/api/v2/logs/forwarders", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatalf("unnamed operation request: %v", err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed operation status = %d, want 404", response.StatusCode)
	}
	records, readErr := server.ReadLog()
	if readErr != nil {
		t.Fatalf("read request log: %v", readErr)
	}
	if len(records) != 0 {
		t.Fatalf("unnamed operation entered named request log: %#v", records)
	}
}

func TestContractProvenanceAndOperationSet(t *testing.T) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		OpenAPI string `json:"openapi"`
		Info    struct {
			Version string `json:"version"`
		} `json:"info"`
		Source struct {
			CommitSHA string `json:"commit_sha"`
			SpecPath  string `json:"spec_path"`
			BlobSHA   string `json:"spec_blob_sha"`
		} `json:"x-official-source"`
		Paths map[string]map[string]struct {
			OperationID string `json:"operationId"`
		} `json:"paths"`
	}
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.OpenAPI != "3.0.1" || contract.Info.Version != "9.1.0.0" {
		t.Fatalf("unexpected OpenAPI identity: %#v", contract)
	}
	const wantCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	const wantPath = "specifications/vcf-operations/log-management-openapi.json"
	const wantBlob = "4ada16fa39ec345674de4126174de94ea70d23a0"
	if contract.Source.CommitSHA != wantCommit ||
		contract.Source.SpecPath != wantPath ||
		contract.Source.BlobSHA != wantBlob {
		t.Fatalf("contract source pin changed: %#v", contract.Source)
	}
	var operationIDs []string
	for _, pathItem := range contract.Paths {
		for _, operation := range pathItem {
			if operation.OperationID != "" {
				operationIDs = append(operationIDs, operation.OperationID)
			}
		}
	}
	sort.Strings(operationIDs)
	wantOperationIDs := []string{
		logforwarder.OperationCreateLogForwarder,
		logforwarder.OperationTestLogForwarderConnection,
	}
	if !reflect.DeepEqual(operationIDs, wantOperationIDs) {
		t.Fatalf("contract operationIds = %#v, want %#v", operationIDs, wantOperationIDs)
	}

	sourceRaw, err := os.ReadFile("../docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var source struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		CommitSHA    string   `json:"commit_sha"`
		SpecBlobSHA  string   `json:"spec_blob_sha"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operationIds"`
	}
	if err := json.Unmarshal(sourceRaw, &source); err != nil {
		t.Fatal(err)
	}
	if source.Repository != "https://github.com/vmware/vcf-api-specs" ||
		source.License != "Apache-2.0" ||
		source.CommitSHA != wantCommit ||
		source.SpecBlobSHA != wantBlob ||
		source.SpecPath != wantPath {
		t.Fatalf("official source metadata changed: %#v", source)
	}
	sort.Strings(source.OperationIDs)
	if !reflect.DeepEqual(source.OperationIDs, wantOperationIDs) {
		t.Fatalf("official source operationIds = %#v, want %#v", source.OperationIDs, wantOperationIDs)
	}
}

func assertOneHeader(t *testing.T, header http.Header, name, value string) {
	t.Helper()
	values := header.Values(name)
	if !reflect.DeepEqual(values, []string{value}) {
		t.Errorf("%s values = %#v, want exactly [%q]", name, values, value)
	}
}

func operationIDs(records []contractmock.RequestRecord) []string {
	result := make([]string, len(records))
	for index := range records {
		result[index] = records[index].OperationID
	}
	return result
}
