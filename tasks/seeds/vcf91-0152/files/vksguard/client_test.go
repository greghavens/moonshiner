package vksguard_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"go/parser"
	"go/token"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"

	"vcf91-0152/internal/contractmock"
	"vcf91-0152/vksguard"
)

const (
	commitSHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	specBlob  = "8028b0824c4ff3503d05f44814f967938a795c40"
	specPath  = "specifications/vsphere/openapi/automation/vcenter.yaml"

	namespaceOperationID = "Vcenter.Namespaces.Instances_getV2"
	clusterOperationKey  = "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch"
)

func TestBlockedPrecheckStatusesNeverMutate(t *testing.T) {
	tests := []struct {
		name   string
		status string
	}{
		{name: "configuration in progress", status: "CONFIGURING"},
		{name: "namespace removal in progress", status: "REMOVING"},
		{name: "configuration error", status: "ERROR"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			scenario := runtimeScenario(t, tt.status, true)
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			before := server.Snapshot()
			result, err := client.ReconcileVersion(
				context.Background(),
				scenario.Supervisor,
				scenario.Namespace,
				scenario.ClusterName,
				scenario.TargetVersion,
			)
			if err != nil {
				t.Fatalf("ReconcileVersion: %v", err)
			}
			want := vksguard.Result{
				Status:  "Blocked",
				Changed: false,
				Precheck: vksguard.PrecheckResult{
					OperationID:  namespaceOperationID,
					Passed:       false,
					ConfigStatus: tt.status,
				},
				Mutation: vksguard.MutationResult{
					OperationKey: clusterOperationKey,
					Attempted:    false,
				},
			}
			if !reflect.DeepEqual(result, want) {
				t.Fatalf("result = %#v, want %#v", result, want)
			}

			after := server.Snapshot()
			if before.ClusterVersion != scenario.OldVersion ||
				after.ClusterVersion != scenario.OldVersion {
				t.Fatalf(
					"blocked precheck changed Cluster: before=%#v after=%#v",
					before,
					after,
				)
			}
			if after.PrecheckRequests != 1 || after.PatchAttempts != 0 {
				t.Fatalf(
					"blocked call counts = %#v, want one GET and zero PATCH",
					after,
				)
			}
			requests := server.Requests()
			if len(requests) != 1 {
				t.Fatalf("blocked request count = %d, want 1", len(requests))
			}
			assertPrecheckWire(t, requests[0], scenario, server.URL())
		})
	}
}

func TestRunningPrecheckPatchesExactWireShape(t *testing.T) {
	tests := []struct {
		name       string
		opaqueText bool
	}{
		{name: "ordinary identifiers", opaqueText: false},
		{name: "reserved and UTF-8 identifiers", opaqueText: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			scenario := runtimeScenario(t, "RUNNING", tt.opaqueText)
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			result, err := client.ReconcileVersion(
				context.Background(),
				scenario.Supervisor,
				scenario.Namespace,
				scenario.ClusterName,
				scenario.TargetVersion,
			)
			if err != nil {
				t.Fatalf("ReconcileVersion: %v", err)
			}
			want := vksguard.Result{
				Status:  "Succeeded",
				Changed: true,
				Precheck: vksguard.PrecheckResult{
					OperationID:  namespaceOperationID,
					Passed:       true,
					ConfigStatus: "RUNNING",
				},
				Mutation: vksguard.MutationResult{
					OperationKey: clusterOperationKey,
					Attempted:    true,
				},
			}
			if !reflect.DeepEqual(result, want) {
				t.Fatalf("result = %#v, want %#v", result, want)
			}
			state := server.Snapshot()
			if state.ClusterVersion != scenario.TargetVersion ||
				state.PrecheckRequests != 1 ||
				state.PatchAttempts != 1 {
				t.Fatalf("successful state = %#v", state)
			}

			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("successful request count = %d, want 2", len(requests))
			}
			assertPrecheckWire(t, requests[0], scenario, server.URL())
			assertPatchWire(t, requests[1], scenario, server.URL())
		})
	}
}

