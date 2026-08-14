package verifier

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"example.com/vcfnetchange/internal/contractmock"
	"example.com/vcfnetchange/vcfnet"
)

func stringPointer(value string) *string {
	return &value
}

func TestUpdateAndEnableReportAndWireContract(t *testing.T) {
	entityID := "18230:902:993642895"
	wantUpdatePath := "/api/ni/data-sources/vcenters/18230:902:993642895"

	tests := []struct {
		name        string
		plan        contractmock.Plan
		update      vcfnet.VCenterUpdate
		wantErr     bool
		wantSteps   []vcfnet.StepResult
		wantBodies  []string
		wantMethods []string
		wantPaths   []string
	}{
		{
			name:    "later enable failure retains successful update",
			plan:    contractmock.Plan{EnableStatus: http.StatusInternalServerError},
			update:  vcfnet.VCenterUpdate{Nickname: stringPointer("Edge vCenter")},
			wantErr: true,
			wantSteps: []vcfnet.StepResult{
				{OperationID: "updateVcenter", StatusCode: http.StatusOK, Succeeded: true},
				{OperationID: "enableVcenter", StatusCode: http.StatusInternalServerError, Succeeded: false},
			},
			wantBodies:  []string{`{"nickname":"Edge vCenter"}`, ""},
			wantMethods: []string{http.MethodPut, http.MethodPost},
			wantPaths:   []string{wantUpdatePath, wantUpdatePath + "/enable"},
		},
		{
			name:    "explicit empty notes are sent and first failure stops",
			plan:    contractmock.Plan{UpdateStatus: http.StatusForbidden},
			update:  vcfnet.VCenterUpdate{Notes: stringPointer("")},
			wantErr: true,
			wantSteps: []vcfnet.StepResult{
				{OperationID: "updateVcenter", StatusCode: http.StatusForbidden, Succeeded: false},
			},
			wantBodies:  []string{`{"notes":""}`},
			wantMethods: []string{http.MethodPut},
			wantPaths:   []string{wantUpdatePath},
		},
		{
			name: "nested unset password and top-level optionals are omitted",
			update: vcfnet.VCenterUpdate{Credentials: &vcfnet.PasswordCredentials{
				Username: "svc-readonly",
			}},
			wantSteps: []vcfnet.StepResult{
				{OperationID: "updateVcenter", StatusCode: http.StatusOK, Succeeded: true},
				{OperationID: "enableVcenter", StatusCode: http.StatusOK, Succeeded: true},
			},
			wantBodies:  []string{`{"credentials":{"username":"svc-readonly"}}`, ""},
			wantMethods: []string{http.MethodPut, http.MethodPost},
			wantPaths:   []string{wantUpdatePath, wantUpdatePath + "/enable"},
		},
		{
			name: "explicit empty optional values are retained",
			update: vcfnet.VCenterUpdate{
				Nickname: stringPointer(""),
				Notes:    stringPointer(""),
				Credentials: &vcfnet.PasswordCredentials{
					Username: "",
					Password: stringPointer(""),
				},
			},
			wantSteps: []vcfnet.StepResult{
				{OperationID: "updateVcenter", StatusCode: http.StatusOK, Succeeded: true},
				{OperationID: "enableVcenter", StatusCode: http.StatusOK, Succeeded: true},
			},
			wantBodies:  []string{`{"nickname":"","notes":"","credentials":{"username":"","password":""}}`, ""},
			wantMethods: []string{http.MethodPut, http.MethodPost},
			wantPaths:   []string{wantUpdatePath, wantUpdatePath + "/enable"},
		},
		{
			name:    "only the documented success status advances the workflow",
			plan:    contractmock.Plan{UpdateStatus: http.StatusCreated},
			update:  vcfnet.VCenterUpdate{},
			wantErr: true,
			wantSteps: []vcfnet.StepResult{
				{OperationID: "updateVcenter", StatusCode: http.StatusCreated, Succeeded: false},
			},
			wantBodies:  []string{`{}`},
			wantMethods: []string{http.MethodPut},
			wantPaths:   []string{wantUpdatePath},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(test.plan)
			t.Cleanup(server.Close)

			client := vcfnet.NewClient(server.URL()+"/", "fixture-token", server.Client())
			report, err := client.UpdateAndEnableVCenter(context.Background(), entityID, test.update)
			if (err != nil) != test.wantErr {
				t.Fatalf("error = %v, wantErr %v", err, test.wantErr)
			}
			if !reflect.DeepEqual(report.Steps, test.wantSteps) {
				t.Fatalf("steps = %#v, want %#v", report.Steps, test.wantSteps)
			}

			requests := server.Requests()
			if len(requests) != len(test.wantMethods) {
				t.Fatalf("request count = %d, want %d", len(requests), len(test.wantMethods))
			}
			for index, request := range requests {
				if request.Method != test.wantMethods[index] || request.EscapedPath != test.wantPaths[index] {
					t.Errorf("request %d target = %s %s, want %s %s", index, request.Method, request.EscapedPath, test.wantMethods[index], test.wantPaths[index])
				}
				if request.RawQuery != "" {
					t.Errorf("request %d query = %q, want empty", index, request.RawQuery)
				}
				if got := request.Header.Get("Authorization"); got != "NetworkInsight fixture-token" {
					t.Errorf("request %d Authorization = %q", index, got)
				}
				if request.Method == http.MethodPut {
					assertJSONEqual(t, index, request.Body, []byte(test.wantBodies[index]))
					if got := request.Header.Get("Content-Type"); got != "application/json" {
						t.Errorf("update Content-Type = %q, want application/json", got)
					}
				} else {
					if got := string(request.Body); got != test.wantBodies[index] {
						t.Errorf("request %d body = %q, want no body", index, got)
					}
					if got := request.Header.Get("Content-Type"); got != "" {
						t.Errorf("enable Content-Type = %q, want omitted", got)
					}
				}
			}
		})
	}
}

