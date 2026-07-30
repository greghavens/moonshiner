package vksapply

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"example.com/vcf91/vksapplytask/internal/contractmock"
)

const (
	testNamespace  = "team blue/edge%?"
	testSupervisor = "supervisor-42"
	testCluster    = "vks +/canary"
	testUID        = "uid-runtime-42"
	testSession    = "session-runtime-DO-NOT-LEAK"
	testToken      = "token-runtime-DO-NOT-LEAK"
	testErrorBody  = `{"server_message":"BODY-DO-NOT-LEAK"}`
)

func baseScenario() contractmock.Scenario {
	return contractmock.Scenario{
		Namespace:  testNamespace,
		Supervisor: testSupervisor,
		Cluster:    testCluster,
		UID:        testUID,
	}
}

func baseRequest() ApplyRequest {
	return ApplyRequest{
		Supervisor:           testSupervisor,
		Namespace:            testNamespace,
		ClusterName:          testCluster,
		FieldManager:         "platform owner/blue",
		ClusterClass:         "builtin-generic-v3.5.0",
		KubernetesVersion:    "v1.33.6+vmware.1-fips",
		VMClass:              "best-effort-medium",
		StorageClass:         "vsan-default",
		ControlPlaneReplicas: 3,
	}
}

func newClientForServer(t *testing.T, server *contractmock.Server) *Client {
	t.Helper()
	client, err := NewClient(Config{
		VCenterURL:      server.URL() + "/",
		KubernetesURL:   server.URL(),
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      server.HTTPClient(),
		Timeout:         3 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func TestApplyWireProjectionTable(t *testing.T) {
	zero := int32(0)
	forceFalse := false
	forceTrue := true
	cases := []struct {
		name      string
		mutate    func(*ApplyRequest)
		wantQuery string
		wantBody  string
	}{
		{
			name: "unset optional fields are absent",
			mutate: func(request *ApplyRequest) {
				request.PodCIDRs = []string{}
				request.ServiceCIDRs = []string{}
			},
			wantQuery: "fieldManager=platform+owner%2Fblue",
			wantBody:  `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?"},"spec":{"topology":{"class":"builtin-generic-v3.5.0","version":"v1.33.6+vmware.1-fips","variables":[{"name":"vmClass","value":"best-effort-medium"},{"name":"storageClass","value":"vsan-default"}],"controlPlane":{"replicas":3}}}}`,
		},
		{
			name: "explicit zero false and one-sided network survive",
			mutate: func(request *ApplyRequest) {
				request.WorkerReplicas = &zero
				request.PodCIDRs = []string{"10.244.0.0/16", "fd00:10:244::/56"}
				request.Force = &forceFalse
			},
			wantQuery: "fieldManager=platform+owner%2Fblue&force=false",
			wantBody:  `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?"},"spec":{"topology":{"class":"builtin-generic-v3.5.0","version":"v1.33.6+vmware.1-fips","variables":[{"name":"vmClass","value":"best-effort-medium"},{"name":"storageClass","value":"vsan-default"}],"controlPlane":{"replicas":3},"workers":{"machineDeployments":[{"class":"node-pool","name":"worker","replicas":0}]}},"clusterNetwork":{"pods":{"cidrBlocks":["10.244.0.0/16","fd00:10:244::/56"]}}}}`,
		},
		{
			name: "both network members and true force preserve order",
			mutate: func(request *ApplyRequest) {
				request.PodCIDRs = []string{"10.244.0.0/16"}
				request.ServiceCIDRs = []string{"10.96.0.0/12", "fd00:10:96::/112"}
				request.Force = &forceTrue
			},
			wantQuery: "fieldManager=platform+owner%2Fblue&force=true",
			wantBody:  `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?"},"spec":{"topology":{"class":"builtin-generic-v3.5.0","version":"v1.33.6+vmware.1-fips","variables":[{"name":"vmClass","value":"best-effort-medium"},{"name":"storageClass","value":"vsan-default"}],"controlPlane":{"replicas":3}},"clusterNetwork":{"pods":{"cidrBlocks":["10.244.0.0/16"]},"services":{"cidrBlocks":["10.96.0.0/12","fd00:10:96::/112"]}}}}`,
		},
	}

	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, baseScenario())
			client := newClientForServer(t, server)
			request := baseRequest()
			test.mutate(&request)

			result, err := client.Apply(context.Background(), request)
			if err != nil {
				t.Fatalf("Apply: %v", err)
			}
			if result != (ApplyResult{
				UID:             testUID,
				ResourceVersion: "1",
				Generation:      1,
				Attempts:        1,
			}) {
				t.Fatalf("Apply result = %#v", result)
			}
			if got := server.EffectCount(); got != 1 {
				t.Fatalf("effect count = %d, want 1", got)
			}

			records := server.Records()
			if len(records) != 2 {
				t.Fatalf("request count = %d, want 2: %#v", len(records), records)
			}
			assertNamespaceWire(t, records[0])
			wantTarget := clusterTarget(request.Namespace, request.ClusterName, test.wantQuery)
			assertApplyWire(t, records[1], wantTarget, test.wantBody)
			assertJSONMembers(
				t,
				records[1].Body,
				request.WorkerReplicas != nil,
				len(request.PodCIDRs) > 0,
				len(request.ServiceCIDRs) > 0,
			)
		})
	}
}

