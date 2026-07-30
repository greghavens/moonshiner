package vksprovision_test

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
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"vcf91-0147/internal/contractmock"
	"vcf91-0147/vksprovision"
)

const (
	testSession = "session-secret-vcf91-0147"
	testToken   = "bearer-secret-vcf91-0147"
	testTimeout = 2 * time.Second

	contractSHA = "deb4263987f2edd01ee36fe9ac31519e2c455bec8d968b3f033674d13d1200cb"
	sourcesSHA  = "e96f515ab56d09aaf3875a2c900cf6fc8b54a589b003c795b0c40315e2571cdb"
)

type expectedNamespaceBody struct {
	Supervisor  string `json:"supervisor"`
	Namespace   string `json:"namespace"`
	Description string `json:"description,omitempty"`
}

type expectedMetadata struct {
	Name      string `json:"name"`
	Namespace string `json:"namespace"`
}

type expectedCIDRs struct {
	CIDRBlocks []string `json:"cidrBlocks"`
}

type expectedClusterNetwork struct {
	Pods     *expectedCIDRs `json:"pods,omitempty"`
	Services *expectedCIDRs `json:"services,omitempty"`
}

type expectedVariable struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type expectedControlPlane struct {
	Replicas int32 `json:"replicas"`
}

type expectedMachineDeployment struct {
	Class    string `json:"class"`
	Name     string `json:"name"`
	Replicas int32  `json:"replicas"`
}

type expectedWorkers struct {
	MachineDeployments []expectedMachineDeployment `json:"machineDeployments"`
}

type expectedTopology struct {
	Class        string               `json:"class"`
	Version      string               `json:"version"`
	Variables    []expectedVariable   `json:"variables"`
	ControlPlane expectedControlPlane `json:"controlPlane"`
	Workers      *expectedWorkers     `json:"workers,omitempty"`
}

type expectedClusterSpec struct {
	ClusterNetwork *expectedClusterNetwork `json:"clusterNetwork,omitempty"`
	Topology       expectedTopology        `json:"topology"`
}

type expectedClusterBody struct {
	APIVersion string              `json:"apiVersion"`
	Kind       string              `json:"kind"`
	Metadata   expectedMetadata    `json:"metadata"`
	Spec       expectedClusterSpec `json:"spec"`
}

func TestProtectedContractAndProvenance(t *testing.T) {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve test source")
	}
	root := filepath.Join(filepath.Dir(file), "..")
	assertFileSHA(t, filepath.Join(root, "docs", "contract.json"), contractSHA)
	assertFileSHA(t, filepath.Join(root, "docs", "official_sources.json"), sourcesSHA)

	var sources struct {
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		OperationIDs        []string `json:"operationIds"`
	}
	readJSONFile(t, filepath.Join(root, "docs", "official_sources.json"), &sources)
	if sources.RepositoryCommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" ||
		sources.SpecPath != "specifications/vsphere/openapi/automation/vcenter.yaml" ||
		!reflect.DeepEqual(sources.OperationIDs, []string{"Vcenter.Namespaces.Instances_createV2"}) {
		t.Fatalf("official source pin = %#v", sources)
	}
}

