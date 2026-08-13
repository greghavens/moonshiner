package fleetlcm

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

const officialSourcesPath = "../docs/official_sources.json"

// ---------------------------------------------------------------------------
// fixture plan
// ---------------------------------------------------------------------------

const fixtureCertificate = "-----BEGIN CERTIFICATE-----\n" +
	"MIIBkTCB+wIJAOxTd0FfBqDLMA0GCSqGSIb3DQEBCwUAMBIxEDAOBgNVBAMMB2Rl\n" +
	"cG90Q0EwHhcNMjYwMTAxMDAwMDAwWhcNMjcwMTAxMDAwMDAwWjASMRAwDgYDVQQD\n" +
	"-----END CERTIFICATE-----\n"

const fixtureComponentID = "9f4c1d2e-6b71-4a55-9f28-3f7a1c0b5d64"

// fixturePlan is the change plan every wire assertion is written against. Optional
// members are deliberately left unset: RepositoryCert, DeploymentOption, DNSSuffix,
// IPv6, ExtraConfig, DepotVersion, the second pin's Version, and every optional
// NodeSizePlan member.
func fixturePlan() Plan {
	return Plan{
		Depot: DepotSpec{
			FQDN:        "depot.vcf.example.com",
			Certificate: fixtureCertificate,
		},
		Pins: []ComponentPin{
			{Component: "VCF_OPERATIONS", Version: "9.1.0.0"},
			{Component: "VCF_AUTOMATION"},
		},
		Components: []ComponentPlan{{
			ComponentType:  "VCF_OPERATIONS",
			DeploymentType: "OvaComponentSpec",
			Nodes: []NodePlan{{
				NodeType:    "VCF_OPERATIONS_ANALYTICS",
				Version:     "9.1.0.0",
				DownloadURL: "https://depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS/9.1.0.0/component.ova",
				FQDN:        "ops-a-01.vcf.example.com",
				Password:    "VMw@re1!Ops",
				DNSServers:  "10.0.0.53",
				NTPServers:  "10.0.0.123",
				NetworkName: "vcf-mgmt",
				IPv4: &IPv4Settings{
					AddressType: "STATIC",
					Address:     "10.0.10.21",
					Gateway:     "10.0.10.1",
					Netmask:     "255.255.255.0",
				},
				DeploymentMode: "DEPLOY_AND_MONITOR",
			}},
		}},
		Config: &ConfigPlan{
			ComponentID: fixtureComponentID,
			Type:        "OvaComponentConfigSpec",
			NodeSizes: []NodeSizePlan{{
				NodeID:             "b1c7e0a4-5d3f-4a1e-8c92-2d6f5b7a9013",
				Size:               "Large",
				AdditionalDiskSize: 512,
			}},
		},
	}
}

