package albdeploy_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"

	albdeploy "vcf91-0030"
	"vcf91-0030/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "fdc012957bfadbff8d04c82a4a456f6c54814be984f5eaa8a3fa36ad4c63e77d"
	sourcesSHA256  = "4de74203e2880158ad3bac9e01b9d17c9565a87cf968fd07f3a6de5aa74a0d72"
	mockSHA256     = "45413791dbb78a373ef57aa71c59916b3919ecc93663abd3489816dbe0889707"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)
	assertFileHash(t, "internal/contractmock/server.go", mockSHA256)

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Operations []struct {
			operationSource
			QueryParameters []struct {
				Name     string `json:"name"`
				In       string `json:"in"`
				Required bool   `json:"required"`
				Schema   struct {
					Type    string `json:"type"`
					Default bool   `json:"default"`
				} `json:"schema"`
			} `json:"query_parameters"`
			RequestBody struct {
				Required  bool   `json:"required"`
				MediaType string `json:"media_type"`
				SchemaRef string `json:"schema_ref"`
			} `json:"request_body"`
			Responses map[string]struct {
				Description string `json:"description"`
				MediaType   string `json:"media_type"`
				SchemaRef   string `json:"schema_ref"`
			} `json:"responses"`
		} `json:"operations"`
		Schemas map[string]struct {
			Required            []string       `json:"required"`
			Properties          map[string]any `json:"properties"`
			ProjectedProperties map[string]any `json:"projected_properties"`
		} `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			URL     string `json:"url"`
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			OpenAPI string `json:"openapi_version"`
			Version string `json:"info_version"`
			RawURL  string `json:"pinned_raw_url"`
		} `json:"specification"`
		Operations []struct {
			operationSource
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			Pointer  string `json:"json_pointer"`
		} `json:"operations"`
		Derivation string `json:"derivation"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit {
		t.Fatalf("wrong repository commit: contract=%q sources=%q",
			contract.DerivedFrom.Commit, sources.Repository.Commit)
	}
	if contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf("wrong specification path: contract=%q sources=%q",
			contract.DerivedFrom.SpecPath, sources.Specification.Path)
	}
	if sources.Repository.URL != "https://github.com/vmware/vcf-api-specs" ||
		!strings.Contains(sources.Specification.RawURL, expectedCommit+"/"+expectedSpec) ||
		contract.DerivedFrom.OpenAPI != "3.0.1" ||
		sources.Specification.OpenAPI != "3.0.1" ||
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") {
		t.Fatal("contract does not pin the official VCF 9.1 OpenAPI source")
	}

	wantOperations := []operationSource{
		{
			OperationID: "validateALBControllerClusterCreationSpec",
			Method:      http.MethodPost,
			Path:        "/v1/alb-clusters/validations",
		},
		{
			OperationID: "deployALBCluster",
			Method:      http.MethodPost,
			Path:        "/v1/alb-clusters",
		},
	}
	if len(contract.Operations) != len(wantOperations) ||
		len(sources.Operations) != len(wantOperations) {
		t.Fatalf("wrong operation counts: contract=%d sources=%d",
			len(contract.Operations), len(sources.Operations))
	}
	wantPointers := []string{
		"/paths/~1v1~1alb-clusters~1validations/post",
		"/paths/~1v1~1alb-clusters/post",
	}
	for index, want := range wantOperations {
		operation := contract.Operations[index]
		source := sources.Operations[index]
		if operation.operationSource != want ||
			source.operationSource != want ||
			source.Commit != expectedCommit ||
			source.SpecPath != expectedSpec ||
			source.Pointer != wantPointers[index] {
			t.Fatalf("operation %d provenance mismatch: %#v %#v",
				index, operation.operationSource, source)
		}
		if len(operation.QueryParameters) != 1 {
			t.Fatalf("operation %d query projection: %#v",
				index, operation.QueryParameters)
		}
		parameter := operation.QueryParameters[0]
		if parameter.Name != "skipCompatibilityCheck" ||
			parameter.In != "query" ||
			parameter.Required ||
			parameter.Schema.Type != "boolean" ||
			parameter.Schema.Default {
			t.Fatalf("operation %d optional query projection: %#v",
				index, parameter)
		}
		if !operation.RequestBody.Required ||
			operation.RequestBody.MediaType != "application/json" ||
			operation.RequestBody.SchemaRef !=
				"#/components/schemas/AlbControllerClusterSpec" {
			t.Fatalf("operation %d request body projection: %#v",
				index, operation.RequestBody)
		}
	}
	if len(contract.Operations[0].Responses) != 3 ||
		contract.Operations[0].Responses["200"].SchemaRef !=
			"#/components/schemas/Validation" ||
		contract.Operations[0].Responses["400"].SchemaRef !=
			"#/components/schemas/Error" ||
		contract.Operations[0].Responses["500"].SchemaRef !=
			"#/components/schemas/Error" {
		t.Fatalf("validation response projection: %#v",
			contract.Operations[0].Responses)
	}
	if len(contract.Operations[1].Responses) != 3 ||
		contract.Operations[1].Responses["202"].SchemaRef !=
			"#/components/schemas/Task" ||
		contract.Operations[1].Responses["400"].SchemaRef !=
			"#/components/schemas/Error" ||
		contract.Operations[1].Responses["500"].SchemaRef !=
			"#/components/schemas/Error" {
		t.Fatalf("deployment response projection: %#v",
			contract.Operations[1].Responses)
	}

	clusterSchema := contract.Schemas["AlbControllerClusterSpec"]
	wantRequired := []string{
		"adminPassword",
		"bundleId",
		"clusterFqdn",
		"clusterName",
		"formFactor",
		"nsxIds",
	}
	if !reflect.DeepEqual(clusterSchema.Required, wantRequired) {
		t.Fatalf("cluster required properties = %v", clusterSchema.Required)
	}
	wantClusterProperties := []string{
		"adminPassword",
		"bundleId",
		"clusterFqdn",
		"clusterName",
		"formFactor",
		"nodes",
		"nsxIds",
		"vcfopsAdminPassword",
	}
	if got := sortedKeys(clusterSchema.Properties); !reflect.DeepEqual(got, wantClusterProperties) {
		t.Fatalf("cluster properties = %v", got)
	}
	wantSchemas := []string{
		"AlbControllerClusterSpec",
		"AlbControllerNodeSpec",
		"Error",
		"Task",
		"Validation",
	}
	if got := sortedSchemaKeys(contract.Schemas); !reflect.DeepEqual(got, wantSchemas) {
		t.Fatalf("contract schemas = %v", got)
	}
}

