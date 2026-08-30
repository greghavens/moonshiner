package grader_tests

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"

	sut "example.com/vcf91/supervisorvks"
	"example.com/vcf91/supervisorvks/internal/mockvcf"
)

const (
	getNamespaceID    = "Vcenter.Namespaces.Instances_getV2"
	createNamespaceID = "Vcenter.Namespaces.Instances_createV2"
)

type tokenSource struct {
	mu    sync.Mutex
	old   string
	fresh string
	calls []bool
}

type requestRecord struct {
	Method string
	URL    string
	Header http.Header
	Body   string
}

type trackingBody struct {
	io.Reader
	closed bool
}

func (b *trackingBody) Close() error {
	b.closed = true
	return nil
}

type scriptedTransport struct {
	statuses []int
	requests []requestRecord
	bodies   []*trackingBody
}

func (s *scriptedTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if len(s.requests) >= len(s.statuses) {
		return nil, fmt.Errorf("unexpected request %s %s", request.Method, request.URL)
	}
	body, err := io.ReadAll(request.Body)
	if err != nil {
		return nil, err
	}
	_ = request.Body.Close()
	s.requests = append(s.requests, requestRecord{
		Method: request.Method,
		URL:    request.URL.String(),
		Header: request.Header.Clone(),
		Body:   string(body),
	})
	responseBody := &trackingBody{Reader: strings.NewReader("response")}
	s.bodies = append(s.bodies, responseBody)
	return &http.Response{
		StatusCode: s.statuses[len(s.requests)-1],
		Header:     make(http.Header),
		Body:       responseBody,
		Request:    request,
	}, nil
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func (s *tokenSource) Token(_ context.Context, force bool) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, force)
	if force {
		return s.fresh, nil
	}
	return s.old, nil
}

func (s *tokenSource) history() []bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]bool(nil), s.calls...)
}

func value(t *testing.T, label string) string {
	t.Helper()
	sum := sha256.Sum256([]byte(t.Name() + ":" + label))
	return label + "-" + hex.EncodeToString(sum[:5])
}

func fixture(t *testing.T, mutate func(*mockvcf.Config)) (*mockvcf.Server, *tokenSource, sut.NamespaceSpec, sut.ClusterSpec) {
	t.Helper()
	ns := sut.NamespaceSpec{Name: value(t, "ns"), Supervisor: value(t, "sup")}
	cluster := sut.ClusterSpec{
		Name:                 value(t, "cluster"),
		Class:                value(t, "class"),
		Version:              "v1.31.4+vmware.1-fips",
		ControlPlaneReplicas: 3,
		WorkerClass:          value(t, "worker-class"),
		WorkerName:           value(t, "worker-name"),
		WorkerReplicas:       4,
		VMClass:              value(t, "vm-class"),
		StorageClass:         value(t, "storage-class"),
	}
	tokens := &tokenSource{old: value(t, "expired"), fresh: value(t, "fresh")}
	cfg := mockvcf.Config{
		Supervisor:   ns.Supervisor,
		Namespace:    ns.Name,
		Cluster:      cluster.Name,
		SessionID:    value(t, "session"),
		ExpiredToken: tokens.old,
		FreshToken:   tokens.fresh,
	}
	if mutate != nil {
		mutate(&cfg)
	}
	ns.Name = cfg.Namespace
	ns.Supervisor = cfg.Supervisor
	cluster.Name = cfg.Cluster
	server, err := mockvcf.Start(filepath.Join("..", "docs", "contract.json"), cfg)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server, tokens, ns, cluster
}

