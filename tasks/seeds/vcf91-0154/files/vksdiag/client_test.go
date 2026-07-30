package vksdiag_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"

	"example.com/vksdiag/internal/contractmock"
	"example.com/vksdiag/vksdiag"
)

const (
	contractPath    = "../docs/contract.json"
	sourcesPath     = "../docs/official_sources.json"
	contractDigest  = "17320f4fead8c5856fd489e97fe5bfdb1787216c622ecad4559d72eed5aa3256"
	sourcesDigest   = "7dfd9d7d834a695ca0bbc2e1292707cb88c539cd50b1a6c7ddfc73dbe75925a2"
	pinnedCommitSHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedSpecPath  = "specifications/vsphere/openapi/automation/vcenter.yaml"
	pinnedOperation = "Vcenter.Namespaces.Instances_getV2"

	vcenterSession = "vcenter-session-7f31"
	kubeBearer     = "kube-bearer-51c9"
)

func TestPinnedSpecificationProjection(t *testing.T) {
	t.Parallel()

	for _, test := range []struct {
		path string
		want string
	}{
		{path: contractPath, want: contractDigest},
		{path: sourcesPath, want: sourcesDigest},
	} {
		test := test
		t.Run(test.path, func(t *testing.T) {
			data, err := os.ReadFile(test.path)
			if err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(data)
			if got := hex.EncodeToString(sum[:]); got != test.want {
				t.Fatalf("protected source document changed: got sha256 %s, want %s", got, test.want)
			}
		})
	}

	var contract struct {
		Source struct {
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
			SpecBlobSHA         string `json:"specBlobSha"`
			License             string `json:"license"`
			APIVersion          string `json:"apiVersion"`
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
				Path                string   `json:"path"`
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
		contract.Source.License != sources.License ||
		contract.Source.APIVersion != "9.1.0.0" {
		t.Fatal("contract and official source provenance are not the pinned VCF 9.1 specification")
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{pinnedOperation}) ||
		len(sources.Operations) != 1 ||
		sources.Operations[0].OperationID != pinnedOperation ||
		sources.Operations[0].SpecPath != pinnedSpecPath ||
		sources.Operations[0].Method != http.MethodGet ||
		sources.Operations[0].Path != "/vcenter/namespaces/instances/v2/{namespace}" {
		t.Fatalf("official operation records = %+v, want exactly %s", sources.Operations, pinnedOperation)
	}
	if len(contract.Operations) != 3 {
		t.Fatalf("contract operations = %d, want 3", len(contract.Operations))
	}
	wantOperations := []struct {
		name, sourceKind, operationID, operationKey, path string
	}{
		{
			name:        vksdiag.OperationGetSupervisorNamespace,
			sourceKind:  "openapi",
			operationID: pinnedOperation,
			path:        "/api/vcenter/namespaces/instances/v2/{namespace}",
		},
		{
			name:         vksdiag.OperationListPodEvents,
			sourceKind:   "kubernetes-resource",
			operationKey: "core/v1:namespaced-events:list",
			path:         "/api/v1/namespaces/{namespace}/events",
		},
		{
			name:         vksdiag.OperationReadPodLog,
			sourceKind:   "kubernetes-resource",
			operationKey: "core/v1:namespaced-pods:log",
			path:         "/api/v1/namespaces/{namespace}/pods/{pod}/log",
		},
	}
	for index, want := range wantOperations {
		got := contract.Operations[index]
		if got.Name != want.name || got.SourceKind != want.sourceKind ||
			got.OperationID != want.operationID || got.OperationKey != want.operationKey ||
			got.Method != http.MethodGet || got.Path != want.path {
			t.Fatalf("contract operation %d = %+v, want %+v", index, got, want)
		}
	}

	wantEventOptional := []string{
		"allowWatchBookmarks", "continue", "fieldSelector", "labelSelector", "limit",
		"pretty", "resourceVersion", "resourceVersionMatch", "sendInitialEvents",
		"timeoutSeconds", "watch",
	}
	wantLogOptional := []string{
		"container", "follow", "insecureSkipTLSVerifyBackend", "limitBytes", "pretty",
		"previous", "sinceSeconds", "sinceTime", "tailLines", "timestamps",
	}
	if len(contract.Kubernetes.Operations) != 2 ||
		!reflect.DeepEqual(contract.Kubernetes.Operations[0].OptionalQueryFields, wantEventOptional) ||
		!reflect.DeepEqual(contract.Kubernetes.Operations[1].OptionalQueryFields, wantLogOptional) {
		t.Fatalf("Kubernetes optional inputs = %+v", contract.Kubernetes.Operations)
	}
}

func TestDiagnoseRequiresAgreeingEventAndLogEvidence(t *testing.T) {
	t.Parallel()

	explicitTrue := true
	explicitFalse := false
	tests := []struct {
		name      string
		events    []contractmock.Event
		log       string
		container string
		previous  *bool
		want      vksdiag.Diagnosis
	}{
		{
			name: "both sources confirm and event choice is deterministic",
			events: []contractmock.Event{
				relevantBackOff("zeta.19"),
				{
					Name:              "scheduler.2",
					Type:              "Warning",
					Reason:            "FailedScheduling",
					Message:           "insufficient resources",
					InvolvedKind:      "Pod",
					InvolvedNamespace: "team blue/checkout",
					InvolvedName:      "checkout api/7",
				},
				relevantBackOff("alpha.11"),
			},
			log:       "starting checkout\nfatal: required environment variable PAYMENT_API_URL is not set\n",
			container: "api sidecar",
			previous:  &explicitTrue,
			want: vksdiag.Diagnosis{
				Outcome:                    vksdiag.OutcomeConfirmed,
				Cause:                      vksdiag.CauseMissingRequiredEnvironment,
				MissingEnvironmentVariable: "PAYMENT_API_URL",
				EventName:                  "alpha.11",
			},
		},
		{
			name: "log alone stays inconclusive and unset log options are omitted",
			events: []contractmock.Event{
				{
					Name:              "pulled.1",
					Type:              "Normal",
					Reason:            "Pulled",
					Message:           "image present",
					InvolvedKind:      "Pod",
					InvolvedNamespace: "team blue/checkout",
					InvolvedName:      "checkout api/7",
				},
			},
			log:  "fatal: required environment variable INVENTORY_URL is not set\n",
			want: vksdiag.Diagnosis{Outcome: vksdiag.OutcomeInconclusive},
		},
		{
			name:      "BackOff alone stays inconclusive and explicit false is preserved",
			events:    []contractmock.Event{relevantBackOff("restart.3")},
			log:       "fatal: upstream request timed out\n",
			container: "api",
			previous:  &explicitFalse,
			want:      vksdiag.Diagnosis{Outcome: vksdiag.OutcomeInconclusive},
		},
		{
			name:      "substring is not a complete diagnostic line",
			events:    []contractmock.Event{relevantBackOff("restart.4")},
			log:       "prefix fatal: required environment variable SECRET is not set suffix\n",
			container: "api",
			previous:  &explicitTrue,
			want:      vksdiag.Diagnosis{Outcome: vksdiag.OutcomeInconclusive},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			server := newServer(t, contractmock.Fixture{
				Namespace:  "team blue/checkout",
				Pod:        "checkout api/7",
				Supervisor: "supervisor-42",
				Events:     test.events,
				Log:        test.log,
			})
			client := newClient(t, server)
			request := vksdiag.DiagnoseRequest{
				Supervisor: "supervisor-42",
				Namespace:  "team blue/checkout",
				Pod:        "checkout api/7",
				Container:  test.container,
				Previous:   test.previous,
			}

			got, err := client.Diagnose(context.Background(), request)
			if err != nil {
				t.Fatalf("Diagnose: %v", err)
			}
			if got != test.want {
				t.Fatalf("Diagnosis = %+v, want %+v", got, test.want)
			}

			requests := server.Requests()
			if len(requests) != 3 {
				t.Fatalf("request count = %d, want 3: %+v", len(requests), requests)
			}
			assertWire(t, requests[0], request, test.container, test.previous)
			assertWire(t, requests[1], request, test.container, test.previous)
			assertWire(t, requests[2], request, test.container, test.previous)
		})
	}
}

func TestNamespaceReadinessStopsBeforeKubernetesEvidence(t *testing.T) {
	t.Parallel()

	for _, status := range []string{"CONFIGURING", "REMOVING", "ERROR"} {
		status := status
		t.Run(status, func(t *testing.T) {
			t.Parallel()

			server := newServer(t, contractmock.Fixture{
				Namespace:    "orders",
				Pod:          "checkout-7",
				Supervisor:   "supervisor-42",
				ConfigStatus: status,
			})
			client := newClient(t, server)
			_, err := client.Diagnose(context.Background(), vksdiag.DiagnoseRequest{
				Supervisor: "supervisor-42",
				Namespace:  "orders",
				Pod:        "checkout-7",
			})
			var notReady *vksdiag.NamespaceNotReadyError
			if !errors.As(err, &notReady) ||
				notReady.Operation != vksdiag.OperationGetSupervisorNamespace ||
				notReady.Status != status {
				t.Fatalf("Diagnose error = %#v", err)
			}
			if got := len(server.Requests()); got != 1 {
				t.Fatalf("request count = %d, want 1", got)
			}
		})
	}
}

func TestNonSuccessPreservesOperationAndStops(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		operation string
		status    int
		wantCalls int
	}{
		{
			name:      "vcenter failure",
			operation: vksdiag.OperationGetSupervisorNamespace,
			status:    http.StatusServiceUnavailable,
			wantCalls: 1,
		},
		{
			name:      "event failure",
			operation: vksdiag.OperationListPodEvents,
			status:    http.StatusTooManyRequests,
			wantCalls: 2,
		},
		{
			name:      "log redirect is not followed",
			operation: vksdiag.OperationReadPodLog,
			status:    http.StatusFound,
			wantCalls: 3,
		},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			server := newServer(t, contractmock.Fixture{
				Namespace:    "orders",
				Pod:          "checkout-7",
				Supervisor:   "supervisor-42",
				Events:       []contractmock.Event{simpleBackOff("orders", "checkout-7", "restart.1")},
				Log:          "fatal: required environment variable PAYMENT_URL is not set\n",
				ForcedStatus: map[string]int{test.operation: test.status},
			})
			client := newClient(t, server)
			_, err := client.Diagnose(context.Background(), vksdiag.DiagnoseRequest{
				Supervisor: "supervisor-42",
				Namespace:  "orders",
				Pod:        "checkout-7",
			})
			var apiError *vksdiag.APIError
			if !errors.As(err, &apiError) ||
				apiError.Operation != test.operation ||
				apiError.StatusCode != test.status {
				t.Fatalf("Diagnose error = %#v", err)
			}
			if strings.Contains(err.Error(), vcenterSession) ||
				strings.Contains(err.Error(), kubeBearer) ||
				strings.Contains(err.Error(), "forced failure") {
				t.Fatalf("error exposed protected data: %v", err)
			}
			if got := len(server.Requests()); got != test.wantCalls {
				t.Fatalf("request count = %d, want %d", got, test.wantCalls)
			}
		})
	}
}