func TestAmbiguousApplyReplayIsByteIdenticalAndNonDuplicating(t *testing.T) {
	scenario := baseScenario()
	scenario.DropApplyCount = 1
	scenario.CommitDroppedApply = true
	server := contractmock.Start(t, scenario)
	client := newClientForServer(t, server)
	request := baseRequest()

	first, err := client.Apply(context.Background(), request)
	if err != nil {
		t.Fatalf("first Apply: %v", err)
	}
	if first.Attempts != 2 {
		t.Fatalf("first Attempts = %d, want 2", first.Attempts)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("effect count after ambiguous replay = %d, want 1", got)
	}

	second, err := client.Apply(context.Background(), request)
	if err != nil {
		t.Fatalf("repeat Apply: %v", err)
	}
	if second.Attempts != 1 {
		t.Fatalf("repeat Attempts = %d, want 1", second.Attempts)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("effect count after whole-call retry = %d, want 1", got)
	}

	records := server.Records()
	if len(records) != 5 {
		t.Fatalf("request count = %d, want 5", len(records))
	}
	for _, index := range []int{0, 3} {
		assertNamespaceWire(t, records[index])
	}
	for _, index := range []int{1, 2, 4} {
		assertApplyWire(t, records[index], records[1].RequestURI, records[1].Body)
	}
	if !reflect.DeepEqual(records[1], records[2]) {
		t.Fatalf("ambiguous replay changed wire bytes:\nfirst:  %#v\nreplay: %#v", records[1], records[2])
	}
	if !reflect.DeepEqual(records[1], records[4]) {
		t.Fatalf("whole-call retry changed apply wire:\nfirst: %#v\nlater: %#v", records[1], records[4])
	}
}