func TestProvisionWireShapeAndTerminalPollingAreTableDriven(t *testing.T) {
	zeroWorkers := int32(0)
	threeWorkers := int32(3)
	tests := []struct {
		name    string
		request vksprovision.ProvisionRequest
	}{
		{
			name: "unset optionals are omitted",
			request: vksprovision.ProvisionRequest{
				Supervisor:           "supervisor-domain-c8",
				Namespace:            "payments-prod",
				ClusterName:          "payments-vks",
				ClusterClass:         "builtin-generic-v3.5.0",
				KubernetesVersion:    "v1.34.1+vmware.1-vkr.4",
				VMClass:              "guaranteed-medium",
				StorageClass:         "vsan-default-storage-policy",
				ControlPlaneReplicas: 3,
			},
		},
		{
			name: "set optionals and explicit zero are retained",
			request: vksprovision.ProvisionRequest{
				Supervisor:           "supervisor-雪",
				Namespace:            "team blue/β",
				NamespaceDescription: "edge team — primary",
				ClusterName:          "edge/vks + β?",
				ClusterClass:         "builtin-generic-v3.5.0",
				KubernetesVersion:    "v1.34.1+vmware.1-vkr.4",
				VMClass:              "guaranteed-large",
				StorageClass:         "vsan-esa-default-policy-raid5",
				ControlPlaneReplicas: 3,
				WorkerReplicas:       &zeroWorkers,
				PodCIDRs:             []string{"10.244.0.0/16"},
				ServiceCIDRs:         []string{"10.96.0.0/12"},
			},
		},
		{
			name: "one network branch remains omitted",
			request: vksprovision.ProvisionRequest{
				Supervisor:           "supervisor-zone-2",
				Namespace:            "research",
				ClusterName:          "research-vks",
				ClusterClass:         "builtin-generic-v3.5.0",
				KubernetesVersion:    "v1.34.1+vmware.1-vkr.4",
				VMClass:              "best-effort-medium",
				StorageClass:         "gold-policy",
				ControlPlaneReplicas: 1,
				WorkerReplicas:       &threeWorkers,
				PodCIDRs:             []string{"172.16.0.0/16"},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractmock.Scenario{
				Namespace:         test.request.Namespace,
				Supervisor:        test.request.Supervisor,
				ClusterName:       test.request.ClusterName,
				ClusterClass:      test.request.ClusterClass,
				KubernetesVersion: test.request.KubernetesVersion,
				Observations: []contractmock.Observation{
					{Status: "False", Reason: "Provisioning", Message: "accepted asynchronously"},
					{Status: "False", Reason: "WaitingForInfrastructure", Message: "machines are starting"},
					{Status: "True", Reason: "Available", Message: "cluster is ready"},
				},
			})
			client := mustClient(t, server.URL(), server.HTTPClient(), 0, 4)
			result, err := client.Provision(context.Background(), test.request)
			if err != nil {
				t.Fatalf("Provision() error = %v", err)
			}
			if result.Namespace != test.request.Namespace ||
				result.ClusterName != test.request.ClusterName ||
				result.ResourceVersion != "3" ||
				result.AvailableReason != "Available" ||
				result.AvailableMessage != "cluster is ready" ||
				result.PollCount != 2 {
				t.Fatalf("Provision() result = %#v", result)
			}

			records := server.Records()
			if len(records) != 4 {
				t.Fatalf("request count = %d, want 4", len(records))
			}
			wantOperations := []string{
				vksprovision.NamespaceCreateOperation,
				vksprovision.ClusterCreateOperation,
				vksprovision.ClusterGetOperation,
				vksprovision.ClusterGetOperation,
			}
			for index, want := range wantOperations {
				if records[index].Operation != want {
					t.Fatalf("request %d operation = %q, want %q", index+1, records[index].Operation, want)
				}
				assertAccept(t, records[index])
				if index == 0 {
					assertVCenterAuth(t, records[index])
				} else {
					assertKubernetesAuth(t, records[index])
				}
			}

			namespaceBody, err := json.Marshal(expectedNamespaceBody{
				Supervisor:  test.request.Supervisor,
				Namespace:   test.request.Namespace,
				Description: test.request.NamespaceDescription,
			})
			if err != nil {
				t.Fatal(err)
			}
			namespaceRecord := records[0]
			if namespaceRecord.ContractName != "createSupervisorNamespace" ||
				namespaceRecord.Method != http.MethodPost ||
				namespaceRecord.RequestURI != "/api/vcenter/namespaces/instances/v2" ||
				namespaceRecord.Body != string(namespaceBody) ||
				namespaceRecord.ContentLength != int64(len(namespaceBody)) {
				t.Fatalf("namespace wire record = %#v\nwant body = %s", namespaceRecord, namespaceBody)
			}
			assertJSONPost(t, namespaceRecord)

			clusterBody := expectedBody(test.request)
			encodedCluster, err := json.Marshal(clusterBody)
			if err != nil {
				t.Fatal(err)
			}
			escapedNamespace := url.PathEscape(test.request.Namespace)
			clusterCollection := "/apis/cluster.x-k8s.io/v1beta2/namespaces/" + escapedNamespace + "/clusters"
			clusterRecord := records[1]
			if clusterRecord.ContractName != "createVksCluster" ||
				clusterRecord.Method != http.MethodPost ||
				clusterRecord.RequestURI != clusterCollection ||
				clusterRecord.Body != string(encodedCluster) ||
				clusterRecord.ContentLength != int64(len(encodedCluster)) {
				t.Fatalf("Cluster create wire record = %#v\nwant body = %s", clusterRecord, encodedCluster)
			}
			assertJSONPost(t, clusterRecord)

			clusterItem := clusterCollection + "/" + url.PathEscape(test.request.ClusterName)
			for _, record := range records[2:] {
				if record.ContractName != "getVksCluster" ||
					record.Method != http.MethodGet ||
					record.RequestURI != clusterItem {
					t.Fatalf("Cluster get wire record = %#v", record)
				}
				assertBodylessGET(t, record)
			}
			assertOptionalOmission(t, namespaceRecord.Body, clusterRecord.Body, test.request)
		})
	}
}

