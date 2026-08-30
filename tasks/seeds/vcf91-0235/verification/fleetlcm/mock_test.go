package fleetlcm

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// contractPath points at the protected projection of
// specifications/sddc-lcm/sddc-lcm-openapi.yaml. The mock refuses to serve any
// route that this document does not name.
const contractPath = "../docs/contract.json"

const (
	scenarioSuccess          = "success"
	scenarioDeployTaskFailed = "deploy-task-failed"
	scenarioDepotTaskFailed  = "depot-task-failed"
	scenarioResolveRejected  = "resolve-rejected"
	scenarioDeployRejected   = "deploy-rejected"
	scenarioWrongDepotStatus = "wrong-depot-status"
	scenarioTaskLookupFailed = "task-lookup-failed"
	scenarioTaskProgression  = "task-progression"
	scenarioAcceptedTerminal = "accepted-terminal"
)

const (
	mockToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sddc-lcm-test.signature"

	depotTaskID  = "11111111-1111-4111-8111-111111111111"
	deployTaskID = "22222222-2222-4222-8222-222222222222"
	configTaskID = "33333333-3333-4333-8333-333333333333"

	depotFailedStage  = "depot-registration"
	deployFailedStage = "component-node-deploy"

	depotFailedMessage  = "Fleet depot registration did not complete: certificate chain was rejected by the SDDC LCM trust store"
	deployFailedMessage = "OVA deployment for node ops-a-01.vcf.example.com timed out after 3600 seconds"
)

// ---------------------------------------------------------------------------
// contract loading
// ---------------------------------------------------------------------------

type contractOperation struct {
	OperationID   string `json:"operationId"`
	Method        string `json:"method"`
	Path          string `json:"path"`
	SuccessStatus int    `json:"successStatus"`
}

type contractDocument struct {
	ContractVersion int `json:"contractVersion"`
	DerivedFrom     struct {
		Repository string `json:"repository"`
		CommitSha  string `json:"commitSha"`
		Path       string `json:"path"`
		SpecRawURL string `json:"specRawUrl"`
		License    string `json:"license"`
		OpenAPI    string `json:"openapi"`
		Title      string `json:"title"`
		APIVersion string `json:"apiVersion"`
	} `json:"derivedFrom"`
	Server struct {
		URL         string `json:"url"`
		BasePath    string `json:"basePath"`
		Description string `json:"description"`
	} `json:"server"`
	Security struct {
		Scheme       string `json:"scheme"`
		Type         string `json:"type"`
		HTTPScheme   string `json:"httpScheme"`
		BearerFormat string `json:"bearerFormat"`
	} `json:"security"`
	OperationIDs []string            `json:"operationIds"`
	Operations   []contractOperation `json:"operations"`
}

func loadContract(t *testing.T) contractDocument {
	t.Helper()
	raw, err := os.ReadFile(filepath.FromSlash(contractPath))
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var doc contractDocument
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if len(doc.Operations) == 0 {
		t.Fatal("contract names no operations")
	}
	return doc
}

// ---------------------------------------------------------------------------
// request log
// ---------------------------------------------------------------------------

type recordedRequest struct {
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
}

// jsonBody parses the recorded body as a generic JSON value.
func (r recordedRequest) jsonBody(t *testing.T) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(r.Body, &out); err != nil {
		t.Fatalf("%s: body is not a JSON object: %v (%q)", r.OperationID, err, string(r.Body))
	}
	return out
}

// ---------------------------------------------------------------------------
// mock server
// ---------------------------------------------------------------------------

type mockServer struct {
	contract contractDocument
	scenario string
	srv      *httptest.Server

	mu        sync.Mutex
	requests  []recordedRequest
	taskPolls map[string]int
}

// newMockServer starts a loopback SDDC LCM stand-in pinned to docs/contract.json.
// It serves only the operations that document names and records every request.
func newMockServer(t *testing.T, scenario string) *mockServer {
	t.Helper()
	m := &mockServer{
		contract:  loadContract(t),
		scenario:  scenario,
		taskPolls: map[string]int{},
	}
	m.srv = httptest.NewServer(http.HandlerFunc(m.serve))
	t.Cleanup(m.srv.Close)
	return m
}

func (m *mockServer) url() string { return m.srv.URL }

func (m *mockServer) log() []recordedRequest {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]recordedRequest, len(m.requests))
	copy(out, m.requests)
	return out
}