func newClient(t *testing.T, server *mockvcf.Server, session string, tokens sut.TokenSource) *sut.Client {
	t.Helper()
	client, err := sut.NewClient(server.URL, server.URL, session, tokens, server.HTTPClient())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func newScriptClient(t *testing.T, transport http.RoundTripper, tokens sut.TokenSource) *sut.Client {
	t.Helper()
	client, err := sut.NewClient(
		"http://vcenter.test",
		"http://kubernetes.test",
		"session",
		tokens,
		&http.Client{Transport: transport},
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func validSpecs() (sut.NamespaceSpec, sut.ClusterSpec) {
	return sut.NamespaceSpec{Name: "namespace", Supervisor: "supervisor"}, sut.ClusterSpec{
		Name:                 "cluster",
		Class:                "cluster-class",
		Version:              "v1.31.4+vmware.1-fips",
		ControlPlaneReplicas: 3,
		WorkerClass:          "worker-class",
		WorkerName:           "worker-name",
		WorkerReplicas:       4,
		VMClass:              "vm-class",
		StorageClass:         "storage-class",
	}
}

func TestRefreshReplaysOnlyExpiredRequest(t *testing.T) {
	server, tokens, ns, cluster := fixture(t, func(cfg *mockvcf.Config) {
		cfg.ExpireOnClusterCreate = true
	})
	session := value(t, "session")
	client := newClient(t, server, session, tokens)

	result, err := client.Ensure(context.Background(), ns, cluster)
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if result != (sut.Result{NamespaceCreated: true, ClusterCreated: true}) {
		t.Fatalf("result = %+v", result)
	}
	if got := tokens.history(); !reflect.DeepEqual(got, []bool{false, true}) {
		t.Fatalf("token calls = %v, want [false true]", got)
	}

	namespaceBody, _ := json.Marshal(struct {
		Namespace  string `json:"namespace"`
		Supervisor string `json:"supervisor"`
	}{ns.Name, ns.Supervisor})
	clusterBody := expectedClusterBody(cluster)

	requests := server.Requests()
	expected := []struct {
		method  string
		target  string
		headers http.Header
		body    string
	}{
		{
			http.MethodGet,
			"/api/vcenter/namespaces/instances/v2/" + ns.Name,
			http.Header{"Vmware-Api-Session-Id": {session}},
			"",
		},
		{
			http.MethodPost,
			"/api/vcenter/namespaces/instances/v2",
			http.Header{"Vmware-Api-Session-Id": {session}},
			string(namespaceBody),
		},
		{
			http.MethodGet,
			"/apis/cluster.x-k8s.io/v1beta2/namespaces/" + ns.Name + "/clusters/" + cluster.Name,
			http.Header{"Authorization": {"Bearer " + tokens.old}},
			"",
		},
		{
			http.MethodPost,
			"/apis/cluster.x-k8s.io/v1beta2/namespaces/" + ns.Name + "/clusters",
			http.Header{"Authorization": {"Bearer " + tokens.old}},
			clusterBody,
		},
		{
			http.MethodPost,
			"/apis/cluster.x-k8s.io/v1beta2/namespaces/" + ns.Name + "/clusters",
			http.Header{"Authorization": {"Bearer " + tokens.fresh}},
			clusterBody,
		},
	}
	if len(requests) != len(expected) {
		t.Fatalf("request count = %d, want %d: %+v", len(requests), len(expected), requests)
	}
	for i, want := range expected {
		t.Run(fmt.Sprintf("wire_%d", i), func(t *testing.T) {
			got := requests[i]
			if got.Method != want.method || got.Target != want.target {
				t.Fatalf("request = %s %s, want %s %s", got.Method, got.Target, want.method, want.target)
			}
			if want.body == "" {
				if got.Body != "" {
					t.Fatalf("body = %q, want empty", got.Body)
				}
			} else {
				assertJSONEqual(t, got.Body, want.body)
			}
			assertRelevantHeaders(t, got.Header, want.headers, want.body != "")
		})
	}
	if requests[3].Body != requests[4].Body {
		t.Fatalf("retried body changed: first %q, second %q", requests[3].Body, requests[4].Body)
	}
	firstHeaders, replayHeaders := requests[3].Header.Clone(), requests[4].Header.Clone()
	firstHeaders.Del("Authorization")
	replayHeaders.Del("Authorization")
	if !reflect.DeepEqual(firstHeaders, replayHeaders) {
		t.Fatalf("retried headers changed: first %#v, second %#v", firstHeaders, replayHeaders)
	}

	var namespaceObject map[string]any
	if err := json.Unmarshal(namespaceBody, &namespaceObject); err != nil {
		t.Fatal(err)
	}
	assertExactKeys(t, namespaceObject, "namespace", "supervisor")
	var clusterObject map[string]any
	if err := json.Unmarshal([]byte(clusterBody), &clusterObject); err != nil {
		t.Fatal(err)
	}
	assertClusterOmissions(t, clusterObject)
}

func TestUnauthorizedClusterGETIsRefreshedInPlace(t *testing.T) {
	tokens := &tokenSource{old: "expired", fresh: "fresh"}
	transport := &scriptedTransport{statuses: []int{
		http.StatusOK,
		http.StatusUnauthorized,
		http.StatusNotFound,
		http.StatusCreated,
	}}
	client := newScriptClient(t, transport, tokens)
	namespace, cluster := validSpecs()

	result, err := client.Ensure(context.Background(), namespace, cluster)
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if result != (sut.Result{ClusterCreated: true}) {
		t.Fatalf("result = %+v, want only cluster created", result)
	}
	if got := tokens.history(); !reflect.DeepEqual(got, []bool{false, true}) {
		t.Fatalf("token calls = %v, want [false true]", got)
	}
	if len(transport.requests) != 4 {
		t.Fatalf("request count = %d, want 4", len(transport.requests))
	}
	first, replay := transport.requests[1], transport.requests[2]
	if first.Method != http.MethodGet ||
		first.Method != replay.Method ||
		first.URL != replay.URL ||
		first.Body != replay.Body {
		t.Fatalf("GET replay changed: first %+v, replay %+v", first, replay)
	}
	if first.Header.Get("Authorization") != "Bearer expired" ||
		replay.Header.Get("Authorization") != "Bearer fresh" ||
		transport.requests[3].Header.Get("Authorization") != "Bearer fresh" {
		t.Fatalf("authorization sequence = %q, %q, %q",
			first.Header.Get("Authorization"),
			replay.Header.Get("Authorization"),
			transport.requests[3].Header.Get("Authorization"),
		)
	}
	for i, body := range transport.bodies {
		if !body.closed {
			t.Fatalf("response body %d was not closed", i)
		}
	}
}

func TestExistingResourcesNeedOnlyGETs(t *testing.T) {
	server, tokens, ns, cluster := fixture(t, func(cfg *mockvcf.Config) {
		cfg.NamespaceExists = true
		cfg.ClusterExists = true
	})
	client := newClient(t, server, value(t, "session"), tokens)
	result, err := client.Ensure(context.Background(), ns, cluster)
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if result != (sut.Result{}) {
		t.Fatalf("result = %+v, want zero", result)
	}
	requests := server.Requests()
	if len(requests) != 2 || requests[0].Method != http.MethodGet || requests[1].Method != http.MethodGet {
		t.Fatalf("requests = %+v, want two GETs", requests)
	}
	if got := tokens.history(); !reflect.DeepEqual(got, []bool{false}) {
		t.Fatalf("token calls = %v, want [false]", got)
	}
}

func TestExistingClusterAfterNamespaceCreationNeedsNoClusterPOST(t *testing.T) {
	server, tokens, ns, cluster := fixture(t, func(cfg *mockvcf.Config) {
		cfg.ClusterExists = true
	})
	client := newClient(t, server, value(t, "session"), tokens)
	result, err := client.Ensure(context.Background(), ns, cluster)
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if result != (sut.Result{NamespaceCreated: true}) {
		t.Fatalf("result = %+v, want only namespace created", result)
	}
	requests := server.Requests()
	if len(requests) != 3 ||
		requests[0].Method != http.MethodGet ||
		requests[1].Method != http.MethodPost ||
		requests[2].Method != http.MethodGet {
		t.Fatalf("requests = %+v, want namespace GET/POST then cluster GET", requests)
	}
	if got := tokens.history(); !reflect.DeepEqual(got, []bool{false}) {
		t.Fatalf("token calls = %v, want [false]", got)
	}
}

func TestPathParametersAreEscapedAsSingleSegments(t *testing.T) {
	server, tokens, ns, cluster := fixture(t, func(cfg *mockvcf.Config) {
		cfg.Namespace = "team/blue?"
		cfg.Cluster = "cluster/name#"
	})
	client := newClient(t, server, value(t, "session"), tokens)
	if _, err := client.Ensure(context.Background(), ns, cluster); err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	requests := server.Requests()
	if len(requests) != 4 {
		t.Fatalf("request count = %d, want 4", len(requests))
	}
	wantNamespace := url.PathEscape(ns.Name)
	wantCluster := url.PathEscape(cluster.Name)
	targets := []string{
		"/api/vcenter/namespaces/instances/v2/" + wantNamespace,
		"/api/vcenter/namespaces/instances/v2",
		"/apis/cluster.x-k8s.io/v1beta2/namespaces/" + wantNamespace + "/clusters/" + wantCluster,
		"/apis/cluster.x-k8s.io/v1beta2/namespaces/" + wantNamespace + "/clusters",
	}
	for i, want := range targets {
		if requests[i].Target != want {
			t.Fatalf("request %d target = %q, want %q", i, requests[i].Target, want)
		}
	}
}

func TestSecondUnauthorizedOnGETIsTerminal(t *testing.T) {
	tokens := &tokenSource{old: "expired", fresh: "fresh"}
	transport := &scriptedTransport{statuses: []int{
		http.StatusOK,
		http.StatusUnauthorized,
		http.StatusUnauthorized,
	}}
	client := newScriptClient(t, transport, tokens)
	namespace, cluster := validSpecs()

	result, err := client.Ensure(context.Background(), namespace, cluster)
	var apiErr *sut.APIError
	if !errors.As(err, &apiErr) ||
		apiErr.StatusCode != http.StatusUnauthorized ||
		apiErr.OperationID != "Kubernetes.Cluster.get" {
		t.Fatalf("error = %v, want Kubernetes GET APIError 401", err)
	}
	if result != (sut.Result{}) {
		t.Fatalf("result = %+v, want zero", result)
	}
	if len(transport.requests) != 3 {
		t.Fatalf("request count = %d, want 3", len(transport.requests))
	}
	if got := tokens.history(); !reflect.DeepEqual(got, []bool{false, true}) {
		t.Fatalf("token calls = %v, want [false true]", got)
	}
}

func TestSecondUnauthorizedIsTerminalWithoutRestart(t *testing.T) {
	server, tokens, ns, cluster := fixture(t, func(cfg *mockvcf.Config) {
		cfg.ExpireOnClusterCreate = true
		cfg.RejectFreshToken = true
	})
	client := newClient(t, server, value(t, "session"), tokens)
	result, err := client.Ensure(context.Background(), ns, cluster)
	var apiErr *sut.APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusUnauthorized || apiErr.OperationID != "Kubernetes.Cluster.create" {
		t.Fatalf("error = %v, want Kubernetes create APIError 401", err)
	}
	if result != (sut.Result{NamespaceCreated: true}) {
		t.Fatalf("result = %+v, want namespace progress preserved", result)
	}
	requests := server.Requests()
	if len(requests) != 5 {
		t.Fatalf("request count = %d, want 5", len(requests))
	}
	for i := 2; i < len(requests); i++ {
		if strings.HasPrefix(requests[i].Target, "/api/vcenter/") {
			t.Fatalf("request %d restarted vCenter work: %+v", i, requests[i])
		}
	}
}

func TestNewClientRejectsInvalidInputs(t *testing.T) {
	tokens := &tokenSource{old: "token", fresh: "fresh"}
	tests := []struct {
		name    string
		vc      string
		kube    string
		session string
		tokens  sut.TokenSource
	}{
		{"vcenter path", "https://vcenter.test/base", "https://kubernetes.test", "session", tokens},
		{"kubernetes scheme", "https://vcenter.test", "ftp://kubernetes.test", "session", tokens},
		{"credentials", "https://user@vcenter.test", "https://kubernetes.test", "session", tokens},
		{"query", "https://vcenter.test?mode=test", "https://kubernetes.test", "session", tokens},
		{"empty session", "https://vcenter.test", "https://kubernetes.test", "", tokens},
		{"newline session", "https://vcenter.test", "https://kubernetes.test", "bad\nheader", tokens},
		{"control session", "https://vcenter.test", "https://kubernetes.test", "bad\x00header", tokens},
		{"nil token source", "https://vcenter.test", "https://kubernetes.test", "session", nil},
	}
	transport := &scriptedTransport{}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := sut.NewClient(tc.vc, tc.kube, tc.session, tc.tokens, &http.Client{Transport: transport}); err == nil {
				t.Fatal("NewClient returned nil error")
			}
		})
	}
	if len(transport.requests) != 0 {
		t.Fatalf("NewClient made %d requests", len(transport.requests))
	}
}

