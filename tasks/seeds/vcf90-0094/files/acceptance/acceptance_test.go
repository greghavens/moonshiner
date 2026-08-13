package acceptance_test

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"

	vcflogs "example.com/vcflogs"
	"example.com/vcflogs/mock"
)

const (
	operationID = "PUT_log-forwarder-id"
	commitSHA   = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	specPath    = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
	forwarderID = "5a105e8b-9d40-3132-9780-d62ea2265d8a"
)

func boolPointer(value bool) *bool       { return &value }
func intPointer(value int) *int          { return &value }
func stringPointer(value string) *string { return &value }

func TestPinnedOfficialContract(t *testing.T) {
	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
		RequestBody struct {
			Schema struct {
				Required   []string                   `json:"required"`
				Properties map[string]json.RawMessage `json:"properties"`
			} `json:"schema"`
		} `json:"request_body"`
		Responses map[string]json.RawMessage `json:"responses"`
	}
	type contractDocument struct {
		Title          string `json:"title"`
		ServerBasePath string `json:"server_base_path"`
		Source         struct {
			Tag       string `json:"tag"`
			CommitSHA string `json:"commit_sha"`
			SpecPath  string `json:"spec_path"`
		} `json:"source"`
		Operations []operation `json:"operations"`
	}

	contractBytes, err := os.ReadFile("../docs/contract.json")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(contractBytes), "9.1") {
		t.Fatal("contract must not contain VCF 9.1 provenance")
	}
	var contract contractDocument
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.Title != "VCF Operations for Logs" || contract.ServerBasePath != "/api/v2" {
		t.Fatalf("wrong product or base path: %#v", contract)
	}
	if contract.Source.Tag != "9.0.0.0" || contract.Source.CommitSHA != commitSHA || contract.Source.SpecPath != specPath {
		t.Fatalf("wrong source pin: %#v", contract.Source)
	}
	if len(contract.Operations) != 1 {
		t.Fatalf("contract must name exactly one operation, got %d", len(contract.Operations))
	}
	op := contract.Operations[0]
	if op.OperationID != operationID || op.Method != http.MethodPut || op.Path != "/log-forwarder/{id}" {
		t.Fatalf("wrong operation: %#v", op)
	}
	wantRequired := []string{"host", "port", "protocol", "sslEnabled"}
	if !reflect.DeepEqual(op.RequestBody.Schema.Required, wantRequired) {
		t.Fatalf("required fields = %v, want %v", op.RequestBody.Schema.Required, wantRequired)
	}
	wantProperties := []string{
		"acceptCert", "name", "host", "port", "protocol", "sslEnabled",
		"workerCount", "diskCacheSize", "tags", "filter", "transportProtocol",
		"forwardComplementaryFields", "testConnection",
	}
	for _, name := range wantProperties {
		if _, ok := op.RequestBody.Schema.Properties[name]; !ok {
			t.Errorf("contract is missing request property %q", name)
		}
	}
	for _, status := range []string{"200", "400", "401", "404", "440", "495", "500"} {
		if _, ok := op.Responses[status]; !ok {
			t.Errorf("contract is missing response %s", status)
		}
	}

	type officialSources struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		Tag          string   `json:"tag"`
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operation_ids"`
	}
	sourceBytes, err := os.ReadFile("../docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var sources officialSources
	if err := json.Unmarshal(sourceBytes, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" || sources.License != "Apache-2.0" || sources.Tag != "9.0.0.0" || sources.CommitSHA != commitSHA || sources.SpecPath != specPath {
		t.Fatalf("official source record is not pinned to the VCF 9.0 specification: %#v", sources)
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{operationID}) {
		t.Fatalf("operation_ids = %v", sources.OperationIDs)
	}
}

