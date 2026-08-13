package contracttest

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"example.com/vcf/fleetlcm/fleetrun"
	"example.com/vcf/fleetlcm/internal/mocklcm"
)

// wantRequest is the exact wire shape one request must have.
type wantRequest struct {
	operation string
	method    string
	// target is the request target as it must appear on the wire: the path,
	// and a query string only when the request actually carries parameters.
	target string
	token  string
	status int
	// headers must be present with exactly these values.
	headers map[string]string
	// absentHeaders must not appear at all. A header sent with an empty value
	// is present, and fails this check.
	absentHeaders []string
	// body is the decoded request body. A nil body means the request must
	// carry no body at all.
	body map[string]any
}

// checkRequests compares what the mock received against the expected wire
// shapes, request by request.
func checkRequests(t *testing.T, m *mocklcm.Mock, want []wantRequest) {
	t.Helper()
	got := m.Requests()
	if len(got) != len(want) {
		t.Fatalf("the run made %d requests, want %d\ngot:  %v\nwant: %v",
			len(got), len(want), operations(m), wantOperations(want))
	}
	for i, w := range want {
		r := got[i]
		label := func(format string, args ...any) {
			t.Errorf("request %d (%s): "+format, append([]any{i + 1, w.operation}, args...)...)
		}
		if r.OperationID != w.operation {
			label("routed to %q, want %q", r.OperationID, w.operation)
		}
		if r.Method != w.method {
			label("method %s, want %s", r.Method, w.method)
		}
		if r.Target != w.target {
			label("target %q, want %q", r.Target, w.target)
		}
		if r.Token != w.token {
			label("presented credential %q, want %q", r.Token, w.token)
		}
		if r.Status != w.status {
			label("answered %d, want %d", r.Status, w.status)
		}
		for name, value := range w.headers {
			if !r.HeaderPresent(name) {
				label("header %s is missing, want %q", name, value)
				continue
			}
			if got := r.HeaderValue(name); got != value {
				label("header %s is %q, want %q", name, got, value)
			}
		}
		for _, name := range w.absentHeaders {
			if r.HeaderPresent(name) {
				label("header %s was sent as %q; an unset optional header is omitted, not sent empty",
					name, r.HeaderValue(name))
			}
		}
		if w.body == nil {
			if len(r.BodyRaw) != 0 {
				label("carries a %d byte body, want none: %s", len(r.BodyRaw), r.BodyRaw)
			}
			if r.HeaderPresent("Content-Type") {
				label("sets Content-Type %q on a request with no body", r.HeaderValue("Content-Type"))
			}
			continue
		}
		if r.Body == nil {
			label("carries no JSON body, want %s", pretty(w.body))
			continue
		}
		if ct := r.HeaderValue("Content-Type"); ct != "application/json" {
			label("Content-Type is %q, want application/json", ct)
		}
		if !reflect.DeepEqual(r.Body, w.body) {
			label("body is\n%s\nwant\n%s", pretty(r.Body), pretty(w.body))
		}
	}
}

func wantOperations(want []wantRequest) []string {
	out := make([]string, 0, len(want))
	for _, w := range want {
		out = append(out, w.operation)
	}
	return out
}

// fixturePlan reads the shipped plan so the expected bodies carry the plan's own
// values rather than a second copy of them.
func fixturePlan(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(planPath)
	if err != nil {
		t.Fatalf("read %s: %v", planPath, err)
	}
	var plan map[string]any
	if err := json.Unmarshal(raw, &plan); err != nil {
		t.Fatalf("parse %s: %v", planPath, err)
	}
	return plan
}

const (
	opsBinaryURL = "https://depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS/9.1.0.0/vcf-operations-9.1.0.0.ova"
	taskTarget   = "/v1/tasks/" + taskID
)

// minimalCreateBody is the body of an install raised from a plan that sets no
// optional input. The depot published no binary for VCF_AUTOMATION and the plan
// carries no repository certificate, so the optional repository object has
// nothing to hold and does not appear at all -- not as {} and not as null.
//
// It is read by two expectations, because the request that met an expired
// credential and the one that replaced it must be byte for byte the same call.
var minimalCreateBody = map[string]any{
	"componentSpecs": []any{
		map[string]any{
			"deploymentType": "ComponentImportSpec",
			"componentType":  "VCF_AUTOMATION",
			"fqdn":           "auto-a.vcf.example.com",
			"password":       "VMw@re123!Auto",
			"version":        "9.1.0.0",
		},
	},
}