func TestEnsureValidatesEverySpecFieldBeforeIO(t *testing.T) {
	namespace, cluster := validSpecs()
	tests := []struct {
		name   string
		mutate func(*sut.NamespaceSpec, *sut.ClusterSpec)
	}{
		{"namespace name", func(ns *sut.NamespaceSpec, _ *sut.ClusterSpec) { ns.Name = "" }},
		{"supervisor", func(ns *sut.NamespaceSpec, _ *sut.ClusterSpec) { ns.Supervisor = "" }},
		{"cluster name", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.Name = "" }},
		{"cluster class", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.Class = "" }},
		{"version", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.Version = "" }},
		{"control-plane replicas zero", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.ControlPlaneReplicas = 0 }},
		{"control-plane replicas negative", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.ControlPlaneReplicas = -1 }},
		{"worker class", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.WorkerClass = "" }},
		{"worker name", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.WorkerName = "" }},
		{"worker replicas zero", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.WorkerReplicas = 0 }},
		{"worker replicas negative", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.WorkerReplicas = -1 }},
		{"VM class", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.VMClass = "" }},
		{"storage class", func(_ *sut.NamespaceSpec, cluster *sut.ClusterSpec) { cluster.StorageClass = "" }},
	}
	transport := &scriptedTransport{}
	tokens := &tokenSource{old: "token", fresh: "fresh"}
	client := newScriptClient(t, transport, tokens)
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			invalidNamespace, invalidCluster := namespace, cluster
			tc.mutate(&invalidNamespace, &invalidCluster)
			if _, err := client.Ensure(context.Background(), invalidNamespace, invalidCluster); err == nil {
				t.Fatal("Ensure returned nil error")
			}
		})
	}
	if len(transport.requests) != 0 {
		t.Fatalf("validation made %d requests", len(transport.requests))
	}
	if got := tokens.history(); len(got) != 0 {
		t.Fatalf("validation requested tokens: %v", got)
	}
}