func TestPrecheckRejectionsAreTableDriven(t *testing.T) {
	tests := []struct {
		name       string
		mutate     func(*contractmock.Scenario)
		assertType func(error) bool
	}{
		{
			name: "Supervisor mismatch",
			mutate: func(s *contractmock.Scenario) {
				s.ReportedSupervisor = "other-" + runtimeSuffix(t)
			},
			assertType: func(err error) bool {
				var target *vksguard.PrecheckError
				return errors.As(err, &target)
			},
		},
		{
			name: "unknown config status",
			mutate: func(s *contractmock.Scenario) {
				s.ConfigStatus = "READY_" + runtimeSuffix(t)
			},
			assertType: func(err error) bool {
				var target *vksguard.ProtocolError
				return errors.As(err, &target)
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			scenario := runtimeScenario(t, "RUNNING", true)
			tt.mutate(&scenario)
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			_, err := client.ReconcileVersion(
				context.Background(),
				scenario.Supervisor,
				scenario.Namespace,
				scenario.ClusterName,
				scenario.TargetVersion,
			)
			if err == nil || !tt.assertType(err) {
				t.Fatalf("error = %T %v, want protected typed rejection", err, err)
			}
			if strings.Contains(err.Error(), scenario.VCenterSessionID) ||
				strings.Contains(err.Error(), scenario.KubernetesToken) {
				t.Fatal("precheck error disclosed a credential")
			}
			state := server.Snapshot()
			if state.ClusterVersion != scenario.OldVersion ||
				state.PrecheckRequests != 1 ||
				state.PatchAttempts != 0 {
				t.Fatalf("rejected precheck state = %#v", state)
			}
			if got := len(server.Requests()); got != 1 {
				t.Fatalf("rejected precheck made %d requests, want 1", got)
			}
		})
	}
}

func TestHTTPFailuresAreTypedRedactedAndDoNotFollowRedirects(t *testing.T) {
	tests := []struct {
		name      string
		operation string
		status    int
		requests  int
		patches   int
	}{
		{
			name:      "vCenter authorization failure",
			operation: contractmock.OperationNamespaceGet,
			status:    http.StatusForbidden,
			requests:  1,
			patches:   0,
		},
		{
			name:      "vCenter redirect is a failure",
			operation: contractmock.OperationNamespaceGet,
			status:    http.StatusFound,
			requests:  1,
			patches:   0,
		},
		{
			name:      "Kubernetes conflict",
			operation: contractmock.OperationClusterPatch,
			status:    http.StatusConflict,
			requests:  2,
			patches:   1,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			scenario := runtimeScenario(t, "RUNNING", true)
			scenario.FailOperation = tt.operation
			scenario.FailStatus = tt.status
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			_, err := client.ReconcileVersion(
				context.Background(),
				scenario.Supervisor,
				scenario.Namespace,
				scenario.ClusterName,
				scenario.TargetVersion,
			)
			var apiError *vksguard.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiError.StatusCode != tt.status {
				t.Fatalf("APIError status = %d, want %d", apiError.StatusCode, tt.status)
			}
			wantOperation := contractmock.OperationNamespaceGet
			if tt.operation == contractmock.OperationClusterPatch {
				wantOperation = contractmock.OperationClusterPatch
			}
			if apiError.Operation != wantOperation {
				t.Fatalf(
					"APIError operation = %q, want %q",
					apiError.Operation,
					wantOperation,
				)
			}
			if strings.Contains(err.Error(), scenario.VCenterSessionID) ||
				strings.Contains(err.Error(), scenario.KubernetesToken) ||
				strings.Contains(err.Error(), "runtime_failure") {
				t.Fatal("APIError disclosed response content or a credential")
			}
			if got := len(server.Requests()); got != tt.requests {
				t.Fatalf("request count = %d, want %d", got, tt.requests)
			}
			state := server.Snapshot()
			if state.PatchAttempts != tt.patches ||
				state.ClusterVersion != scenario.OldVersion {
				t.Fatalf("failure state = %#v", state)
			}
		})
	}
}

