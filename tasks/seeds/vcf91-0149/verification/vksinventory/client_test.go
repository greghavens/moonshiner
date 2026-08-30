package vksinventory_test

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"net/http"
	"net/url"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"

	"vcf91-0149/internal/contractmock"
	"vcf91-0149/vksinventory"
)

func TestListClustersConsumesEveryPageAndStabilizesOrder(t *testing.T) {
	layouts := []struct {
		name  string
		pages [][]int
	}{
		{
			name:  "mixed three page layout",
			pages: [][]int{{4, 0}, {3}, {1, 2}},
		},
		{
			name:  "different boundaries and order",
			pages: [][]int{{2}, {4, 1}, {0, 3}},
		},
	}

	for _, tt := range layouts {
		t.Run(tt.name, func(t *testing.T) {
			suffix := randomSuffix(t)
			namespace := "team /+雪-" + suffix
			sessionID := "session-" + randomSuffix(t)
			cursors := []string{
				"next /+雪=&" + randomSuffix(t),
				"cursor two?=yes&run=" + randomSuffix(t),
			}
			resources := []contractmock.Resource{
				resource("alpha", suffix),
				resource("beta", suffix),
				resource("gamma", suffix),
				resource("delta", suffix),
				resource("zeta", suffix),
			}
			pages := make([]contractmock.Page, len(tt.pages))
			for pageIndex, indexes := range tt.pages {
				for _, itemIndex := range indexes {
					pages[pageIndex].Items = append(pages[pageIndex].Items, resources[itemIndex])
				}
				if pageIndex < len(tt.pages)-1 {
					pages[pageIndex].Continue = cursors[pageIndex]
				}
			}

			scenario := contractmock.Scenario{
				Namespace: namespace,
				SessionID: sessionID,
				PageLimit: 2,
				Pages:     pages,
			}
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			got, err := client.ListClusters(context.Background())
			if err != nil {
				t.Fatalf("ListClusters: %v", err)
			}
			want := make([]vksinventory.Cluster, len(resources))
			for i, item := range resources {
				want[i] = vksinventory.Cluster{
					Name:            item.Name,
					UID:             item.UID,
					ResourceVersion: item.ResourceVersion,
				}
			}
			sort.Slice(want, func(i, j int) bool { return want[i].Name < want[j].Name })
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("ListClusters returned incomplete or unstable data\n got: %#v\nwant: %#v", got, want)
			}
			assertExactTranscript(t, server.Requests(), scenario, server.URL())
		})
	}
}

func TestNewClientValidationIsTableDriven(t *testing.T) {
	valid := func() vksinventory.Config {
		return vksinventory.Config{
			VCenterURL:       "http://127.0.0.1:9443",
			Namespace:        "tenant-a",
			SessionID:        "session-a",
			KubernetesScheme: "http",
			PageLimit:        2,
			HTTPClient:       &http.Client{},
		}
	}
	tests := []struct {
		name   string
		mutate func(*vksinventory.Config)
	}{
		{name: "blank vCenter URL", mutate: func(c *vksinventory.Config) { c.VCenterURL = " \t" }},
		{name: "relative vCenter URL", mutate: func(c *vksinventory.Config) { c.VCenterURL = "/vcenter" }},
		{name: "unsupported vCenter scheme", mutate: func(c *vksinventory.Config) { c.VCenterURL = "ftp://127.0.0.1" }},
		{name: "vCenter credentials", mutate: func(c *vksinventory.Config) { c.VCenterURL = "http://user@127.0.0.1" }},
		{name: "vCenter API path", mutate: func(c *vksinventory.Config) { c.VCenterURL = "http://127.0.0.1/api" }},
		{name: "vCenter query", mutate: func(c *vksinventory.Config) { c.VCenterURL = "http://127.0.0.1?x=1" }},
		{name: "vCenter fragment", mutate: func(c *vksinventory.Config) { c.VCenterURL = "http://127.0.0.1#x" }},
		{name: "blank namespace", mutate: func(c *vksinventory.Config) { c.Namespace = " \n" }},
		{name: "blank session", mutate: func(c *vksinventory.Config) { c.SessionID = " \t" }},
		{name: "unsafe session", mutate: func(c *vksinventory.Config) { c.SessionID = "session\r\nleak" }},
		{name: "uppercase Kubernetes scheme", mutate: func(c *vksinventory.Config) { c.KubernetesScheme = "HTTP" }},
		{name: "zero page limit", mutate: func(c *vksinventory.Config) { c.PageLimit = 0 }},
		{name: "negative page limit", mutate: func(c *vksinventory.Config) { c.PageLimit = -2 }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			config := valid()
			tt.mutate(&config)
			if _, err := vksinventory.NewClient(config); err == nil {
				t.Fatal("NewClient returned nil error")
			}
		})
	}

	callerOwned := &http.Client{}
	config := valid()
	config.HTTPClient = callerOwned
	if _, err := vksinventory.NewClient(config); err != nil {
		t.Fatalf("NewClient(valid): %v", err)
	}
	if callerOwned.CheckRedirect != nil {
		t.Fatal("NewClient mutated the caller-owned HTTP client")
	}
}