func TestTerminalFailuresAndPollLimitAreTableDriven(t *testing.T) {
	tests := []struct {
		name         string
		observations []contractmock.Observation
		maxPolls     int
		wantPolls    int
		wantType     string
	}{
		{
			name: "terminal provisioning failure",
			observations: []contractmock.Observation{
				{Status: "False", Reason: "Provisioning", Message: "accepted"},
				{Status: "False", Reason: "ProvisioningFailed", Message: "secret condition detail"},
			},
			maxPolls:  4,
			wantPolls: 1,
			wantType:  "failed",
		},
		{
			name: "exact poll exhaustion",
			observations: []contractmock.Observation{
				{Status: "False", Reason: "Provisioning", Message: "accepted"},
				{Status: "False", Reason: "WaitingForInfrastructure", Message: "still waiting"},
			},
			maxPolls:  1,
			wantPolls: 1,
			wantType:  "limit",
		},
		{
			name: "unknown condition is protocol error",
			observations: []contractmock.Observation{
				{Status: "False", Reason: "ReconcilingElsewhere", Message: "server-only detail"},
			},
			maxPolls:  4,
			wantPolls: 0,
			wantType:  "protocol",
		},
		{
			name: "unknown status is protocol error",
			observations: []contractmock.Observation{
				{Status: "Unknown", Reason: "Provisioning", Message: "server-only detail"},
			},
			maxPolls:  4,
			wantPolls: 0,
			wantType:  "protocol",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := validRequest()
			server := contractmock.Start(t, contractmock.Scenario{
				Namespace:         request.Namespace,
				Supervisor:        request.Supervisor,
				ClusterName:       request.ClusterName,
				ClusterClass:      request.ClusterClass,
				KubernetesVersion: request.KubernetesVersion,
				Observations:      test.observations,
			})
			client := mustClient(t, server.URL(), server.HTTPClient(), 0, test.maxPolls)
			_, err := client.Provision(context.Background(), request)
			if err == nil {
				t.Fatal("Provision() error = nil")
			}
			switch test.wantType {
			case "failed":
				var target *vksprovision.ClusterFailedError
				if !errors.As(err, &target) || target.Reason != "ProvisioningFailed" {
					t.Fatalf("error = %#v, want *ClusterFailedError", err)
				}
			case "limit":
				var target *vksprovision.PollLimitError
				if !errors.As(err, &target) || target.MaxPolls != test.maxPolls {
					t.Fatalf("error = %#v, want *PollLimitError", err)
				}
			case "protocol":
				var target *vksprovision.ProtocolError
				if !errors.As(err, &target) || target.Operation != vksprovision.ClusterCreateOperation {
					t.Fatalf("error = %#v, want create *ProtocolError", err)
				}
			}
			for _, formatted := range []string{fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err), fmt.Sprintf("%#v", err)} {
				for _, forbidden := range []string{testSession, testToken, "secret condition detail", "server-only detail"} {
					if strings.Contains(formatted, forbidden) {
						t.Fatalf("formatted error leaked %q: %q", forbidden, formatted)
					}
				}
			}
			records := server.Records()
			if len(records) != 2+test.wantPolls {
				t.Fatalf("request count = %d, want %d", len(records), 2+test.wantPolls)
			}
		})
	}
}

