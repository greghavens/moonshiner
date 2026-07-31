package logforwarder_test

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"moonshiner.local/vcf91/logforwarders/internal/contractmock"
	"moonshiner.local/vcf91/logforwarders/logforwarder"
)

const contractPath = "../docs/contract.json"

func TestReconcileRefreshesOnlyInterruptedCreateAndPreservesWireShape(t *testing.T) {
	t.Parallel()

	oldToken := runtimeValue(t, "old-token")
	newToken := runtimeValue(t, "new-token")
	existingName := runtimeValue(t, "existing")
	firstName := runtimeValue(t, "first") + "-<&>"
	expiringName := runtimeValue(t, "expiring")
	lastName := runtimeValue(t, "last")

	existing := map[string]any{
		"id":                runtimeValue(t, "existing-id"),
		"host":              "existing.example",
		"name":              existingName,
		"port":              1514,
		"protocol":          "RAW",
		"transportProtocol": "UDP",
	}
	server, err := contractmock.Start(contractPath, contractmock.Scenario{
		OldToken:     oldToken,
		NewToken:     newToken,
		ExpireOnName: expiringName,
		Existing:     []map[string]any{existing},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	var providerMu sync.Mutex
	var providerCalls []bool
	provider := func(_ context.Context, forceRefresh bool) (string, error) {
		providerMu.Lock()
		defer providerMu.Unlock()
		providerCalls = append(providerCalls, forceRefresh)
		if forceRefresh {
			return newToken, nil
		}
		return oldToken, nil
	}

	callerClient := server.Client()
	if callerClient.CheckRedirect != nil {
		t.Fatal("unexpected redirect policy on loopback client")
	}
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	serverURL, err := url.Parse(server.URL())
	if err != nil {
		t.Fatal(err)
	}
	jar.SetCookies(serverURL, []*http.Cookie{{Name: "session", Value: runtimeValue(t, "cookie")}})
	callerClient.Jar = jar
	client, err := logforwarder.NewClient(logforwarder.Config{
		BaseURL:       server.URL(),
		TokenProvider: provider,
		HTTPClient:    callerClient,
	})
	if err != nil {
		t.Fatal(err)
	}
	if callerClient.CheckRedirect != nil || callerClient.Jar != jar {
		t.Fatal("NewClient mutated caller-owned HTTP client")
	}

	explicitFalse := false
	explicitTrue := true
	explicitZero := int32(0)
	emptyString := ""
	desired := []logforwarder.DesiredForwarder{
		{
			Host:              "existing.example",
			Name:              existingName,
			Port:              1514,
			Protocol:          "RAW",
			TransportProtocol: "UDP",
		},
		{
			Certificate:               &emptyString,
			ConnectionRefreshInterval: &explicitZero,
			Constraints:               map[string]any{},
			Enabled:                   &explicitFalse,
			Host:                      "first.example",
			Name:                      firstName,
			Port:                      514,
			Protocol:                  "SYSLOG",
			Tags:                      map[string]string{},
			TransportProtocol:         "TCP",
		},
		{
			Host:              "expiring.example",
			Name:              expiringName,
			Port:              6514,
			Protocol:          "RAWPLUS",
			TransportProtocol: "TCP",
			WorkerCount:       &explicitZero,
		},
		{
			Enabled:           &explicitTrue,
			Host:              "last.example",
			Name:              lastName,
			Port:              514,
			Protocol:          "SYSLOG",
			SSLEnabled:        &explicitFalse,
			Tags:              map[string]string{"site": "west"},
			TransportProtocol: "UDP",
		},
		{
			Host:              "ignored-duplicate.example",
			Name:              firstName,
			Port:              5514,
			Protocol:          "RAW",
			TransportProtocol: "TCP",
		},
	}

	got, err := client.Reconcile(context.Background(), desired)
	if err != nil {
		t.Fatalf("Reconcile returned error: %v", err)
	}
	gotNames := make([]string, len(got))
	for i := range got {
		gotNames[i] = got[i].Name
	}
	wantNames := []string{existingName, firstName, expiringName, lastName}
	if fmt.Sprint(gotNames) != fmt.Sprint(wantNames) {
		t.Fatalf("result names = %q, want %q", gotNames, wantNames)
	}

	providerMu.Lock()
	calls := append([]bool(nil), providerCalls...)
	providerMu.Unlock()
	if fmt.Sprint(calls) != fmt.Sprint([]bool{false, true}) {
		t.Fatalf("provider calls = %v, want [false true]", calls)
	}

	firstBody := `{"connectionRefreshInterval":0,"enabled":false,"host":` +
		strconv.Quote("first.example") + `,"name":` + strconv.Quote(firstName) +
		`,"port":514,"protocol":"SYSLOG","transportProtocol":"TCP"}`
	expiringBody := `{"host":"expiring.example","name":` + strconv.Quote(expiringName) +
		`,"port":6514,"protocol":"RAWPLUS","transportProtocol":"TCP","workerCount":0}`
	lastBody := `{"enabled":true,"host":"last.example","name":` + strconv.Quote(lastName) +
		`,"port":514,"protocol":"SYSLOG","sslEnabled":false,"tags":{"site":"west"},"transportProtocol":"UDP"}`

	requests := server.Requests()
	want := []struct {
		operation string
		method    string
		status    int
		token     string
		body      string
	}{
		{logforwarder.GetAllLogForwardersOperation, http.MethodGet, 200, oldToken, ""},
		{logforwarder.CreateLogForwarderOperation, http.MethodPost, 201, oldToken, firstBody},
		{logforwarder.CreateLogForwarderOperation, http.MethodPost, 403, oldToken, expiringBody},
		{logforwarder.CreateLogForwarderOperation, http.MethodPost, 201, newToken, expiringBody},
		{logforwarder.CreateLogForwarderOperation, http.MethodPost, 201, newToken, lastBody},
	}
	if len(requests) != len(want) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(want), requests)
	}
	for i, expected := range want {
		request := requests[i]
		if request.OperationID != expected.operation ||
			request.Method != expected.method ||
			request.RequestURI != "/api/v2/logs/forwarders" ||
			request.Status != expected.status {
			t.Errorf("request %d route = (%q %q %q status %d), want (%q %q path status %d)",
				i, request.OperationID, request.Method, request.RequestURI, request.Status,
				expected.operation, expected.method, expected.status)
		}
		assertSingleHeader(t, i, request.Header, "Accept", "application/json")
		assertSingleHeader(t, i, request.Header, "X-JWT-Token", expected.token)
		assertNoHeader(t, i, request.Header, "Authorization")
		assertNoHeader(t, i, request.Header, "Cookie")
		if string(request.Body) != expected.body {
			t.Errorf("request %d body = %q, want %q", i, request.Body, expected.body)
		}
		if len(request.TransferEncoding) != 0 {
			t.Errorf("request %d transfer encoding = %v, want none", i, request.TransferEncoding)
		}
		if expected.method == http.MethodGet {
			assertNoHeader(t, i, request.Header, "Content-Type")
			if request.ContentLength > 0 {
				t.Errorf("GET request %d ContentLength = %d, want non-positive", i, request.ContentLength)
			}
		} else {
			assertSingleHeader(t, i, request.Header, "Content-Type", "application/json")
			if request.ContentLength != int64(len(expected.body)) {
				t.Errorf("request %d ContentLength = %d, want %d", i, request.ContentLength, len(expected.body))
			}
		}
	}

	for _, requestIndex := range []int{1, 2, 3, 4} {
		var members map[string]json.RawMessage
		if err := json.Unmarshal(requests[requestIndex].Body, &members); err != nil {
			t.Fatalf("decode request %d: %v", requestIndex, err)
		}
		for _, absent := range []string{
			"certificate", "constraints", "forwardComplementaryFields", "id",
		} {
			if _, found := members[absent]; found {
				t.Errorf("request %d unexpectedly contains %q", requestIndex, absent)
			}
		}
	}
	for _, absent := range []string{
		"certificate", "connectionRefreshInterval", "constraints", "enabled",
		"forwardComplementaryFields", "sslEnabled", "tags", "workerCount", "id",
	} {
		var members map[string]json.RawMessage
		if err := json.Unmarshal(requests[2].Body, &members); err != nil {
			t.Fatal(err)
		}
		if _, found := members[absent]; found && absent != "workerCount" {
			t.Errorf("expiring request unexpectedly contains unset %q", absent)
		}
	}

	state := server.State()
	if len(state) != 4 {
		t.Fatalf("server state length = %d, want 4", len(state))
	}
}

