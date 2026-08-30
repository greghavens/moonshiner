package vcfauto_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"testing"

	"example.com/vcfauto"
	"example.com/vcfauto/mockapi"
)

const (
	contractPath = "docs/contract.json"
	token        = "unit-test-token"
	deployment   = "b71c0d3e-2a44-4f0b-9c88-7d5e1a2f6c30"

	actionSnapshot = "Deployment.CreateSnapshot"
	actionPowerOn  = "Deployment.PowerOn"
)

func act(id string, valid bool) mockapi.Action {
	return mockapi.Action{
		ID:          id,
		Name:        id,
		DisplayName: id,
		ActionType:  "RESOURCE_ACTION",
		Valid:       valid,
	}
}

func serve(t *testing.T, opts mockapi.Options) (*mockapi.Server, *vcfauto.Client) {
	t.Helper()
	if opts.ContractPath == "" {
		opts.ContractPath = contractPath
	}
	if opts.Token == "" {
		opts.Token = token
	}
	srv, err := mockapi.Start(opts)
	if err != nil {
		t.Fatalf("mockapi.Start: %v", err)
	}
	t.Cleanup(srv.Close)

	c, err := vcfauto.New(vcfauto.Config{BaseURL: srv.URL(), Token: token})
	if err != nil {
		t.Fatalf("vcfauto.New: %v", err)
	}
	return srv, c
}

func withActions(actions ...mockapi.Action) mockapi.Options {
	return mockapi.Options{
		Deployments: map[string]mockapi.Deployment{deployment: {Actions: actions}},
	}
}

func TestNew(t *testing.T) {
	tests := []struct {
		name    string
		cfg     vcfauto.Config
		wantErr bool
	}{
		{"ok", vcfauto.Config{BaseURL: "https://vcf.example.test", Token: "t"}, false},
		{"trailing slash is trimmed", vcfauto.Config{BaseURL: "https://vcf.example.test/", Token: "t"}, false},
		{"missing base url", vcfauto.Config{Token: "t"}, true},
		{"relative base url", vcfauto.Config{BaseURL: "vcf.example.test", Token: "t"}, true},
		{"missing token", vcfauto.Config{BaseURL: "https://vcf.example.test"}, true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c, err := vcfauto.New(tc.cfg)
			if tc.wantErr {
				if err == nil {
					t.Fatal("New returned a nil error")
				}
				if !errors.Is(err, vcfauto.ErrInvalidRequest) {
					t.Errorf("error = %v, want errors.Is(err, ErrInvalidRequest)", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			if c == nil {
				t.Fatal("New returned a nil Client")
			}
		})
	}
}

func TestListActions(t *testing.T) {
	tests := []struct {
		name    string
		opts    mockapi.Options
		depID   string
		want    []vcfauto.Action
		wantErr func(t *testing.T, err error)
	}{
		{
			name:  "two actions",
			opts:  withActions(act(actionSnapshot, true), act(actionPowerOn, false)),
			depID: deployment,
			want: []vcfauto.Action{
				{ID: actionSnapshot, Name: actionSnapshot, DisplayName: actionSnapshot, ActionType: "RESOURCE_ACTION", Valid: true},
				{ID: actionPowerOn, Name: actionPowerOn, DisplayName: actionPowerOn, ActionType: "RESOURCE_ACTION", Valid: false},
			},
		},
		{
			name:  "no actions",
			opts:  withActions(),
			depID: deployment,
			want:  []vcfauto.Action{},
		},
		{
			name:  "unknown deployment",
			opts:  withActions(act(actionSnapshot, true)),
			depID: "does-not-exist",
			wantErr: func(t *testing.T, err error) {
				var apiErr *vcfauto.APIError
				if !errors.As(err, &apiErr) {
					t.Fatalf("error = %v, want *APIError", err)
				}
				if apiErr.StatusCode != http.StatusNotFound {
					t.Errorf("status = %d, want 404", apiErr.StatusCode)
				}
			},
		},
		{
			name:  "empty deployment id",
			opts:  withActions(act(actionSnapshot, true)),
			depID: "",
			wantErr: func(t *testing.T, err error) {
				if !errors.Is(err, vcfauto.ErrInvalidRequest) {
					t.Fatalf("error = %v, want ErrInvalidRequest", err)
				}
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, c := serve(t, tc.opts)

			got, err := c.ListActions(context.Background(), tc.depID)
			if tc.wantErr != nil {
				tc.wantErr(t, err)
				return
			}
			if err != nil {
				t.Fatalf("ListActions: %v", err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("ListActions = %+v, want %+v", got, tc.want)
			}
		})
	}
}

// TestSubmitActionGate walks the precheck outcomes and, for each, checks whether
// the mutating call was allowed through.
func TestSubmitActionGate(t *testing.T) {
	tests := []struct {
		name         string
		actions      []mockapi.Action
		wantErr      error
		wantRequests int
	}{
		{
			name:         "valid action passes the gate",
			actions:      []mockapi.Action{act(actionSnapshot, true)},
			wantRequests: 2,
		},
		{
			name:         "action missing from the list",
			actions:      []mockapi.Action{act(actionPowerOn, true)},
			wantErr:      vcfauto.ErrActionNotFound,
			wantRequests: 1,
		},
		{
			name:         "action invalid for the current state",
			actions:      []mockapi.Action{act(actionSnapshot, false)},
			wantErr:      vcfauto.ErrActionNotValid,
			wantRequests: 1,
		},
		{
			name:         "empty action list",
			actions:      nil,
			wantErr:      vcfauto.ErrActionNotFound,
			wantRequests: 1,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv, c := serve(t, withActions(tc.actions...))

			got, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
				DeploymentID: deployment,
				ActionID:     actionSnapshot,
			})

			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("error = %v, want errors.Is(err, %v)", err, tc.wantErr)
				}
			} else {
				if err != nil {
					t.Fatalf("SubmitAction: %v", err)
				}
				if got.Status != "PENDING" {
					t.Errorf("Status = %q, want PENDING", got.Status)
				}
			}

			recs := srv.Requests()
			if len(recs) != tc.wantRequests {
				t.Fatalf("recorded %d requests, want %d", len(recs), tc.wantRequests)
			}
			if tc.wantErr != nil {
				for _, r := range recs {
					if r.Method == http.MethodPost {
						t.Errorf("a mutating %s %s was sent despite the failed precheck", r.Method, r.Path)
					}
				}
			}
		})
	}
}