func newTestClient(t *testing.T, m *mockServer, correlationID string) *Client {
	t.Helper()
	c, err := NewClient(Config{
		BaseURL:       m.url(),
		Token:         mockToken,
		CorrelationID: correlationID,
		PollInterval:  time.Millisecond,
		PollTimeout:   10 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c == nil {
		t.Fatal("NewClient returned a nil client with a nil error")
	}
	return c
}

// ---------------------------------------------------------------------------
// expected wire sequences
// ---------------------------------------------------------------------------

type wireCall struct {
	operationID string
	method      string
	path        string
}

func depotTaskCall() wireCall {
	return wireCall{OpGetTask, http.MethodGet, "/sddc-lcm/v1/tasks/" + depotTaskID}
}
func deployTaskCall() wireCall {
	return wireCall{OpGetTask, http.MethodGet, "/sddc-lcm/v1/tasks/" + deployTaskID}
}
func configTaskCall() wireCall {
	return wireCall{OpGetTask, http.MethodGet, "/sddc-lcm/v1/tasks/" + configTaskID}
}

var (
	setDepotCall     = wireCall{OpSetDepot, http.MethodPost, "/sddc-lcm/v1/depot"}
	resolveCall      = wireCall{OpResolveDepotComponents, http.MethodPost, "/sddc-lcm/v1/depot/components"}
	createCall       = wireCall{OpCreateComponents, http.MethodPost, "/sddc-lcm/v1/components"}
	updateConfigCall = wireCall{OpUpdateComponentConfig, http.MethodPatch,
		"/sddc-lcm/v1/components/" + fixtureComponentID + "/config"}
)

func assertWireSequence(t *testing.T, got []recordedRequest, want []wireCall) {
	t.Helper()
	render := func() string {
		var b strings.Builder
		for i, r := range got {
			fmt.Fprintf(&b, "\n  [%d] %-24s %-6s %s", i, r.OperationID, r.Method, r.Path)
			if r.RawQuery != "" {
				fmt.Fprintf(&b, "?%s", r.RawQuery)
			}
		}
		if b.Len() == 0 {
			return "\n  (no requests)"
		}
		return b.String()
	}
	if len(got) != len(want) {
		t.Fatalf("request count = %d, want %d; log:%s", len(got), len(want), render())
	}
	for i, w := range want {
		g := got[i]
		if g.OperationID != w.operationID || g.Method != w.method || g.Path != w.path {
			t.Fatalf("request %d = %s %s %s (op %q), want %s %s (op %q); log:%s",
				i, g.Method, g.Path, g.RawQuery, g.OperationID, w.method, w.path, w.operationID, render())
		}
		if g.RawQuery != "" {
			t.Errorf("request %d (%s) carries query %q; no contract operation declares a query parameter",
				i, g.OperationID, g.RawQuery)
		}
	}
}

// ---------------------------------------------------------------------------
// scenario table
// ---------------------------------------------------------------------------

func skippedAfter(op string) string { return "skipped after " + op + " failed" }

const noConfigSkipMessage = "skipped: plan requests no component configuration change"

func TestApplyScenarios(t *testing.T) {
	successResolved := []ResolvedVersion{
		{
			Component: "VCF_OPERATIONS",
			Version:   "9.1.0.0",
			BinaryURL: "https://depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS/9.1.0.0/component.ova",
		},
		{
			Component: "VCF_AUTOMATION",
			Version:   "9.1.0.0",
			BinaryURL: "https://depot.vcf.example.com/PROD/COMP/VCF_AUTOMATION/9.1.0.0/component.ova",
		},
	}

	tests := []struct {
		name         string
		scenario     string
		plan         Plan
		wantWire     []wireCall
		wantSteps    []StepReport
		wantResolved []ResolvedVersion
		wantErrOp    string
	}{
		{
			name:     "whole plan applies",
			scenario: scenarioSuccess,
			plan:     fixturePlan(),
			wantWire: []wireCall{
				setDepotCall, depotTaskCall(), depotTaskCall(),
				resolveCall,
				createCall, deployTaskCall(), deployTaskCall(),
				updateConfigCall, configTaskCall(), configTaskCall(),
			},
			wantSteps: []StepReport{
				{OperationID: OpSetDepot, Status: StatusSucceeded, TaskID: depotTaskID, TaskStatus: "SUCCEEDED"},
				{OperationID: OpResolveDepotComponents, Status: StatusSucceeded},
				{OperationID: OpCreateComponents, Status: StatusSucceeded, TaskID: deployTaskID, TaskStatus: "SUCCEEDED"},
				{OperationID: OpUpdateComponentConfig, Status: StatusSucceeded, TaskID: configTaskID, TaskStatus: "SUCCEEDED"},
			},
			wantResolved: successResolved,
		},
		{
			name:     "deployment task fails after depot and resolve succeeded",
			scenario: scenarioDeployTaskFailed,
			plan:     fixturePlan(),
			wantWire: []wireCall{
				setDepotCall, depotTaskCall(), depotTaskCall(),
				resolveCall,
				createCall, deployTaskCall(), deployTaskCall(),
			},
			wantSteps: []StepReport{
				{OperationID: OpSetDepot, Status: StatusSucceeded, TaskID: depotTaskID, TaskStatus: "SUCCEEDED"},
				{OperationID: OpResolveDepotComponents, Status: StatusSucceeded},
				{
					OperationID: OpCreateComponents,
					Status:      StatusFailed,
					TaskID:      deployTaskID,
					TaskStatus:  "FAILED",
					FailedStage: deployFailedStage,
					Message:     deployFailedMessage,
				},
				{
					OperationID: OpUpdateComponentConfig,
					Status:      StatusSkipped,
					Message:     skippedAfter(OpCreateComponents),
				},
			},
			wantResolved: successResolved,
			wantErrOp:    OpCreateComponents,
		},
		{
			name:     "depot task fails before anything else runs",
			scenario: scenarioDepotTaskFailed,
			plan:     fixturePlan(),
			wantWire: []wireCall{setDepotCall, depotTaskCall(), depotTaskCall()},
			wantSteps: []StepReport{
				{
					OperationID: OpSetDepot,
					Status:      StatusFailed,
					TaskID:      depotTaskID,
					TaskStatus:  "FAILED",
					FailedStage: depotFailedStage,
					Message:     depotFailedMessage,
				},
				{OperationID: OpResolveDepotComponents, Status: StatusSkipped, Message: skippedAfter(OpSetDepot)},
				{OperationID: OpCreateComponents, Status: StatusSkipped, Message: skippedAfter(OpSetDepot)},
				{OperationID: OpUpdateComponentConfig, Status: StatusSkipped, Message: skippedAfter(OpSetDepot)},
			},
			wantErrOp: OpSetDepot,
		},
		{
			name:     "resolve rejected with a status code",
			scenario: scenarioResolveRejected,
			plan:     fixturePlan(),
			wantWire: []wireCall{setDepotCall, depotTaskCall(), depotTaskCall(), resolveCall},
			wantSteps: []StepReport{
				{OperationID: OpSetDepot, Status: StatusSucceeded, TaskID: depotTaskID, TaskStatus: "SUCCEEDED"},
				{
					OperationID: OpResolveDepotComponents,
					Status:      StatusFailed,
					HTTPStatus:  http.StatusBadRequest,
					Message:     "Fleet depot rejected the component resolution request",
				},
				{OperationID: OpCreateComponents, Status: StatusSkipped, Message: skippedAfter(OpResolveDepotComponents)},
				{OperationID: OpUpdateComponentConfig, Status: StatusSkipped, Message: skippedAfter(OpResolveDepotComponents)},
			},
			wantErrOp: OpResolveDepotComponents,
		},
		{
			name:     "create rejected with a status code",
			scenario: scenarioDeployRejected,
			plan:     fixturePlan(),
			wantWire: []wireCall{setDepotCall, depotTaskCall(), depotTaskCall(), resolveCall, createCall},
			wantSteps: []StepReport{
				{OperationID: OpSetDepot, Status: StatusSucceeded, TaskID: depotTaskID, TaskStatus: "SUCCEEDED"},
				{OperationID: OpResolveDepotComponents, Status: StatusSucceeded},
				{
					OperationID: OpCreateComponents,
					Status:      StatusFailed,
					HTTPStatus:  http.StatusBadRequest,
					Message:     "componentSpecs[0].nodeSpecs[0].deploymentSpec is not valid for this fleet",
				},
				{OperationID: OpUpdateComponentConfig, Status: StatusSkipped, Message: skippedAfter(OpCreateComponents)},
			},
			wantResolved: successResolved,
			wantErrOp:    OpCreateComponents,
		},
		{
			name:     "plan without a config change skips the last step",
			scenario: scenarioSuccess,
			plan: func() Plan {
				p := fixturePlan()
				p.Config = nil
				return p
			}(),
			wantWire: []wireCall{
				setDepotCall, depotTaskCall(), depotTaskCall(),
				resolveCall,
				createCall, deployTaskCall(), deployTaskCall(),
			},
			wantSteps: []StepReport{
				{OperationID: OpSetDepot, Status: StatusSucceeded, TaskID: depotTaskID, TaskStatus: "SUCCEEDED"},
				{OperationID: OpResolveDepotComponents, Status: StatusSucceeded},
				{OperationID: OpCreateComponents, Status: StatusSucceeded, TaskID: deployTaskID, TaskStatus: "SUCCEEDED"},
				{OperationID: OpUpdateComponentConfig, Status: StatusSkipped, Message: noConfigSkipMessage},
			},
			wantResolved: successResolved,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			m := newMockServer(t, tc.scenario)
			c := newTestClient(t, m, "")

			report, err := c.Apply(context.Background(), tc.plan)

			if report == nil {
				t.Fatalf("Apply returned a nil report (err=%v); the report must describe every step even on failure", err)
			}
			if tc.wantErrOp == "" {
				if err != nil {
					t.Fatalf("Apply err = %v, want nil", err)
				}
			} else {
				if err == nil {
					t.Fatalf("Apply err = nil, want a failure for %s", tc.wantErrOp)
				}
				var se *StepError
				if !errors.As(err, &se) {
					t.Fatalf("Apply err = %v (%T), want a *StepError", err, err)
				}
				if se.OperationID != tc.wantErrOp {
					t.Errorf("StepError.OperationID = %q, want %q", se.OperationID, tc.wantErrOp)
				}
				for _, want := range tc.wantSteps {
					if want.OperationID != tc.wantErrOp {
						continue
					}
					if se.HTTPStatus != want.HTTPStatus || se.TaskID != want.TaskID ||
						se.TaskStatus != want.TaskStatus || se.FailedStage != want.FailedStage ||
						se.Message != want.Message {
						t.Errorf("StepError diagnosis = %+v, want the same diagnosis as report step %+v", se, want)
					}
				}
				if strings.Contains(err.Error(), mockToken) {
					t.Errorf("StepError string exposes the bearer token: %q", err.Error())
				}
			}

			assertWireSequence(t, m.log(), tc.wantWire)
			assertSteps(t, report, tc.wantSteps)
			assertResolved(t, report, tc.wantResolved)
		})
	}
}

func assertSteps(t *testing.T, report *PlanReport, want []StepReport) {
	t.Helper()
	if len(report.Steps) != len(PlanOperations) {
		t.Fatalf("report has %d steps, want one per PlanOperations entry (%d): %+v",
			len(report.Steps), len(PlanOperations), report.Steps)
	}
	for i, w := range want {
		g := report.Steps[i]
		if g.OperationID != PlanOperations[i] {
			t.Fatalf("step %d operationId = %q, want %q (steps must follow PlanOperations order)",
				i, g.OperationID, PlanOperations[i])
		}
		if g != w {
			t.Errorf("step %d (%s) mismatch\n got: %+v\nwant: %+v", i, w.OperationID, g, w)
		}
		if looked, ok := report.Step(w.OperationID); !ok || looked != w {
			t.Errorf("Step(%q) = (%+v, %v), want (%+v, true)", w.OperationID, looked, ok, w)
		}
	}
}

func assertResolved(t *testing.T, report *PlanReport, want []ResolvedVersion) {
	t.Helper()
	if len(want) == 0 {
		if len(report.ResolvedVersions) != 0 {
			t.Errorf("ResolvedVersions = %+v, want empty", report.ResolvedVersions)
		}
		return
	}
	if !reflect.DeepEqual(report.ResolvedVersions, want) {
		t.Errorf("ResolvedVersions mismatch\n got: %+v\nwant: %+v", report.ResolvedVersions, want)
	}
}

// ---------------------------------------------------------------------------
// exact request wire shape
// ---------------------------------------------------------------------------

func mustJSONObject(t *testing.T, raw string) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		t.Fatalf("bad expectation literal: %v", err)
	}
	return out
}