func TestProtocolGuardsAreTableDriven(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*contractmock.Scenario)
	}{
		{
			name: "repeated continuation token",
			mutate: func(s *contractmock.Scenario) {
				s.Pages = []contractmock.Page{
					{Items: []contractmock.Resource{resource("alpha", s.SessionID)}, Continue: "repeat-" + s.SessionID},
					{Items: []contractmock.Resource{resource("beta", s.SessionID)}, Continue: "repeat-" + s.SessionID},
				}
			},
		},
		{
			name: "duplicate Cluster name across pages",
			mutate: func(s *contractmock.Scenario) {
				s.Pages = []contractmock.Page{
					{Items: []contractmock.Resource{resource("alpha", s.SessionID)}, Continue: "next-" + s.SessionID},
					{Items: []contractmock.Resource{resource("alpha", s.SessionID)}},
				}
			},
		},
		{
			name: "blank required Cluster uid",
			mutate: func(s *contractmock.Scenario) {
				item := resource("alpha", s.SessionID)
				item.UID = ""
				s.Pages = []contractmock.Page{{Items: []contractmock.Resource{item}}}
			},
		},
		{
			name: "malformed unrelated namespace summary",
			mutate: func(s *contractmock.Scenario) {
				s.CorruptUnrelatedSummary = true
			},
		},
		{
			name: "duplicate selected namespace",
			mutate: func(s *contractmock.Scenario) {
				s.DuplicateNamespace = true
			},
		},
		{
			name: "master host with a path",
			mutate: func(s *contractmock.Scenario) {
				s.MasterHostOverride = "127.0.0.1/should-not-be-a-path"
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			scenario := basicScenario(t)
			tt.mutate(&scenario)
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			_, err := client.ListClusters(context.Background())
			var protocolError *vksinventory.ProtocolError
			if !errors.As(err, &protocolError) {
				t.Fatalf("ListClusters error = %T %v, want *ProtocolError", err, err)
			}
			if strings.Contains(err.Error(), scenario.SessionID) {
				t.Fatal("ProtocolError disclosed the session identifier")
			}
		})
	}
}

func TestHTTPFailuresAreTypedAndRedacted(t *testing.T) {
	tests := []struct {
		name      string
		operation string
		status    int
		requests  int
	}{
		{
			name:      "vCenter namespace failure",
			operation: contractmock.OperationNamespaceList,
			status:    http.StatusForbidden,
			requests:  1,
		},
		{
			name:      "Kubernetes collection failure",
			operation: contractmock.OperationClusterList,
			status:    http.StatusTooManyRequests,
			requests:  2,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			scenario := basicScenario(t)
			scenario.FailOperation = tt.operation
			scenario.FailStatus = tt.status
			server := newServer(t, scenario)
			defer server.Close()
			client := newClient(t, server, scenario)

			_, err := client.ListClusters(context.Background())
			var apiError *vksinventory.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("ListClusters error = %T %v, want *APIError", err, err)
			}
			if apiError.Operation != tt.operation || apiError.StatusCode != tt.status {
				t.Fatalf("APIError = %#v, want operation %q status %d", apiError, tt.operation, tt.status)
			}
			if strings.Contains(err.Error(), scenario.SessionID) {
				t.Fatal("APIError disclosed response content or the session identifier")
			}
			if got := len(server.Requests()); got != tt.requests {
				t.Fatalf("request count = %d, want %d", got, tt.requests)
			}
		})
	}
}

func TestCancellationRemainsDiscoverableWithoutTraffic(t *testing.T) {
	scenario := basicScenario(t)
	server := newServer(t, scenario)
	defer server.Close()
	client := newClient(t, server, scenario)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := client.ListClusters(ctx)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("ListClusters error = %v, want errors.Is(context.Canceled)", err)
	}
	if strings.Contains(err.Error(), scenario.SessionID) {
		t.Fatal("cancellation error disclosed the session identifier")
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("canceled call made %d requests, want 0", got)
	}

	if _, err := client.ListClusters(nil); err == nil {
		t.Fatal("ListClusters(nil) returned nil error")
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("nil-context call made %d requests, want 0", got)
	}
}