func TestValidationIsTrafficFreeAndCallerClientIsUnchanged(t *testing.T) {
	t.Parallel()

	server := newServer(t, contractmock.Fixture{
		Namespace:  "orders",
		Pod:        "checkout-7",
		Supervisor: "supervisor-42",
	})
	originalRedirect := func(_ *http.Request, _ []*http.Request) error {
		return errors.New("caller redirect policy")
	}
	httpClient := server.Client()
	httpClient.CheckRedirect = originalRedirect
	originalPointer := reflect.ValueOf(httpClient.CheckRedirect).Pointer()

	client, err := vksdiag.NewClient(vksdiag.Config{
		VCenterURL:            server.URL(),
		KubernetesURL:         server.URL(),
		VCenterSessionID:      vcenterSession,
		KubernetesBearerToken: kubeBearer,
		HTTPClient:            httpClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if reflect.ValueOf(httpClient.CheckRedirect).Pointer() != originalPointer {
		t.Fatal("NewClient mutated the caller-owned HTTP client")
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("NewClient performed %d requests", got)
	}

	for _, test := range []struct {
		name   string
		config vksdiag.Config
	}{
		{
			name: "vcenter relative URL",
			config: vksdiag.Config{
				VCenterURL:            "/relative",
				KubernetesURL:         server.URL(),
				VCenterSessionID:      vcenterSession,
				KubernetesBearerToken: kubeBearer,
			},
		},
		{
			name: "kubernetes URL path",
			config: vksdiag.Config{
				VCenterURL:            server.URL(),
				KubernetesURL:         server.URL() + "/prefix",
				VCenterSessionID:      vcenterSession,
				KubernetesBearerToken: kubeBearer,
			},
		},
		{
			name: "vcenter URL query",
			config: vksdiag.Config{
				VCenterURL:            server.URL() + "?debug=true",
				KubernetesURL:         server.URL(),
				VCenterSessionID:      vcenterSession,
				KubernetesBearerToken: kubeBearer,
			},
		},
		{
			name: "credential newline",
			config: vksdiag.Config{
				VCenterURL:            server.URL(),
				KubernetesURL:         server.URL(),
				VCenterSessionID:      vcenterSession + "\nnext",
				KubernetesBearerToken: kubeBearer,
			},
		},
		{
			name: "blank bearer",
			config: vksdiag.Config{
				VCenterURL:            server.URL(),
				KubernetesURL:         server.URL(),
				VCenterSessionID:      vcenterSession,
				KubernetesBearerToken: " \t ",
			},
		},
	} {
		test := test
		t.Run(test.name, func(t *testing.T) {
			if _, err := vksdiag.NewClient(test.config); err == nil {
				t.Fatal("NewClient returned nil error")
			}
		})
	}

	if _, err := client.Diagnose(nil, vksdiag.DiagnoseRequest{}); err == nil {
		t.Fatal("Diagnose accepted a nil context")
	}
	for _, request := range []vksdiag.DiagnoseRequest{
		{Namespace: "orders", Pod: "checkout-7"},
		{Supervisor: "supervisor-42", Pod: "checkout-7"},
		{Supervisor: "supervisor-42", Namespace: "orders"},
	} {
		if _, err := client.Diagnose(context.Background(), request); err == nil {
			t.Fatalf("Diagnose accepted invalid request %+v", request)
		}
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("validation performed %d requests", got)
	}
}

func TestContextCancellationRemainsDiscoverable(t *testing.T) {
	t.Parallel()

	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		<-request.Context().Done()
		return nil, request.Context().Err()
	})}
	client, err := vksdiag.NewClient(vksdiag.Config{
		VCenterURL:            "http://127.0.0.1",
		KubernetesURL:         "http://127.0.0.1",
		VCenterSessionID:      vcenterSession,
		KubernetesBearerToken: kubeBearer,
		HTTPClient:            httpClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = client.Diagnose(ctx, vksdiag.DiagnoseRequest{
		Supervisor: "supervisor-42",
		Namespace:  "orders",
		Pod:        "checkout-7",
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Diagnose error = %v, want context cancellation", err)
	}
}

func assertWire(
	t *testing.T,
	request contractmock.Request,
	diagnose vksdiag.DiagnoseRequest,
	container string,
	previous *bool,
) {
	t.Helper()

	if request.Method != http.MethodGet ||
		len(request.Body) != 0 ||
		request.ContentLength != 0 ||
		len(request.TransferEncoding) != 0 ||
		len(request.Header.Values("Content-Length")) != 0 ||
		len(request.Header.Values("Content-Type")) != 0 ||
		!reflect.DeepEqual(request.Header.Values("Accept"), []string{"application/json"}) {
		t.Fatalf("%s wire framing = %+v", request.Operation, request)
	}

	namespace := url.PathEscape(diagnose.Namespace)
	pod := url.PathEscape(diagnose.Pod)
	var wantTarget string
	switch request.Operation {
	case vksdiag.OperationGetSupervisorNamespace:
		wantTarget = "/api/vcenter/namespaces/instances/v2/" + namespace
		assertHeaderValues(t, request.Header, "vmware-api-session-id", []string{vcenterSession})
		assertHeaderValues(t, request.Header, "Authorization", nil)
	case vksdiag.OperationListPodEvents:
		query := url.Values{}
		query.Set(
			"fieldSelector",
			"involvedObject.kind=Pod,involvedObject.namespace="+diagnose.Namespace+
				",involvedObject.name="+diagnose.Pod,
		)
		wantTarget = "/api/v1/namespaces/" + namespace + "/events?" + query.Encode()
		assertHeaderValues(t, request.Header, "Authorization", []string{"Bearer " + kubeBearer})
		assertHeaderValues(t, request.Header, "vmware-api-session-id", nil)
		parsed, err := url.ParseRequestURI(request.RawTarget)
		if err != nil {
			t.Fatal(err)
		}
		if values := parsed.Query(); len(values) != 1 ||
			!reflect.DeepEqual(values["fieldSelector"], query["fieldSelector"]) {
			t.Fatalf("event query = %q", parsed.RawQuery)
		}
	case vksdiag.OperationReadPodLog:
		query := url.Values{}
		if container != "" {
			query.Set("container", container)
		}
		if previous != nil {
			if *previous {
				query.Set("previous", "true")
			} else {
				query.Set("previous", "false")
			}
		}
		wantTarget = "/api/v1/namespaces/" + namespace + "/pods/" + pod + "/log"
		if encoded := query.Encode(); encoded != "" {
			wantTarget += "?" + encoded
		}
		assertHeaderValues(t, request.Header, "Authorization", []string{"Bearer " + kubeBearer})
		assertHeaderValues(t, request.Header, "vmware-api-session-id", nil)
		parsed, err := url.ParseRequestURI(request.RawTarget)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(parsed.Query(), query) {
			t.Fatalf("pod log query = %q, want %q", parsed.RawQuery, query.Encode())
		}
	default:
		t.Fatalf("request matched no contract operation: %+v", request)
	}
	if request.RawTarget != wantTarget {
		t.Fatalf("%s raw target = %q, want %q", request.Operation, request.RawTarget, wantTarget)
	}
}

func assertHeaderValues(t *testing.T, header http.Header, name string, want []string) {
	t.Helper()
	if got := header.Values(name); !reflect.DeepEqual(got, want) {
		t.Fatalf("%s = %q, want %q", name, got, want)
	}
}

func relevantBackOff(name string) contractmock.Event {
	return simpleBackOff("team blue/checkout", "checkout api/7", name)
}

func simpleBackOff(namespace, pod, name string) contractmock.Event {
	return contractmock.Event{
		Name:              name,
		Type:              "Warning",
		Reason:            "BackOff",
		Message:           "Back-off restarting failed container",
		InvolvedKind:      "Pod",
		InvolvedNamespace: namespace,
		InvolvedName:      pod,
	}
}

func newServer(t *testing.T, fixture contractmock.Fixture) *contractmock.Server {
	t.Helper()
	fixture.Auth = contractmock.Auth{
		VCenterSessionID:      vcenterSession,
		KubernetesBearerToken: kubeBearer,
	}
	server, err := contractmock.New(contractPath, fixture)
	if err != nil {
		t.Fatalf("contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server) *vksdiag.Client {
	t.Helper()
	client, err := vksdiag.NewClient(vksdiag.Config{
		VCenterURL:            server.URL(),
		KubernetesURL:         server.URL(),
		VCenterSessionID:      vcenterSession,
		KubernetesBearerToken: kubeBearer,
		HTTPClient:            server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func readJSON(t *testing.T, path string, destination any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatal(err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