func assertJSONEqual(t *testing.T, requestIndex int, gotBytes, wantBytes []byte) {
	t.Helper()
	var got any
	if err := json.Unmarshal(gotBytes, &got); err != nil {
		t.Errorf("request %d body is not valid JSON: %v", requestIndex, err)
		return
	}
	var want any
	if err := json.Unmarshal(wantBytes, &want); err != nil {
		t.Fatalf("invalid verifier expectation for request %d: %v", requestIndex, err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("request %d JSON body = %s, want %s", requestIndex, gotBytes, wantBytes)
	}
}

func TestNilHTTPClient(t *testing.T) {
	server := contractmock.New(contractmock.Plan{})
	t.Cleanup(server.Close)

	client := vcfnet.NewClient(server.URL(), "fixture-token", nil)
	report, err := client.UpdateAndEnableVCenter(context.Background(), "vc-1", vcfnet.VCenterUpdate{})
	if err != nil {
		t.Fatalf("UpdateAndEnableVCenter: %v", err)
	}
	wantSteps := []vcfnet.StepResult{
		{OperationID: "updateVcenter", StatusCode: http.StatusOK, Succeeded: true},
		{OperationID: "enableVcenter", StatusCode: http.StatusOK, Succeeded: true},
	}
	if !reflect.DeepEqual(report.Steps, wantSteps) {
		t.Fatalf("steps = %#v, want %#v", report.Steps, wantSteps)
	}

	requests := server.Requests()
	wantPaths := []string{
		"/api/ni/data-sources/vcenters/vc-1",
		"/api/ni/data-sources/vcenters/vc-1/enable",
	}
	if len(requests) != len(wantPaths) {
		t.Fatalf("request count = %d, want %d", len(requests), len(wantPaths))
	}
	for index, request := range requests {
		if request.EscapedPath != wantPaths[index] {
			t.Errorf("request %d path = %q, want %q", index, request.EscapedPath, wantPaths[index])
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestEnableTransportFailureRetainsUpdateAndUsesZeroStatus(t *testing.T) {
	server := contractmock.New(contractmock.Plan{})
	t.Cleanup(server.Close)

	loopbackTransport := server.Client().Transport
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if strings.HasSuffix(request.URL.EscapedPath(), "/enable") {
			if request.Body != nil {
				t.Errorf("enable body = %v, want nil", request.Body)
			}
			return nil, errors.New("planned enable transport failure")
		}
		return loopbackTransport.RoundTrip(request)
	})}

	client := vcfnet.NewClient(server.URL(), "fixture-token", httpClient)
	report, err := client.UpdateAndEnableVCenter(context.Background(), "vc-1", vcfnet.VCenterUpdate{})
	if err == nil {
		t.Fatal("expected transport error")
	}
	want := []vcfnet.StepResult{
		{OperationID: "updateVcenter", StatusCode: http.StatusOK, Succeeded: true},
		{OperationID: "enableVcenter", StatusCode: 0, Succeeded: false},
	}
	if !reflect.DeepEqual(report.Steps, want) {
		t.Fatalf("steps = %#v, want %#v", report.Steps, want)
	}
	if requests := server.Requests(); len(requests) != 1 {
		t.Fatalf("server request count = %d, want 1", len(requests))
	}
}

func TestCanceledContextStopsBeforeRequest(t *testing.T) {
	tests := []struct {
		name    string
		context func() context.Context
	}{
		{
			name: "already canceled",
			context: func() context.Context {
				ctx, cancel := context.WithCancel(context.Background())
				cancel()
				return ctx
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(contractmock.Plan{})
			t.Cleanup(server.Close)

			client := vcfnet.NewClient(server.URL(), "fixture-token", nil)
			report, err := client.UpdateAndEnableVCenter(test.context(), "vc-1", vcfnet.VCenterUpdate{})
			if err == nil {
				t.Fatal("expected context error")
			}
			want := []vcfnet.StepResult{{OperationID: "updateVcenter", StatusCode: 0, Succeeded: false}}
			if !reflect.DeepEqual(report.Steps, want) {
				t.Fatalf("steps = %#v, want %#v", report.Steps, want)
			}
			if requests := server.Requests(); len(requests) != 0 {
				t.Fatalf("request count = %d, want 0", len(requests))
			}
		})
	}
}