func TestValidationAndCancellationHappenBeforeTraffic(t *testing.T) {
	request := validRequest()
	server := contractmock.Start(t, contractmock.Scenario{
		Namespace:         request.Namespace,
		Supervisor:        request.Supervisor,
		ClusterName:       request.ClusterName,
		ClusterClass:      request.ClusterClass,
		KubernetesVersion: request.KubernetesVersion,
		Observations: []contractmock.Observation{
			{Status: "True", Reason: "Available", Message: "ready"},
		},
	})
	validConfig := vksprovision.Config{
		VCenterURL:      server.URL(),
		KubernetesURL:   server.URL(),
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      server.HTTPClient(),
		Timeout:         testTimeout,
		MaxPolls:        3,
	}
	configTests := []struct {
		name   string
		mutate func(*vksprovision.Config)
	}{
		{"relative vCenter URL", func(config *vksprovision.Config) { config.VCenterURL = "/relative" }},
		{"vCenter URL path", func(config *vksprovision.Config) { config.VCenterURL += "/api" }},
		{"Kubernetes URL query", func(config *vksprovision.Config) { config.KubernetesURL += "?x=1" }},
		{"Kubernetes URL credentials", func(config *vksprovision.Config) { config.KubernetesURL = "http://u:p@127.0.0.1" }},
		{"blank session", func(config *vksprovision.Config) { config.VCenterSession = " \t" }},
		{"unsafe token", func(config *vksprovision.Config) { config.KubernetesToken = "token\r\nX: y" }},
		{"zero timeout", func(config *vksprovision.Config) { config.Timeout = 0 }},
		{"negative polling", func(config *vksprovision.Config) { config.PollInterval = -time.Nanosecond }},
		{"zero max polls", func(config *vksprovision.Config) { config.MaxPolls = 0 }},
	}
	for _, test := range configTests {
		t.Run(test.name, func(t *testing.T) {
			config := validConfig
			test.mutate(&config)
			if _, err := vksprovision.NewClient(config); err == nil {
				t.Fatal("NewClient() error = nil")
			}
		})
	}

	client, err := vksprovision.NewClient(validConfig)
	if err != nil {
		t.Fatal(err)
	}
	requestTests := []struct {
		name    string
		context context.Context
		mutate  func(*vksprovision.ProvisionRequest)
	}{
		{"nil context", nil, func(*vksprovision.ProvisionRequest) {}},
		{"blank supervisor", context.Background(), func(request *vksprovision.ProvisionRequest) { request.Supervisor = "\t" }},
		{"blank namespace", context.Background(), func(request *vksprovision.ProvisionRequest) { request.Namespace = "" }},
		{"blank Cluster name", context.Background(), func(request *vksprovision.ProvisionRequest) { request.ClusterName = " " }},
		{"blank Cluster class", context.Background(), func(request *vksprovision.ProvisionRequest) { request.ClusterClass = "" }},
		{"blank Kubernetes version", context.Background(), func(request *vksprovision.ProvisionRequest) { request.KubernetesVersion = "" }},
		{"blank VM class", context.Background(), func(request *vksprovision.ProvisionRequest) { request.VMClass = "" }},
		{"blank storage class", context.Background(), func(request *vksprovision.ProvisionRequest) { request.StorageClass = "" }},
		{"zero control plane", context.Background(), func(request *vksprovision.ProvisionRequest) { request.ControlPlaneReplicas = 0 }},
	}
	for _, test := range requestTests {
		t.Run(test.name, func(t *testing.T) {
			candidate := request
			test.mutate(&candidate)
			if _, err := client.Provision(test.context, candidate); err == nil {
				t.Fatal("Provision() error = nil")
			}
		})
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.Provision(ctx, request); !errors.Is(err, context.Canceled) {
		t.Fatalf("pre-canceled Provision() error = %v, want context.Canceled", err)
	}
	if records := server.Records(); len(records) != 0 {
		t.Fatalf("invalid inputs made %d requests", len(records))
	}
}

func TestHTTPFailuresAreStructuredAndNeverRetried(t *testing.T) {
	tests := []struct {
		name         string
		mutate       func(*contractmock.Scenario)
		wantOp       string
		wantStatus   int
		wantRequests int
	}{
		{
			name: "namespace create fails",
			mutate: func(scenario *contractmock.Scenario) {
				scenario.NamespaceStatus = http.StatusConflict
			},
			wantOp:       vksprovision.NamespaceCreateOperation,
			wantStatus:   http.StatusConflict,
			wantRequests: 1,
		},
		{
			name: "Cluster create fails",
			mutate: func(scenario *contractmock.Scenario) {
				scenario.ClusterCreateStatus = http.StatusUnprocessableEntity
			},
			wantOp:       vksprovision.ClusterCreateOperation,
			wantStatus:   http.StatusUnprocessableEntity,
			wantRequests: 2,
		},
		{
			name: "Cluster get fails",
			mutate: func(scenario *contractmock.Scenario) {
				scenario.ClusterGetStatus = http.StatusInternalServerError
			},
			wantOp:       vksprovision.ClusterGetOperation,
			wantStatus:   http.StatusInternalServerError,
			wantRequests: 3,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := validRequest()
			scenario := contractmock.Scenario{
				Namespace:         request.Namespace,
				Supervisor:        request.Supervisor,
				ClusterName:       request.ClusterName,
				ClusterClass:      request.ClusterClass,
				KubernetesVersion: request.KubernetesVersion,
				Observations: []contractmock.Observation{
					{Status: "False", Reason: "Provisioning", Message: "not terminal"},
					{Status: "True", Reason: "Available", Message: "ready"},
				},
				ErrorBody: "server body " + testSession + " " + testToken,
			}
			test.mutate(&scenario)
			server := contractmock.Start(t, scenario)
			client := mustClient(t, server.URL(), server.HTTPClient(), 0, 3)
			_, err := client.Provision(context.Background(), request)
			var apiError *vksprovision.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %v (%T), want *APIError", err, err)
			}
			if apiError.Operation != test.wantOp || apiError.StatusCode != test.wantStatus {
				t.Fatalf("APIError = %#v", apiError)
			}
			for _, formatted := range []string{fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err), fmt.Sprintf("%#v", err)} {
				if strings.Contains(formatted, testSession) || strings.Contains(formatted, testToken) ||
					strings.Contains(formatted, "server body") {
					t.Fatalf("formatted API error leaked protected data: %q", formatted)
				}
			}
			if records := server.Records(); len(records) != test.wantRequests {
				t.Fatalf("request count = %d, want %d", len(records), test.wantRequests)
			}
		})
	}
}

