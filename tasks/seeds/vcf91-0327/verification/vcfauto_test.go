package vcfauto

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"testing"
	"time"
)

func testContract(t *testing.T) *Contract {
	t.Helper()
	c, err := LoadContract("docs/contract.json")
	if err != nil {
		t.Fatalf("LoadContract: %v", err)
	}
	return c
}

func testFixture(t *testing.T, cfg MockConfig) (*Mock, *Client) {
	t.Helper()
	c := testContract(t)
	if cfg.DeploymentID == "" {
		cfg.DeploymentID = "dep-1"
	}
	m, err := NewMock(c, cfg)
	if err != nil {
		t.Fatalf("NewMock: %v", err)
	}
	t.Cleanup(m.Close)
	cl, err := NewClient(ClientConfig{
		BaseURL:      m.URL(),
		Token:        "unit-token",
		Contract:     c,
		PollInterval: time.Millisecond,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return m, cl
}

func TestExpandPath(t *testing.T) {
	c := testContract(t)

	tests := []struct {
		name    string
		op      string
		params  map[string]string
		want    string
		wantErr bool
	}{
		{"no placeholders beyond one", "GetDeployment", map[string]string{"deploymentId": "d1"}, "/deployment/api/deployments/d1", false},
		{"two placeholders", "SubmitResourceActionRequest", map[string]string{"deploymentId": "d1", "resourceId": "r1"}, "/deployment/api/deployments/d1/resources/r1/requests", false},
		{"request id", "GetRequest", map[string]string{"requestId": "req-9"}, "/deployment/api/requests/req-9", false},
		{"missing param", "SubmitResourceActionRequest", map[string]string{"deploymentId": "d1"}, "", true},
		{"empty param", "GetDeployment", map[string]string{"deploymentId": ""}, "", true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			op, ok := c.Operation(tc.op)
			if !ok {
				t.Fatalf("operation %q not in contract", tc.op)
			}
			got, err := op.ExpandPath(tc.params)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("want error, got %q", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("ExpandPath: %v", err)
			}
			if got != tc.want {
				t.Errorf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestContractLoadsAllDocumentedOperations(t *testing.T) {
	c := testContract(t)

	tests := []struct {
		id     string
		method string
	}{
		{"GetDeployment", "GET"},
		{"PatchDeployment", "PATCH"},
		{"GetDeploymentActions", "GET"},
		{"SubmitDeploymentActionRequest", "POST"},
		{"GetDeploymentResources", "GET"},
		{"SubmitResourceActionRequest", "POST"},
		{"GetRequest", "GET"},
	}
	for _, tc := range tests {
		t.Run(tc.id, func(t *testing.T) {
			op, ok := c.Operation(tc.id)
			if !ok {
				t.Fatalf("missing from contract")
			}
			if op.Method != tc.method {
				t.Errorf("method = %q, want %q", op.Method, tc.method)
			}
			if op.DocURL == "" {
				t.Error("doc_url is empty")
			}
		})
	}
	if c.Provenance.SpecificationAvailable {
		t.Error("provenance claims a published specification exists")
	}
}

func TestOptionalBodyFieldsAreOmitted(t *testing.T) {
	tests := []struct {
		name string
		body any
		want map[string]any
	}{
		{"patch all unset", deploymentUpdate{}, map[string]any{}},
		{"patch description only", deploymentUpdate{Description: optional("d")}, map[string]any{"description": "d"}},
		{"patch name and icon", deploymentUpdate{Name: optional("n"), IconID: optional("i")}, map[string]any{"name": "n", "iconId": "i"}},
		{"action id only", resourceActionRequest{ActionID: optional("A")}, map[string]any{"actionId": "A"}},
		{"nil inputs omitted", resourceActionRequest{ActionID: optional("A"), Inputs: nil}, map[string]any{"actionId": "A"}},
		{"empty inputs omitted", resourceActionRequest{ActionID: optional("A"), Inputs: map[string]any{}}, map[string]any{"actionId": "A"}},
		{"inputs kept", resourceActionRequest{ActionID: optional("A"), Inputs: map[string]any{"k": "v"}}, map[string]any{"actionId": "A", "inputs": map[string]any{"k": "v"}}},
		{"reason kept", resourceActionRequest{ActionID: optional("A"), Reason: optional("r")}, map[string]any{"actionId": "A", "reason": "r"}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			raw, err := json.Marshal(tc.body)
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			var got map[string]any
			if err := json.Unmarshal(raw, &got); err != nil {
				t.Fatalf("unmarshal: %v", err)
			}
			if got == nil {
				got = map[string]any{}
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("body = %s, want %v", raw, tc.want)
			}
		})
	}
}

func TestMockRoutesOnlyContractPaths(t *testing.T) {
	m, _ := testFixture(t, MockConfig{ResourceID: "res-1"})

	tests := []struct {
		method string
		path   string
		want   int
	}{
		{"GET", "/deployment/api/deployments/dep-1", http.StatusOK},
		{"GET", "/deployment/api/deployments/dep-1/actions", http.StatusOK},
		{"GET", "/deployment/api/deployments/dep-1/resources", http.StatusOK},
		{"GET", "/deployment/api/deployments/dep-9", http.StatusNotFound},
		{"GET", "/deployment/api/policies", http.StatusNotFound},
		{"PUT", "/deployment/api/deployments/dep-1", http.StatusMethodNotAllowed},
	}
	for _, tc := range tests {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			req, _ := http.NewRequest(tc.method, m.URL()+tc.path, nil)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Errorf("status = %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}
}

func TestApplyPlanOutcomes(t *testing.T) {
	tests := []struct {
		name         string
		cfg          MockConfig
		steps        []Step
		wantOutcomes []StepOutcome
		wantApplied  []string
	}{
		{
			name: "all steps succeed",
			cfg:  MockConfig{ResourceID: "res-1", PollsBeforeTerminal: 1},
			steps: []Step{
				{Name: "a", OperationID: "PatchDeployment", DeploymentID: "dep-1", Description: "d"},
				{Name: "b", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease"},
			},
			wantOutcomes: []StepOutcome{StepSucceeded, StepSucceeded},
			wantApplied:  []string{"a", "b"},
		},
		{
			name: "later action settles FAILED",
			cfg: MockConfig{
				ResourceID:          "res-1",
				ActionResult:        map[string]string{"Deployment.PowerOff": "FAILED"},
				ActionDetails:       map[string]string{"Deployment.PowerOff": "guest refused shutdown"},
				PollsBeforeTerminal: 1,
			},
			steps: []Step{
				{Name: "a", OperationID: "PatchDeployment", DeploymentID: "dep-1", Description: "d"},
				{Name: "b", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.PowerOff"},
				{Name: "c", OperationID: "GetDeployment", DeploymentID: "dep-1"},
			},
			wantOutcomes: []StepOutcome{StepSucceeded, StepFailed, StepSkipped},
			wantApplied:  []string{"a"},
		},
		{
			name: "first step fails on a missing deployment",
			cfg:  MockConfig{PollsBeforeTerminal: 0},
			steps: []Step{
				{Name: "a", OperationID: "PatchDeployment", DeploymentID: "dep-absent", Description: "d"},
				{Name: "b", OperationID: "GetDeployment", DeploymentID: "dep-1"},
			},
			wantOutcomes: []StepOutcome{StepFailed, StepSkipped},
			wantApplied:  nil,
		},
		{
			name: "action rejected as a resource action on an unknown resource",
			cfg:  MockConfig{ResourceID: "res-1", PollsBeforeTerminal: 0},
			steps: []Step{
				{Name: "a", OperationID: "SubmitResourceActionRequest", DeploymentID: "dep-1", ResourceID: "res-absent", ActionID: "Deployment.PowerOff"},
			},
			wantOutcomes: []StepOutcome{StepFailed},
			wantApplied:  nil,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, cl := testFixture(t, tc.cfg)
			res, err := cl.ApplyPlan(context.Background(), tc.steps)
			if err != nil {
				t.Fatalf("ApplyPlan: %v", err)
			}
			got := make([]StepOutcome, len(res.Results))
			for i, r := range res.Results {
				got[i] = r.Outcome
			}
			if !reflect.DeepEqual(got, tc.wantOutcomes) {
				t.Errorf("outcomes = %v, want %v\n%s", got, tc.wantOutcomes, res.Summary())
			}
			if !reflect.DeepEqual(res.Applied(), tc.wantApplied) {
				t.Errorf("Applied() = %v, want %v", res.Applied(), tc.wantApplied)
			}
			if res.RolledBack {
				t.Error("RolledBack = true, but no compensating request is ever sent")
			}
		})
	}
}

func TestApplyPlanValidatesBeforeSending(t *testing.T) {
	m, cl := testFixture(t, MockConfig{})

	tests := []struct {
		name  string
		steps []Step
	}{
		{"unknown operation", []Step{{Name: "x", OperationID: "DeleteDeployment", DeploymentID: "dep-1"}}},
		{"empty operation", []Step{{Name: "x"}}},
		{"unknown operation after a valid one", []Step{
			{Name: "ok", OperationID: "GetDeployment", DeploymentID: "dep-1"},
			{Name: "x", OperationID: "PurgeDeployment", DeploymentID: "dep-1"},
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := cl.ApplyPlan(context.Background(), tc.steps); err == nil {
				t.Fatal("want an error")
			}
		})
	}
	if n := len(m.Requests()); n != 0 {
		t.Errorf("mock received %d requests; an invalid plan must send nothing", n)
	}
}

func TestSummaryReportsPartialApplication(t *testing.T) {
	_, cl := testFixture(t, MockConfig{
		ResourceID:          "res-1",
		ActionResult:        map[string]string{"Deployment.PowerOff": "FAILED"},
		ActionDetails:       map[string]string{"Deployment.PowerOff": "guest refused shutdown"},
		PollsBeforeTerminal: 1,
	})

	res, err := cl.ApplyPlan(context.Background(), []Step{
		{Name: "rename", OperationID: "PatchDeployment", DeploymentID: "dep-1", NewName: "n"},
		{Name: "poweroff", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.PowerOff"},
	})
	if err != nil {
		t.Fatalf("ApplyPlan: %v", err)
	}

	want := "1. rename [PatchDeployment] SUCCEEDED\n" +
		"2. poweroff [SubmitDeploymentActionRequest] FAILED: guest refused shutdown\n" +
		"applied: rename\n" +
		"rolled back: false\n"
	if got := res.Summary(); got != want {
		t.Errorf("Summary() =\n%s\nwant\n%s", got, want)
	}
}

func TestAwaitRequestPollsUntilTerminal(t *testing.T) {
	m, cl := testFixture(t, MockConfig{PollsBeforeTerminal: 3})

	res, err := cl.ApplyPlan(context.Background(), []Step{
		{Name: "lease", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease"},
	})
	if err != nil {
		t.Fatalf("ApplyPlan: %v", err)
	}
	if res.Results[0].Outcome != StepSucceeded {
		t.Fatalf("outcome = %q: %s", res.Results[0].Outcome, res.Results[0].Detail)
	}
	if res.Results[0].Status != "SUCCESSFUL" {
		t.Errorf("Status = %q, want SUCCESSFUL", res.Results[0].Status)
	}

	var polls int
	for _, r := range m.Requests() {
		if r.OperationID == "GetRequest" {
			polls++
		}
	}
	if polls != 4 {
		t.Errorf("GetRequest polls = %d, want 4 (3 in-progress then the terminal one)", polls)
	}
}
