package vksrotate_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"example.com/vksrotate/internal/contractmock"
	"example.com/vksrotate/vksrotate"
)

const (
	contractPath    = "../docs/contract.json"
	sourcesPath     = "../docs/official_sources.json"
	contractDigest  = "3baefdef07ad307c17a597c4420b6ef5f60039ec548408a91421f93169527983"
	sourcesDigest   = "5366cd032b2cf4893b0d2c66d46b8b03db970ac513e0a493640ce34c2dada2e6"
	pinnedCommitSHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedSpecPath  = "specifications/vsphere/openapi/automation/vcenter.yaml"
	pinnedOperation = "Vcenter.Namespaces.Instances_getV2"
)

var (
	oldAuth = contractmock.Auth{
		VCenterSessionID:      "old-vcenter-session-7f31",
		KubernetesBearerToken: "old-kube-bearer-51c9",
	}
	newAuth = contractmock.Auth{
		VCenterSessionID:      "new-vcenter-session-82b4",
		KubernetesBearerToken: "new-kube-bearer-93ad",
	}
)

func TestPinnedSpecificationProjection(t *testing.T) {
	t.Parallel()

	for _, tc := range []struct {
		path string
		want string
	}{
		{path: contractPath, want: contractDigest},
		{path: sourcesPath, want: sourcesDigest},
	} {
		tc := tc
		t.Run(tc.path, func(t *testing.T) {
			data, err := os.ReadFile(tc.path)
			if err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(data)
			if got := hex.EncodeToString(sum[:]); got != tc.want {
				t.Fatalf("protected source document changed: got sha256 %s, want %s", got, tc.want)
			}
		})
	}

	var contract struct {
		Source struct {
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
			SpecBlobSHA         string `json:"specBlobSha"`
			License             string `json:"license"`
		} `json:"source"`
		Operations []struct {
			Name         string `json:"contractName"`
			SourceKind   string `json:"sourceKind"`
			OperationID  string `json:"operationId"`
			OperationKey string `json:"operationKey"`
			Method       string `json:"method"`
			Path         string `json:"pathTemplate"`
		} `json:"operations"`
		Kubernetes struct {
			Operations []struct {
				Name                string   `json:"name"`
				OperationKey        string   `json:"operationKey"`
				OptionalQueryFields []string `json:"optionalQueryFields"`
			} `json:"operations"`
		} `json:"kubernetesApi"`
	}
	readJSON(t, contractPath, &contract)

	var sources struct {
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		SpecBlobSHA         string   `json:"specBlobSha"`
		License             string   `json:"license"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID string `json:"operationId"`
			SpecPath    string `json:"specPath"`
			Method      string `json:"method"`
			Path        string `json:"specPathItem"`
		} `json:"operations"`
	}
	readJSON(t, sourcesPath, &sources)

	if contract.Source.RepositoryCommitSHA != pinnedCommitSHA ||
		sources.RepositoryCommitSHA != pinnedCommitSHA ||
		contract.Source.SpecPath != pinnedSpecPath ||
		sources.SpecPath != pinnedSpecPath ||
		contract.Source.SpecBlobSHA == "" ||
		contract.Source.SpecBlobSHA != sources.SpecBlobSHA ||
		contract.Source.License != "Apache-2.0" ||
		contract.Source.License != sources.License {
		t.Fatal("contract and official source provenance are not the pinned specification")
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{pinnedOperation}) ||
		len(sources.Operations) != 1 ||
		sources.Operations[0].OperationID != pinnedOperation ||
		sources.Operations[0].SpecPath != pinnedSpecPath {
		t.Fatalf("official operation records = %+v, want exactly %s", sources.Operations, pinnedOperation)
	}
	if len(contract.Operations) != 2 {
		t.Fatalf("contract operations = %d, want 2", len(contract.Operations))
	}
	if got := contract.Operations[0]; got.SourceKind != "openapi" ||
		got.OperationID != pinnedOperation || got.OperationKey != "" ||
		got.Method != http.MethodGet ||
		got.Path != "/api/vcenter/namespaces/instances/v2/{namespace}" {
		t.Fatalf("vCenter projection = %+v", got)
	}
	if got := contract.Operations[1]; got.SourceKind != "kubernetes-resource" ||
		got.OperationID != "" || got.OperationKey == "" ||
		got.Method != http.MethodGet ||
		got.Path != "/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters" {
		t.Fatalf("Kubernetes integration record = %+v", got)
	}
	wantOptional := []string{
		"allowWatchBookmarks",
		"continue",
		"fieldSelector",
		"labelSelector",
		"limit",
		"pretty",
		"resourceVersion",
		"resourceVersionMatch",
		"sendInitialEvents",
		"timeoutSeconds",
		"watch",
	}
	if len(contract.Kubernetes.Operations) != 1 ||
		!reflect.DeepEqual(contract.Kubernetes.Operations[0].OptionalQueryFields, wantOptional) {
		t.Fatalf("optional Kubernetes list inputs = %+v", contract.Kubernetes.Operations)
	}
}

func TestRotationRescuesOnlyTheInFlightOperation(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name           string
		blockOperation string
		wantOperations []string
		wantAuth       []contractmock.Auth
	}{
		{
			name:           "vcenter request retires while in flight",
			blockOperation: vksrotate.OperationGetSupervisorNamespace,
			wantOperations: []string{
				vksrotate.OperationGetSupervisorNamespace,
				vksrotate.OperationGetSupervisorNamespace,
				vksrotate.OperationListVKSClusters,
			},
			wantAuth: []contractmock.Auth{oldAuth, newAuth, newAuth},
		},
		{
			name:           "kubernetes request retires while in flight",
			blockOperation: vksrotate.OperationListVKSClusters,
			wantOperations: []string{
				vksrotate.OperationGetSupervisorNamespace,
				vksrotate.OperationListVKSClusters,
				vksrotate.OperationListVKSClusters,
			},
			wantAuth: []contractmock.Auth{oldAuth, oldAuth, newAuth},
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			namespace := "team blue/" + strings.ReplaceAll(tc.name, " ", "%")
			server := newServer(t, namespace, tc.blockOperation, nil)
			client := newClient(t, server)

			type result struct {
				snapshot vksrotate.Snapshot
				err      error
			}
			finished := make(chan result, 1)
			go func() {
				snapshot, err := client.Inspect(context.Background(), namespace)
				finished <- result{snapshot: snapshot, err: err}
			}()

			waitCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			if err := server.WaitBlocked(waitCtx); err != nil {
				t.Fatalf("old-generation request did not reach loopback mock: %v", err)
			}
			if err := client.Rotate(vksrotate.Credentials{
				VCenterSessionID:      newAuth.VCenterSessionID,
				KubernetesBearerToken: newAuth.KubernetesBearerToken,
			}); err != nil {
				t.Fatalf("Rotate: %v", err)
			}
			server.PublishReplacement()

			var got result
			select {
			case got = <-finished:
			case <-time.After(3 * time.Second):
				t.Fatal("Inspect remained stranded after credential rotation")
			}
			if got.err != nil {
				t.Fatalf("Inspect: %v", got.err)
			}
			wantClusters := []vksrotate.Cluster{
				{Name: "alpha", Namespace: namespace, UID: "uid-alpha", ResourceVersion: "17"},
				{Name: "zeta", Namespace: namespace, UID: "uid-zeta", ResourceVersion: "29"},
			}
			if got.snapshot.Namespace != namespace ||
				got.snapshot.Supervisor != "supervisor-42" ||
				got.snapshot.ConfigStatus != "RUNNING" ||
				!reflect.DeepEqual(got.snapshot.Clusters, wantClusters) {
				t.Fatalf("Snapshot = %+v", got.snapshot)
			}

			requests := server.Requests()
			if len(requests) != len(tc.wantOperations) {
				t.Fatalf("request count = %d, want %d: %+v", len(requests), len(tc.wantOperations), requests)
			}
			for i := range requests {
				if requests[i].Operation != tc.wantOperations[i] {
					t.Fatalf("request %d operation = %q, want %q", i, requests[i].Operation, tc.wantOperations[i])
				}
				assertWire(t, requests[i], namespace, tc.wantAuth[i])
			}
		})
	}
}

func TestCurrentGenerationUnauthorizedIsTerminal(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name            string
		failedOperation string
		wantCalls       int
	}{
		{
			name:            "vcenter current generation",
			failedOperation: vksrotate.OperationGetSupervisorNamespace,
			wantCalls:       1,
		},
		{
			name:            "kubernetes current generation",
			failedOperation: vksrotate.OperationListVKSClusters,
			wantCalls:       2,
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			server := newServer(t, "terminal-auth", "", map[string]int{
				tc.failedOperation: http.StatusUnauthorized,
			})
			client := newClient(t, server)
			_, err := client.Inspect(context.Background(), "terminal-auth")
			if err == nil {
				t.Fatal("Inspect returned nil error")
			}
			var apiError *vksrotate.APIError
			if !errors.As(err, &apiError) ||
				apiError.Operation != tc.failedOperation ||
				apiError.StatusCode != http.StatusUnauthorized {
				t.Fatalf("Inspect error = %#v", err)
			}
			if containsCredential(err.Error()) || strings.Contains(err.Error(), "forced failure") {
				t.Fatalf("error exposed protected response data: %v", err)
			}
			if got := len(server.Requests()); got != tc.wantCalls {
				t.Fatalf("request count = %d, want %d", got, tc.wantCalls)
			}
		})
	}
}

func TestValidationAndInvalidRotationPerformNoIO(t *testing.T) {
	t.Parallel()

	valid := vksrotate.Config{
		VCenterURL:    "http://127.0.0.1:7443",
		KubernetesURL: "https://127.0.0.1:6443",
		Credentials: vksrotate.Credentials{
			VCenterSessionID:      oldAuth.VCenterSessionID,
			KubernetesBearerToken: oldAuth.KubernetesBearerToken,
		},
	}
	configTests := []struct {
		name   string
		mutate func(*vksrotate.Config)
	}{
		{name: "vcenter scheme", mutate: func(c *vksrotate.Config) { c.VCenterURL = "ftp://127.0.0.1" }},
		{name: "vcenter path", mutate: func(c *vksrotate.Config) { c.VCenterURL += "/api" }},
		{name: "vcenter query", mutate: func(c *vksrotate.Config) { c.VCenterURL += "?x=1" }},
		{name: "vcenter forced query", mutate: func(c *vksrotate.Config) { c.VCenterURL += "?" }},
		{name: "kubernetes credentials", mutate: func(c *vksrotate.Config) { c.KubernetesURL = "https://user@127.0.0.1" }},
		{name: "kubernetes fragment", mutate: func(c *vksrotate.Config) { c.KubernetesURL += "#fragment" }},
		{name: "empty session", mutate: func(c *vksrotate.Config) { c.Credentials.VCenterSessionID = " " }},
		{name: "session newline", mutate: func(c *vksrotate.Config) { c.Credentials.VCenterSessionID = "bad\nvalue" }},
		{name: "empty token", mutate: func(c *vksrotate.Config) { c.Credentials.KubernetesBearerToken = "" }},
		{name: "token carriage return", mutate: func(c *vksrotate.Config) { c.Credentials.KubernetesBearerToken = "bad\rvalue" }},
	}
	for _, tc := range configTests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			config := valid
			tc.mutate(&config)
			if _, err := vksrotate.NewClient(config); err == nil {
				t.Fatal("NewClient accepted invalid config")
			}
		})
	}

	server := newServer(t, "still-old", "", nil)
	client := newClient(t, server)
	for _, credentials := range []vksrotate.Credentials{
		{VCenterSessionID: "", KubernetesBearerToken: newAuth.KubernetesBearerToken},
		{VCenterSessionID: newAuth.VCenterSessionID, KubernetesBearerToken: "bad\nvalue"},
	} {
		if err := client.Rotate(credentials); err == nil {
			t.Fatalf("Rotate accepted invalid credentials: %+v", credentials)
		}
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("rotation performed %d requests", got)
	}
	if _, err := client.Inspect(context.Background(), "still-old"); err != nil {
		t.Fatalf("invalid rotation changed the active credentials: %v", err)
	}

	before := len(server.Requests())
	canceledContext, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.Inspect(canceledContext, "still-old"); !errors.Is(err, context.Canceled) {
		t.Fatalf("pre-canceled Inspect error = %v, want context.Canceled", err)
	}
	for _, tc := range []struct {
		name      string
		ctx       context.Context
		namespace string
	}{
		{name: "nil context", ctx: nil, namespace: "still-old"},
		{name: "blank namespace", ctx: context.Background(), namespace: " \t"},
	} {
		if _, err := client.Inspect(tc.ctx, tc.namespace); err == nil {
			t.Fatalf("%s: Inspect accepted invalid input", tc.name)
		}
	}
	if got := len(server.Requests()); got != before {
		t.Fatalf("Inspect validation made %d requests", got-before)
	}
}

func TestLoopbackMockRejectsOperationsOutsideContract(t *testing.T) {
	t.Parallel()

	server := newServer(t, "allow-list", "", nil)
	request, err := http.NewRequest(http.MethodGet, server.URL()+"/api/not-in-contract", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", response.StatusCode)
	}
	requests := server.Requests()
	if len(requests) != 1 || requests[0].Operation != "" {
		t.Fatalf("unnamed request log = %+v", requests)
	}
}

func newServer(t *testing.T, namespace, blockOperation string, forced map[string]int) *contractmock.Server {
	t.Helper()

	server, err := contractmock.New(contractPath, contractmock.Fixture{
		Namespace:  namespace,
		Supervisor: "supervisor-42",
		Clusters: []contractmock.Cluster{
			{Name: "zeta", UID: "uid-zeta", ResourceVersion: "29"},
			{Name: "alpha", UID: "uid-alpha", ResourceVersion: "17"},
		},
		OldAuth:        oldAuth,
		NewAuth:        newAuth,
		BlockOperation: blockOperation,
		ForcedStatus:   forced,
	})
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server) *vksrotate.Client {
	t.Helper()

	client, err := vksrotate.NewClient(vksrotate.Config{
		VCenterURL:    server.URL(),
		KubernetesURL: server.URL(),
		Credentials: vksrotate.Credentials{
			VCenterSessionID:      oldAuth.VCenterSessionID,
			KubernetesBearerToken: oldAuth.KubernetesBearerToken,
		},
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertWire(t *testing.T, request contractmock.Request, namespace string, auth contractmock.Auth) {
	t.Helper()

	if request.Method != http.MethodGet {
		t.Fatalf("%s method = %q", request.Operation, request.Method)
	}
	var wantTarget string
	switch request.Operation {
	case vksrotate.OperationGetSupervisorNamespace:
		wantTarget = "/api/vcenter/namespaces/instances/v2/" + url.PathEscape(namespace)
	case vksrotate.OperationListVKSClusters:
		wantTarget = "/apis/cluster.x-k8s.io/v1beta2/namespaces/" +
			url.PathEscape(namespace) + "/clusters"
	default:
		t.Fatalf("unknown logged operation %q", request.Operation)
	}
	if request.RawTarget != wantTarget {
		t.Fatalf("%s raw target = %q, want %q", request.Operation, request.RawTarget, wantTarget)
	}
	if strings.Contains(request.RawTarget, "?") {
		t.Fatalf("%s sent a query or bare ?: %q", request.Operation, request.RawTarget)
	}
	if len(request.Body) != 0 || request.ContentLength != 0 || len(request.TransferEncoding) != 0 {
		t.Fatalf("%s body framing = body %q, length %d, transfer %v",
			request.Operation, request.Body, request.ContentLength, request.TransferEncoding)
	}
	if got := request.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("%s Accept = %v", request.Operation, got)
	}
	for _, absent := range []string{"Content-Type", "Content-Length", "Transfer-Encoding", "Accept-Encoding"} {
		if got := request.Header.Values(absent); len(got) != 0 {
			t.Fatalf("%s unexpectedly sent %s: %v", request.Operation, absent, got)
		}
	}
	allowedHeaders := map[string]bool{"Accept": true}
	if request.Operation == vksrotate.OperationGetSupervisorNamespace {
		allowedHeaders["Vmware-Api-Session-Id"] = true
	} else {
		allowedHeaders["Authorization"] = true
	}
	for name := range request.Header {
		if !allowedHeaders[http.CanonicalHeaderKey(name)] {
			t.Fatalf("%s unexpectedly sent header %s", request.Operation, name)
		}
	}
	switch request.Operation {
	case vksrotate.OperationGetSupervisorNamespace:
		if got := request.Header.Values("vmware-api-session-id"); !reflect.DeepEqual(got, []string{auth.VCenterSessionID}) {
			t.Fatalf("vCenter session values = %v", got)
		}
		if got := request.Header.Values("Authorization"); len(got) != 0 {
			t.Fatalf("vCenter received Authorization: %v", got)
		}
	case vksrotate.OperationListVKSClusters:
		want := []string{"Bearer " + auth.KubernetesBearerToken}
		if got := request.Header.Values("Authorization"); !reflect.DeepEqual(got, want) {
			t.Fatalf("Kubernetes Authorization = %v", got)
		}
		if got := request.Header.Values("vmware-api-session-id"); len(got) != 0 {
			t.Fatalf("Kubernetes received vCenter session: %v", got)
		}
	}
}

func containsCredential(value string) bool {
	for _, credential := range []string{
		oldAuth.VCenterSessionID,
		oldAuth.KubernetesBearerToken,
		newAuth.VCenterSessionID,
		newAuth.KubernetesBearerToken,
	} {
		if strings.Contains(value, credential) {
			return true
		}
	}
	return false
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func TestErrorFormattingDoesNotExposeNestedValues(t *testing.T) {
	t.Parallel()

	errorValues := []error{
		&vksrotate.APIError{Operation: vksrotate.OperationGetSupervisorNamespace, StatusCode: 401},
		&vksrotate.ProtocolError{Operation: vksrotate.OperationListVKSClusters, Problem: "invalid success payload"},
	}
	for _, err := range errorValues {
		if containsCredential(fmt.Sprintf("%+v", err)) {
			t.Fatalf("formatted error leaked credentials: %v", err)
		}
	}
}