func assertJSONEqual(t *testing.T, label string, got, want map[string]any) {
	t.Helper()
	if reflect.DeepEqual(got, want) {
		return
	}
	gotPretty, _ := json.MarshalIndent(got, "", "  ")
	wantPretty, _ := json.MarshalIndent(want, "", "  ")
	t.Errorf("%s body mismatch\n got: %s\nwant: %s", label, gotPretty, wantPretty)
}

func TestApplyRequestWireShape(t *testing.T) {
	m := newMockServer(t, scenarioSuccess)
	c := newTestClient(t, m, "")

	if _, err := c.Apply(context.Background(), fixturePlan()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	log := m.log()
	byOp := map[string]recordedRequest{}
	for _, r := range log {
		if r.OperationID != OpGetTask {
			byOp[r.OperationID] = r
		}
	}

	wantBodies := map[string]string{
		OpSetDepot: `{
			"fqdn": "depot.vcf.example.com",
			"certificate": ` + mustQuote(fixtureCertificate) + `
		}`,
		OpResolveDepotComponents: `{
			"fleetDepotSpec": {
				"fqdn": "depot.vcf.example.com",
				"certificate": ` + mustQuote(fixtureCertificate) + `
			},
			"componentVersions": [
				{"component": "VCF_OPERATIONS", "version": "9.1.0.0"},
				{"component": "VCF_AUTOMATION"}
			]
		}`,
		OpCreateComponents: `{
			"componentSpecs": [{
				"deploymentType": "OvaComponentSpec",
				"componentType": "VCF_OPERATIONS",
				"nodeSpecs": [{
					"nodeType": "VCF_OPERATIONS_ANALYTICS",
					"version": "9.1.0.0",
					"repository": {
						"downloadUrl": "https://depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS/9.1.0.0/component.ova"
					},
					"deploymentSpec": {
						"fqdn": "ops-a-01.vcf.example.com",
						"password": "VMw@re1!Ops",
						"dnsServers": "10.0.0.53",
						"ntpServers": "10.0.0.123",
						"networkName": "vcf-mgmt",
						"ipv4Settings": {
							"addressType": "STATIC",
							"address": "10.0.10.21",
							"gateway": "10.0.10.1",
							"netmask": "255.255.255.0"
						}
					},
					"deploymentMode": "DEPLOY_AND_MONITOR"
				}]
			}]
		}`,
		OpUpdateComponentConfig: `{
			"type": "OvaComponentConfigSpec",
			"nodeSizes": [{
				"nodeId": "b1c7e0a4-5d3f-4a1e-8c92-2d6f5b7a9013",
				"size": "Large",
				"additionalDiskSize": 512
			}]
		}`,
	}

	for _, op := range []string{OpSetDepot, OpResolveDepotComponents, OpCreateComponents, OpUpdateComponentConfig} {
		req, ok := byOp[op]
		if !ok {
			t.Fatalf("no %s request was recorded", op)
		}
		assertJSONEqual(t, op, req.jsonBody(t), mustJSONObject(t, wantBodies[op]))

		if ct := req.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("%s Content-Type = %q, want application/json", op, ct)
		}
		if accept := req.Header.Get("Accept"); accept != "application/json" {
			t.Errorf("%s Accept = %q, want application/json", op, accept)
		}
		if auth := req.Header.Get("Authorization"); auth != "Bearer "+mockToken {
			t.Errorf("%s Authorization = %q, want the pinned bearerToken scheme", op, auth)
		}
	}

	for _, r := range log {
		if r.OperationID != OpGetTask {
			continue
		}
		if len(r.Body) != 0 {
			t.Errorf("getTask sent a %d byte body; the contract declares no request body", len(r.Body))
		}
		if ct := r.Header.Get("Content-Type"); ct != "" {
			t.Errorf("getTask sent Content-Type %q; a bodyless request must not declare one", ct)
		}
		if auth := r.Header.Get("Authorization"); auth != "Bearer "+mockToken {
			t.Errorf("getTask Authorization = %q, want the pinned bearerToken scheme", auth)
		}
	}
}