func TestValidationFinishesBeforeProviderOrTraffic(t *testing.T) {
	t.Parallel()

	good := logforwarder.DesiredForwarder{
		Host:              "logs.example",
		Name:              "forwarder",
		Port:              514,
		Protocol:          "SYSLOG",
		TransportProtocol: "TCP",
	}
	tests := []struct {
		name   string
		ctx    context.Context
		mutate func(*logforwarder.DesiredForwarder)
	}{
		{"nil context", nil, func(*logforwarder.DesiredForwarder) {}},
		{"blank name", context.Background(), func(v *logforwarder.DesiredForwarder) { v.Name = "" }},
		{"surrounding name whitespace", context.Background(), func(v *logforwarder.DesiredForwarder) { v.Name = " name" }},
		{"blank host", context.Background(), func(v *logforwarder.DesiredForwarder) { v.Host = " " }},
		{"zero port", context.Background(), func(v *logforwarder.DesiredForwarder) { v.Port = 0 }},
		{"high port", context.Background(), func(v *logforwarder.DesiredForwarder) { v.Port = 65536 }},
		{"bad protocol", context.Background(), func(v *logforwarder.DesiredForwarder) { v.Protocol = "TLS" }},
		{"bad transport", context.Background(), func(v *logforwarder.DesiredForwarder) { v.TransportProtocol = "SCTP" }},
		{"unencodable constraints", context.Background(), func(v *logforwarder.DesiredForwarder) {
			v.Constraints = map[string]any{"bad": make(chan int)}
		}},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			var providerCalls int
			client, err := logforwarder.NewClient(logforwarder.Config{
				BaseURL: "http://127.0.0.1:1",
				TokenProvider: func(context.Context, bool) (string, error) {
					providerCalls++
					return "must-not-be-used", nil
				},
			})
			if err != nil {
				t.Fatal(err)
			}
			value := good
			test.mutate(&value)
			if _, err := client.Reconcile(test.ctx, []logforwarder.DesiredForwarder{value}); err == nil {
				t.Fatal("Reconcile succeeded, want validation error")
			}
			if providerCalls != 0 {
				t.Fatalf("provider called %d times before validation completed", providerCalls)
			}
		})
	}
}