// match resolves method+path against the contract's operation table, honouring
// {placeholder} path segments. The contract's server base path is required.
func (m *mockServer) match(method, path string) (contractOperation, map[string]string, bool) {
	base := m.contract.Server.BasePath
	if !strings.HasPrefix(path, base+"/") {
		return contractOperation{}, nil, false
	}
	rest := strings.TrimPrefix(path, base)
	got := strings.Split(strings.Trim(rest, "/"), "/")
	for _, op := range m.contract.Operations {
		if !strings.EqualFold(op.Method, method) {
			continue
		}
		want := strings.Split(strings.Trim(op.Path, "/"), "/")
		if len(want) != len(got) {
			continue
		}
		params := map[string]string{}
		ok := true
		for i, seg := range want {
			switch {
			case strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}"):
				if got[i] == "" {
					ok = false
				} else {
					params[strings.Trim(seg, "{}")] = got[i]
				}
			case seg != got[i]:
				ok = false
			}
			if !ok {
				break
			}
		}
		if ok {
			return op, params, true
		}
	}
	return contractOperation{}, nil, false
}

func (m *mockServer) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	op, params, matched := m.match(r.Method, r.URL.Path)

	rec := recordedRequest{
		OperationID: op.OperationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        body,
	}
	m.mu.Lock()
	m.requests = append(m.requests, rec)
	m.mu.Unlock()

	if !matched {
		writeError(w, http.StatusNotFound, "SDDC_LCM_ROUTE_NOT_IN_CONTRACT",
			"No operation in docs/contract.json serves "+r.Method+" "+r.URL.Path)
		return
	}
	if r.Header.Get("Authorization") != "Bearer "+mockToken {
		writeError(w, http.StatusUnauthorized, "SDDC_LCM_UNAUTHORIZED",
			"A valid bearer token is required")
		return
	}

	switch op.OperationID {
	case OpSetDepot:
		if m.scenario == scenarioWrongDepotStatus {
			writeJSON(w, http.StatusOK, m.task(depotTaskID, "fleet-depot-register", "RUNNING"))
			return
		}
		if m.scenario == scenarioAcceptedTerminal {
			writeJSON(w, op.SuccessStatus, m.task(depotTaskID, "fleet-depot-register", "SUCCEEDED"))
			return
		}
		writeJSON(w, op.SuccessStatus, m.task(depotTaskID, "fleet-depot-register", "RUNNING"))
	case OpResolveDepotComponents:
		if m.scenario == scenarioResolveRejected {
			writeError(w, http.StatusBadRequest, "SDDC_LCM_DEPOT_RESOLUTION_REJECTED",
				"Fleet depot rejected the component resolution request")
			return
		}
		writeJSON(w, op.SuccessStatus, resolveResponse(body))
	case OpCreateComponents:
		if m.scenario == scenarioDeployRejected {
			writeError(w, http.StatusBadRequest, "SDDC_LCM_COMPONENT_SPEC_INVALID",
				"componentSpecs[0].nodeSpecs[0].deploymentSpec is not valid for this fleet")
			return
		}
		writeJSON(w, op.SuccessStatus, m.task(deployTaskID, "component-install", "RUNNING"))
	case OpUpdateComponentConfig:
		writeJSON(w, op.SuccessStatus, m.task(configTaskID, "component-config-update", "RUNNING"))
	case OpGetTask:
		m.serveTask(w, params["taskId"])
	default:
		writeError(w, http.StatusNotImplemented, "SDDC_LCM_UNSUPPORTED",
			"operation "+op.OperationID+" is not modelled by the mock")
	}
}

// In the default scenarios serveTask reports RUNNING on the first poll and the
// terminal state on every later poll. Focused scenarios exercise the other task
// states, an immediate terminal reply and lookup failure.
func (m *mockServer) serveTask(w http.ResponseWriter, taskID string) {
	name, ok := map[string]string{
		depotTaskID:  "fleet-depot-register",
		deployTaskID: "component-install",
		configTaskID: "component-config-update",
	}[taskID]
	if !ok {
		writeError(w, http.StatusNotFound, "SDDC_LCM_TASK_NOT_FOUND", "Unknown task "+taskID)
		return
	}
	if m.scenario == scenarioTaskLookupFailed && taskID == depotTaskID {
		writeError(w, http.StatusInternalServerError, "SDDC_LCM_TASK_LOOKUP_FAILED",
			"The accepted depot task could not be read")
		return
	}
	if m.scenario == scenarioAcceptedTerminal && taskID == depotTaskID {
		writeJSON(w, http.StatusOK, m.task(taskID, name, "SUCCEEDED"))
		return
	}
	m.mu.Lock()
	m.taskPolls[taskID]++
	polls := m.taskPolls[taskID]
	m.mu.Unlock()

	if m.scenario == scenarioTaskProgression {
		progression := []string{"PENDING", "SCHEDULED", "RUNNING"}
		if polls <= len(progression) {
			writeJSON(w, http.StatusOK, m.task(taskID, name, progression[polls-1]))
			return
		}
	} else if polls < 2 {
		writeJSON(w, http.StatusOK, m.task(taskID, name, "RUNNING"))
		return
	}
	writeJSON(w, http.StatusOK, m.task(taskID, name, m.terminalStatus(taskID)))
}