func TestNewClientDoesNotMutateCallerHTTPClient(t *testing.T) {
	redirect := func(*http.Request, []*http.Request) error { return nil }
	caller := &http.Client{Timeout: 77 * time.Second, CheckRedirect: redirect}
	_, err := vksprovision.NewClient(vksprovision.Config{
		VCenterURL:      "http://127.0.0.1:1",
		KubernetesURL:   "http://127.0.0.1:2",
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      caller,
		Timeout:         testTimeout,
		MaxPolls:        2,
	})
	if err != nil {
		t.Fatal(err)
	}
	if caller.Timeout != 77*time.Second || reflect.ValueOf(caller.CheckRedirect).Pointer() != reflect.ValueOf(redirect).Pointer() {
		t.Fatal("NewClient mutated the caller-owned HTTP client")
	}
}

func expectedBody(request vksprovision.ProvisionRequest) expectedClusterBody {
	var network *expectedClusterNetwork
	if len(request.PodCIDRs) > 0 || len(request.ServiceCIDRs) > 0 {
		network = &expectedClusterNetwork{}
		if len(request.PodCIDRs) > 0 {
			network.Pods = &expectedCIDRs{CIDRBlocks: request.PodCIDRs}
		}
		if len(request.ServiceCIDRs) > 0 {
			network.Services = &expectedCIDRs{CIDRBlocks: request.ServiceCIDRs}
		}
	}
	var workers *expectedWorkers
	if request.WorkerReplicas != nil {
		workers = &expectedWorkers{MachineDeployments: []expectedMachineDeployment{
			{Class: "node-pool", Name: "workers", Replicas: *request.WorkerReplicas},
		}}
	}
	return expectedClusterBody{
		APIVersion: "cluster.x-k8s.io/v1beta2",
		Kind:       "Cluster",
		Metadata: expectedMetadata{
			Name:      request.ClusterName,
			Namespace: request.Namespace,
		},
		Spec: expectedClusterSpec{
			ClusterNetwork: network,
			Topology: expectedTopology{
				Class:   request.ClusterClass,
				Version: request.KubernetesVersion,
				Variables: []expectedVariable{
					{Name: "vmClass", Value: request.VMClass},
					{Name: "storageClass", Value: request.StorageClass},
				},
				ControlPlane: expectedControlPlane{Replicas: request.ControlPlaneReplicas},
				Workers:      workers,
			},
		},
	}
}