func TestNewClientValidationIsTableDrivenAndTrafficFree(t *testing.T) {
	valid := func() vksguard.Config {
		return vksguard.Config{
			VCenterURL:       "http://127.0.0.1:9443",
			KubernetesURL:    "https://127.0.0.1:6443/",
			VCenterSessionID: "session-value",
			KubernetesToken:  "token-value",
			HTTPClient:       &http.Client{},
		}
	}
	tests := []struct {
		name   string
		mutate func(*vksguard.Config)
	}{
		{name: "blank vCenter URL", mutate: func(c *vksguard.Config) { c.VCenterURL = " \t" }},
		{name: "relative vCenter URL", mutate: func(c *vksguard.Config) { c.VCenterURL = "/api" }},
		{name: "unsupported vCenter scheme", mutate: func(c *vksguard.Config) { c.VCenterURL = "ftp://127.0.0.1" }},
		{name: "vCenter credentials", mutate: func(c *vksguard.Config) { c.VCenterURL = "http://user@127.0.0.1" }},
		{name: "vCenter path", mutate: func(c *vksguard.Config) { c.VCenterURL = "http://127.0.0.1/api" }},
		{name: "vCenter query", mutate: func(c *vksguard.Config) { c.VCenterURL = "http://127.0.0.1?x=1" }},
		{name: "vCenter fragment", mutate: func(c *vksguard.Config) { c.VCenterURL = "http://127.0.0.1#x" }},
		{name: "relative Kubernetes URL", mutate: func(c *vksguard.Config) { c.KubernetesURL = "//127.0.0.1" }},
		{name: "Kubernetes credentials", mutate: func(c *vksguard.Config) { c.KubernetesURL = "https://user@127.0.0.1" }},
		{name: "Kubernetes path", mutate: func(c *vksguard.Config) { c.KubernetesURL = "https://127.0.0.1/apis" }},
		{name: "Kubernetes query", mutate: func(c *vksguard.Config) { c.KubernetesURL = "https://127.0.0.1?pretty=" }},
		{name: "blank vCenter session", mutate: func(c *vksguard.Config) { c.VCenterSessionID = "\t " }},
		{name: "unsafe vCenter session", mutate: func(c *vksguard.Config) { c.VCenterSessionID = "secret\r\nx" }},
		{name: "blank Kubernetes token", mutate: func(c *vksguard.Config) { c.KubernetesToken = "\n " }},
		{name: "unsafe Kubernetes token", mutate: func(c *vksguard.Config) { c.KubernetesToken = "token\nx" }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			config := valid()
			tt.mutate(&config)
			if _, err := vksguard.NewClient(config); err == nil {
				t.Fatal("NewClient returned nil error")
			}
		})
	}

	redirectPolicy := func(
		*http.Request,
		[]*http.Request,
	) error {
		return nil
	}
	callerOwned := &http.Client{CheckRedirect: redirectPolicy}
	config := valid()
	config.HTTPClient = callerOwned
	if _, err := vksguard.NewClient(config); err != nil {
		t.Fatalf("NewClient(valid): %v", err)
	}
	if reflect.ValueOf(callerOwned.CheckRedirect).Pointer() !=
		reflect.ValueOf(redirectPolicy).Pointer() {
		t.Fatal("NewClient mutated the caller-owned HTTP client")
	}
	config.HTTPClient = nil
	if _, err := vksguard.NewClient(config); err != nil {
		t.Fatalf("NewClient(valid nil HTTPClient): %v", err)
	}
}