func (m *mockServer) terminalStatus(taskID string) string {
	switch {
	case taskID == depotTaskID && m.scenario == scenarioDepotTaskFailed:
		return "FAILED"
	case taskID == deployTaskID && m.scenario == scenarioDeployTaskFailed:
		return "FAILED"
	default:
		return "SUCCEEDED"
	}
}

// stagePlan returns the ordered stage names of a task and the index that fails.
func stagePlan(taskID string) ([]string, int) {
	switch taskID {
	case depotTaskID:
		return []string{"depot-connectivity-check", depotFailedStage}, 1
	case deployTaskID:
		return []string{"component-precheck", deployFailedStage, "component-registration"}, 1
	default:
		return []string{"config-validate", "config-apply"}, 1
	}
}

func failureMessage(taskID string) (string, string) {
	if taskID == depotTaskID {
		return "com.broadcom.lcm.depot.register.failed", depotFailedMessage
	}
	return "com.broadcom.lcm.components.deploy.node.failed", deployFailedMessage
}

// task renders a specification-shaped Task document.
func (m *mockServer) task(id, name, status string) map[string]any {
	names, failAt := stagePlan(id)

	stages := make([]any, 0, len(names))
	for i, stageName := range names {
		stage := map[string]any{
			"id":   id + "-stage-" + strconv.Itoa(i),
			"name": stageName,
		}
		switch status {
		case "RUNNING":
			if i == 0 {
				stage["status"] = "RUNNING"
			} else {
				stage["status"] = "PENDING"
			}
		case "SUCCEEDED":
			stage["status"] = "SUCCEEDED"
		case "FAILED":
			switch {
			case i < failAt:
				stage["status"] = "SUCCEEDED"
			case i == failAt:
				msgID, msgText := failureMessage(id)
				stage["status"] = "FAILED"
				stage["messages"] = []any{map[string]any{
					"stageId":   id + "-stage-" + strconv.Itoa(i),
					"level":     "ERROR",
					"timestamp": "2026-05-13T11:31:04Z",
					"message": map[string]any{
						"id":               msgID,
						"defaultMessage":   msgText,
						"localizedMessage": msgText,
					},
				}}
			default:
				stage["status"] = "SKIPPED"
			}
		}
		stages = append(stages, stage)
	}

	task := map[string]any{
		"id":           id,
		"name":         name,
		"status":       status,
		"type":         "apply",
		"createdBy":    "svc-fleet-lcm",
		"resourceType": "COMPONENT",
		"createTime":   "2026-05-13T11:29:41Z",
		"updateTime":   "2026-05-13T11:31:04Z",
		"cancellable":  status == "RUNNING",
		"retriable":    status == "FAILED",
		"stages":       stages,
		"taskSummary": map[string]any{
			"totalSubTasks": 0,
			"totalSteps":    len(names),
		},
	}
	if status == "FAILED" {
		msgID, msgText := failureMessage(id)
		task["messages"] = []any{map[string]any{
			"level":     "ERROR",
			"timestamp": "2026-05-13T11:31:04Z",
			"message": map[string]any{
				"id":               msgID,
				"defaultMessage":   msgText,
				"localizedMessage": msgText,
			},
		}}
	}
	return task
}

// resolveResponse echoes the submitted pins back as ResolvedComponentVersions so
// that the reply provably depends on the request body that reached the server.
func resolveResponse(body []byte) map[string]any {
	var req struct {
		FleetDepotSpec struct {
			FQDN string `json:"fqdn"`
		} `json:"fleetDepotSpec"`
		ComponentVersions []struct {
			Component string `json:"component"`
			Version   string `json:"version"`
		} `json:"componentVersions"`
	}
	_ = json.Unmarshal(body, &req)

	out := make([]any, 0, len(req.ComponentVersions))
	for _, cv := range req.ComponentVersions {
		version := cv.Version
		if version == "" {
			version = "9.1.0.0"
		}
		out = append(out, map[string]any{
			"component": cv.Component,
			"version":   version,
			"binaryUrl": "https://" + req.FleetDepotSpec.FQDN + "/PROD/COMP/" +
				cv.Component + "/" + version + "/component.ova",
		})
	}
	return map[string]any{"componentVersions": out}
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{
		"code": code,
		"message": map[string]any{
			"id":               "com.broadcom.lcm.error." + strings.ToLower(code),
			"defaultMessage":   message,
			"localizedMessage": message,
		},
		"resolution": map[string]any{
			"id":               "com.broadcom.lcm.error.resolution",
			"defaultMessage":   "Review the SDDC LCM service log for details.",
			"localizedMessage": "Review the SDDC LCM service log for details.",
		},
		"referenceId": "ref-" + strings.ToLower(code),
		"timestamp":   "2026-05-13T11:31:04Z",
	})
}