func mustQuote(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		panic(err)
	}
	return string(b)
}

// ---------------------------------------------------------------------------
// unset optional members must be omitted, never sent empty
// ---------------------------------------------------------------------------

func TestUnsetOptionalFieldsAreOmitted(t *testing.T) {
	m := newMockServer(t, scenarioSuccess)
	c := newTestClient(t, m, "")

	if _, err := c.Apply(context.Background(), fixturePlan()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	byOp := map[string]recordedRequest{}
	for _, r := range m.log() {
		if r.OperationID != OpGetTask {
			byOp[r.OperationID] = r
		}
	}

	// No request body may contain a null, an empty string, an empty object or an
	// empty array anywhere: an unset optional member is omitted, not emptied.
	for op, req := range byOp {
		var v any
		if err := json.Unmarshal(req.Body, &v); err != nil {
			t.Fatalf("%s: body is not JSON: %v", op, err)
		}
		assertNoEmptyLeaves(t, op, v, "$")
	}

	absences := []struct {
		op      string
		pointer string
		why     string
	}{
		{OpResolveDepotComponents, "$.version", "Plan.DepotVersion is unset"},
		{OpResolveDepotComponents, "$.componentVersions[1].version", "the second pin has no Version"},
		{OpCreateComponents, "$.componentSpecs[0].configSpec", "ComponentPlan carries no component configSpec"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].configSpec", "NodePlan carries no node configSpec"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].repository.certificate", "NodePlan.RepositoryCert is unset"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.deploymentOption", "NodePlan.DeploymentOption is unset"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.dnsSuffix", "NodePlan.DNSSuffix is unset"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.ipv6Settings", "NodePlan.IPv6 is nil"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.extraConfigProperties", "NodePlan.ExtraConfig is nil"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].nodeName", "NodeSizePlan.NodeName is unset"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].nodeType", "NodeSizePlan.NodeType is unset"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].numCores", "NodeSizePlan.NumCores is nil"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].memoryGb", "NodeSizePlan.MemoryGB is nil"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].configMode", "NodeSizePlan.ConfigMode is unset"},
	}
	for _, a := range absences {
		req, ok := byOp[a.op]
		if !ok {
			t.Fatalf("no %s request was recorded", a.op)
		}
		var v any
		if err := json.Unmarshal(req.Body, &v); err != nil {
			t.Fatalf("%s: body is not JSON: %v", a.op, err)
		}
		if got, present := lookup(v, a.pointer); present {
			t.Errorf("%s body contains %s = %#v, but it must be omitted because %s",
				a.op, a.pointer, got, a.why)
		}
	}
}

func TestOptionalFieldsPreservePopulatedValuesAndExplicitZeroes(t *testing.T) {
	force := false
	zero := 0
	p := fixturePlan()
	p.DepotVersion = "9.1-release"
	p.Pins[1].Version = "9.1.0.1"
	n := &p.Components[0].Nodes[0]
	n.RepositoryCert = "repository-certificate"
	n.DeploymentOption = "large"
	n.DNSSuffix = "vcf.example.com"
	n.IPv6 = &IPv6Settings{
		AddressType: "STATIC",
		Address:     "2001:db8::21",
		Gateway:     "2001:db8::1",
		Netmask:     "64",
		Force:       &force,
	}
	n.ExtraConfig = map[string]string{"guestinfo.ntp.enabled": "false"}
	ns := &p.Config.NodeSizes[0]
	ns.NodeName = "ops-a-01"
	ns.NodeType = "VCF_OPERATIONS_ANALYTICS"
	ns.NumCores = &zero
	ns.MemoryGB = &zero
	ns.ConfigMode = "CONFIG_ONLY"

	m := newMockServer(t, scenarioSuccess)
	c := newTestClient(t, m, "")
	if _, err := c.Apply(context.Background(), p); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	byOp := map[string]recordedRequest{}
	for _, r := range m.log() {
		if r.OperationID != OpGetTask {
			byOp[r.OperationID] = r
		}
	}
	checks := []struct {
		op      string
		pointer string
		want    any
	}{
		{OpResolveDepotComponents, "$.version", "9.1-release"},
		{OpResolveDepotComponents, "$.componentVersions[1].version", "9.1.0.1"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].repository.certificate", "repository-certificate"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.deploymentOption", "large"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.dnsSuffix", "vcf.example.com"},
		{OpCreateComponents, "$.componentSpecs[0].nodeSpecs[0].deploymentSpec.ipv6Settings.force", false},
		{OpUpdateComponentConfig, "$.nodeSizes[0].nodeName", "ops-a-01"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].nodeType", "VCF_OPERATIONS_ANALYTICS"},
		{OpUpdateComponentConfig, "$.nodeSizes[0].numCores", float64(0)},
		{OpUpdateComponentConfig, "$.nodeSizes[0].memoryGb", float64(0)},
		{OpUpdateComponentConfig, "$.nodeSizes[0].configMode", "CONFIG_ONLY"},
	}
	for _, check := range checks {
		var body any
		if err := json.Unmarshal(byOp[check.op].Body, &body); err != nil {
			t.Fatalf("decode %s: %v", check.op, err)
		}
		got, present := lookup(body, check.pointer)
		if !present || !reflect.DeepEqual(got, check.want) {
			t.Errorf("%s %s = (%#v, %v), want (%#v, true)",
				check.op, check.pointer, got, present, check.want)
		}
	}

	var createBody any
	if err := json.Unmarshal(byOp[OpCreateComponents].Body, &createBody); err != nil {
		t.Fatalf("decode createComponents: %v", err)
	}
	extra, present := lookup(createBody,
		"$.componentSpecs[0].nodeSpecs[0].deploymentSpec.extraConfigProperties")
	if !present || !reflect.DeepEqual(extra, map[string]any{"guestinfo.ntp.enabled": "false"}) {
		t.Errorf("extraConfigProperties = (%#v, %v), want the populated map", extra, present)
	}
}