func TestNilAndCanceledContextsMakeNoTraffic(t *testing.T) {
	scenario := runtimeScenario(t, "RUNNING", true)
	server := newServer(t, scenario)
	defer server.Close()
	client := newClient(t, server, scenario)

	arguments := func(ctx context.Context) error {
		_, err := client.ReconcileVersion(
			ctx,
			scenario.Supervisor,
			scenario.Namespace,
			scenario.ClusterName,
			scenario.TargetVersion,
		)
		return err
	}
	if err := arguments(nil); err == nil {
		t.Fatal("nil context returned nil error")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := arguments(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled error = %v, want errors.Is(context.Canceled)", err)
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("nil/canceled contexts made %d requests, want 0", got)
	}
}

func TestStringArgumentsAreValidatedBeforeTraffic(t *testing.T) {
	scenario := runtimeScenario(t, "RUNNING", false)
	server := newServer(t, scenario)
	defer server.Close()
	client := newClient(t, server, scenario)

	tests := []struct {
		name       string
		supervisor string
		namespace  string
		cluster    string
		version    string
	}{
		{
			name:       "blank Supervisor",
			supervisor: " ",
			namespace:  scenario.Namespace,
			cluster:    scenario.ClusterName,
			version:    scenario.TargetVersion,
		},
		{
			name:       "blank namespace",
			supervisor: scenario.Supervisor,
			namespace:  "\t",
			cluster:    scenario.ClusterName,
			version:    scenario.TargetVersion,
		},
		{
			name:       "blank Cluster name",
			supervisor: scenario.Supervisor,
			namespace:  scenario.Namespace,
			cluster:    "\n",
			version:    scenario.TargetVersion,
		},
		{
			name:       "blank target version",
			supervisor: scenario.Supervisor,
			namespace:  scenario.Namespace,
			cluster:    scenario.ClusterName,
			version:    "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := client.ReconcileVersion(
				context.Background(),
				tt.supervisor,
				tt.namespace,
				tt.cluster,
				tt.version,
			); err == nil {
				t.Fatal("ReconcileVersion returned nil error")
			}
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("invalid arguments made %d requests, want 0", got)
	}
}

func TestMockRejectsOperationsAbsentFromContract(t *testing.T) {
	scenario := runtimeScenario(t, "RUNNING", false)
	server := newServer(t, scenario)
	defer server.Close()

	request, err := http.NewRequest(
		http.MethodGet,
		server.URL()+"/api/vcenter/vm",
		nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed operation status = %d, want 404", response.StatusCode)
	}
	log := server.Requests()
	if len(log) != 1 || log[0].Operation != "" {
		t.Fatalf("unnamed operation log = %#v", log)
	}
}

func TestPinnedContractAndOfficialSources(t *testing.T) {
	contractRaw := readFile(t, filepath.Join("..", "docs", "contract.json"))
	var contract contractDocument
	if err := json.Unmarshal(contractRaw, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if contract.Source.RepositoryCommitSHA != commitSHA ||
		contract.Source.SpecBlobSHA != specBlob ||
		contract.Source.SpecPath != specPath ||
		contract.Source.License != "Apache-2.0" ||
		contract.Source.OpenAPI != "3.0.3" ||
		contract.Source.APIVersion != "9.1.0.0" ||
		contract.Source.ServerTemplate != "https://{host}/api" ||
		contract.Source.BasePath != "/api" {
		t.Fatalf("pinned contract source changed: %#v", contract.Source)
	}
	if contract.SecuritySchemes.APIKeyAuth.Type != "apiKey" ||
		contract.SecuritySchemes.APIKeyAuth.In != "header" ||
		contract.SecuritySchemes.APIKeyAuth.Name !=
			"vmware-api-session-id" ||
		contract.SecuritySchemes.SupervisorBearer.Type != "http" ||
		contract.SecuritySchemes.SupervisorBearer.Scheme != "bearer" {
		t.Fatalf(
			"contract security projection changed: %#v",
			contract.SecuritySchemes,
		)
	}
	if len(contract.Operations) != 2 {
		t.Fatalf("contract operation count = %d, want 2", len(contract.Operations))
	}
	precheck := contract.Operations[0]
	if precheck.ContractName != contractmock.OperationNamespaceGet ||
		precheck.SourceKind != "vcenter-openapi-operation" ||
		precheck.OperationID != namespaceOperationID ||
		precheck.Method != http.MethodGet ||
		precheck.SpecPathItem !=
			"/vcenter/namespaces/instances/v2/{namespace}" ||
		precheck.PathTemplate !=
			"/api/vcenter/namespaces/instances/v2/{namespace}" ||
		string(precheck.QueryParameters) != "[]" {
		t.Fatalf("vCenter operation projection changed: %#v", precheck)
	}
	mutation := contract.Operations[1]
	var mutationBody struct {
		ContentType string `json:"contentType"`
	}
	if err := json.Unmarshal(mutation.RequestBody, &mutationBody); err != nil {
		t.Fatalf("decode Kubernetes requestBody: %v", err)
	}
	if mutation.ContractName != contractmock.OperationClusterPatch ||
		mutation.SourceKind != "supervisor-kubernetes-resource" ||
		mutation.OperationKey != clusterOperationKey ||
		mutation.OperationID != "" ||
		mutation.Method != http.MethodPatch ||
		mutation.PathTemplate !=
			"/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters/{clusterName}" ||
		mutationBody.ContentType !=
			"application/merge-patch+json" {
		t.Fatalf("Kubernetes operation projection changed: %#v", mutation)
	}
	wantOptionals := []string{
		"dryRun",
		"fieldManager",
		"fieldValidation",
		"force",
		"pretty",
	}
	if !reflect.DeepEqual(mutation.OptionalQueryFields, wantOptionals) {
		t.Fatalf(
			"Kubernetes optional fields = %q, want %q",
			mutation.OptionalQueryFields,
			wantOptionals,
		)
	}
	wantRequired := []string{
		"access_list",
		"config_status",
		"description",
		"messages",
		"stats",
		"storage_specs",
		"supervisor",
	}
	if !reflect.DeepEqual(contract.Schemas.InfoV2.Required, wantRequired) {
		t.Fatalf(
			"InfoV2 required fields = %q, want %q",
			contract.Schemas.InfoV2.Required,
			wantRequired,
		)
	}
	wantStatuses := []string{"CONFIGURING", "REMOVING", "RUNNING", "ERROR"}
	if !reflect.DeepEqual(
		contract.Schemas.InfoV2.FocusedProperties.ConfigStatus.Enum,
		wantStatuses,
	) || !reflect.DeepEqual(contract.Schemas.ConfigStatus.Enum, wantStatuses) {
		t.Fatal("ConfigStatus specification projection changed")
	}
	if contract.Gate.PrecheckContractName !=
		contractmock.OperationNamespaceGet ||
		contract.Gate.MutationContractName !=
			contractmock.OperationClusterPatch ||
		contract.Gate.FailedPrecheckMutationAttempts != 0 {
		t.Fatalf("gate projection changed: %#v", contract.Gate)
	}
	if !strings.Contains(
		contract.KubernetesProvenanceNote,
		"not represented as a VMware operationId",
	) {
		t.Fatal("Kubernetes provenance note changed")
	}

	var rawOperations struct {
		Operations []map[string]json.RawMessage `json:"operations"`
	}
	if err := json.Unmarshal(contractRaw, &rawOperations); err != nil {
		t.Fatal(err)
	}
	if _, fictional := rawOperations.Operations[1]["operationId"]; fictional {
		t.Fatal("Kubernetes route claims a fictional VMware operationId")
	}

	var sources officialSources
	if err := json.Unmarshal(
		readFile(t, filepath.Join("..", "docs", "official_sources.json")),
		&sources,
	); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.Repository != "vmware/vcf-api-specs" ||
		sources.RepositoryCommitSHA != commitSHA ||
		sources.SpecBlobSHA != specBlob ||
		sources.SpecPath != specPath ||
		sources.License != "Apache-2.0" ||
		!strings.Contains(sources.SpecURL, commitSHA) ||
		!strings.HasSuffix(sources.SpecURL, specPath) ||
		!reflect.DeepEqual(
			sources.OperationIDs,
			[]string{namespaceOperationID},
		) {
		t.Fatalf("official source pin changed: %#v", sources)
	}
	if len(sources.Operations) != 1 {
		t.Fatalf("official operation count = %d, want 1", len(sources.Operations))
	}
	for _, operation := range sources.Operations {
		if operation.OperationID != namespaceOperationID ||
			operation.RepositoryCommitSHA != commitSHA ||
			operation.SpecPath != specPath {
			t.Fatalf(
				"each operation must repeat operationId, commit, and path: %#v",
				operation,
			)
		}
	}
	if sources.KubernetesIntegration.OperationKey != clusterOperationKey {
		t.Fatalf(
			"Kubernetes integration key = %q",
			sources.KubernetesIntegration.OperationKey,
		)
	}
}

func TestClientUsesOnlyApprovedStandardLibraryBoundaries(t *testing.T) {
	path := "client.go"
	source := readFile(t, path)
	parsed, err := parser.ParseFile(
		token.NewFileSet(),
		path,
		source,
		parser.ImportsOnly,
	)
	if err != nil {
		t.Fatalf("parse client imports: %v", err)
	}
	allowed := map[string]bool{
		"bytes":         true,
		"context":       true,
		"encoding/json": true,
		"errors":        true,
		"fmt":           true,
		"io":            true,
		"net/http":      true,
		"net/url":       true,
		"strings":       true,
	}
	for _, imported := range parsed.Imports {
		name, err := strconv.Unquote(imported.Path.Value)
		if err != nil {
			t.Fatal(err)
		}
		if !allowed[name] {
			t.Fatalf("client imports an unapproved boundary: %s", name)
		}
	}
	folded := strings.ToLower(string(source))
	for _, forbidden := range []string{
		"os/exec",
		"syscall",
		"unsafe",
		"net.dial",
		"net.listen",
		"exec.command",
		"http.defaulttransport",
	} {
		if strings.Contains(folded, forbidden) {
			t.Fatalf("client uses forbidden mechanism %q", forbidden)
		}
	}
	if strings.Contains(folded, "implementation incomplete") {
		t.Fatal("client implementation is still incomplete")
	}
}

func assertPrecheckWire(
	t *testing.T,
	request contractmock.Request,
	scenario contractmock.Scenario,
	serverURL string,
) {
	t.Helper()
	assertCommonWire(
		t,
		request,
		0,
		contractmock.OperationNamespaceGet,
		http.MethodGet,
		serverURL,
	)
	wantTarget := "/api/vcenter/namespaces/instances/v2/" +
		url.PathEscape(scenario.Namespace)
	if request.RawTarget != wantTarget {
		t.Errorf(
			"precheck raw target = %q, want %q",
			request.RawTarget,
			wantTarget,
		)
	}
	if len(request.Body) != 0 || request.ContentLength != 0 {
		t.Errorf(
			"precheck entity = %q length %d, want bodyless",
			request.Body,
			request.ContentLength,
		)
	}
	assertHeaderSet(t, request.Header, map[string][]string{
		"Accept":                {"application/json"},
		"Accept-Encoding":       {"gzip"},
		"User-Agent":            {"Go-http-client/1.1"},
		"vmware-api-session-id": {scenario.VCenterSessionID},
	})
	assertAbsent(t, request.Header, "Authorization")
	assertAbsent(t, request.Header, "Content-Type")
	assertNoQuery(t, request.RawTarget, nil)
}

func assertPatchWire(
	t *testing.T,
	request contractmock.Request,
	scenario contractmock.Scenario,
	serverURL string,
) {
	t.Helper()
	assertCommonWire(
		t,
		request,
		1,
		contractmock.OperationClusterPatch,
		http.MethodPatch,
		serverURL,
	)
	wantTarget := "/apis/cluster.x-k8s.io/v1beta2/namespaces/" +
		url.PathEscape(scenario.Namespace) +
		"/clusters/" +
		url.PathEscape(scenario.ClusterName)
	if request.RawTarget != wantTarget {
		t.Errorf(
			"PATCH raw target = %q, want %q",
			request.RawTarget,
			wantTarget,
		)
	}
	wantBody := expectedPatchBody(t, scenario.TargetVersion)
	if !bytes.Equal(request.Body, wantBody) {
		t.Errorf("PATCH body = %q, want byte-exact %q", request.Body, wantBody)
	}
	if request.ContentLength != int64(len(wantBody)) {
		t.Errorf(
			"PATCH content length = %d, want %d",
			request.ContentLength,
			len(wantBody),
		)
	}
	if strings.Contains(scenario.TargetVersion, "雪") &&
		(!bytes.Contains(request.Body, []byte("雪")) ||
			bytes.Contains(request.Body, []byte(`\u96ea`)) ||
			bytes.Contains(request.Body, []byte(`\u003c`)) ||
			bytes.Contains(request.Body, []byte(`\u0026`))) {
		t.Errorf("PATCH body did not preserve direct UTF-8/printable JSON: %q", request.Body)
	}
	assertHeaderSet(t, request.Header, map[string][]string{
		"Accept":          {"application/json"},
		"Accept-Encoding": {"gzip"},
		"Authorization":   {"Bearer " + scenario.KubernetesToken},
		"Content-Type":    {"application/merge-patch+json"},
		"User-Agent":      {"Go-http-client/1.1"},
	})
	assertAbsent(t, request.Header, "vmware-api-session-id")
	assertNoQuery(t, request.RawTarget, []string{
		"dryRun",
		"fieldManager",
		"fieldValidation",
		"force",
		"pretty",
	})
}

func assertCommonWire(
	t *testing.T,
	request contractmock.Request,
	sequence int,
	operation string,
	method string,
	serverURL string,
) {
	t.Helper()
	parsed, err := url.Parse(serverURL)
	if err != nil {
		t.Fatal(err)
	}
	if request.Sequence != sequence ||
		request.Operation != operation ||
		request.Method != method ||
		request.Protocol != "HTTP/1.1" ||
		request.Host != parsed.Host {
		t.Errorf("request wire identity = %#v", request)
	}
}

func assertHeaderSet(
	t *testing.T,
	got http.Header,
	want map[string][]string,
) {
	t.Helper()
	canonicalWant := make(map[string][]string, len(want))
	for name, values := range want {
		canonicalWant[http.CanonicalHeaderKey(name)] = values
	}
	if !reflect.DeepEqual(got, http.Header(canonicalWant)) {
		gotKeys := make([]string, 0, len(got))
		for key := range got {
			gotKeys = append(gotKeys, key)
		}
		sort.Strings(gotKeys)
		t.Errorf("headers = %#v (keys %q), want exactly %#v", got, gotKeys, canonicalWant)
	}
	for name, values := range got {
		if len(values) != 1 {
			t.Errorf("header %s has %d values, want exactly one", name, len(values))
		}
	}
}

func assertAbsent(t *testing.T, header http.Header, name string) {
	t.Helper()
	if values := header.Values(name); values != nil {
		t.Errorf("%s header unexpectedly present: %q", name, values)
	}
}

func assertNoQuery(t *testing.T, rawTarget string, optionals []string) {
	t.Helper()
	if strings.Contains(rawTarget, "?") {
		t.Errorf("raw target contains a query marker: %q", rawTarget)
	}
	parsed, err := url.ParseRequestURI(rawTarget)
	if err != nil {
		t.Fatalf("parse raw target: %v", err)
	}
	if parsed.RawQuery != "" || parsed.ForceQuery {
		t.Errorf(
			"raw target has query %q or bare marker: %q",
			parsed.RawQuery,
			rawTarget,
		)
	}
	for _, name := range optionals {
		if parsed.Query().Has(name) {
			t.Errorf("unset optional query field %q was sent", name)
		}
	}
}

func expectedPatchBody(t *testing.T, version string) []byte {
	t.Helper()
	value := struct {
		Spec struct {
			Topology struct {
				Version string `json:"version"`
			} `json:"topology"`
		} `json:"spec"`
	}{}
	value.Spec.Topology.Version = version
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		t.Fatal(err)
	}
	return bytes.TrimSuffix(output.Bytes(), []byte{'\n'})
}

func newServer(
	t *testing.T,
	scenario contractmock.Scenario,
) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New(
		filepath.Join("..", "docs", "contract.json"),
		scenario,
	)
	if err != nil {
		t.Fatalf("contractmock.New: %v", err)
	}
	return server
}