func TestNonSuccessStatusesReturnAPIErrorAndDoNotRefresh(t *testing.T) {
	tests := []struct {
		name      string
		statuses  []int
		operation string
		status    int
		tokenCall []bool
	}{
		{"namespace GET", []int{http.StatusServiceUnavailable}, getNamespaceID, http.StatusServiceUnavailable, nil},
		{"namespace POST", []int{http.StatusNotFound, http.StatusConflict}, createNamespaceID, http.StatusConflict, nil},
		{"cluster GET", []int{http.StatusOK, http.StatusForbidden}, "Kubernetes.Cluster.get", http.StatusForbidden, []bool{false}},
		{"cluster POST", []int{http.StatusOK, http.StatusNotFound, http.StatusUnprocessableEntity}, "Kubernetes.Cluster.create", http.StatusUnprocessableEntity, []bool{false}},
	}
	namespace, cluster := validSpecs()
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			tokens := &tokenSource{old: "token", fresh: "fresh"}
			transport := &scriptedTransport{statuses: tc.statuses}
			client := newScriptClient(t, transport, tokens)

			_, err := client.Ensure(context.Background(), namespace, cluster)
			var apiErr *sut.APIError
			if !errors.As(err, &apiErr) ||
				apiErr.OperationID != tc.operation ||
				apiErr.StatusCode != tc.status {
				t.Fatalf("error = %v, want %s APIError %d", err, tc.operation, tc.status)
			}
			if got := tokens.history(); !reflect.DeepEqual(got, tc.tokenCall) {
				t.Fatalf("token calls = %v, want %v", got, tc.tokenCall)
			}
			if len(transport.requests) != len(tc.statuses) {
				t.Fatalf("request count = %d, want %d", len(transport.requests), len(tc.statuses))
			}
			for i, body := range transport.bodies {
				if !body.closed {
					t.Fatalf("response body %d was not closed", i)
				}
			}
		})
	}
}