// TestWireShape drives complete runs and checks every request against the shape
// the specification-derived contract calls for.
//
// The credential expires part way through each run. The requests that follow
// must carry the replacement and must not repeat work the service has already
// accepted.
func TestWireShape(t *testing.T) {
	t.Parallel()

	plan := fixturePlan(t)
	depot := plan["depot"].(map[string]any)
	binaryCert := plan["binaryCertificate"].(string)
	components := plan["components"].([]any)
	ops := components[0].(map[string]any)
	auto := components[1].(map[string]any)
	correlationID := plan["correlationId"].(string)

	cases := []struct {
		name string
		// plan is nil for the shipped fixture, otherwise written to a temp file.
		plan      map[string]any
		tokenUses int
		tokens    []string
		want      []wantRequest
	}{
		{
			// The shipped plan sets every optional input, so each one must
			// appear on the wire in its declared place.
			name:      "all optional inputs set",
			tokenUses: 3,
			tokens:    []string{"tok-alpha", "tok-beta", "tok-gamma"},
			want: []wantRequest{
				{
					operation:     "getComponents",
					method:        http.MethodGet,
					target:        "/v1/components?scope=FLEET",
					token:         "tok-alpha",
					status:        http.StatusOK,
					absentHeaders: []string{"X-Correlation-Id"},
				},
				{
					operation:     "resolveDepotComponents",
					method:        http.MethodPost,
					target:        "/v1/depot/components",
					token:         "tok-alpha",
					status:        http.StatusOK,
					absentHeaders: []string{"X-Correlation-Id"},
					body: map[string]any{
						"fleetDepotSpec": map[string]any{
							"fqdn":        depot["fqdn"],
							"certificate": depot["certificate"],
						},
						// The depot version is optional and the plan pins it.
						"version": "9.1.0.0",
						"componentVersions": []any{
							// VCF_OPERATIONS pins a version, VCF_AUTOMATION does
							// not, so the second entry carries no version field
							// at all.
							map[string]any{"component": "VCF_OPERATIONS", "version": "9.1.0.0"},
							map[string]any{"component": "VCF_AUTOMATION"},
						},
					},
				},
				{
					operation: "createComponents",
					method:    http.MethodPost,
					target:    "/v1/components",
					token:     "tok-alpha",
					status:    http.StatusAccepted,
					headers:   map[string]string{"X-Correlation-Id": correlationID},
					body: map[string]any{
						"componentSpecs": []any{
							map[string]any{
								"deploymentType": "ComponentImportSpec",
								"componentType":  "VCF_OPERATIONS",
								"fqdn":           ops["fqdn"],
								"password":       ops["password"],
								"username":       ops["username"],
								"size":           ops["size"],
								"sslThumbprint":  ops["sslThumbprint"],
								"vmId":           ops["vmId"],
								"version":        "9.1.0.0",
								"repository": map[string]any{
									"downloadUrl": opsBinaryURL,
									"certificate": binaryCert,
								},
							},
							map[string]any{
								"deploymentType": "ComponentImportSpec",
								"componentType":  "VCF_AUTOMATION",
								"fqdn":           auto["fqdn"],
								"password":       auto["password"],
								"version":        "9.1.0.0",
								// The depot published no binary for this
								// component, so the repository carries only the
								// certificate.
								"repository": map[string]any{
									"certificate": binaryCert,
								},
							},
						},
					},
				},
				// The credential expires here. The request is not abandoned:
				// it is sent again with the replacement.
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-alpha", status: http.StatusUnauthorized},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-beta", status: http.StatusOK},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-beta", status: http.StatusOK},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-beta", status: http.StatusOK},
				// And again, to show the replacement is not a one-off.
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-beta", status: http.StatusUnauthorized},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-gamma", status: http.StatusOK},
			},
		},
		{
			// A plan that sets no optional input at all. Nothing optional may
			// appear on the wire, in any encoding.
			name:      "no optional inputs set",
			tokenUses: 2,
			tokens:    []string{"tok-alpha", "tok-beta", "tok-gamma", "tok-delta"},
			plan: map[string]any{
				"depot": depotSpec(),
				"components": []any{
					map[string]any{
						"componentType": "VCF_AUTOMATION",
						"fqdn":          "auto-a.vcf.example.com",
						"password":      "VMw@re123!Auto",
					},
				},
			},
			want: []wantRequest{
				{
					// No scope in the plan means no query string at all, not a
					// bare "?" and not an empty scope.
					operation:     "getComponents",
					method:        http.MethodGet,
					target:        "/v1/components",
					token:         "tok-alpha",
					status:        http.StatusOK,
					absentHeaders: []string{"X-Correlation-Id"},
				},
				{
					operation:     "resolveDepotComponents",
					method:        http.MethodPost,
					target:        "/v1/depot/components",
					token:         "tok-alpha",
					status:        http.StatusOK,
					absentHeaders: []string{"X-Correlation-Id"},
					body: map[string]any{
						"fleetDepotSpec":    depotSpec(),
						"componentVersions": []any{map[string]any{"component": "VCF_AUTOMATION"}},
					},
				},
				// The credential expires on the one request that changes the
				// service's state. It must be sent again unchanged, and the
				// install must be raised exactly once.
				{
					operation: "createComponents",
					method:    http.MethodPost,
					target:    "/v1/components",
					token:     "tok-alpha",
					status:    http.StatusUnauthorized,
					// The plan sets no correlation id, so the header the
					// operation declares is not sent.
					absentHeaders: []string{"X-Correlation-Id"},
					body:          minimalCreateBody,
				},
				{
					operation:     "createComponents",
					method:        http.MethodPost,
					target:        "/v1/components",
					token:         "tok-beta",
					status:        http.StatusAccepted,
					absentHeaders: []string{"X-Correlation-Id"},
					body:          minimalCreateBody,
				},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-beta", status: http.StatusOK},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-beta", status: http.StatusUnauthorized},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-gamma", status: http.StatusOK},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-gamma", status: http.StatusOK},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-gamma", status: http.StatusUnauthorized},
				{operation: "getTask", method: http.MethodGet, target: taskTarget, token: "tok-delta", status: http.StatusOK},
			},
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			path := planPath
			if tc.plan != nil {
				path = writePlan(t, tc.plan)
			}
			m := startMock(t, mocklcm.Config{
				Tokens:    tc.tokens,
				TokenUses: tc.tokenUses,
				Task:      mocklcm.DefaultTaskScript(),
			})
			creds := newCredentials(tc.tokens...)
			report, err := runPlan(t, m, path, creds)
			if err != nil {
				t.Fatalf("run: %v", err)
			}
			if report.Outcome != "succeeded" {
				t.Errorf("outcome %q, want succeeded", report.Outcome)
			}
			checkRequests(t, m, tc.want)
			requireNoViolations(t, m)
		})
	}
}

