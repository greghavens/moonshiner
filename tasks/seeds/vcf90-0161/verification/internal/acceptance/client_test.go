package acceptance_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"

	vcfautomation "example.com/vcfautomation"
	"example.com/vcfautomation/mock"
)

func TestPatchDeploymentWireShape(t *testing.T) {
	iconID := "617bdbdb-6e92-4b40-a79a-329c6f6c1c14"
	tests := []struct {
		name           string
		deploymentID   string
		update         vcfautomation.DeploymentUpdate
		wantBody       string
		wantName       string
		wantDesc       string
		wantIconID     string
		wantRequestURI string
	}{
		{
			name:           "unset optional fields are absent",
			deploymentID:   "dep-123",
			update:         vcfautomation.DeploymentUpdate{Name: vcfautomation.String("edge-prod")},
			wantBody:       `{"name":"edge-prod"}`,
			wantName:       "edge-prod",
			wantDesc:       "old description",
			wantIconID:     "",
			wantRequestURI: "/deployment/api/deployments/dep-123",
		},
		{
			name:           "explicit empty string is present",
			deploymentID:   "dep-123",
			update:         vcfautomation.DeploymentUpdate{Description: vcfautomation.String("")},
			wantBody:       `{"description":""}`,
			wantName:       "old-name",
			wantDesc:       "",
			wantIconID:     "",
			wantRequestURI: "/deployment/api/deployments/dep-123",
		},
		{
			name:           "contract field casing is retained",
			deploymentID:   "dep-123",
			update:         vcfautomation.DeploymentUpdate{IconID: &iconID},
			wantBody:       `{"iconId":"617bdbdb-6e92-4b40-a79a-329c6f6c1c14"}`,
			wantName:       "old-name",
			wantDesc:       "old description",
			wantIconID:     iconID,
			wantRequestURI: "/deployment/api/deployments/dep-123",
		},
		{
			name:           "path parameter is one escaped segment",
			deploymentID:   "dep/with space",
			update:         vcfautomation.DeploymentUpdate{Name: vcfautomation.String("escaped")},
			wantBody:       `{"name":"escaped"}`,
			wantName:       "escaped",
			wantDesc:       "old description",
			wantIconID:     "",
			wantRequestURI: "/deployment/api/deployments/dep%2Fwith%20space",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			initial := vcfautomation.Deployment{
				ID:          test.deploymentID,
				Name:        "old-name",
				Description: "old description",
			}
			server, err := mock.New(initial)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)

			client, err := vcfautomation.NewClient(server.URL(), "fixture-token", server.Client())
			if err != nil {
				t.Fatal(err)
			}
			got, err := client.PatchDeployment(context.Background(), test.deploymentID, test.update)
			if err != nil {
				t.Fatalf("PatchDeployment returned error: %v", err)
			}
			if got.ID != test.deploymentID || got.Name != test.wantName || got.Description != test.wantDesc || got.IconID != test.wantIconID {
				t.Fatalf("decoded deployment = %#v", got)
			}

			requests := server.Requests()
			if len(requests) != 1 {
				t.Fatalf("request count = %d, want 1", len(requests))
			}
			request := requests[0]
			if request.Method != http.MethodPatch {
				t.Errorf("method = %q, want PATCH", request.Method)
			}
			if request.RequestURI != test.wantRequestURI {
				t.Errorf("RequestURI = %q, want %q", request.RequestURI, test.wantRequestURI)
			}
			if request.Authorization != "Bearer fixture-token" {
				t.Errorf("Authorization = %q", request.Authorization)
			}
			if request.ContentType != "application/json" {
				t.Errorf("Content-Type = %q", request.ContentType)
			}
			if request.Accept != "application/json" {
				t.Errorf("Accept = %q", request.Accept)
			}
			assertJSONEqual(t, []byte(test.wantBody), request.Body)
		})
	}
}