// TestSubmitActionBodyOmitsUnsetFields reads the mock's request log and checks
// the body key by key.
func TestSubmitActionBodyOmitsUnsetFields(t *testing.T) {
	tests := []struct {
		name string
		req  vcfauto.ActionRequest
		want map[string]any
	}{
		{
			name: "nothing optional",
			req:  vcfauto.ActionRequest{DeploymentID: deployment, ActionID: actionSnapshot},
			want: map[string]any{"actionId": actionSnapshot},
		},
		{
			name: "nil inputs and empty reason stay off the wire",
			req:  vcfauto.ActionRequest{DeploymentID: deployment, ActionID: actionSnapshot, Inputs: nil, Reason: ""},
			want: map[string]any{"actionId": actionSnapshot},
		},
		{
			name: "an empty inputs map is not an input",
			req:  vcfauto.ActionRequest{DeploymentID: deployment, ActionID: actionSnapshot, Inputs: map[string]any{}},
			want: map[string]any{"actionId": actionSnapshot},
		},
		{
			name: "reason travels when set",
			req:  vcfauto.ActionRequest{DeploymentID: deployment, ActionID: actionSnapshot, Reason: "pre-upgrade snapshot"},
			want: map[string]any{"actionId": actionSnapshot, "reason": "pre-upgrade snapshot"},
		},
		{
			name: "inputs travel when set",
			req: vcfauto.ActionRequest{
				DeploymentID: deployment,
				ActionID:     actionSnapshot,
				Inputs:       map[string]any{"name": "pre-upgrade", "memory": false},
			},
			want: map[string]any{
				"actionId": actionSnapshot,
				"inputs":   map[string]any{"name": "pre-upgrade", "memory": false},
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv, c := serve(t, withActions(act(actionSnapshot, true)))

			if _, err := c.SubmitAction(context.Background(), tc.req); err != nil {
				t.Fatalf("SubmitAction: %v", err)
			}

			recs := srv.Requests()
			if len(recs) != 2 {
				t.Fatalf("recorded %d requests, want 2", len(recs))
			}
			post := recs[1]

			var got map[string]any
			if err := json.Unmarshal(post.Body, &got); err != nil {
				t.Fatalf("decode body %q: %v", post.Body, err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("body = %#v, want %#v", got, tc.want)
			}
		})
	}
}

func TestSubmitActionRejectsBadArgumentsBeforeAnyCall(t *testing.T) {
	tests := []struct {
		name string
		req  vcfauto.ActionRequest
	}{
		{"no deployment id", vcfauto.ActionRequest{ActionID: actionSnapshot}},
		{"no action id", vcfauto.ActionRequest{DeploymentID: deployment}},
		{"deployment id with a slash", vcfauto.ActionRequest{DeploymentID: "a/b", ActionID: actionSnapshot}},
		{"deployment id with a query marker", vcfauto.ActionRequest{DeploymentID: "a?b", ActionID: actionSnapshot}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv, c := serve(t, withActions(act(actionSnapshot, true)))

			if _, err := c.SubmitAction(context.Background(), tc.req); !errors.Is(err, vcfauto.ErrInvalidRequest) {
				t.Fatalf("error = %v, want ErrInvalidRequest", err)
			}
			if n := len(srv.Requests()); n != 0 {
				t.Errorf("recorded %d requests, want none", n)
			}
		})
	}
}