func TestNewClientValidationAndProviderOutput(t *testing.T) {
	t.Parallel()

	provider := func(context.Context, bool) (string, error) { return "token", nil }
	tests := []struct {
		name string
		cfg  logforwarder.Config
	}{
		{"relative", logforwarder.Config{BaseURL: "/api", TokenProvider: provider}},
		{"userinfo", logforwarder.Config{BaseURL: "https://user@example.test", TokenProvider: provider}},
		{"path", logforwarder.Config{BaseURL: "https://example.test/api", TokenProvider: provider}},
		{"query", logforwarder.Config{BaseURL: "https://example.test?x=1", TokenProvider: provider}},
		{"fragment", logforwarder.Config{BaseURL: "https://example.test/#x", TokenProvider: provider}},
		{"scheme", logforwarder.Config{BaseURL: "ftp://example.test", TokenProvider: provider}},
		{"missing provider", logforwarder.Config{BaseURL: "https://example.test"}},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			if _, err := logforwarder.NewClient(test.cfg); err == nil {
				t.Fatal("NewClient succeeded, want error")
			}
		})
	}

	outputs := []string{"", " ", " token", "token ", "token\nnext", "token\rnext"}
	for _, output := range outputs {
		output := output
		t.Run("provider-"+strconv.Quote(output), func(t *testing.T) {
			t.Parallel()
			client, err := logforwarder.NewClient(logforwarder.Config{
				BaseURL: "http://127.0.0.1:1",
				TokenProvider: func(context.Context, bool) (string, error) {
					return output, nil
				},
			})
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.Reconcile(context.Background(), nil)
			var providerError *logforwarder.TokenProviderError
			if !errors.As(err, &providerError) {
				t.Fatalf("error = %T %v, want *TokenProviderError", err, err)
			}
		})
	}
}