func TestPatchDeploymentCanBeRepeatedWithoutDuplicatingEffect(t *testing.T) {
	server, err := mock.New(vcfautomation.Deployment{ID: "dep-retry", Name: "before"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	client, err := vcfautomation.NewClient(server.URL(), "fixture-token", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	update := vcfautomation.DeploymentUpdate{Name: vcfautomation.String("after")}

	for attempt := 0; attempt < 2; attempt++ {
		if _, err := client.PatchDeployment(context.Background(), "dep-retry", update); err != nil {
			t.Fatalf("attempt %d: %v", attempt+1, err)
		}
	}
	if got := server.Mutations(); got != 1 {
		t.Fatalf("logical mutations = %d, want 1", got)
	}
	requests := server.Requests()
	if len(requests) != 2 {
		t.Fatalf("wire requests = %d, want 2", len(requests))
	}
	if !reflect.DeepEqual(decodeJSON(t, requests[0].Body), decodeJSON(t, requests[1].Body)) {
		t.Fatalf("retry bodies differ: %q and %q", requests[0].Body, requests[1].Body)
	}
}

func TestPatchDeploymentReturnsNonSuccessError(t *testing.T) {
	tests := []struct {
		name         string
		token        string
		deploymentID string
	}{
		{name: "unauthorized", token: "", deploymentID: "dep-errors"},
		{name: "unknown deployment", token: "fixture-token", deploymentID: "missing"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, err := mock.New(vcfautomation.Deployment{ID: "dep-errors", Name: "before"})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)
			client, err := vcfautomation.NewClient(server.URL(), test.token, server.Client())
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.PatchDeployment(context.Background(), test.deploymentID, vcfautomation.DeploymentUpdate{Name: vcfautomation.String("after")})
			if err == nil {
				t.Fatal("PatchDeployment returned nil error for a non-success response")
			}
			if got := len(server.Requests()); got != 1 {
				t.Fatalf("request count = %d, want 1", got)
			}
		})
	}
}

func TestPatchDeploymentReturnsTransportError(t *testing.T) {
	transportCalled := false
	client, err := vcfautomation.NewClient("http://127.0.0.1", "fixture-token", &http.Client{
		Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			transportCalled = true
			return nil, errors.New("fixture transport failure")
		}),
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.PatchDeployment(context.Background(), "dep-transport", vcfautomation.DeploymentUpdate{Name: vcfautomation.String("after")})
	if err == nil {
		t.Fatal("PatchDeployment returned nil error for a transport failure")
	}
	if !transportCalled {
		t.Fatal("configured transport was not called")
	}
}

func TestPatchDeploymentUsesTwoHundredStatusRange(t *testing.T) {
	tests := []struct {
		name       string
		statusCode int
		body       string
		wantError  bool
	}{
		{name: "other 2xx is successful", statusCode: 299, body: `{"id":"dep-status","name":"after"}`},
		{name: "3xx is not successful", statusCode: 300, body: `redirect not allowed`, wantError: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, err := vcfautomation.NewClient("http://127.0.0.1", "fixture-token", &http.Client{
				Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
					return &http.Response{
						StatusCode: test.statusCode,
						Status:     http.StatusText(test.statusCode),
						Body:       io.NopCloser(strings.NewReader(test.body)),
						Header:     make(http.Header),
					}, nil
				}),
			})
			if err != nil {
				t.Fatal(err)
			}

			deployment, err := client.PatchDeployment(context.Background(), "dep-status", vcfautomation.DeploymentUpdate{Name: vcfautomation.String("after")})
			if (err != nil) != test.wantError {
				t.Fatalf("error = %v, wantError = %v", err, test.wantError)
			}
			if !test.wantError && (deployment.ID != "dep-status" || deployment.Name != "after") {
				t.Fatalf("decoded deployment = %#v", deployment)
			}
		})
	}
}

func TestMockServesOnlyContractedOperation(t *testing.T) {
	server, err := mock.New(vcfautomation.Deployment{ID: "dep-123", Name: "before"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)

	tests := []struct {
		name   string
		method string
		path   string
	}{
		{name: "uncontracted method", method: http.MethodGet, path: "/deployment/api/deployments/dep-123"},
		{name: "uncontracted path", method: http.MethodPatch, path: "/deployment/api/resources/dep-123"},
		{name: "uncontracted query shape", method: http.MethodPatch, path: "/deployment/api/deployments/dep-123?force=true"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request, err := http.NewRequest(test.method, server.URL()+test.path, strings.NewReader(`{}`))
			if err != nil {
				t.Fatal(err)
			}
			request.Header.Set("Authorization", "Bearer fixture-token")
			request.Header.Set("Content-Type", "application/json")
			response, err := server.Client().Do(request)
			if err != nil {
				t.Fatal(err)
			}
			_, _ = io.Copy(io.Discard, response.Body)
			_ = response.Body.Close()
			if response.StatusCode != http.StatusNotFound {
				t.Fatalf("status = %d, want 404", response.StatusCode)
			}
		})
	}
	if got := len(server.Requests()); got != len(tests) {
		t.Fatalf("request log length = %d, want %d", got, len(tests))
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func assertJSONEqual(t *testing.T, want, got []byte) {
	t.Helper()
	if !reflect.DeepEqual(decodeJSON(t, want), decodeJSON(t, got)) {
		t.Errorf("JSON body = %s, want %s", got, want)
	}
}

func decodeJSON(t *testing.T, data []byte) any {
	t.Helper()
	var value any
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatalf("invalid JSON %q: %v", data, err)
	}
	return value
}