func TestAPIErrorsCarryTheOperationId(t *testing.T) {
	tests := []struct {
		name       string
		opts       mockapi.Options
		wantOp     string
		wantStatus int
	}{
		{
			name: "precheck fails",
			opts: mockapi.Options{
				Deployments:   map[string]mockapi.Deployment{deployment: {Actions: []mockapi.Action{act(actionSnapshot, true)}}},
				ActionsStatus: http.StatusServiceUnavailable,
			},
			wantOp:     "getDeploymentActions",
			wantStatus: http.StatusServiceUnavailable,
		},
		{
			name: "mutating call fails",
			opts: mockapi.Options{
				Deployments:   map[string]mockapi.Deployment{deployment: {Actions: []mockapi.Action{act(actionSnapshot, true)}}},
				RequestStatus: http.StatusConflict,
			},
			wantOp:     "submitDeploymentActionRequest",
			wantStatus: http.StatusConflict,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, c := serve(t, tc.opts)

			_, err := c.SubmitAction(context.Background(), vcfauto.ActionRequest{
				DeploymentID: deployment,
				ActionID:     actionSnapshot,
			})

			var apiErr *vcfauto.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %v, want *APIError", err)
			}
			if apiErr.Op != tc.wantOp {
				t.Errorf("Op = %q, want %q", apiErr.Op, tc.wantOp)
			}
			if apiErr.StatusCode != tc.wantStatus {
				t.Errorf("StatusCode = %d, want %d", apiErr.StatusCode, tc.wantStatus)
			}
			if apiErr.Error() == "" {
				t.Error("Error() is empty")
			}
		})
	}
}

func TestPrecheckHeaders(t *testing.T) {
	srv, c := serve(t, withActions(act(actionSnapshot, true)))

	if _, err := c.ListActions(context.Background(), deployment); err != nil {
		t.Fatalf("ListActions: %v", err)
	}
	rec := srv.Requests()[0]

	tests := []struct {
		header string
		want   string
	}{
		{"Authorization", "Bearer " + token},
		{"Accept", "application/json"},
		{"Content-Type", ""}, // a GET carries no body, so it describes none
	}
	for _, tc := range tests {
		t.Run(tc.header, func(t *testing.T) {
			if got := rec.Header.Get(tc.header); got != tc.want {
				t.Errorf("%s = %q, want %q", tc.header, got, tc.want)
			}
		})
	}
}