func TestUpdateForwarderWireRetryAndIdempotency(t *testing.T) {
	testCases := []struct {
		name          string
		update        vcflogs.ForwarderUpdate
		wantBody      string
		wantForwarder vcflogs.Forwarder
	}{
		{
			name: "unset optionals are absent",
			update: vcflogs.ForwarderUpdate{
				Host:       "logs-backup.example.test",
				Port:       9543,
				Protocol:   "CFAPI",
				SSLEnabled: false,
			},
			wantBody: `{"host":"logs-backup.example.test","port":9543,"protocol":"CFAPI","sslEnabled":false}`,
			wantForwarder: vcflogs.Forwarder{
				Name:          "forwarder-" + forwarderID,
				Host:          "logs-backup.example.test",
				Port:          9543,
				Protocol:      "CFAPI",
				WorkerCount:   4,
				DiskCacheSize: 1000000000,
				Tags:          map[string]string{},
				ID:            forwarderID,
			},
		},
		{
			name: "explicit zero value optionals are present",
			update: vcflogs.ForwarderUpdate{
				AcceptCert:                 boolPointer(false),
				Name:                       stringPointer(""),
				Host:                       "logs-backup.example.test",
				Port:                       9543,
				Protocol:                   "CFAPI",
				SSLEnabled:                 false,
				WorkerCount:                intPointer(0),
				DiskCacheSize:              intPointer(0),
				Filter:                     stringPointer(""),
				TransportProtocol:          stringPointer("TCP"),
				ForwardComplementaryFields: boolPointer(false),
				TestConnection:             boolPointer(false),
			},
			wantBody: `{"acceptCert":false,"name":"","host":"logs-backup.example.test","port":9543,"protocol":"CFAPI","sslEnabled":false,"workerCount":0,"diskCacheSize":0,"filter":"","transportProtocol":"TCP","forwardComplementaryFields":false,"testConnection":false}`,
			wantForwarder: vcflogs.Forwarder{
				Host:              "logs-backup.example.test",
				Port:              9543,
				Protocol:          "CFAPI",
				Tags:              map[string]string{},
				TransportProtocol: "TCP",
				ID:                forwarderID,
			},
		},
	}

	for _, testCase := range testCases {
		t.Run(testCase.name, func(t *testing.T) {
			server, err := mock.New()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)

			client := vcflogs.NewClient(server.URL(), "session-token", server.HTTPClient())
			_, err = client.UpdateForwarder(context.Background(), forwarderID, testCase.update)
			if err == nil || !strings.Contains(err.Error(), "HTTP 500") {
				t.Fatalf("first UpdateForwarder error = %v, want HTTP 500", err)
			}
			if len(server.Requests()) != 1 || server.EffectCount() != 1 {
				t.Fatalf("after ambiguous response: requests=%d effects=%d, want 1 and 1", len(server.Requests()), server.EffectCount())
			}
			forwarder, err := client.UpdateForwarder(context.Background(), forwarderID, testCase.update)
			if err != nil {
				t.Fatalf("UpdateForwarder returned error: %v", err)
			}
			if !reflect.DeepEqual(forwarder, testCase.wantForwarder) {
				t.Fatalf("response\n got: %#v\nwant: %#v", forwarder, testCase.wantForwarder)
			}

			records := server.Requests()
			if len(records) != 2 {
				t.Fatalf("request count = %d, want 2", len(records))
			}
			for index, record := range records {
				if record.Method != http.MethodPut {
					t.Errorf("request %d method = %q", index+1, record.Method)
				}
				wantURI := "/api/v2/log-forwarder/" + forwarderID
				if record.RequestURI != wantURI {
					t.Errorf("request %d URI = %q, want %q", index+1, record.RequestURI, wantURI)
				}
				if record.ContentType != "application/json" || record.Accept != "application/json" || record.Authorization != "Bearer session-token" {
					t.Errorf("request %d headers: content-type=%q accept=%q authorization=%q", index+1, record.ContentType, record.Accept, record.Authorization)
				}
				if string(record.Body) != testCase.wantBody {
					t.Errorf("request %d body\n got: %s\nwant: %s", index+1, record.Body, testCase.wantBody)
				}
			}
			if server.EffectCount() != 1 {
				t.Fatalf("effect count = %d, want 1", server.EffectCount())
			}

			records[0].Method = "mutated"
			records[0].Body[0] = '!'
			freshRecords := server.Requests()
			if freshRecords[0].Method != http.MethodPut || string(freshRecords[0].Body) != testCase.wantBody {
				t.Fatal("Requests did not return an isolated snapshot")
			}
		})
	}
}

func TestMockServesOnlyContractOperation(t *testing.T) {
	server, err := mock.New()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	serverURL, err := url.Parse(server.URL())
	if err != nil {
		t.Fatal(err)
	}
	if address := net.ParseIP(serverURL.Hostname()); address == nil || !address.IsLoopback() {
		t.Fatalf("mock URL is not loopback-only: %q", server.URL())
	}

	response, err := server.HTTPClient().Get(server.URL() + "/api/v2/log-forwarder/" + forwarderID)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed GET operation status = %d, want 404", response.StatusCode)
	}
	if server.EffectCount() != 0 {
		t.Fatalf("unexpected operation changed state %d times", server.EffectCount())
	}
	records := server.Requests()
	if len(records) != 1 || records[0].Method != http.MethodGet || records[0].RequestURI != "/api/v2/log-forwarder/"+forwarderID || len(records[0].Body) != 0 {
		t.Fatalf("unnamed operation was not recorded exactly: %#v", records)
	}

	request, err := http.NewRequest(http.MethodPut, server.URL()+"/api/v2/log-forwarder/"+forwarderID+"/extra", strings.NewReader(`{"host":"logs.example.test","port":9543,"protocol":"CFAPI","sslEnabled":false}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer session-token")
	response, err = server.HTTPClient().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusNotFound || server.EffectCount() != 0 {
		t.Fatalf("non-contract path status=%d effects=%d, want 404 and 0", response.StatusCode, server.EffectCount())
	}
}

func TestMockRejectsInvalidUpdateBeforeAmbiguousFailure(t *testing.T) {
	server, err := mock.New()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)

	request, err := http.NewRequest(http.MethodPut, server.URL()+"/api/v2/log-forwarder/"+forwarderID, strings.NewReader(`{"host":"logs.example.test","port":9543,"protocol":"CFAPI"}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer session-token")
	response, err := server.HTTPClient().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusBadRequest || server.EffectCount() != 0 {
		t.Fatalf("invalid update status=%d effects=%d, want 400 and 0", response.StatusCode, server.EffectCount())
	}

	client := vcflogs.NewClient(server.URL(), "session-token", server.HTTPClient())
	_, err = client.UpdateForwarder(context.Background(), forwarderID, vcflogs.ForwarderUpdate{
		Host: "logs.example.test", Port: 9543, Protocol: "CFAPI", SSLEnabled: false,
	})
	if err == nil || !strings.Contains(err.Error(), "HTTP 500") || server.EffectCount() != 1 {
		t.Fatalf("first valid update error=%v effects=%d, want HTTP 500 and 1", err, server.EffectCount())
	}
}
