package verifier_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"

	"example.com/vcfautomationtask/internal/contractmock"
	"example.com/vcfautomationtask/vcfautomation"
)

func TestRenameDeploymentWireShape(t *testing.T) {
	t.Parallel()

	description := "edge services"
	iconID := "d34db33f-2222-4444-8888-123456789abc"
	empty := ""
	tests := []struct {
		name      string
		newName   string
		options   vcfautomation.UpdateOptions
		wantQuery string
		wantBody  string
	}{
		{
			name:      "unset optional fields are absent",
			newName:   "edge west/blue",
			options:   vcfautomation.UpdateOptions{},
			wantQuery: "name=edge+west%2Fblue",
			wantBody:  `{"name":"edge west/blue"}`,
		},
		{
			name:    "populated optional fields are encoded",
			newName: "edge-green",
			options: vcfautomation.UpdateOptions{
				Description: &description,
				IconID:      &iconID,
			},
			wantQuery: "name=edge-green",
			wantBody:  `{"name":"edge-green","description":"edge services","iconId":"d34db33f-2222-4444-8888-123456789abc"}`,
		},
		{
			name:    "explicit empty values remain present",
			newName: "edge-empty",
			options: vcfautomation.UpdateOptions{
				Description: &empty,
				IconID:      &empty,
			},
			wantQuery: "name=edge-empty",
			wantBody:  `{"name":"edge-empty","description":"","iconId":""}`,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			const deploymentID = "7ec79988-e37a-4d7c-a8d1-711f49a63e22"
			const responseDescription = "description returned by the service"
			const responseIconID = "72fc9f0c-faa0-48f2-a57f-33c4ba0e6f75"
			mock := contractmock.New(contractmock.Config{
				PatchResponse: contractmock.Deployment{
					ID:          deploymentID,
					Name:        test.newName,
					Description: responseDescription,
					IconID:      responseIconID,
					Status:      "UPDATE_SUCCESSFUL",
				},
			})
			defer mock.Close()

			client, err := vcfautomation.NewClient(mock.URL(), "fixture-token", mock.Client())
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}
			got, err := client.RenameDeployment(context.Background(), deploymentID, test.newName, test.options)
			if err != nil {
				t.Fatalf("RenameDeployment() error = %v", err)
			}
			wantDeployment := (vcfautomation.Deployment{
				ID:          deploymentID,
				Name:        test.newName,
				Description: responseDescription,
				IconID:      responseIconID,
				Status:      "UPDATE_SUCCESSFUL",
			})
			if got != wantDeployment {
				t.Fatalf("deployment = %+v, want %+v", got, wantDeployment)
			}

			requests := mock.Requests()
			if len(requests) != 2 {
				t.Fatalf("request count = %d, want 2", len(requests))
			}
			precheck := requests[0]
			wantPrecheckURI := contractmock.NamesPath + "?" + test.wantQuery
			assertRequest(t, precheck, http.MethodGet, wantPrecheckURI, "", "")

			patch := requests[1]
			wantPatchURI := contractmock.DeploymentsPath + "/" + deploymentID
			assertRequest(t, patch, http.MethodPatch, wantPatchURI, "application/json", test.wantBody)
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(patch.Body, &fields); err != nil {
				t.Fatalf("PATCH body is not JSON: %v", err)
			}
			if test.options.Description == nil {
				if _, exists := fields["description"]; exists {
					t.Error("unset description was sent")
				}
			}
			if test.options.IconID == nil {
				if _, exists := fields["iconId"]; exists {
					t.Error("unset iconId was sent")
				}
			}
		})
	}
}

func TestPrecheckGatesTheMutation(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name            string
		status          int
		wantConflictErr bool
	}{
		{name: "name already exists", status: http.StatusOK, wantConflictErr: true},
		{name: "unexpected success status", status: http.StatusCreated},
		{name: "precheck service failure", status: http.StatusServiceUnavailable},
		{name: "precheck authentication failure", status: http.StatusUnauthorized},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			mock := contractmock.New(contractmock.Config{CheckStatus: test.status})
			defer mock.Close()
			client, err := vcfautomation.NewClient(mock.URL(), "fixture-token", mock.Client())
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.RenameDeployment(context.Background(), "dep-1", "occupied", vcfautomation.UpdateOptions{})
			if err == nil {
				t.Fatal("RenameDeployment() returned no error")
			}
			if errors.Is(err, vcfautomation.ErrNameConflict) != test.wantConflictErr {
				t.Fatalf("errors.Is(error, ErrNameConflict) = %v, want %v: %v", errors.Is(err, vcfautomation.ErrNameConflict), test.wantConflictErr, err)
			}
			requests := mock.Requests()
			if len(requests) != 1 || requests[0].Method != http.MethodGet {
				t.Fatalf("requests = %+v; a failed precheck must issue no mutation", requests)
			}
		})
	}
}

func TestPrecheckTransportFailureGatesTheMutation(t *testing.T) {
	t.Parallel()

	var requests []*http.Request
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		requests = append(requests, request)
		return nil, errors.New("transport unavailable")
	})}
	client, err := vcfautomation.NewClient("https://vcf.example.test", "fixture-token", httpClient)
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.RenameDeployment(context.Background(), "dep-1", "candidate", vcfautomation.UpdateOptions{})
	if err == nil {
		t.Fatalf("RenameDeployment() error = %v, want transport failure", err)
	}
	if errors.Is(err, vcfautomation.ErrNameConflict) {
		t.Fatalf("transport failure reported ErrNameConflict: %v", err)
	}
	if len(requests) != 1 || requests[0].Method != http.MethodGet {
		t.Fatalf("requests = %+v; a failed precheck must issue no mutation", requests)
	}
}