func TestReplayDoesNotRebuildBodyFromMutatedInput(t *testing.T) {
	request := baseRequest()
	request.PodCIDRs = []string{"10.244.0.0/16"}
	transport := &immutableReplayTransport{
		cidrs: request.PodCIDRs,
	}
	client, err := NewClient(Config{
		VCenterURL:      "https://vcenter.example/",
		KubernetesURL:   "https://supervisor.example/",
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      &http.Client{Transport: transport},
		Timeout:         3 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	result, err := client.Apply(context.Background(), request)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if result.Attempts != 2 {
		t.Fatalf("Attempts = %d, want 2", result.Attempts)
	}
	if len(transport.patchBodies) != 2 || transport.patchBodies[0] != transport.patchBodies[1] {
		t.Fatalf("PATCH bodies changed across replay: %#v", transport.patchBodies)
	}
	if strings.Contains(transport.patchBodies[1], "192.168.0.0/16") {
		t.Fatal("replay rebuilt the body from caller-owned slice storage")
	}
}

func TestApplyFailureTable(t *testing.T) {
	cases := []struct {
		name        string
		scenario    func() contractmock.Scenario
		mutate      func(*ApplyRequest)
		wantRecords int
		check       func(*testing.T, error)
	}{
		{
			name: "recognized namespace state is not ready",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.NamespaceConfigStatus = "CONFIGURING"
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				var target *NamespaceNotReadyError
				if !errors.As(err, &target) || target.ConfigStatus != "CONFIGURING" {
					t.Fatalf("error = %#v, want NamespaceNotReadyError(CONFIGURING)", err)
				}
			},
		},
		{
			name: "removing namespace is not ready",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.NamespaceConfigStatus = "REMOVING"
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				var target *NamespaceNotReadyError
				if !errors.As(err, &target) || target.ConfigStatus != "REMOVING" {
					t.Fatalf("error = %#v, want NamespaceNotReadyError(REMOVING)", err)
				}
			},
		},
		{
			name: "namespace error state is not ready",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.NamespaceConfigStatus = "ERROR"
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				var target *NamespaceNotReadyError
				if !errors.As(err, &target) || target.ConfigStatus != "ERROR" {
					t.Fatalf("error = %#v, want NamespaceNotReadyError(ERROR)", err)
				}
			},
		},
		{
			name: "unknown namespace state is protocol failure",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.NamespaceConfigStatus = "READY"
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				assertProtocolOperation(t, err, NamespaceGetOperation)
			},
		},
		{
			name:     "supervisor mismatch is protocol failure",
			scenario: baseScenario,
			mutate: func(request *ApplyRequest) {
				request.Supervisor = "another-supervisor"
			},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				assertProtocolOperation(t, err, NamespaceGetOperation)
			},
		},
		{
			name: "vcenter HTTP failure is not retried",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.NamespaceHTTPStatus = http.StatusServiceUnavailable
				value.ErrorBody = testErrorBody
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				assertAPIError(t, err, NamespaceGetOperation, http.StatusServiceUnavailable)
			},
		},
		{
			name: "vcenter non-200 success class is rejected",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.NamespaceHTTPStatus = http.StatusCreated
				value.ErrorBody = testErrorBody
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 1,
			check: func(t *testing.T, err error) {
				assertAPIError(t, err, NamespaceGetOperation, http.StatusCreated)
			},
		},
		{
			name: "kubernetes HTTP failure is not retried",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.ApplyHTTPStatus = http.StatusInternalServerError
				value.ErrorBody = testErrorBody
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 2,
			check: func(t *testing.T, err error) {
				assertAPIError(t, err, ClusterApplyOperation, http.StatusInternalServerError)
			},
		},
		{
			name: "kubernetes non-200 success class is rejected",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.ApplyHTTPStatus = http.StatusCreated
				value.ErrorBody = testErrorBody
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 2,
			check: func(t *testing.T, err error) {
				assertAPIError(t, err, ClusterApplyOperation, http.StatusCreated)
			},
		},
		{
			name: "malformed apply success is not retried",
			scenario: func() contractmock.Scenario {
				value := baseScenario()
				value.ApplyResponse = `{"apiVersion":"wrong","kind":"Cluster","metadata":{}}`
				return value
			},
			mutate:      func(*ApplyRequest) {},
			wantRecords: 2,
			check: func(t *testing.T, err error) {
				assertProtocolOperation(t, err, ClusterApplyOperation)
			},
		},
	}

	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, test.scenario())
			client := newClientForServer(t, server)
			request := baseRequest()
			test.mutate(&request)
			_, err := client.Apply(context.Background(), request)
			if err == nil {
				t.Fatal("Apply unexpectedly succeeded")
			}
			test.check(t, err)
			assertRedacted(t, err)
			if got := len(server.Records()); got != test.wantRecords {
				t.Fatalf("request count = %d, want %d", got, test.wantRecords)
			}
		})
	}
}

