package vkschange_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	vkschange "example.com/vkschange"
	"example.com/vkschange/internal/contractmock"
)

const (
	contractDigest = "5dcb8bf227b407e66c9969a73bebbc02e0c819ab3a805419ca4f931bc282ad8c"
	sourcesDigest  = "7aa33ad78ccffdf04f462e368651a2088c8a391c33bfbd2b981ba0d49918c93b"
)

func TestPinnedContractDocuments(t *testing.T) {
	t.Parallel()

	for _, tc := range []struct {
		path string
		want string
	}{
		{path: "docs/contract.json", want: contractDigest},
		{path: "docs/official_sources.json", want: sourcesDigest},
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

	var contractDoc struct {
		Source struct {
			CommitSha string `json:"commitSha"`
			SpecPath  string `json:"specPath"`
			License   string `json:"license"`
		} `json:"source"`
		Operations []struct {
			Name        string `json:"name"`
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	mustReadJSON(t, "docs/contract.json", &contractDoc)

	var sourceDoc struct {
		CommitSha  string `json:"commitSha"`
		SpecPath   string `json:"specPath"`
		License    string `json:"license"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	mustReadJSON(t, "docs/official_sources.json", &sourceDoc)

	if contractDoc.Source.CommitSha != sourceDoc.CommitSha ||
		contractDoc.Source.SpecPath != sourceDoc.SpecPath ||
		contractDoc.Source.License != sourceDoc.License {
		t.Fatal("contract and official source provenance do not match")
	}
	for _, want := range sourceDoc.Operations {
		found := false
		for _, got := range contractDoc.Operations {
			if got.OperationID == want.OperationID && got.Method == want.Method && got.Path == want.Path {
				found = true
			}
		}
		if !found {
			t.Fatalf("official operation missing from contract: %+v", want)
		}
	}
}

func TestApplyLateFailureReportsEarlierStepsAndExactWire(t *testing.T) {
	t.Parallel()

	description := "orders production namespace"
	emptyPolicies := []string{}
	tests := []struct {
		name              string
		patch             vkschange.NamespacePatch
		wantNamespaceJSON string
	}{
		{
			name: "unset policies are omitted",
			patch: vkschange.NamespacePatch{
				Description: &description,
			},
			wantNamespaceJSON: `{"description":"orders production namespace"}`,
		},
		{
			name: "unset description omitted and explicit empty policies retained",
			patch: vkschange.NamespacePatch{
				InfrastructurePolicies: &emptyPolicies,
			},
			wantNamespaceJSON: `{"infrastructure_policies":[]}`,
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.New(t, "docs/contract.json", contractmock.Fixture{
				Namespace: "team-a",
				Cluster:   "orders",
				Replies: map[string]contractmock.Reply{
					vkschange.OperationPatchVKSCluster: {
						Status: http.StatusUnprocessableEntity,
						Body:   `{"kind":"Status","status":"Failure","message":"admission rejected version for kube-token from session-123"}`,
					},
				},
			})
			client := newClient(mock.URL(), mock.Client())

			report, err := client.Apply(context.Background(), vkschange.Change{
				Namespace:         "team-a",
				Cluster:           "orders",
				NamespacePatch:    tc.patch,
				KubernetesVersion: "v1.33.1+vmware.1-fips-vkr.2",
			})
			if err == nil || !strings.Contains(err.Error(), "admission rejected version") {
				t.Fatalf("Apply error = %v, want bounded Kubernetes response detail", err)
			}
			if strings.Contains(err.Error(), "kube-token") || strings.Contains(err.Error(), "session-123") {
				t.Fatalf("Apply error leaked credentials: %v", err)
			}
			if report.PreviousDescription != "before" {
				t.Fatalf("PreviousDescription = %q, want before", report.PreviousDescription)
			}
			assertSteps(t, report.Steps, []vkschange.StepResult{
				{Operation: vkschange.OperationGetNamespace, State: vkschange.StepSucceeded, HTTPStatus: 200},
				{Operation: vkschange.OperationUpdateNamespace, State: vkschange.StepSucceeded, HTTPStatus: 204},
				{
					Operation:  vkschange.OperationPatchVKSCluster,
					State:      vkschange.StepFailed,
					HTTPStatus: 422,
					Error:      report.Steps[2].Error,
				},
			})
			if !strings.Contains(report.Steps[2].Error, "admission rejected version") {
				t.Fatalf("failed step detail = %q", report.Steps[2].Error)
			}
			if strings.Contains(report.Steps[2].Error, "kube-token") ||
				strings.Contains(report.Steps[2].Error, "session-123") {
				t.Fatalf("failed step leaked credentials: %q", report.Steps[2].Error)
			}

			requests := mock.Requests()
			if len(requests) != 3 {
				t.Fatalf("request count = %d, want 3: %+v", len(requests), requests)
			}
			assertRequest(t, requests[0], requestExpectation{
				operation: vkschange.OperationGetNamespace,
				method:    http.MethodGet,
				path:      "/api/vcenter/namespaces/instances/team-a",
				session:   "session-123",
				accept:    "application/json",
			})
			assertRequest(t, requests[1], requestExpectation{
				operation:   vkschange.OperationUpdateNamespace,
				method:      http.MethodPatch,
				path:        "/api/vcenter/namespaces/instances/team-a",
				session:     "session-123",
				accept:      "application/json",
				contentType: "application/json",
				body:        tc.wantNamespaceJSON,
			})
			assertRequest(t, requests[2], requestExpectation{
				operation:   vkschange.OperationPatchVKSCluster,
				method:      http.MethodPatch,
				path:        "/apis/cluster.x-k8s.io/v1beta1/namespaces/team-a/clusters/orders",
				bearer:      "Bearer kube-token",
				accept:      "application/json",
				contentType: "application/merge-patch+json",
				body:        `{"spec":{"topology":{"version":"v1.33.1+vmware.1-fips-vkr.2"}}}`,
			})
		})
	}
}

func TestApplyStopsAfterFirstFailure(t *testing.T) {
	t.Parallel()

	description := "new description"
	tests := []struct {
		name      string
		replies   map[string]contractmock.Reply
		wantSteps []vkschange.StepResult
		wantCalls int
	}{
		{
			name: "namespace read fails",
			replies: map[string]contractmock.Reply{
				vkschange.OperationGetNamespace: {
					Status: http.StatusUnauthorized,
					Body:   `{"message":"session expired"}`,
				},
			},
			wantSteps: []vkschange.StepResult{
				{Operation: vkschange.OperationGetNamespace, State: vkschange.StepFailed, HTTPStatus: 401},
				{Operation: vkschange.OperationUpdateNamespace, State: vkschange.StepSkipped},
				{Operation: vkschange.OperationPatchVKSCluster, State: vkschange.StepSkipped},
			},
			wantCalls: 1,
		},
		{
			name: "namespace update fails",
			replies: map[string]contractmock.Reply{
				vkschange.OperationUpdateNamespace: {
					Status: http.StatusBadRequest,
					Body:   `{"message":"bad namespace patch"}`,
				},
			},
			wantSteps: []vkschange.StepResult{
				{Operation: vkschange.OperationGetNamespace, State: vkschange.StepSucceeded, HTTPStatus: 200},
				{Operation: vkschange.OperationUpdateNamespace, State: vkschange.StepFailed, HTTPStatus: 400},
				{Operation: vkschange.OperationPatchVKSCluster, State: vkschange.StepSkipped},
			},
			wantCalls: 2,
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.New(t, "docs/contract.json", contractmock.Fixture{
				Namespace: "team-a",
				Cluster:   "orders",
				Replies:   tc.replies,
			})
			client := newClient(mock.URL(), mock.Client())
			report, err := client.Apply(context.Background(), vkschange.Change{
				Namespace:         "team-a",
				Cluster:           "orders",
				NamespacePatch:    vkschange.NamespacePatch{Description: &description},
				KubernetesVersion: "v1.33.1+vmware.1-fips-vkr.2",
			})
			if err == nil {
				t.Fatal("Apply returned nil error")
			}
			for i := range tc.wantSteps {
				if tc.wantSteps[i].State == vkschange.StepFailed {
					tc.wantSteps[i].Error = report.Steps[i].Error
				}
			}
			assertSteps(t, report.Steps, tc.wantSteps)
			if got := len(mock.Requests()); got != tc.wantCalls {
				t.Fatalf("request count = %d, want %d", got, tc.wantCalls)
			}
		})
	}
}

func TestApplyValidationMakesNoRequestsAndDoesNotLeakCredentials(t *testing.T) {
	t.Parallel()

	description := "new description"
	tests := []struct {
		name   string
		mutate func(*vkschange.Client, *vkschange.Change)
	}{
		{name: "missing vcenter URL", mutate: func(c *vkschange.Client, _ *vkschange.Change) { c.VCenterURL = "" }},
		{name: "missing kubernetes URL", mutate: func(c *vkschange.Client, _ *vkschange.Change) { c.KubernetesURL = "" }},
		{name: "missing session", mutate: func(c *vkschange.Client, _ *vkschange.Change) { c.SessionID = "" }},
		{name: "missing bearer", mutate: func(c *vkschange.Client, _ *vkschange.Change) { c.BearerToken = "" }},
		{name: "missing namespace", mutate: func(_ *vkschange.Client, c *vkschange.Change) { c.Namespace = "" }},
		{name: "missing cluster", mutate: func(_ *vkschange.Client, c *vkschange.Change) { c.Cluster = "" }},
		{name: "empty namespace patch", mutate: func(_ *vkschange.Client, c *vkschange.Change) { c.NamespacePatch = vkschange.NamespacePatch{} }},
		{name: "missing version", mutate: func(_ *vkschange.Client, c *vkschange.Change) { c.KubernetesVersion = "" }},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.New(t, "docs/contract.json", contractmock.Fixture{
				Namespace: "team-a",
				Cluster:   "orders",
			})
			client := newClient(mock.URL(), mock.Client())
			change := vkschange.Change{
				Namespace:         "team-a",
				Cluster:           "orders",
				NamespacePatch:    vkschange.NamespacePatch{Description: &description},
				KubernetesVersion: "v1.33.1+vmware.1-fips-vkr.2",
			}
			tc.mutate(&client, &change)

			report, err := client.Apply(context.Background(), change)
			if err == nil {
				t.Fatal("Apply returned nil error")
			}
			if strings.Contains(err.Error(), "session-123") || strings.Contains(err.Error(), "kube-token") {
				t.Fatalf("error leaked credentials: %v", err)
			}
			assertSteps(t, report.Steps, newSkippedSteps())
			if got := len(mock.Requests()); got != 0 {
				t.Fatalf("validation made %d requests", got)
			}
		})
	}
}

func TestClientCanBeUsedConcurrently(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, "docs/contract.json", contractmock.Fixture{
		Namespace: "team-a",
		Cluster:   "orders",
	})
	client := newClient(mock.URL(), mock.Client())
	description := "new description"

	const workers = 8
	done := make(chan error, workers)
	for i := 0; i < workers; i++ {
		go func() {
			_, err := client.Apply(context.Background(), vkschange.Change{
				Namespace:         "team-a",
				Cluster:           "orders",
				NamespacePatch:    vkschange.NamespacePatch{Description: &description},
				KubernetesVersion: "v1.33.1+vmware.1-fips-vkr.2",
			})
			done <- err
		}()
	}
	for i := 0; i < workers; i++ {
		if err := <-done; err != nil {
			t.Fatalf("concurrent Apply: %v", err)
		}
	}
	if got := len(mock.Requests()); got != workers*3 {
		t.Fatalf("request count = %d, want %d", got, workers*3)
	}
}

func newClient(serverURL string, httpClient *http.Client) vkschange.Client {
	httpClient.Timeout = 2 * time.Second
	return vkschange.Client{
		VCenterURL:    serverURL + "/api",
		KubernetesURL: serverURL,
		SessionID:     "session-123",
		BearerToken:   "kube-token",
		HTTPClient:    httpClient,
	}
}

func newSkippedSteps() []vkschange.StepResult {
	return []vkschange.StepResult{
		{Operation: vkschange.OperationGetNamespace, State: vkschange.StepSkipped},
		{Operation: vkschange.OperationUpdateNamespace, State: vkschange.StepSkipped},
		{Operation: vkschange.OperationPatchVKSCluster, State: vkschange.StepSkipped},
	}
}

func assertSteps(t *testing.T, got, want []vkschange.StepResult) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("steps mismatch:\n got: %#v\nwant: %#v", got, want)
	}
}

type requestExpectation struct {
	operation   string
	method      string
	path        string
	session     string
	bearer      string
	accept      string
	contentType string
	body        string
}

func assertRequest(t *testing.T, got contractmock.Request, want requestExpectation) {
	t.Helper()
	if got.Operation != want.operation || got.Method != want.method || got.Path != want.path {
		t.Fatalf("request target = %s %s (%s), want %s %s (%s)",
			got.Method, got.Path, got.Operation, want.method, want.path, want.operation)
	}
	if got.RawQuery != "" {
		t.Fatalf("%s raw query = %q, want empty", want.operation, got.RawQuery)
	}
	if value := got.Header.Get("vmware-api-session-id"); value != want.session {
		t.Fatalf("%s session header = %q, want %q", want.operation, value, want.session)
	}
	if value := got.Header.Get("Authorization"); value != want.bearer {
		t.Fatalf("%s authorization = %q, want %q", want.operation, value, want.bearer)
	}
	if value := got.Header.Get("Accept"); value != want.accept {
		t.Fatalf("%s accept = %q, want %q", want.operation, value, want.accept)
	}
	if value := got.Header.Get("Content-Type"); value != want.contentType {
		t.Fatalf("%s content type = %q, want %q", want.operation, value, want.contentType)
	}
	if string(got.Body) != want.body {
		t.Fatalf("%s body = %q, want %q", want.operation, got.Body, want.body)
	}
	if got.ContentLength != int64(len(want.body)) {
		t.Fatalf("%s content length = %d, want %d",
			want.operation, got.ContentLength, len(want.body))
	}
	if len(got.TransferEncoding) != 0 {
		t.Fatalf("%s transfer encoding = %v, want none", want.operation, got.TransferEncoding)
	}
}

func mustReadJSON(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatal(err)
	}
}