func TestConcurrentExpiredRequestsShareOneRefresh(t *testing.T) {
	oldToken := runtimeValue(t, "old-token")
	newToken := runtimeValue(t, "new-token")
	expiringName := runtimeValue(t, "expire")
	const callers = 8
	arrived := make(chan struct{}, callers)
	release := make(chan struct{})

	server, err := contractmock.Start(contractPath, contractmock.Scenario{
		OldToken:       oldToken,
		NewToken:       newToken,
		ExpireOnName:   expiringName,
		OldPostArrived: arrived,
		ReleaseOldPost: release,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	var mu sync.Mutex
	var initialCalls, refreshCalls int
	client, err := logforwarder.NewClient(logforwarder.Config{
		BaseURL: server.URL(),
		TokenProvider: func(_ context.Context, refresh bool) (string, error) {
			mu.Lock()
			defer mu.Unlock()
			if refresh {
				refreshCalls++
				return newToken, nil
			}
			initialCalls++
			return oldToken, nil
		},
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Reconcile(context.Background(), nil); err != nil {
		t.Fatal(err)
	}

	desired := []logforwarder.DesiredForwarder{{
		Host:              "concurrent.example",
		Name:              expiringName,
		Port:              514,
		Protocol:          "SYSLOG",
		TransportProtocol: "TCP",
	}}
	errs := make(chan error, callers)
	for i := 0; i < callers; i++ {
		go func() {
			_, err := client.Reconcile(context.Background(), desired)
			errs <- err
		}()
	}
	for i := 0; i < callers; i++ {
		select {
		case <-arrived:
		case <-time.After(5 * time.Second):
			t.Fatal("timed out waiting for old-token creates")
		}
	}
	close(release)
	for i := 0; i < callers; i++ {
		if err := <-errs; err != nil {
			t.Errorf("concurrent Reconcile: %v", err)
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if initialCalls != 1 || refreshCalls != 1 {
		t.Fatalf("provider calls initial=%d refresh=%d, want 1 and 1", initialCalls, refreshCalls)
	}
}

func TestSecondForbiddenResponseIsTerminal(t *testing.T) {
	t.Parallel()

	oldToken := runtimeValue(t, "old-token")
	serverToken := runtimeValue(t, "server-replacement")
	rejectedToken := runtimeValue(t, "rejected-replacement")
	name := runtimeValue(t, "expire")
	server, err := contractmock.Start(contractPath, contractmock.Scenario{
		OldToken:     oldToken,
		NewToken:     serverToken,
		ExpireOnName: name,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	var calls []bool
	client, err := logforwarder.NewClient(logforwarder.Config{
		BaseURL: server.URL(),
		TokenProvider: func(_ context.Context, refresh bool) (string, error) {
			calls = append(calls, refresh)
			if refresh {
				return rejectedToken, nil
			}
			return oldToken, nil
		},
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.Reconcile(context.Background(), []logforwarder.DesiredForwarder{{
		Host:              "terminal.example",
		Name:              name,
		Port:              514,
		Protocol:          "SYSLOG",
		TransportProtocol: "TCP",
	}})
	var apiError *logforwarder.APIError
	if !errors.As(err, &apiError) ||
		apiError.OperationID != logforwarder.CreateLogForwarderOperation ||
		apiError.StatusCode != http.StatusForbidden {
		t.Fatalf("error = %T %v, want create *APIError with status 403", err, err)
	}
	if strings.Contains(err.Error(), oldToken) ||
		strings.Contains(err.Error(), rejectedToken) ||
		strings.Contains(err.Error(), "authentication required") {
		t.Fatalf("error exposes credential or response content: %v", err)
	}
	if fmt.Sprint(calls) != fmt.Sprint([]bool{false, true}) {
		t.Fatalf("provider calls = %v, want [false true]", calls)
	}
	requests := server.Requests()
	if len(requests) != 3 ||
		requests[0].OperationID != logforwarder.GetAllLogForwardersOperation ||
		requests[1].Status != http.StatusForbidden ||
		requests[2].Status != http.StatusForbidden ||
		requests[1].Header.Get("X-JWT-Token") != oldToken ||
		requests[2].Header.Get("X-JWT-Token") != rejectedToken ||
		string(requests[1].Body) != string(requests[2].Body) {
		t.Fatalf("unexpected terminal authentication sequence: %#v", requests)
	}
	if state := server.State(); len(state) != 0 {
		t.Fatalf("failed create changed server state: %#v", state)
	}
}

func TestContractProvenanceAndMockRouteAllowList(t *testing.T) {
	t.Parallel()

	type source struct {
		RepositoryCommitSha string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
		License             string `json:"license"`
		APIVersion          string `json:"apiVersion"`
	}
	type operation struct {
		OperationID  string `json:"operationId"`
		Method       string `json:"method"`
		PathTemplate string `json:"pathTemplate"`
		Success      int    `json:"successStatus"`
	}
	var contract struct {
		Source     source      `json:"source"`
		Operations []operation `json:"operations"`
	}
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.Source.RepositoryCommitSha != "c3f3b52c845dd967cabbc21680e893292077d5ba" ||
		contract.Source.SpecPath != "specifications/vcf-operations/log-management-openapi.json" ||
		contract.Source.License != "Apache-2.0" ||
		contract.Source.APIVersion != "9.1.0.0" {
		t.Fatalf("unexpected contract source: %+v", contract.Source)
	}
	wantOperations := []operation{
		{"getAllLogForwarders", "GET", "/api/v2/logs/forwarders", 200},
		{"createLogForwarder", "POST", "/api/v2/logs/forwarders", 201},
	}
	if fmt.Sprint(contract.Operations) != fmt.Sprint(wantOperations) {
		t.Fatalf("operations = %+v, want %+v", contract.Operations, wantOperations)
	}

	var official struct {
		RepositoryCommitSha string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID string `json:"operationId"`
		} `json:"operations"`
	}
	officialRaw, err := os.ReadFile("../docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(officialRaw, &official); err != nil {
		t.Fatal(err)
	}
	if official.RepositoryCommitSha != contract.Source.RepositoryCommitSha ||
		official.SpecPath != contract.Source.SpecPath ||
		fmt.Sprint(official.OperationIDs) != fmt.Sprint([]string{
			"getAllLogForwarders", "createLogForwarder",
		}) ||
		len(official.Operations) != 2 ||
		official.Operations[0].OperationID != "getAllLogForwarders" ||
		official.Operations[1].OperationID != "createLogForwarder" {
		t.Fatalf("official source provenance does not match contract: %+v", official)
	}

	server, err := contractmock.Start(contractPath, contractmock.Scenario{
		OldToken: "runtime-only",
		NewToken: "runtime-replacement",
	})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	response, err := server.Client().Post(server.URL()+"/api/v2/logs/search", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed route status = %d, want 404", response.StatusCode)
	}
}

func assertSingleHeader(t *testing.T, index int, header http.Header, name, value string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != value {
		t.Errorf("request %d %s values = %q, want exactly [%q]", index, name, values, value)
	}
}

func assertNoHeader(t *testing.T, index int, header http.Header, name string) {
	t.Helper()
	if values := header.Values(name); len(values) != 0 {
		t.Errorf("request %d unexpectedly has %s: %q", index, name, values)
	}
}

func runtimeValue(t *testing.T, prefix string) string {
	t.Helper()
	var value [12]byte
	if _, err := rand.Read(value[:]); err != nil {
		t.Fatal(err)
	}
	return prefix + "-" + hex.EncodeToString(value[:])
}