func TestApplyResponseValidationTable(t *testing.T) {
	cases := []struct {
		name     string
		response string
	}{
		{"malformed JSON", `not-json`},
		{"wrong apiVersion", `{"apiVersion":"wrong","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":"uid","resourceVersion":"1","generation":1}}`},
		{"wrong kind", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Wrong","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":"uid","resourceVersion":"1","generation":1}}`},
		{"wrong name", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"wrong","namespace":"team blue/edge%?","uid":"uid","resourceVersion":"1","generation":1}}`},
		{"wrong namespace", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"wrong","uid":"uid","resourceVersion":"1","generation":1}}`},
		{"blank uid", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":" ","resourceVersion":"1","generation":1}}`},
		{"blank resourceVersion", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":"uid","resourceVersion":"","generation":1}}`},
		{"missing generation", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":"uid","resourceVersion":"1"}}`},
		{"zero generation", `{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":"uid","resourceVersion":"1","generation":0}}`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			scenario := baseScenario()
			scenario.ApplyResponse = test.response
			server := contractmock.Start(t, scenario)
			client := newClientForServer(t, server)

			_, err := client.Apply(context.Background(), baseRequest())
			assertProtocolOperation(t, err, ClusterApplyOperation)
			assertRedacted(t, err)
			if got := len(server.Records()); got != 2 {
				t.Fatalf("request count = %d, want 2", got)
			}
		})
	}
}

func TestAPIErrorPreservesLargeResponseBody(t *testing.T) {
	const prefix = "LARGE-BODY-START"
	const suffix = "LARGE-BODY-END"
	body := prefix + strings.Repeat("x", (4<<20)+1) + suffix
	scenario := baseScenario()
	scenario.NamespaceHTTPStatus = http.StatusServiceUnavailable
	scenario.ErrorBody = body
	server := contractmock.Start(t, scenario)
	client := newClientForServer(t, server)

	_, err := client.Apply(context.Background(), baseRequest())
	var apiError *APIError
	if !errors.As(err, &apiError) ||
		apiError.Operation != NamespaceGetOperation ||
		apiError.StatusCode != http.StatusServiceUnavailable ||
		string(apiError.Body) != body {
		t.Fatalf("error = %#v, want APIError with complete %d-byte body", err, len(body))
	}
	formatted := fmt.Sprintf("%s | %v | %+v | %#v | value=%#v", err, err, err, err, *apiError)
	if strings.Contains(formatted, prefix) || strings.Contains(formatted, suffix) {
		t.Fatalf("formatted APIError leaked its response body markers: %s", formatted)
	}
	if got := len(server.Records()); got != 1 {
		t.Fatalf("request count = %d, want 1", got)
	}
}

func TestMalformedNamespaceSuccessTable(t *testing.T) {
	cases := []struct {
		name string
		body string
	}{
		{"malformed JSON", `not-json`},
		{"null", `null`},
		{"array", `[]`},
		{"missing supervisor", `{"config_status":"RUNNING"}`},
		{"missing config status", `{"supervisor":"supervisor-42"}`},
		{"blank supervisor", `{"supervisor":" ","config_status":"RUNNING"}`},
		{"blank config status", `{"supervisor":"supervisor-42","config_status":" "}`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			transport := &namespaceResponseTransport{body: test.body}
			client, err := NewClient(Config{
				VCenterURL:      "https://vcenter.example/",
				KubernetesURL:   "https://supervisor.example/",
				VCenterSession:  testSession,
				KubernetesToken: testToken,
				HTTPClient:      &http.Client{Transport: transport},
				Timeout:         3 * time.Second,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			_, err = client.Apply(context.Background(), baseRequest())
			assertProtocolOperation(t, err, NamespaceGetOperation)
			assertRedacted(t, err)
			if transport.calls != 1 {
				t.Fatalf("transport calls = %d, want 1", transport.calls)
			}
		})
	}
}

func TestApplyStopsAfterOneTransportReplay(t *testing.T) {
	scenario := baseScenario()
	scenario.DropApplyCount = 2
	scenario.CommitDroppedApply = true
	server := contractmock.Start(t, scenario)
	client := newClientForServer(t, server)

	_, err := client.Apply(context.Background(), baseRequest())
	var transportError *TransportError
	if !errors.As(err, &transportError) || transportError.Operation != ClusterApplyOperation {
		t.Fatalf("error = %#v, want ClusterApplyOperation TransportError", err)
	}
	assertRedacted(t, err)
	if got := len(server.Records()); got != 3 {
		t.Fatalf("request count = %d, want namespace GET plus two PATCH attempts", got)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("effect count = %d, want 1", got)
	}
}

func TestValidationBeforeTrafficTable(t *testing.T) {
	scenario := baseScenario()
	server := contractmock.Start(t, scenario)
	client := newClientForServer(t, server)
	negative := int32(-1)

	cases := []struct {
		name   string
		ctx    context.Context
		mutate func(*ApplyRequest)
	}{
		{"nil context", nil, func(*ApplyRequest) {}},
		{"blank supervisor", context.Background(), func(r *ApplyRequest) { r.Supervisor = " " }},
		{"blank namespace", context.Background(), func(r *ApplyRequest) { r.Namespace = "" }},
		{"blank cluster", context.Background(), func(r *ApplyRequest) { r.ClusterName = "\t" }},
		{"blank field manager", context.Background(), func(r *ApplyRequest) { r.FieldManager = "" }},
		{"blank cluster class", context.Background(), func(r *ApplyRequest) { r.ClusterClass = " " }},
		{"blank kubernetes version", context.Background(), func(r *ApplyRequest) { r.KubernetesVersion = "" }},
		{"blank vm class", context.Background(), func(r *ApplyRequest) { r.VMClass = "\t" }},
		{"blank storage class", context.Background(), func(r *ApplyRequest) { r.StorageClass = "" }},
		{"zero control plane", context.Background(), func(r *ApplyRequest) { r.ControlPlaneReplicas = 0 }},
		{"negative control plane", context.Background(), func(r *ApplyRequest) { r.ControlPlaneReplicas = -1 }},
		{"negative workers", context.Background(), func(r *ApplyRequest) { r.WorkerReplicas = &negative }},
		{"empty pod cidr member", context.Background(), func(r *ApplyRequest) { r.PodCIDRs = []string{"10.0.0.0/8", ""} }},
		{"blank service cidr member", context.Background(), func(r *ApplyRequest) { r.ServiceCIDRs = []string{" "} }},
		{"invalid pod cidr", context.Background(), func(r *ApplyRequest) { r.PodCIDRs = []string{"10.0.0.1"} }},
		{"invalid service cidr", context.Background(), func(r *ApplyRequest) { r.ServiceCIDRs = []string{"not-a-cidr"} }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			request := baseRequest()
			test.mutate(&request)
			_, err := client.Apply(test.ctx, request)
			var validationError *ValidationError
			if !errors.As(err, &validationError) {
				t.Fatalf("error = %#v, want ValidationError", err)
			}
		})
	}
	if got := len(server.Records()); got != 0 {
		t.Fatalf("invalid calls made %d requests", got)
	}
}

func TestNewClientValidationTable(t *testing.T) {
	valid := Config{
		VCenterURL:      "https://vcenter.example/",
		KubernetesURL:   "https://supervisor.example",
		VCenterSession:  "session",
		KubernetesToken: "token",
		Timeout:         time.Second,
	}
	cases := []struct {
		name   string
		mutate func(*Config)
	}{
		{"vcenter relative", func(c *Config) { c.VCenterURL = "/api" }},
		{"vcenter path", func(c *Config) { c.VCenterURL = "https://vcenter.example/api" }},
		{"vcenter userinfo", func(c *Config) { c.VCenterURL = "https://user@vcenter.example/" }},
		{"vcenter query", func(c *Config) { c.VCenterURL = "https://vcenter.example/?x=1" }},
		{"vcenter bare query", func(c *Config) { c.VCenterURL = "https://vcenter.example/?" }},
		{"vcenter fragment", func(c *Config) { c.VCenterURL = "https://vcenter.example/#x" }},
		{"vcenter bare fragment", func(c *Config) { c.VCenterURL = "https://vcenter.example/#" }},
		{"vcenter empty hostname", func(c *Config) { c.VCenterURL = "https://:443/" }},
		{"vcenter non-http", func(c *Config) { c.VCenterURL = "ftp://vcenter.example/" }},
		{"kubernetes relative", func(c *Config) { c.KubernetesURL = "/api" }},
		{"kubernetes path", func(c *Config) { c.KubernetesURL = "https://supervisor.example/api" }},
		{"kubernetes userinfo", func(c *Config) { c.KubernetesURL = "https://user@supervisor.example/" }},
		{"kubernetes query", func(c *Config) { c.KubernetesURL = "https://supervisor.example/?x=1" }},
		{"kubernetes bare query", func(c *Config) { c.KubernetesURL = "https://supervisor.example/?" }},
		{"kubernetes fragment", func(c *Config) { c.KubernetesURL = "https://supervisor.example/#x" }},
		{"kubernetes bare fragment", func(c *Config) { c.KubernetesURL = "https://supervisor.example/#" }},
		{"kubernetes empty hostname", func(c *Config) { c.KubernetesURL = "https://:443/" }},
		{"kubernetes non-http", func(c *Config) { c.KubernetesURL = "ftp://supervisor.example/" }},
		{"blank session", func(c *Config) { c.VCenterSession = " " }},
		{"unsafe session", func(c *Config) { c.VCenterSession = "ok\rbad" }},
		{"unsafe session newline", func(c *Config) { c.VCenterSession = "ok\nbad" }},
		{"blank token", func(c *Config) { c.KubernetesToken = "" }},
		{"unsafe token", func(c *Config) { c.KubernetesToken = "ok\nbad" }},
		{"unsafe token carriage return", func(c *Config) { c.KubernetesToken = "ok\rbad" }},
		{"zero timeout", func(c *Config) { c.Timeout = 0 }},
		{"negative timeout", func(c *Config) { c.Timeout = -time.Second }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			config := valid
			test.mutate(&config)
			client, err := NewClient(config)
			if client != nil {
				t.Fatalf("client = %#v, want nil", client)
			}
			var validationError *ValidationError
			if !errors.As(err, &validationError) {
				t.Fatalf("error = %#v, want ValidationError", err)
			}
		})
	}
}