func newClient(
	t *testing.T,
	server *contractmock.Server,
	scenario contractmock.Scenario,
) *vksguard.Client {
	t.Helper()
	client, err := vksguard.NewClient(vksguard.Config{
		VCenterURL:       server.URL(),
		KubernetesURL:    server.URL(),
		VCenterSessionID: scenario.VCenterSessionID,
		KubernetesToken:  scenario.KubernetesToken,
		HTTPClient:       server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func runtimeScenario(
	t *testing.T,
	configStatus string,
	opaqueText bool,
) contractmock.Scenario {
	t.Helper()
	suffix := runtimeSuffix(t)
	namespace := "team-" + suffix
	clusterName := "cluster-" + suffix
	targetVersion := "v1.31.4-" + suffix
	if opaqueText {
		namespace = "team /+雪-" + suffix
		clusterName = "cluster ?#+雪-" + suffix
		targetVersion = "v1.31+雪<&-" + suffix
	}
	return contractmock.Scenario{
		Namespace:        namespace,
		Supervisor:       "supervisor-" + suffix,
		ClusterName:      clusterName,
		OldVersion:       "v1.30.9-" + suffix,
		TargetVersion:    targetVersion,
		VCenterSessionID: "session-" + runtimeSuffix(t),
		KubernetesToken:  "token-" + runtimeSuffix(t),
		ConfigStatus:     configStatus,
	}
}

func runtimeSuffix(t *testing.T) string {
	t.Helper()
	var value [8]byte
	if _, err := rand.Read(value[:]); err != nil {
		t.Fatalf("rand.Read: %v", err)
	}
	return hex.EncodeToString(value[:])
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	value, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return value
}

type contractDocument struct {
	Source struct {
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
		SpecBlobSHA         string `json:"specBlobSha"`
		License             string `json:"license"`
		OpenAPI             string `json:"openapi"`
		APIVersion          string `json:"apiVersion"`
		ServerTemplate      string `json:"serverTemplate"`
		BasePath            string `json:"basePath"`
	} `json:"source"`
	SecuritySchemes struct {
		APIKeyAuth struct {
			Type string `json:"type"`
			In   string `json:"in"`
			Name string `json:"name"`
		} `json:"api_key_auth"`
		SupervisorBearer struct {
			Type   string `json:"type"`
			Scheme string `json:"scheme"`
		} `json:"supervisor_bearer"`
	} `json:"securitySchemes"`
	Operations []struct {
		ContractName        string          `json:"contractName"`
		SourceKind          string          `json:"sourceKind"`
		OperationID         string          `json:"operationId"`
		OperationKey        string          `json:"operationKey"`
		Method              string          `json:"method"`
		SpecPathItem        string          `json:"specPathItem"`
		PathTemplate        string          `json:"pathTemplate"`
		QueryParameters     json.RawMessage `json:"queryParameters"`
		RequestBody         json.RawMessage `json:"requestBody"`
		OptionalQueryFields []string        `json:"optionalQueryFields"`
	} `json:"operations"`
	Schemas struct {
		InfoV2 struct {
			Required          []string `json:"required"`
			FocusedProperties struct {
				ConfigStatus struct {
					Enum []string `json:"enum"`
				} `json:"config_status"`
			} `json:"focusedProperties"`
		} `json:"Vcenter.Namespaces.Instances.InfoV2"`
		ConfigStatus struct {
			Enum []string `json:"enum"`
		} `json:"Vcenter.Namespaces.Instances.ConfigStatus"`
	} `json:"schemas"`
	Gate struct {
		PrecheckContractName           string `json:"precheckContractName"`
		MutationContractName           string `json:"mutationContractName"`
		FailedPrecheckMutationAttempts int    `json:"failedPrecheckMutationAttempts"`
	} `json:"gate"`
	KubernetesProvenanceNote string `json:"kubernetesProvenanceNote"`
}

type officialSources struct {
	Repository          string   `json:"repository"`
	RepositoryCommitSHA string   `json:"repositoryCommitSha"`
	SpecPath            string   `json:"specPath"`
	SpecBlobSHA         string   `json:"specBlobSha"`
	SpecURL             string   `json:"specUrl"`
	License             string   `json:"license"`
	OperationIDs        []string `json:"operationIds"`
	Operations          []struct {
		OperationID         string `json:"operationId"`
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
	} `json:"operations"`
	KubernetesIntegration struct {
		OperationKey string `json:"operationKey"`
	} `json:"kubernetesIntegration"`
}