func TestContextCancellationStopsTheWorkflow(t *testing.T) {
	var calls int
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		return nil, request.Context().Err()
	})
	tokens := &tokenSource{old: "token", fresh: "fresh"}
	client := newScriptClient(t, transport, tokens)
	namespace, cluster := validSpecs()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, err := client.Ensure(ctx, namespace, cluster); !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if calls > 1 {
		t.Fatalf("canceled workflow made %d requests", calls)
	}
	if got := tokens.history(); len(got) != 0 {
		t.Fatalf("canceled workflow requested tokens: %v", got)
	}
}

func TestContractProvenance(t *testing.T) {
	var sources struct {
		Commit       string   `json:"repository_commit_sha"`
		SpecPath     string   `json:"spec_path"`
		License      string   `json:"license"`
		OperationIDs []string `json:"operation_ids"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Commit != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" ||
		sources.SpecPath != "specifications/vsphere/openapi/automation/vcenter.yaml" ||
		sources.License != "Apache-2.0" ||
		!reflect.DeepEqual(sources.OperationIDs, []string{getNamespaceID, createNamespaceID}) {
		t.Fatalf("unexpected source record: %+v", sources)
	}

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"commit_sha"`
			SpecPath string `json:"spec_path"`
			License  string `json:"license"`
		} `json:"derived_from"`
		Operations []struct {
			OperationID string `json:"operation_id"`
		} `json:"operations"`
	}
	raw, err = os.ReadFile(filepath.Join("..", "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatal(err)
	}
	var contractIDs []string
	for _, operation := range contract.Operations {
		contractIDs = append(contractIDs, operation.OperationID)
	}
	if contract.DerivedFrom.Commit != sources.Commit ||
		contract.DerivedFrom.SpecPath != sources.SpecPath ||
		contract.DerivedFrom.License != sources.License ||
		!reflect.DeepEqual(contractIDs, sources.OperationIDs) {
		t.Fatalf("contract provenance does not match official source record")
	}
}