func TestGuardedDeployExactWireShape(t *testing.T) {
	explicitFalse := false
	opsPassword := "ops-secret-b"
	nodes := []albdeploy.AlbControllerNodeSpec{
		{IPAddress: "192.0.2.10"},
		{IPAddress: "192.0.2.11"},
	}
	tests := []struct {
		name      string
		spec      albdeploy.AlbControllerClusterSpec
		options   albdeploy.DeployOptions
		wantQuery string
		wantBody  string
	}{
		{
			name: "unset optionals are omitted",
			spec: albdeploy.AlbControllerClusterSpec{
				NSXIDs:        []string{"nsx-a"},
				ClusterName:   "alb-a",
				ClusterFQDN:   "alb-a.example.test",
				FormFactor:    "SMALL",
				AdminPassword: "admin-secret-a",
				BundleID:      "bundle-a",
			},
			wantBody: `{"nsxIds":["nsx-a"],"clusterName":"alb-a","clusterFqdn":"alb-a.example.test","formFactor":"SMALL","adminPassword":"admin-secret-a","bundleId":"bundle-a"}`,
		},
		{
			name: "explicit false and optional body members are present",
			spec: albdeploy.AlbControllerClusterSpec{
				NSXIDs:              []string{"nsx-b"},
				ClusterName:         "alb-b",
				ClusterFQDN:         "alb-b.example.test",
				FormFactor:          "LARGE",
				AdminPassword:       "admin-secret-b",
				Nodes:               &nodes,
				BundleID:            "bundle-b",
				VCFOpsAdminPassword: &opsPassword,
			},
			options: albdeploy.DeployOptions{
				SkipCompatibilityCheck: &explicitFalse,
			},
			wantQuery: "skipCompatibilityCheck=false",
			wantBody:  `{"nsxIds":["nsx-b"],"clusterName":"alb-b","clusterFqdn":"alb-b.example.test","formFactor":"LARGE","adminPassword":"admin-secret-b","nodes":[{"ipAddress":"192.0.2.10"},{"ipAddress":"192.0.2.11"}],"bundleId":"bundle-b","vcfopsAdminPassword":"ops-secret-b"}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(contractmock.Plan{})
			defer server.Close()
			client := newClient(t, server)

			result, err := client.DeployALBCluster(
				context.Background(),
				test.spec,
				test.options,
			)
			if err != nil {
				t.Fatalf("DeployALBCluster: %v", err)
			}
			if !result.Deployed ||
				result.Validation.ExecutionStatus != "COMPLETED" ||
				result.Validation.ResultStatus != "SUCCEEDED" ||
				result.Task == nil ||
				result.Task.ID == "" ||
				result.Task.Status != "IN_PROGRESS" {
				t.Fatalf("result = %#v", result)
			}
			if server.EffectCount() != 1 {
				t.Fatalf("effect count = %d, want 1", server.EffectCount())
			}

			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("request count = %d, want 2", len(requests))
			}
			wantPaths := []string{
				"/v1/alb-clusters/validations",
				"/v1/alb-clusters",
			}
			wantOperations := []string{
				"validateALBControllerClusterCreationSpec",
				"deployALBCluster",
			}
			wantStatuses := []int{http.StatusOK, http.StatusAccepted}
			wantHost := strings.TrimPrefix(server.URL(), "http://")
			for index, request := range requests {
				wantTarget := wantPaths[index]
				if test.wantQuery != "" {
					wantTarget += "?" + test.wantQuery
				}
				if request.OperationID != wantOperations[index] ||
					request.Method != http.MethodPost ||
					request.RequestURI != wantTarget ||
					request.Path != wantPaths[index] ||
					request.RawQuery != test.wantQuery ||
					request.Host != wantHost ||
					request.ResponseStatus != wantStatuses[index] {
					t.Fatalf("request %d target mismatch: %#v", index, request)
				}
				if request.Header.Get("Accept") != "application/json" ||
					request.Header.Get("Authorization") !=
						"Bearer "+server.Token() ||
					request.Header.Get("Content-Type") != "application/json" {
					t.Fatalf("request %d headers: %#v", index, request.Header)
				}
				if request.ContentLength != int64(len(test.wantBody)) ||
					len(request.TransferEncoding) != 0 ||
					string(request.Body) != test.wantBody {
					t.Fatalf("request %d body = %q, length=%d, transfer=%v",
						index, request.Body, request.ContentLength,
						request.TransferEncoding)
				}
			}
			if !reflect.DeepEqual(requests[0].Body, requests[1].Body) {
				t.Fatal("precheck and mutation request documents differ")
			}

			var body map[string]json.RawMessage
			if err := json.Unmarshal(requests[0].Body, &body); err != nil {
				t.Fatalf("decode recorded body: %v", err)
			}
			if test.spec.Nodes == nil {
				if _, exists := body["nodes"]; exists {
					t.Fatal("unset nodes was serialized")
				}
			}
			if test.spec.VCFOpsAdminPassword == nil {
				if _, exists := body["vcfopsAdminPassword"]; exists {
					t.Fatal("unset vcfopsAdminPassword was serialized")
				}
			}
			if test.options.SkipCompatibilityCheck == nil &&
				strings.Contains(requests[0].RequestURI,
					"skipCompatibilityCheck") {
				t.Fatal("unset skipCompatibilityCheck was serialized")
			}
		})
	}
}

func TestPrecheckResultGatesMutation(t *testing.T) {
	tests := []struct {
		name      string
		execution string
		result    string
	}{
		{name: "failed result", execution: "COMPLETED", result: "FAILED"},
		{name: "warning result", execution: "COMPLETED", result: "WARNING"},
		{name: "cancelled execution", execution: "CANCELLED", result: "UNKNOWN"},
		{name: "unknown execution", execution: "UNKNOWN", result: "UNKNOWN"},
		{name: "still in progress", execution: "IN_PROGRESS", result: "UNKNOWN"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(contractmock.Plan{
				PrecheckExecutionStatus: test.execution,
				PrecheckResultStatus:    test.result,
			})
			defer server.Close()
			client := newClient(t, server)

			result, err := client.DeployALBCluster(
				context.Background(),
				minimalSpec(),
				albdeploy.DeployOptions{},
			)
			var precheckError *albdeploy.PrecheckError
			if !errors.As(err, &precheckError) {
				t.Fatalf("error = %T %v, want *PrecheckError", err, err)
			}
			if result.Deployed || result.Task != nil ||
				result.Validation.ExecutionStatus != test.execution ||
				result.Validation.ResultStatus != test.result ||
				precheckError.Validation != result.Validation {
				t.Fatalf("result=%#v error=%#v", result, precheckError)
			}
			requests := server.Requests()
			if len(requests) != 1 ||
				requests[0].OperationID !=
					"validateALBControllerClusterCreationSpec" {
				t.Fatalf("requests = %#v", requests)
			}
			if server.EffectCount() != 0 {
				t.Fatalf("failed precheck changed state %d times",
					server.EffectCount())
			}
		})
	}
}

func TestHTTPFailuresAndExactStatuses(t *testing.T) {
	tests := []struct {
		name           string
		plan           contractmock.Plan
		wantOperation  string
		wantStatus     int
		wantRequests   int
		wantValidation bool
	}{
		{
			name: "precheck server error blocks mutation",
			plan: contractmock.Plan{
				PrecheckStatus: http.StatusInternalServerError,
			},
			wantOperation: "validateALBControllerClusterCreationSpec",
			wantStatus:    http.StatusInternalServerError,
			wantRequests:  1,
		},
		{
			name: "undocumented precheck accepted status is not success",
			plan: contractmock.Plan{
				PrecheckStatus: http.StatusAccepted,
			},
			wantOperation: "validateALBControllerClusterCreationSpec",
			wantStatus:    http.StatusAccepted,
			wantRequests:  1,
		},
		{
			name: "deployment server error follows successful precheck",
			plan: contractmock.Plan{
				DeployStatus: http.StatusInternalServerError,
			},
			wantOperation:  "deployALBCluster",
			wantStatus:     http.StatusInternalServerError,
			wantRequests:   2,
			wantValidation: true,
		},
		{
			name: "undocumented deployment ok status is not success",
			plan: contractmock.Plan{
				DeployStatus: http.StatusOK,
			},
			wantOperation:  "deployALBCluster",
			wantStatus:     http.StatusOK,
			wantRequests:   2,
			wantValidation: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(test.plan)
			defer server.Close()
			client := newClient(t, server)

			result, err := client.DeployALBCluster(
				context.Background(),
				minimalSpec(),
				albdeploy.DeployOptions{},
			)
			var apiError *albdeploy.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiError.OperationID != test.wantOperation ||
				apiError.StatusCode != test.wantStatus ||
				apiError.ErrorCode == "" ||
				apiError.Message == "" ||
				apiError.RemediationMessage == "" ||
				apiError.ReferenceToken == "" {
				t.Fatalf("APIError = %#v", apiError)
			}
			if strings.Contains(err.Error(), server.Token()) ||
				strings.Contains(err.Error(), "admin-secret") ||
				strings.Contains(err.Error(), apiError.Message) {
				t.Fatalf("error text exposes sensitive detail: %q", err)
			}
			if len(server.Requests()) != test.wantRequests {
				t.Fatalf("request count = %d, want %d",
					len(server.Requests()), test.wantRequests)
			}
			if test.wantValidation != (result.Validation.ID != "") {
				t.Fatalf("result validation = %#v", result.Validation)
			}
			if result.Deployed || result.Task != nil ||
				server.EffectCount() != 0 {
				t.Fatalf("failed operation changed state: result=%#v effect=%d",
					result, server.EffectCount())
			}
		})
	}
}

func TestLocalValidationPreventsTraffic(t *testing.T) {
	server := contractmock.New(contractmock.Plan{})
	defer server.Close()
	client := newClient(t, server)

	twoNSX := minimalSpec()
	twoNSX.NSXIDs = []string{"nsx-a", "nsx-b"}
	badFormFactor := minimalSpec()
	badFormFactor.FormFactor = "TINY"
	emptyRequired := minimalSpec()
	emptyRequired.BundleID = ""
	noNodes := []albdeploy.AlbControllerNodeSpec{}
	emptyOptionalPassword := ""
	badNodes := minimalSpec()
	badNodes.Nodes = &noNodes
	badOptionalPassword := minimalSpec()
	badOptionalPassword.VCFOpsAdminPassword = &emptyOptionalPassword

	tests := []struct {
		name string
		ctx  context.Context
		spec albdeploy.AlbControllerClusterSpec
	}{
		{
			name: "nil context",
			ctx:  nil,
			spec: minimalSpec(),
		},
		{
			name: "missing NSX id",
			ctx:  context.Background(),
			spec: func() albdeploy.AlbControllerClusterSpec {
				spec := minimalSpec()
				spec.NSXIDs = nil
				return spec
			}(),
		},
		{
			name: "more than one NSX id",
			ctx:  context.Background(),
			spec: twoNSX,
		},
		{
			name: "unsupported form factor",
			ctx:  context.Background(),
			spec: badFormFactor,
		},
		{
			name: "empty required string",
			ctx:  context.Background(),
			spec: emptyRequired,
		},
		{
			name: "provided nodes violates min items",
			ctx:  context.Background(),
			spec: badNodes,
		},
		{
			name: "provided optional password is empty",
			ctx:  context.Background(),
			spec: badOptionalPassword,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result, err := client.DeployALBCluster(
				test.ctx,
				test.spec,
				albdeploy.DeployOptions{},
			)
			if err == nil || result != (albdeploy.Result{}) {
				t.Fatalf("result=%#v error=%v", result, err)
			}
		})
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("local validation made %d requests", len(requests))
	}
	if server.EffectCount() != 0 {
		t.Fatalf("local validation changed state %d times",
			server.EffectCount())
	}
}

func TestCancelledContextPreventsTraffic(t *testing.T) {
	server := contractmock.New(contractmock.Plan{})
	defer server.Close()
	client := newClient(t, server)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := client.DeployALBCluster(
		ctx,
		minimalSpec(),
		albdeploy.DeployOptions{},
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %T %v, want context.Canceled", err, err)
	}
	if len(server.Requests()) != 0 || server.EffectCount() != 0 {
		t.Fatal("cancelled context caused traffic or mutation")
	}
}

func TestNewClientValidation(t *testing.T) {
	valid := albdeploy.Config{
		BaseURL:     "https://sddc.example.test",
		AccessToken: "runtime-token",
	}
	tests := []struct {
		name   string
		mutate func(*albdeploy.Config)
	}{
		{name: "empty URL", mutate: func(c *albdeploy.Config) { c.BaseURL = "" }},
		{name: "wrong scheme", mutate: func(c *albdeploy.Config) {
			c.BaseURL = "ftp://sddc.example.test"
		}},
		{name: "userinfo", mutate: func(c *albdeploy.Config) {
			c.BaseURL = "https://user@sddc.example.test"
		}},
		{name: "path", mutate: func(c *albdeploy.Config) {
			c.BaseURL = "https://sddc.example.test/api"
		}},
		{name: "query", mutate: func(c *albdeploy.Config) {
			c.BaseURL = "https://sddc.example.test?x=1"
		}},
		{name: "fragment", mutate: func(c *albdeploy.Config) {
			c.BaseURL = "https://sddc.example.test#fragment"
		}},
		{name: "empty token", mutate: func(c *albdeploy.Config) {
			c.AccessToken = ""
		}},
		{name: "token whitespace", mutate: func(c *albdeploy.Config) {
			c.AccessToken = "token value"
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := valid
			test.mutate(&config)
			client, err := albdeploy.NewClient(config)
			if err == nil || client != nil {
				t.Fatalf("client=%#v error=%v", client, err)
			}
		})
	}
	client, err := albdeploy.NewClient(valid)
	if err != nil || client == nil {
		t.Fatalf("valid NewClient: client=%#v error=%v", client, err)
	}
}

func minimalSpec() albdeploy.AlbControllerClusterSpec {
	return albdeploy.AlbControllerClusterSpec{
		NSXIDs:        []string{"nsx-minimal"},
		ClusterName:   "alb-minimal",
		ClusterFQDN:   "alb-minimal.example.test",
		FormFactor:    "SMALL",
		AdminPassword: "admin-secret",
		BundleID:      "bundle-minimal",
	}
}

func newClient(t *testing.T, server *contractmock.Server) *albdeploy.Client {
	t.Helper()
	client, err := albdeploy.NewClient(albdeploy.Config{
		BaseURL:     server.URL() + "/",
		AccessToken: server.Token(),
		HTTPClient:  server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertFileHash(t *testing.T, path string, want string) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected file %s: %v", path, err)
	}
	sum := sha256.Sum256(content)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("protected file %s has SHA-256 %s, want %s",
			path, got, want)
	}
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		t.Fatalf("%s has trailing JSON content", path)
	}
}

func sortedKeys(values map[string]any) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func sortedSchemaKeys(
	values map[string]struct {
		Required            []string       `json:"required"`
		Properties          map[string]any `json:"properties"`
		ProjectedProperties map[string]any `json:"projected_properties"`
	},
) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