func TestAdditionalUnsetOptionalFieldsAreOmitted(t *testing.T) {
	p := fixturePlan()
	n := &p.Components[0].Nodes[0]
	n.NetworkName = ""
	n.IPv4 = nil
	n.DeploymentMode = ""

	m := newMockServer(t, scenarioSuccess)
	c := newTestClient(t, m, "")
	if _, err := c.Apply(context.Background(), p); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	var body any
	for _, r := range m.log() {
		if r.OperationID == OpCreateComponents {
			if err := json.Unmarshal(r.Body, &body); err != nil {
				t.Fatalf("decode createComponents: %v", err)
			}
			break
		}
	}
	for _, pointer := range []string{
		"$.componentSpecs[0].nodeSpecs[0].deploymentSpec.networkName",
		"$.componentSpecs[0].nodeSpecs[0].deploymentSpec.ipv4Settings",
		"$.componentSpecs[0].nodeSpecs[0].deploymentMode",
	} {
		if got, present := lookup(body, pointer); present {
			t.Errorf("createComponents contains unset %s = %#v", pointer, got)
		}
	}
}

func assertNoEmptyLeaves(t *testing.T, op string, v any, path string) {
	t.Helper()
	switch x := v.(type) {
	case nil:
		t.Errorf("%s body has null at %s; an unset optional member must be omitted", op, path)
	case string:
		if x == "" {
			t.Errorf("%s body has an empty string at %s; an unset optional member must be omitted", op, path)
		}
	case map[string]any:
		if len(x) == 0 {
			t.Errorf("%s body has an empty object at %s; an unset optional member must be omitted", op, path)
			return
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			assertNoEmptyLeaves(t, op, x[k], path+"."+k)
		}
	case []any:
		if len(x) == 0 {
			t.Errorf("%s body has an empty array at %s; an unset optional member must be omitted", op, path)
			return
		}
		for i, item := range x {
			assertNoEmptyLeaves(t, op, item, fmt.Sprintf("%s[%d]", path, i))
		}
	}
}

var pointerStep = regexp.MustCompile(`^([A-Za-z0-9_]+)|^\[(\d+)\]`)

// lookup walks a "$.a.b[0].c" style path and reports whether the member exists.
func lookup(v any, pointer string) (any, bool) {
	rest := strings.TrimPrefix(pointer, "$")
	cur := v
	for rest != "" {
		rest = strings.TrimPrefix(rest, ".")
		match := pointerStep.FindStringSubmatch(rest)
		if match == nil {
			return nil, false
		}
		rest = rest[len(match[0]):]
		if match[1] != "" {
			obj, ok := cur.(map[string]any)
			if !ok {
				return nil, false
			}
			cur, ok = obj[match[1]]
			if !ok {
				return nil, false
			}
			continue
		}
		arr, ok := cur.([]any)
		if !ok {
			return nil, false
		}
		idx := 0
		fmt.Sscanf(match[2], "%d", &idx)
		if idx >= len(arr) {
			return nil, false
		}
		cur = arr[idx]
	}
	return cur, true
}

// ---------------------------------------------------------------------------
// X-Correlation-Id is itself an optional wire member
// ---------------------------------------------------------------------------

func TestCorrelationIDHeaderFollowsContract(t *testing.T) {
	// The pinned specification declares the X-Correlation-Id header parameter on
	// setDepot, createComponents and updateComponentConfig only.
	declaring := map[string]bool{
		OpSetDepot:              true,
		OpCreateComponents:      true,
		OpUpdateComponentConfig: true,
	}

	tests := []struct {
		name          string
		correlationID string
	}{
		{name: "configured", correlationID: "39ab89c8-a945-4290-9327-13c5bd3f595c"},
		{name: "unset", correlationID: ""},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			m := newMockServer(t, scenarioSuccess)
			c := newTestClient(t, m, tc.correlationID)
			if _, err := c.Apply(context.Background(), fixturePlan()); err != nil {
				t.Fatalf("Apply: %v", err)
			}
			for i, r := range m.log() {
				values, present := r.Header[http.CanonicalHeaderKey("X-Correlation-Id")]
				wantPresent := tc.correlationID != "" && declaring[r.OperationID]
				if present != wantPresent {
					t.Errorf("request %d (%s): X-Correlation-Id present = %v, want %v (values %q)",
						i, r.OperationID, present, wantPresent, values)
					continue
				}
				if wantPresent && (len(values) != 1 || values[0] != tc.correlationID) {
					t.Errorf("request %d (%s): X-Correlation-Id = %q, want [%q]",
						i, r.OperationID, values, tc.correlationID)
				}
			}
		})
	}
}

// ---------------------------------------------------------------------------
// configuration validation
// ---------------------------------------------------------------------------