func expectedClusterBody(cluster sut.ClusterSpec) string {
	type metadata struct {
		Name string `json:"name"`
	}
	type controlPlane struct {
		Replicas int `json:"replicas"`
	}
	type machineDeployment struct {
		Class    string `json:"class"`
		Name     string `json:"name"`
		Replicas int    `json:"replicas"`
	}
	type workers struct {
		MachineDeployments []machineDeployment `json:"machineDeployments"`
	}
	type variable struct {
		Name  string `json:"name"`
		Value string `json:"value"`
	}
	type topology struct {
		Class        string       `json:"class"`
		Version      string       `json:"version"`
		ControlPlane controlPlane `json:"controlPlane"`
		Workers      workers      `json:"workers"`
		Variables    []variable   `json:"variables"`
	}
	type spec struct {
		Topology topology `json:"topology"`
	}
	type clusterWire struct {
		APIVersion string   `json:"apiVersion"`
		Kind       string   `json:"kind"`
		Metadata   metadata `json:"metadata"`
		Spec       spec     `json:"spec"`
	}
	object := clusterWire{
		APIVersion: "cluster.x-k8s.io/v1beta2",
		Kind:       "Cluster",
		Metadata:   metadata{Name: cluster.Name},
		Spec: spec{Topology: topology{
			Class:        cluster.Class,
			Version:      cluster.Version,
			ControlPlane: controlPlane{Replicas: cluster.ControlPlaneReplicas},
			Workers: workers{MachineDeployments: []machineDeployment{{
				Class: cluster.WorkerClass, Name: cluster.WorkerName, Replicas: cluster.WorkerReplicas,
			}}},
			Variables: []variable{
				{Name: "vmClass", Value: cluster.VMClass},
				{Name: "storageClass", Value: cluster.StorageClass},
			},
		}},
	}
	value, _ := json.Marshal(object)
	return string(value)
}