func TestMockRejectsOperationsAbsentFromContract(t *testing.T) {
	scenario := basicScenario(t)
	server := newServer(t, scenario)
	defer server.Close()

	request, err := http.NewRequest(http.MethodGet, server.URL()+"/api/vcenter/vm", nil)
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

func assertExactTranscript(t *testing.T, got []contractmock.Request, scenario contractmock.Scenario, serverURL string) {
	t.Helper()
	if len(got) != 1+len(scenario.Pages) {
		t.Fatalf("request count = %d, want %d: %#v", len(got), 1+len(scenario.Pages), got)
	}
	parsedServerURL, err := url.Parse(serverURL)
	if err != nil {
		t.Fatalf("parse server URL: %v", err)
	}
	for i, request := range got {
		if request.Host != parsedServerURL.Host {
			t.Errorf("request[%d] Host = %q, want %q", i, request.Host, parsedServerURL.Host)
		}
	}

	namespaceRequest := got[0]
	assertRequestCommon(t, namespaceRequest, 0, contractmock.OperationNamespaceList)
	if namespaceRequest.RawTarget != "/api/vcenter/namespaces-user/namespaces" {
		t.Errorf("namespace raw target = %q", namespaceRequest.RawTarget)
	}
	assertOnlyValue(t, namespaceRequest.Header, "Accept", "application/json")
	assertOnlyValue(t, namespaceRequest.Header, "vmware-api-session-id", scenario.SessionID)
	assertAbsent(t, namespaceRequest.Header, "Authorization")
	assertAbsent(t, namespaceRequest.Header, "Content-Type")

	basePath := "/apis/cluster.x-k8s.io/v1beta2/namespaces/" +
		url.PathEscape(scenario.Namespace) + "/clusters"
	for pageIndex, request := range got[1:] {
		assertRequestCommon(t, request, pageIndex+1, contractmock.OperationClusterList)
		query := url.Values{
			"limit": {strconv.FormatInt(scenario.PageLimit, 10)},
		}
		if pageIndex > 0 {
			query.Set("continue", scenario.Pages[pageIndex-1].Continue)
		}
		wantTarget := basePath + "?" + query.Encode()
		if request.RawTarget != wantTarget {
			t.Errorf("page %d raw target = %q, want %q", pageIndex, request.RawTarget, wantTarget)
		}
		assertOnlyValue(t, request.Header, "Accept", "application/json")
		assertOnlyValue(t, request.Header, "Authorization", "Bearer "+scenario.SessionID)
		assertAbsent(t, request.Header, "vmware-api-session-id")
		assertAbsent(t, request.Header, "Content-Type")
	}
}

func assertRequestCommon(t *testing.T, request contractmock.Request, sequence int, operation string) {
	t.Helper()
	if request.Sequence != sequence ||
		request.Operation != operation ||
		request.Method != http.MethodGet ||
		len(request.Body) != 0 ||
		request.ContentLength != 0 {
		t.Errorf("request[%d] wire shape = %#v", sequence, request)
	}
}

func assertOnlyValue(t *testing.T, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s header = %q, want exactly %q", name, values, want)
	}
}

func assertAbsent(t *testing.T, header http.Header, name string) {
	t.Helper()
	if values := header.Values(name); values != nil {
		t.Errorf("%s header unexpectedly present: %q", name, values)
	}
}

func newServer(t *testing.T, scenario contractmock.Scenario) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New(filepath.Join("..", "docs", "contract.json"), scenario)
	if err != nil {
		t.Fatalf("contractmock.New: %v", err)
	}
	return server
}

func newClient(t *testing.T, server *contractmock.Server, scenario contractmock.Scenario) *vksinventory.Client {
	t.Helper()
	client, err := vksinventory.NewClient(vksinventory.Config{
		VCenterURL:       server.URL(),
		Namespace:        scenario.Namespace,
		SessionID:        scenario.SessionID,
		KubernetesScheme: "http",
		PageLimit:        scenario.PageLimit,
		HTTPClient:       server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func basicScenario(t *testing.T) contractmock.Scenario {
	t.Helper()
	suffix := randomSuffix(t)
	return contractmock.Scenario{
		Namespace: "tenant /+雪-" + suffix,
		SessionID: "session-" + randomSuffix(t),
		PageLimit: 2,
		Pages: []contractmock.Page{{
			Items: []contractmock.Resource{resource("alpha", suffix)},
		}},
	}
}

func resource(name, suffix string) contractmock.Resource {
	return contractmock.Resource{
		Name:            name + "-" + suffix,
		UID:             "uid-" + name + "-" + suffix,
		ResourceVersion: "rv-" + name + "-" + suffix,
	}
}

func randomSuffix(t *testing.T) string {
	t.Helper()
	var value [8]byte
	if _, err := rand.Read(value[:]); err != nil {
		t.Fatalf("rand.Read: %v", err)
	}
	return hex.EncodeToString(value[:])
}