func TestNewClientValidation(t *testing.T) {
	tests := []struct {
		name    string
		cfg     Config
		wantErr bool
	}{
		{name: "loopback root accepted", cfg: Config{BaseURL: "http://127.0.0.1:8080", Token: mockToken}},
		{name: "trailing slash accepted", cfg: Config{BaseURL: "https://sddc.vcf.example.com/", Token: mockToken}},
		{name: "scheme is case insensitive", cfg: Config{BaseURL: "HTTPS://sddc.vcf.example.com", Token: mockToken}},
		{name: "empty base url", cfg: Config{BaseURL: "", Token: mockToken}, wantErr: true},
		{name: "scheme missing", cfg: Config{BaseURL: "sddc.vcf.example.com", Token: mockToken}, wantErr: true},
		{name: "wrong scheme", cfg: Config{BaseURL: "ftp://sddc.vcf.example.com", Token: mockToken}, wantErr: true},
		{name: "host missing", cfg: Config{BaseURL: "https://", Token: mockToken}, wantErr: true},
		{name: "hostname missing", cfg: Config{BaseURL: "https://:8443", Token: mockToken}, wantErr: true},
		{name: "userinfo present", cfg: Config{BaseURL: "https://admin:secret@sddc.vcf.example.com", Token: mockToken}, wantErr: true},
		{name: "non root path", cfg: Config{BaseURL: "https://sddc.vcf.example.com/sddc-lcm", Token: mockToken}, wantErr: true},
		{name: "repeated slash path", cfg: Config{BaseURL: "https://sddc.vcf.example.com//", Token: mockToken}, wantErr: true},
		{name: "query present", cfg: Config{BaseURL: "https://sddc.vcf.example.com?tenant=a", Token: mockToken}, wantErr: true},
		{name: "empty query present", cfg: Config{BaseURL: "https://sddc.vcf.example.com?", Token: mockToken}, wantErr: true},
		{name: "fragment present", cfg: Config{BaseURL: "https://sddc.vcf.example.com#top", Token: mockToken}, wantErr: true},
		{name: "empty fragment present", cfg: Config{BaseURL: "https://sddc.vcf.example.com#", Token: mockToken}, wantErr: true},
		{name: "empty token", cfg: Config{BaseURL: "https://sddc.vcf.example.com", Token: ""}, wantErr: true},
		{name: "blank token", cfg: Config{BaseURL: "https://sddc.vcf.example.com", Token: "   "}, wantErr: true},
		{name: "token with newline", cfg: Config{BaseURL: "https://sddc.vcf.example.com", Token: "abc\ndef"}, wantErr: true},
		{name: "token with delete", cfg: Config{BaseURL: "https://sddc.vcf.example.com", Token: "abc\x7fdef"}, wantErr: true},
		{
			name:    "correlation id with control character",
			cfg:     Config{BaseURL: "https://sddc.vcf.example.com", Token: mockToken, CorrelationID: "abc\r\ndef"},
			wantErr: true,
		},
		{
			name:    "correlation id with tab",
			cfg:     Config{BaseURL: "https://sddc.vcf.example.com", Token: mockToken, CorrelationID: "abc\tdef"},
			wantErr: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c, err := NewClient(tc.cfg)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("NewClient(%+v) err = nil, want an error", tc.cfg)
				}
				return
			}
			if err != nil {
				t.Fatalf("NewClient(%+v) err = %v, want nil", tc.cfg, err)
			}
			if c == nil {
				t.Fatal("NewClient returned a nil client with a nil error")
			}
		})
	}
}

func TestNewClientDefaultsAndOverrides(t *testing.T) {
	c, err := NewClient(Config{BaseURL: "https://sddc.vcf.example.com", Token: mockToken})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c.pollInterval != 2*time.Second {
		t.Errorf("default pollInterval = %s, want 2s", c.pollInterval)
	}
	if c.pollTimeout != 30*time.Minute {
		t.Errorf("default pollTimeout = %s, want 30m", c.pollTimeout)
	}
	if c.httpClient == nil || c.httpClient.Timeout != 30*time.Second {
		t.Errorf("default HTTP client = %#v, want a client with a 30s timeout", c.httpClient)
	}

	customHTTP := &http.Client{Timeout: 7 * time.Second}
	c, err = NewClient(Config{
		BaseURL:      "https://sddc.vcf.example.com",
		Token:        mockToken,
		PollInterval: 3 * time.Second,
		PollTimeout:  4 * time.Minute,
		HTTPClient:   customHTTP,
	})
	if err != nil {
		t.Fatalf("NewClient with overrides: %v", err)
	}
	if c.pollInterval != 3*time.Second || c.pollTimeout != 4*time.Minute || c.httpClient != customHTTP {
		t.Errorf("overrides were not preserved: interval=%s timeout=%s client=%p",
			c.pollInterval, c.pollTimeout, c.httpClient)
	}
}

func TestOnlyTheDocumentedStatusIsSuccessful(t *testing.T) {
	m := newMockServer(t, scenarioWrongDepotStatus)
	c := newTestClient(t, m, "")
	report, err := c.Apply(context.Background(), fixturePlan())
	if err == nil {
		t.Fatal("Apply err = nil after setDepot returned 200, want a status failure (only 202 succeeds)")
	}
	var se *StepError
	if !errors.As(err, &se) {
		t.Fatalf("Apply err = %T, want *StepError", err)
	}
	if se.OperationID != OpSetDepot || se.HTTPStatus != http.StatusOK {
		t.Errorf("StepError = %+v, want setDepot rejected with HTTP 200", se)
	}
	step, _ := report.Step(OpSetDepot)
	if step.Status != StatusFailed || step.HTTPStatus != http.StatusOK {
		t.Errorf("setDepot report = %+v, want FAILED with HTTP 200", step)
	}
	assertWireSequence(t, m.log(), []wireCall{setDepotCall})
}

func TestAcceptedTerminalTaskIsStillFetchedImmediately(t *testing.T) {
	m := newMockServer(t, scenarioAcceptedTerminal)
	c := newTestClient(t, m, "")
	if _, err := c.Apply(context.Background(), fixturePlan()); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	log := m.log()
	if len(log) < 2 || log[0].OperationID != OpSetDepot || log[1].OperationID != OpGetTask {
		t.Fatalf("first requests = %+v, want setDepot followed immediately by getTask", log)
	}
	if log[1].Path != "/sddc-lcm/v1/tasks/"+depotTaskID {
		t.Errorf("first getTask path = %q, want depot task path", log[1].Path)
	}
}

func TestEveryNonterminalTaskStatusContinuesPolling(t *testing.T) {
	m := newMockServer(t, scenarioTaskProgression)
	c := newTestClient(t, m, "")
	if _, err := c.Apply(context.Background(), fixturePlan()); err != nil {
		t.Fatalf("Apply through PENDING, SCHEDULED and RUNNING: %v", err)
	}
	polls := map[string]int{}
	for _, r := range m.log() {
		if r.OperationID == OpGetTask {
			polls[r.Path]++
		}
	}
	for _, id := range []string{depotTaskID, deployTaskID, configTaskID} {
		path := "/sddc-lcm/v1/tasks/" + id
		if polls[path] != 4 {
			t.Errorf("getTask polls for %s = %d, want 4", id, polls[path])
		}
	}
}