func assertExactKeys(t *testing.T, object map[string]any, keys ...string) {
	t.Helper()
	got := make(map[string]bool, len(object))
	for key := range object {
		got[key] = true
	}
	for _, key := range keys {
		if !got[key] {
			t.Fatalf("missing key %q in %#v", key, object)
		}
		delete(got, key)
	}
	if len(got) != 0 {
		t.Fatalf("unexpected optional keys: %#v", got)
	}
}

func assertJSONEqual(t *testing.T, got, want string) {
	t.Helper()
	var gotValue, wantValue any
	if err := json.Unmarshal([]byte(got), &gotValue); err != nil {
		t.Fatalf("body is not JSON: %v", err)
	}
	if err := json.Unmarshal([]byte(want), &wantValue); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(gotValue, wantValue) {
		t.Fatalf("JSON body = %s, want %s", got, want)
	}
}

func assertRelevantHeaders(t *testing.T, got, want http.Header, hasBody bool) {
	t.Helper()
	for name, values := range want {
		if gotValues := got.Values(name); !reflect.DeepEqual(gotValues, values) {
			t.Fatalf("header %s = %q, want %q", name, gotValues, values)
		}
	}
	for _, name := range []string{"Authorization", "Vmware-Api-Session-Id"} {
		if want.Get(name) == "" && got.Get(name) != "" {
			t.Fatalf("unexpected %s header %q", name, got.Values(name))
		}
	}
	if hasBody {
		mediaType, _, err := mime.ParseMediaType(got.Get("Content-Type"))
		if err != nil || mediaType != "application/json" {
			t.Fatalf("Content-Type = %q, want application/json", got.Get("Content-Type"))
		}
	}
}

func assertClusterOmissions(t *testing.T, object map[string]any) {
	t.Helper()
	assertExactKeys(t, object, "apiVersion", "kind", "metadata", "spec")
	metadata := object["metadata"].(map[string]any)
	assertExactKeys(t, metadata, "name")
	spec := object["spec"].(map[string]any)
	assertExactKeys(t, spec, "topology")
	topology := spec["topology"].(map[string]any)
	assertExactKeys(t, topology, "class", "version", "controlPlane", "workers", "variables")
	controlPlane := topology["controlPlane"].(map[string]any)
	assertExactKeys(t, controlPlane, "replicas")
	workers := topology["workers"].(map[string]any)
	assertExactKeys(t, workers, "machineDeployments")
}