func TestRenameDeploymentReportsPatchErrors(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		config contractmock.Config
	}{
		{
			name:   "non success response",
			config: contractmock.Config{PatchStatus: http.StatusServiceUnavailable},
		},
		{
			name:   "malformed JSON",
			config: contractmock.Config{PatchResponseRaw: []byte(`{not-json`)},
		},
		{
			name:   "valid value followed by malformed JSON",
			config: contractmock.Config{PatchResponseRaw: []byte(`{"id":"dep-1"} trailing`)},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			mock := contractmock.New(test.config)
			defer mock.Close()
			client, err := vcfautomation.NewClient(mock.URL(), "fixture-token", mock.Client())
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.RenameDeployment(context.Background(), "dep-1", "available", vcfautomation.UpdateOptions{})
			if err == nil {
				t.Fatal("RenameDeployment() returned no error")
			}
			requests := mock.Requests()
			if len(requests) != 2 || requests[0].Method != http.MethodGet || requests[1].Method != http.MethodPatch {
				t.Fatalf("request sequence = %+v", requests)
			}
		})
	}
}

func TestContractProvenanceAndMockSurface(t *testing.T) {
	t.Parallel()

	root := repositoryRoot(t)
	contractData, err := os.ReadFile(filepath.Join(root, "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		Provenance struct {
			SourceKind string `json:"source_kind"`
			Statement  string `json:"statement"`
		} `json:"provenance"`
		Operations []struct {
			Operation string `json:"operation"`
			Method    string `json:"method"`
			Path      string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractData, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	wantOperations := []struct {
		Operation string `json:"operation"`
		Method    string `json:"method"`
		Path      string `json:"path"`
	}{
		{"Check Deployment Name Exists", http.MethodGet, contractmock.NamesPath},
		{"Patch Deployment", http.MethodPatch, contractmock.DeploymentsPath + "/{deploymentId}"},
	}
	if contract.Provenance.SourceKind != "reference_documentation" ||
		!strings.Contains(contract.Provenance.Statement, "not from a published API specification") {
		t.Fatalf("contract provenance does not identify reference documentation: %+v", contract.Provenance)
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations = %+v, want %+v", contract.Operations, wantOperations)
	}

	sourcesData, err := os.ReadFile(filepath.Join(root, "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		FetchedOn string `json:"fetched_on"`
		Sources   []struct {
			URL       string `json:"url"`
			Operation string `json:"operation"`
			FetchedOn string `json:"fetched_on"`
		} `json:"sources"`
	}
	if err := json.Unmarshal(sourcesData, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.FetchedOn == "" || len(sources.Sources) != 2 {
		t.Fatalf("official source index is incomplete: %+v", sources)
	}
	for _, source := range sources.Sources {
		if !strings.HasPrefix(source.URL, "https://developer.broadcom.com/xapis/") ||
			source.Operation == "" || source.FetchedOn != sources.FetchedOn {
			t.Fatalf("invalid official source: %+v", source)
		}
	}

	mock := contractmock.New(contractmock.Config{})
	defer mock.Close()
	for _, request := range []struct {
		method string
		path   string
	}{
		{method: http.MethodPost, path: contractmock.NamesPath},
		{method: http.MethodGet, path: contractmock.DeploymentsPath + "/dep-1"},
		{method: http.MethodGet, path: "/not-in-contract"},
	} {
		req, err := http.NewRequest(request.method, mock.URL()+request.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := mock.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_ = response.Body.Close()
		if response.StatusCode >= 200 && response.StatusCode < 300 {
			t.Fatalf("mock served unnamed operation %s %s", request.method, request.path)
		}
	}
}

func assertRequest(t *testing.T, got contractmock.Request, method, requestURI, contentType, body string) {
	t.Helper()
	if got.Method != method {
		t.Errorf("method = %q, want %q", got.Method, method)
	}
	if got.RequestURI != requestURI {
		t.Errorf("RequestURI = %q, want %q", got.RequestURI, requestURI)
	}
	if got.Authorization != "Bearer fixture-token" {
		t.Errorf("Authorization = %q", got.Authorization)
	}
	if got.Accept != "application/json" {
		t.Errorf("Accept = %q", got.Accept)
	}
	if got.ContentType != contentType {
		t.Errorf("Content-Type = %q, want %q", got.ContentType, contentType)
	}
	if contentType == "application/json" {
		var gotJSON, wantJSON any
		if err := json.Unmarshal(got.Body, &gotJSON); err != nil {
			t.Errorf("body is not valid JSON: %q: %v", got.Body, err)
			return
		}
		if err := json.Unmarshal([]byte(body), &wantJSON); err != nil {
			t.Fatalf("invalid expected JSON %q: %v", body, err)
		}
		if !reflect.DeepEqual(gotJSON, wantJSON) {
			t.Errorf("body = %s, want JSON %s", got.Body, body)
		}
		return
	}
	if string(got.Body) != body {
		t.Errorf("body = %q, want %q", got.Body, body)
	}
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve verifier source path")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}