func TestNewClientAcceptsCaseInsensitiveHTTPSchemes(t *testing.T) {
	client, err := NewClient(Config{
		VCenterURL:      "HTTP://vcenter.example/",
		KubernetesURL:   "hTtPs://supervisor.example",
		VCenterSession:  "session",
		KubernetesToken: "token",
		Timeout:         time.Second,
	})
	if err != nil || client == nil {
		t.Fatalf("NewClient = %#v, %v; want valid client", client, err)
	}
}

func TestDefaultClientIsCopiedAndConfiguredTimeoutIsApplied(t *testing.T) {
	savedDefault := *http.DefaultClient
	t.Cleanup(func() {
		*http.DefaultClient = savedDefault
	})

	const configuredTimeout = 10 * time.Second
	transport := &deadlineCheckingTransport{
		t:      t,
		want:   configuredTimeout,
		status: http.StatusFound,
	}
	originalRedirect := func(*http.Request, []*http.Request) error {
		return nil
	}
	http.DefaultClient.Transport = transport
	http.DefaultClient.Timeout = 23 * time.Second
	http.DefaultClient.CheckRedirect = originalRedirect

	client, err := NewClient(Config{
		VCenterURL:      "https://vcenter.example/",
		KubernetesURL:   "https://supervisor.example/",
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		Timeout:         configuredTimeout,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if transport.calls != 0 {
		t.Fatalf("NewClient made %d transport calls", transport.calls)
	}
	if http.DefaultClient.Timeout != 23*time.Second ||
		reflect.ValueOf(http.DefaultClient.CheckRedirect).Pointer() != reflect.ValueOf(originalRedirect).Pointer() {
		t.Fatal("NewClient mutated http.DefaultClient")
	}

	// Mutating the caller-owned default after construction must not alter the
	// independent client copy.
	http.DefaultClient.Timeout = time.Nanosecond
	_, err = client.Apply(context.Background(), baseRequest())
	assertAPIError(t, err, NamespaceGetOperation, http.StatusFound)
	if transport.calls != 1 {
		t.Fatalf("transport calls = %d, want 1", transport.calls)
	}
}

func TestProvidedClientConfiguredTimeoutIsApplied(t *testing.T) {
	const configuredTimeout = 10 * time.Second
	transport := &deadlineCheckingTransport{
		t:      t,
		want:   configuredTimeout,
		status: http.StatusServiceUnavailable,
	}
	caller := &http.Client{
		Transport: transport,
		Timeout:   23 * time.Second,
	}
	client, err := NewClient(Config{
		VCenterURL:      "https://vcenter.example/",
		KubernetesURL:   "https://supervisor.example/",
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      caller,
		Timeout:         configuredTimeout,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if caller.Timeout != 23*time.Second {
		t.Fatal("NewClient mutated the provided http.Client timeout")
	}

	caller.Timeout = time.Nanosecond
	_, err = client.Apply(context.Background(), baseRequest())
	assertAPIError(t, err, NamespaceGetOperation, http.StatusServiceUnavailable)
	if transport.calls != 1 {
		t.Fatalf("transport calls = %d, want 1", transport.calls)
	}
}

func TestNamespaceTransportFailureIsNotRetried(t *testing.T) {
	sentinel := errors.New("namespace-transport-sentinel")
	transport := &countingFailureTransport{err: sentinel}
	client, err := NewClient(Config{
		VCenterURL:      "https://vcenter.example/",
		KubernetesURL:   "https://supervisor.example/",
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      &http.Client{Transport: transport},
		Timeout:         3 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	_, err = client.Apply(context.Background(), baseRequest())
	var transportError *TransportError
	if !errors.As(err, &transportError) || transportError.Operation != NamespaceGetOperation {
		t.Fatalf("error = %#v, want NamespaceGetOperation TransportError", err)
	}
	assertRedacted(t, err)
	formatted := fmt.Sprintf("%s | %v | %+v | %#v | value=%#v", err, err, err, err, *transportError)
	if strings.Contains(formatted, sentinel.Error()) {
		t.Fatalf("formatted TransportError leaked lower-level text: %s", formatted)
	}
	if transport.calls != 1 {
		t.Fatalf("namespace transport calls = %d, want 1", transport.calls)
	}
}

func TestClientCopyAndRedirectRefusal(t *testing.T) {
	scenario := baseScenario()
	scenario.NamespaceHTTPStatus = http.StatusFound
	server := contractmock.Start(t, scenario)
	caller := server.HTTPClient()
	originalTimeout := caller.Timeout
	originalRedirect := caller.CheckRedirect

	client, err := NewClient(Config{
		VCenterURL:      server.URL(),
		KubernetesURL:   server.URL(),
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      caller,
		Timeout:         1700 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if caller.Timeout != originalTimeout || reflect.ValueOf(caller.CheckRedirect).Pointer() != reflect.ValueOf(originalRedirect).Pointer() {
		t.Fatal("NewClient mutated caller-owned http.Client")
	}
	caller.CheckRedirect = func(*http.Request, []*http.Request) error {
		return nil
	}

	_, err = client.Apply(context.Background(), baseRequest())
	assertAPIError(t, err, NamespaceGetOperation, http.StatusFound)
	if got := len(server.Records()); got != 1 {
		t.Fatalf("redirect generated %d requests, want 1", got)
	}
}

func TestCanceledContextAndDeadlineRemainDiscoverable(t *testing.T) {
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	expired, expire := context.WithDeadline(context.Background(), time.Unix(1, 0))
	defer expire()

	cases := []struct {
		name string
		ctx  context.Context
		want error
	}{
		{"canceled", canceled, context.Canceled},
		{"deadline", expired, context.DeadlineExceeded},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, baseScenario())
			client := newClientForServer(t, server)

			_, err := client.Apply(test.ctx, baseRequest())
			if !errors.Is(err, test.want) {
				t.Fatalf("errors.Is(error, %v) = false: %#v", test.want, err)
			}
			assertRedacted(t, err)
			if got := len(server.Records()); got != 0 {
				t.Fatalf("pre-terminated call made %d requests", got)
			}
		})
	}
}

func TestMockRejectsUnnamedOperation(t *testing.T) {
	server := contractmock.Start(t, baseScenario())
	request, err := http.NewRequest(http.MethodGet, server.URL()+"/not-in-contract", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.HTTPClient().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", response.StatusCode)
	}
	records := server.Records()
	if len(records) != 1 || records[0].ContractName != "" || records[0].Operation != "" {
		t.Fatalf("unexpected unnamed-operation record: %#v", records)
	}
}

func TestOfficialSourcePin(t *testing.T) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve test path")
	}
	data, err := os.ReadFile(filepath.Join(filepath.Dir(currentFile), "..", "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var source struct {
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		SpecBlobSHA         string   `json:"specBlobSha"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"specPathItem"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(data, &source); err != nil {
		t.Fatal(err)
	}
	if source.RepositoryCommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" ||
		source.SpecPath != "specifications/vsphere/openapi/automation/vcenter.yaml" ||
		source.SpecBlobSHA != "8028b0824c4ff3503d05f44814f967938a795c40" ||
		!reflect.DeepEqual(source.OperationIDs, []string{NamespaceGetOperation}) ||
		len(source.Operations) != 1 ||
		source.Operations[0].OperationID != NamespaceGetOperation ||
		source.Operations[0].Method != http.MethodGet ||
		source.Operations[0].Path != "/vcenter/namespaces/instances/v2/{namespace}" {
		t.Fatalf("official source pin changed: %#v", source)
	}
}

func assertNamespaceWire(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	wantTarget := "/api/vcenter/namespaces/instances/v2/" + url.PathEscape(testNamespace)
	if record.ContractName != "getSupervisorNamespace" ||
		record.Operation != NamespaceGetOperation ||
		record.Method != http.MethodGet ||
		record.RequestURI != wantTarget ||
		record.Body != "" ||
		record.ContentLength != 0 {
		t.Fatalf("namespace wire mismatch: %#v", record)
	}
	assertSingleHeader(t, record, "Accept", "application/json")
	assertSingleHeader(t, record, "vmware-api-session-id", testSession)
	assertHeaderAbsent(t, record, "Authorization")
	assertHeaderAbsent(t, record, "Content-Type")
	assertHeaderAbsent(t, record, "Content-Length")
}

func assertApplyWire(t *testing.T, record contractmock.RequestRecord, wantTarget, wantBody string) {
	t.Helper()
	if record.ContractName != "applyVksCluster" ||
		record.Operation != ClusterApplyOperation ||
		record.Method != http.MethodPatch ||
		record.RequestURI != wantTarget ||
		record.Body != wantBody ||
		record.ContentLength != int64(len(wantBody)) {
		t.Fatalf("apply wire mismatch:\n got: %#v\nbody: %s\nwant target: %s\nwant body: %s", record, record.Body, wantTarget, wantBody)
	}
	assertSingleHeader(t, record, "Accept", "application/json")
	assertSingleHeader(t, record, "Authorization", "Bearer "+testToken)
	assertSingleHeader(t, record, "Content-Type", "application/apply-patch+yaml")
	assertHeaderAbsent(t, record, "vmware-api-session-id")

	parsed, err := url.ParseRequestURI(record.RequestURI)
	if err != nil {
		t.Fatalf("parse request URI: %v", err)
	}
	query := parsed.Query()
	if len(query["fieldManager"]) != 1 {
		t.Fatalf("fieldManager cardinality = %d, want 1", len(query["fieldManager"]))
	}
	for _, field := range []string{"dryRun", "fieldValidation", "pretty"} {
		if _, exists := query[field]; exists {
			t.Fatalf("unset optional query %q was present", field)
		}
	}
}

func assertJSONMembers(t *testing.T, body string, wantWorkers, wantPods, wantServices bool) {
	t.Helper()
	var document map[string]any
	if err := json.Unmarshal([]byte(body), &document); err != nil {
		t.Fatalf("decode apply body: %v", err)
	}
	metadata := document["metadata"].(map[string]any)
	for _, forbidden := range []string{"uid", "resourceVersion", "generation", "labels", "annotations"} {
		if _, exists := metadata[forbidden]; exists {
			t.Errorf("metadata.%s must be omitted", forbidden)
		}
	}
	spec := document["spec"].(map[string]any)
	topology := spec["topology"].(map[string]any)
	_, workers := topology["workers"]
	if workers != wantWorkers {
		t.Errorf("workers present = %v, want %v", workers, wantWorkers)
	}
	network, hasNetwork := spec["clusterNetwork"].(map[string]any)
	if !wantPods && !wantServices {
		if hasNetwork {
			t.Error("unset clusterNetwork was present")
		}
		return
	}
	if !hasNetwork {
		t.Fatal("clusterNetwork was omitted")
	}
	_, pods := network["pods"]
	_, services := network["services"]
	if pods != wantPods || services != wantServices {
		t.Errorf("network members pods=%v services=%v, want %v/%v", pods, services, wantPods, wantServices)
	}
}

func assertSingleHeader(t *testing.T, record contractmock.RequestRecord, name, want string) {
	t.Helper()
	values := record.Header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Fatalf("%s values = %#v, want [%q]", name, values, want)
	}
}

func assertHeaderAbsent(t *testing.T, record contractmock.RequestRecord, name string) {
	t.Helper()
	if values := record.Header.Values(name); len(values) != 0 {
		t.Fatalf("%s unexpectedly present: %#v", name, values)
	}
}

func assertAPIError(t *testing.T, err error, operation string, status int) {
	t.Helper()
	var apiError *APIError
	if !errors.As(err, &apiError) ||
		apiError.Operation != operation ||
		apiError.StatusCode != status {
		t.Fatalf("error = %#v, want APIError(%s, %d)", err, operation, status)
	}
	if string(apiError.Body) != testErrorBody && status != http.StatusFound {
		t.Fatalf("APIError.Body = %q, want fixture body", apiError.Body)
	}
}

func assertProtocolOperation(t *testing.T, err error, operation string) {
	t.Helper()
	var protocolError *ProtocolError
	if !errors.As(err, &protocolError) || protocolError.Operation != operation {
		t.Fatalf("error = %#v, want ProtocolError(%s)", err, operation)
	}
}

func assertRedacted(t *testing.T, err error) {
	t.Helper()
	formatted := fmt.Sprintf("%s | %v | %+v | %#v", err, err, err, err)
	for _, secret := range []string{testSession, testToken, testErrorBody, "BODY-DO-NOT-LEAK"} {
		if strings.Contains(formatted, secret) {
			t.Fatalf("formatted error leaked %q: %s", secret, formatted)
		}
	}
}

func clusterTarget(namespace, cluster, rawQuery string) string {
	return "/apis/cluster.x-k8s.io/v1beta2/namespaces/" +
		url.PathEscape(namespace) + "/clusters/" + url.PathEscape(cluster) + "?" + rawQuery
}

type deadlineCheckingTransport struct {
	t      testing.TB
	want   time.Duration
	status int
	calls  int
}

func (transport *deadlineCheckingTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	transport.calls++
	deadline, ok := request.Context().Deadline()
	if !ok {
		transport.t.Error("configured http.Client timeout did not set a request deadline")
	} else {
		remaining := time.Until(deadline)
		if remaining <= transport.want-time.Second || remaining > transport.want {
			transport.t.Errorf("request deadline remaining = %v, want approximately %v", remaining, transport.want)
		}
	}
	header := make(http.Header)
	if transport.status >= 300 && transport.status < 400 {
		header.Set("Location", "/redirected")
	}
	return &http.Response{
		StatusCode: transport.status,
		Header:     header,
		Body:       io.NopCloser(strings.NewReader(testErrorBody)),
		Request:    request,
	}, nil
}

type countingFailureTransport struct {
	err   error
	calls int
}

func (transport *countingFailureTransport) RoundTrip(*http.Request) (*http.Response, error) {
	transport.calls++
	return nil, transport.err
}

type namespaceResponseTransport struct {
	body  string
	calls int
}

func (transport *namespaceResponseTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	transport.calls++
	return jsonResponse(request, http.StatusOK, transport.body), nil
}

type immutableReplayTransport struct {
	cidrs       []string
	patchBodies []string
}

func (transport *immutableReplayTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	switch request.Method {
	case http.MethodGet:
		body := `{"supervisor":"supervisor-42","config_status":"RUNNING"}`
		return jsonResponse(request, http.StatusOK, body), nil
	case http.MethodPatch:
		body, err := io.ReadAll(request.Body)
		if err != nil {
			return nil, err
		}
		transport.patchBodies = append(transport.patchBodies, string(body))
		if len(transport.patchBodies) == 1 {
			transport.cidrs[0] = "192.168.0.0/16"
			return nil, errors.New("ambiguous apply response loss")
		}
		body = []byte(`{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":"vks +/canary","namespace":"team blue/edge%?","uid":"uid-runtime-42","resourceVersion":"1","generation":1}}`)
		return jsonResponse(request, http.StatusOK, string(body)), nil
	default:
		return jsonResponse(request, http.StatusNotFound, `{}`), nil
	}
}

func jsonResponse(request *http.Request, status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(body)),
		Request:    request,
	}
}