// TestContractDrivesTransport proves that the client reads transport details
// from ContractPath at run time. The canonical document is checked separately;
// this copy deliberately moves every operation so a hard-coded client cannot
// accidentally agree with the mock merely because both use the canonical file.
func TestContractDrivesTransport(t *testing.T) {
	t.Parallel()
	doc := readJSONFile(t, contractPath)
	security := doc["security"].(map[string]any)
	security["httpScheme"] = "Token"
	operationsDoc := doc["operations"].(map[string]any)
	type transport struct {
		method string
		path   string
		status int
	}
	transports := map[string]transport{
		"getComponents":          {method: http.MethodPatch, path: "/runtime/inventory", status: 203},
		"resolveDepotComponents": {method: http.MethodPut, path: "/runtime/resolve", status: 201},
		"createComponents":       {method: http.MethodPut, path: "/runtime/create", status: 202},
		"getTask":                {method: http.MethodPatch, path: "/runtime/jobs/{taskId}", status: 203},
		"retryTask":              {method: http.MethodPatch, path: "/runtime/jobs/{taskId}", status: 203},
	}
	for id, wire := range transports {
		op := operationsDoc[id].(map[string]any)
		op["method"] = wire.method
		op["path"] = wire.path
		op["successStatus"] = wire.status
	}
	operationsDoc["retryTask"].(map[string]any)["fixedQuery"] = map[string]any{"action": "again"}
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatalf("encode moved contract: %v", err)
	}
	movedContract := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(movedContract, raw, 0o644); err != nil {
		t.Fatalf("write moved contract: %v", err)
	}

	m := startMock(t, mocklcm.Config{
		ContractPath: movedContract,
		Tokens:       []string{"tok-alpha"},
		Task:         mocklcm.FailThenSucceedScript(),
	})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	report, err := fleetrun.Run(ctx, fleetrun.Options{
		PlanPath:     writePlan(t, singleComponentPlan()),
		ContractPath: movedContract,
		BaseURL:      m.URL(),
		Credentials:  fleetrun.StaticCredential("tok-alpha"),
		PollInterval: pollInterval,
		PollTimeout:  20 * time.Second,
	})
	if err != nil {
		t.Fatalf("run with moved contract: %v", err)
	}
	if report.Outcome != "succeeded" || report.Task == nil || !report.Task.Retried {
		t.Errorf("run with moved contract did not finish through retry: %s", pretty(viewReport(t, report)))
	}

	wantOperations := []string{
		"getComponents", "resolveDepotComponents", "createComponents",
		"getTask", "getTask", "retryTask", "getTask", "getTask",
	}
	if got := operations(m); !equalStrings(got, wantOperations) {
		t.Fatalf("operations %v, want %v", got, wantOperations)
	}
	for _, request := range m.Requests() {
		wire := transports[request.OperationID]
		if request.Method != wire.method {
			t.Errorf("%s used method %s, want contract method %s", request.OperationID, request.Method, wire.method)
		}
		if request.Status != wire.status {
			t.Errorf("%s accepted status %d, want contract status %d", request.OperationID, request.Status, wire.status)
		}
		if request.Token != "tok-alpha" {
			t.Errorf("%s did not use the contract's Token authorization scheme", request.OperationID)
		}
	}
	if got := only(t, m, "retryTask").Target; got != "/runtime/jobs/"+taskID+"?action=again" {
		t.Errorf("retryTask target %q, want the moved path and fixed query", got)
	}
	requireNoViolations(t, m)
}