func assertOptionalOmission(t *testing.T, namespaceBody, clusterBody string, request vksprovision.ProvisionRequest) {
	t.Helper()
	var namespaceObject map[string]any
	var clusterObject map[string]any
	if err := json.Unmarshal([]byte(namespaceBody), &namespaceObject); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(clusterBody), &clusterObject); err != nil {
		t.Fatal(err)
	}
	if request.NamespaceDescription == "" {
		if _, exists := namespaceObject["description"]; exists {
			t.Fatal("unset namespace description was serialized")
		}
	}
	for _, key := range []string{
		"zones", "network_spec", "resource_spec", "access_list", "storage_specs",
		"networks", "vm_service_spec", "content_libraries", "creator",
		"namespace_network", "edges", "infrastructure_policies",
	} {
		if _, exists := namespaceObject[key]; exists {
			t.Fatalf("unset CreateSpecV2 optional field %q was serialized", key)
		}
	}
	spec := clusterObject["spec"].(map[string]any)
	topology := spec["topology"].(map[string]any)
	if len(request.PodCIDRs) == 0 && len(request.ServiceCIDRs) == 0 {
		if _, exists := spec["clusterNetwork"]; exists {
			t.Fatal("unset clusterNetwork was serialized")
		}
	}
	if request.WorkerReplicas == nil {
		if _, exists := topology["workers"]; exists {
			t.Fatal("unset workers was serialized")
		}
	}
	for _, forbidden := range []string{"dryRun", "fieldManager", "fieldValidation", "pretty"} {
		if strings.Contains(clusterBody, forbidden) {
			t.Fatalf("unset Kubernetes option %q was serialized", forbidden)
		}
	}
}

func assertAccept(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	if got := headerValues(record, "Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("Accept values = %v", got)
	}
}

func assertVCenterAuth(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	if got := headerValues(record, "vmware-api-session-id"); !reflect.DeepEqual(got, []string{testSession}) {
		t.Fatalf("vCenter session values = %v", got)
	}
	if got := headerValues(record, "Authorization"); len(got) != 0 {
		t.Fatalf("vCenter request carried Authorization: %v", got)
	}
}

func assertKubernetesAuth(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	if got := headerValues(record, "Authorization"); !reflect.DeepEqual(got, []string{"Bearer " + testToken}) {
		t.Fatalf("Kubernetes Authorization values = %v", got)
	}
	if got := headerValues(record, "vmware-api-session-id"); len(got) != 0 {
		t.Fatalf("Kubernetes request carried vCenter session: %v", got)
	}
}

func assertJSONPost(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	if got := headerValues(record, "Content-Type"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("POST Content-Type values = %v", got)
	}
	if strings.Contains(record.RequestURI, "?") {
		t.Fatalf("POST request has query or bare ?: %q", record.RequestURI)
	}
}

func assertBodylessGET(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	if record.Body != "" || record.ContentLength != 0 {
		t.Fatalf("GET has a body: %#v", record)
	}
	if got := headerValues(record, "Content-Type"); len(got) != 0 {
		t.Fatalf("GET Content-Type values = %v", got)
	}
	if got := headerValues(record, "Content-Length"); len(got) != 0 {
		t.Fatalf("GET Content-Length values = %v", got)
	}
	if strings.Contains(record.RequestURI, "?") {
		t.Fatalf("GET request has query or bare ?: %q", record.RequestURI)
	}
}

func headerValues(record contractmock.RequestRecord, name string) []string {
	for key, values := range record.Header {
		if strings.EqualFold(key, name) {
			return values
		}
	}
	return nil
}

func mustClient(t *testing.T, baseURL string, httpClient *http.Client, interval time.Duration, maxPolls int) *vksprovision.Client {
	t.Helper()
	client, err := vksprovision.NewClient(vksprovision.Config{
		VCenterURL:      baseURL,
		KubernetesURL:   baseURL,
		VCenterSession:  testSession,
		KubernetesToken: testToken,
		HTTPClient:      httpClient,
		Timeout:         testTimeout,
		PollInterval:    interval,
		MaxPolls:        maxPolls,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	return client
}

func validRequest() vksprovision.ProvisionRequest {
	return vksprovision.ProvisionRequest{
		Supervisor:           "supervisor-1",
		Namespace:            "team-a",
		ClusterName:          "team-a-vks",
		ClusterClass:         "builtin-generic-v3.5.0",
		KubernetesVersion:    "v1.34.1+vmware.1-vkr.4",
		VMClass:              "guaranteed-medium",
		StorageClass:         "vsan-default-storage-policy",
		ControlPlaneReplicas: 3,
	}
}

func assertFileSHA(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != want {
		t.Fatalf("%s SHA256 = %s, want %s", filepath.Base(path), got, want)
	}
}

func readJSONFile(t *testing.T, path string, value any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, value); err != nil {
		t.Fatal(err)
	}
}