func TestGetTaskFailureBelongsToSubmittingStep(t *testing.T) {
	m := newMockServer(t, scenarioTaskLookupFailed)
	c := newTestClient(t, m, "")
	report, err := c.Apply(context.Background(), fixturePlan())
	if err == nil {
		t.Fatal("Apply err = nil, want task lookup failure")
	}
	var se *StepError
	if !errors.As(err, &se) {
		t.Fatalf("Apply err = %T, want *StepError", err)
	}
	if se.OperationID != OpSetDepot || se.HTTPStatus != http.StatusInternalServerError ||
		se.TaskID != depotTaskID || se.Message != "The accepted depot task could not be read" {
		t.Errorf("StepError = %+v, want task lookup failure attributed to setDepot", se)
	}
	step, _ := report.Step(OpSetDepot)
	if step.OperationID != se.OperationID || step.HTTPStatus != se.HTTPStatus ||
		step.TaskID != se.TaskID || step.TaskStatus != se.TaskStatus || step.Message != se.Message {
		t.Errorf("report diagnosis %+v does not match StepError %+v", step, se)
	}
	if step.OperationID == OpGetTask {
		t.Error("getTask was incorrectly reported as a plan step")
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func TestStepErrorRedactsTransportDetailsAndToken(t *testing.T) {
	const transportDetail = "dial tcp secret.internal:443: connection refused"
	httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return nil, errors.New(transportDetail + " token=" + mockToken)
	})}
	c, err := NewClient(Config{
		BaseURL:    "https://sddc.vcf.example.com",
		Token:      mockToken,
		HTTPClient: httpClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	report, err := c.Apply(context.Background(), fixturePlan())
	if err == nil {
		t.Fatal("Apply err = nil, want transport failure")
	}
	var se *StepError
	if !errors.As(err, &se) {
		t.Fatalf("Apply err = %T, want *StepError", err)
	}
	for _, check := range []struct {
		label   string
		message string
	}{
		{label: "error string", message: err.Error()},
		{label: "StepError message", message: se.Message},
	} {
		if strings.Contains(check.message, transportDetail) || strings.Contains(check.message, mockToken) {
			t.Errorf("%s exposes a transport detail or token: %q", check.label, check.message)
		}
	}
	step, _ := report.Step(OpSetDepot)
	if step.Message != se.Message || step.Status != StatusFailed {
		t.Errorf("setDepot report = %+v, want failed step with the StepError diagnosis", step)
	}
}

func TestApplyHonoursContextCancellation(t *testing.T) {
	m := newMockServer(t, scenarioSuccess)
	c := newTestClient(t, m, "")

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	report, err := c.Apply(ctx, fixturePlan())
	if err == nil {
		t.Fatal("Apply err = nil, want a cancellation failure")
	}
	if report == nil {
		t.Fatal("Apply returned a nil report on cancellation; the report must always describe every step")
	}
	if len(report.Steps) != len(PlanOperations) {
		t.Fatalf("report has %d steps, want %d", len(report.Steps), len(PlanOperations))
	}
}

// ---------------------------------------------------------------------------
// the mock itself is pinned to the contract
// ---------------------------------------------------------------------------

func TestMockRefusesOperationsOutsideContract(t *testing.T) {
	m := newMockServer(t, scenarioSuccess)

	tests := []struct {
		name   string
		method string
		target string
	}{
		{name: "getHealth is not in the contract", method: http.MethodGet, target: "/sddc-lcm/v1/health"},
		{name: "getComponents is not in the contract", method: http.MethodGet, target: "/sddc-lcm/v1/components"},
		{name: "getTasks collection is not in the contract", method: http.MethodGet, target: "/sddc-lcm/v1/tasks"},
		{name: "service base path is required", method: http.MethodPost, target: "/v1/depot"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, m.url()+tc.target, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			req.Header.Set("Authorization", "Bearer "+mockToken)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound {
				t.Errorf("%s %s = %d, want 404: the mock must serve only contract operations",
					tc.method, tc.target, resp.StatusCode)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// the contract is a faithful projection of the pinned specification
// ---------------------------------------------------------------------------

var shaPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)

func TestContractIsSpecificationDerived(t *testing.T) {
	doc := loadContract(t)

	if doc.DerivedFrom.Repository != "https://github.com/vmware/vcf-api-specs" {
		t.Errorf("contract repository = %q", doc.DerivedFrom.Repository)
	}
	if doc.DerivedFrom.Path != "specifications/sddc-lcm/sddc-lcm-openapi.yaml" {
		t.Errorf("contract spec path = %q", doc.DerivedFrom.Path)
	}
	if !shaPattern.MatchString(doc.DerivedFrom.CommitSha) {
		t.Errorf("contract commit sha = %q, want a 40 character hex sha", doc.DerivedFrom.CommitSha)
	}
	if doc.DerivedFrom.License != "Apache-2.0" {
		t.Errorf("contract license = %q, want Apache-2.0", doc.DerivedFrom.License)
	}
	if doc.DerivedFrom.OpenAPI != "3.0.4" {
		t.Errorf("contract openapi = %q, want the specification's 3.0.4", doc.DerivedFrom.OpenAPI)
	}
	if doc.DerivedFrom.APIVersion != "9.1.0.0" {
		t.Errorf("contract apiVersion = %q, want the specification's 9.1.0.0", doc.DerivedFrom.APIVersion)
	}
	if doc.Server.URL != "https://vcf.broadcom.com/sddc-lcm" {
		t.Errorf("contract server url = %q, want the specification's single server url", doc.Server.URL)
	}
	if doc.Server.BasePath != ServiceBasePath {
		t.Errorf("contract basePath = %q, want %q", doc.Server.BasePath, ServiceBasePath)
	}
	if doc.Security.Scheme != "bearerToken" || doc.Security.HTTPScheme != "Bearer" {
		t.Errorf("contract security = %+v, want the specification's bearerToken/Bearer scheme", doc.Security)
	}

	wantOps := []contractOperation{
		{OperationID: OpSetDepot, Method: "POST", Path: "/v1/depot", SuccessStatus: 202},
		{OperationID: OpResolveDepotComponents, Method: "POST", Path: "/v1/depot/components", SuccessStatus: 200},
		{OperationID: OpCreateComponents, Method: "POST", Path: "/v1/components", SuccessStatus: 202},
		{OperationID: OpGetTask, Method: "GET", Path: "/v1/tasks/{taskId}", SuccessStatus: 200},
		{OperationID: OpUpdateComponentConfig, Method: "PATCH", Path: "/v1/components/{componentId}/config", SuccessStatus: 202},
	}
	if len(doc.Operations) != len(wantOps) {
		t.Fatalf("contract names %d operations, want exactly %d", len(doc.Operations), len(wantOps))
	}
	index := map[string]contractOperation{}
	for _, op := range doc.Operations {
		index[op.OperationID] = op
	}
	for _, w := range wantOps {
		got, ok := index[w.OperationID]
		if !ok {
			t.Errorf("contract omits operationId %q", w.OperationID)
			continue
		}
		if got != w {
			t.Errorf("contract operation %s = %+v, want %+v", w.OperationID, got, w)
		}
	}

	wantIDs := make([]string, 0, len(wantOps))
	for _, w := range wantOps {
		wantIDs = append(wantIDs, w.OperationID)
	}
	gotIDs := append([]string(nil), doc.OperationIDs...)
	sortedWant := append([]string(nil), wantIDs...)
	sort.Strings(gotIDs)
	sort.Strings(sortedWant)
	if !reflect.DeepEqual(gotIDs, sortedWant) {
		t.Errorf("contract operationIds = %v, want %v", doc.OperationIDs, wantIDs)
	}
}

func TestOfficialSourcesRecordsSpecPathCommitAndOperations(t *testing.T) {
	raw, err := os.ReadFile(filepath.FromSlash(officialSourcesPath))
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	var src struct {
		Repository          string `json:"repository"`
		RepositoryCommitSha string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
		SpecRawURL          string `json:"specRawUrl"`
		SpecPermalink       string `json:"specPermalink"`
		License             string `json:"license"`
		SpecInfo            struct {
			Title     string `json:"title"`
			Version   string `json:"version"`
			OpenAPI   string `json:"openapi"`
			ServerURL string `json:"serverUrl"`
		} `json:"specInfo"`
		OperationIDs []string `json:"operationIds"`
		Operations   []struct {
			OperationID         string `json:"operationId"`
			Method              string `json:"method"`
			Path                string `json:"path"`
			SpecPath            string `json:"specPath"`
			RepositoryCommitSha string `json:"repositoryCommitSha"`
			SpecJSONPointer     string `json:"specJsonPointer"`
		} `json:"operations"`
		DocumentationPageUsedAsContractSource bool `json:"documentationPageUsedAsContractSource"`
	}
	if err := json.Unmarshal(raw, &src); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}

	if src.Repository != "https://github.com/vmware/vcf-api-specs" {
		t.Errorf("repository = %q", src.Repository)
	}
	if !shaPattern.MatchString(src.RepositoryCommitSha) {
		t.Fatalf("repositoryCommitSha = %q, want a 40 character hex sha", src.RepositoryCommitSha)
	}
	if src.SpecPath != "specifications/sddc-lcm/sddc-lcm-openapi.yaml" {
		t.Errorf("specPath = %q", src.SpecPath)
	}
	if src.License != "Apache-2.0" {
		t.Errorf("license = %q, want Apache-2.0", src.License)
	}
	if src.DocumentationPageUsedAsContractSource {
		t.Error("documentationPageUsedAsContractSource = true; the contract must come from the specification")
	}
	if src.SpecInfo.Version != "9.1.0.0" || src.SpecInfo.OpenAPI != "3.0.4" {
		t.Errorf("specInfo = %+v, want version 9.1.0.0 / openapi 3.0.4", src.SpecInfo)
	}

	wantRaw := "https://raw.githubusercontent.com/vmware/vcf-api-specs/" + src.RepositoryCommitSha +
		"/" + src.SpecPath
	if src.SpecRawURL != wantRaw {
		t.Errorf("specRawUrl = %q, want %q", src.SpecRawURL, wantRaw)
	}
	wantPermalink := "https://github.com/vmware/vcf-api-specs/blob/" + src.RepositoryCommitSha +
		"/" + src.SpecPath
	if src.SpecPermalink != wantPermalink {
		t.Errorf("specPermalink = %q, want %q", src.SpecPermalink, wantPermalink)
	}

	contract := loadContract(t)
	if src.RepositoryCommitSha != contract.DerivedFrom.CommitSha {
		t.Errorf("official sources commit %q disagrees with contract commit %q",
			src.RepositoryCommitSha, contract.DerivedFrom.CommitSha)
	}
	if src.SpecPath != contract.DerivedFrom.Path {
		t.Errorf("official sources specPath %q disagrees with contract path %q",
			src.SpecPath, contract.DerivedFrom.Path)
	}

	wantIDs := map[string]string{
		OpSetDepot:               "/v1/depot",
		OpResolveDepotComponents: "/v1/depot/components",
		OpCreateComponents:       "/v1/components",
		OpGetTask:                "/v1/tasks/{taskId}",
		OpUpdateComponentConfig:  "/v1/components/{componentId}/config",
	}
	if len(src.OperationIDs) != len(wantIDs) {
		t.Errorf("operationIds = %v, want one entry per contract operation", src.OperationIDs)
	}
	seen := map[string]bool{}
	for _, op := range src.Operations {
		want, ok := wantIDs[op.OperationID]
		if !ok {
			t.Errorf("official sources records unexpected operationId %q", op.OperationID)
			continue
		}
		seen[op.OperationID] = true
		if op.Path != want {
			t.Errorf("%s path = %q, want %q", op.OperationID, op.Path, want)
		}
		if op.SpecPath != src.SpecPath {
			t.Errorf("%s specPath = %q, want %q", op.OperationID, op.SpecPath, src.SpecPath)
		}
		if op.RepositoryCommitSha != src.RepositoryCommitSha {
			t.Errorf("%s commit sha = %q, want %q", op.OperationID, op.RepositoryCommitSha, src.RepositoryCommitSha)
		}
		if op.SpecJSONPointer == "" {
			t.Errorf("%s records no specJsonPointer", op.OperationID)
		}
	}
	for id := range wantIDs {
		if !seen[id] {
			t.Errorf("official sources omits a source record for operationId %q", id)
		}
	}
}
