package sessionrotation

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"vcf91-0115/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedBlob   = "8028b0824c4ff3503d05f44814f967938a795c40"
	expectedSpec   = "specifications/vsphere/openapi/automation/vcenter.yaml"
)

var expectedOperations = []string{
	SessionCreateOperation,
	VMListOperation,
	SessionDeleteOperation,
}

type sourceOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	Commit      string `json:"repository_commit_sha"`
	SpecPath    string `json:"spec_path"`
	Pointer     string `json:"yaml_pointer"`
}

func TestProtectedContractProvenance(t *testing.T) {
	t.Parallel()

	var contract struct {
		Source struct {
			Kind     string `json:"kind"`
			Repo     string `json:"repository"`
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			Blob     string `json:"spec_blob_sha"`
			License  string `json:"license"`
		} `json:"source"`
		OpenAPI        string `json:"openapi"`
		BasePath       string `json:"base_path"`
		ServerTemplate string `json:"server_template"`
		Info           struct {
			Title   string `json:"title"`
			Version string `json:"version"`
		} `json:"info"`
		Security map[string]struct {
			Type   string `json:"type"`
			Scheme string `json:"scheme"`
			In     string `json:"in"`
			Name   string `json:"name"`
		} `json:"security_schemes"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			RequestBody any    `json:"requestBody"`
			Parameters  []struct {
				Name     string `json:"name"`
				In       string `json:"in"`
				Required bool   `json:"required"`
				Style    string `json:"style"`
				Explode  bool   `json:"explode"`
				Schema   struct {
					Type        string `json:"type"`
					UniqueItems bool   `json:"uniqueItems"`
				} `json:"schema"`
			} `json:"parameters"`
			Responses map[string]json.RawMessage `json:"responses"`
			Security  []map[string][]string      `json:"security"`
		} `json:"operations"`
		Schemas map[string]struct {
			Type       string                     `json:"type"`
			Required   []string                   `json:"required"`
			Properties map[string]json.RawMessage `json:"properties"`
		} `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	if contract.Source.Kind != "pinned-openapi-specification" ||
		contract.Source.Repo != "vmware/vcf-api-specs" ||
		contract.Source.Commit != expectedCommit ||
		contract.Source.SpecPath != expectedSpec ||
		contract.Source.Blob != expectedBlob ||
		contract.Source.License != "Apache-2.0" {
		t.Fatalf("unexpected contract source: %#v", contract.Source)
	}
	if contract.OpenAPI != "3.0.3" ||
		contract.Info.Title != "vSphere Automation API" ||
		contract.Info.Version != "9.1.0.0" ||
		contract.ServerTemplate != "https://{host}/api" ||
		contract.BasePath != "/api" {
		t.Fatalf(
			"unexpected specification identity: openapi=%q info=%#v server=%q base=%q",
			contract.OpenAPI,
			contract.Info,
			contract.ServerTemplate,
			contract.BasePath,
		)
	}
	basic := contract.Security["basic_auth"]
	apiKey := contract.Security["api_key_auth"]
	if basic.Type != "http" || basic.Scheme != "basic" ||
		apiKey.Type != "apiKey" || apiKey.In != "header" ||
		apiKey.Name != "vmware-api-session-id" {
		t.Fatalf("unexpected security projection: %#v", contract.Security)
	}

	if len(contract.Operations) != 3 {
		t.Fatalf("operation count = %d, want 3", len(contract.Operations))
	}
	wantOperations := []struct {
		id       string
		method   string
		path     string
		security string
		success  string
	}{
		{SessionCreateOperation, http.MethodPost, "/session", "basic_auth", "201"},
		{VMListOperation, http.MethodGet, "/vcenter/vm", "api_key_auth", "200"},
		{SessionDeleteOperation, http.MethodDelete, "/session", "api_key_auth", "204"},
	}
	for index, want := range wantOperations {
		got := contract.Operations[index]
		if got.OperationID != want.id ||
			got.Method != want.method ||
			got.Path != want.path ||
			got.RequestBody != nil ||
			len(got.Security) != 1 {
			t.Fatalf("operation %d mismatch: %#v", index, got)
		}
		if _, ok := got.Security[0][want.security]; !ok {
			t.Fatalf("operation %s security mismatch: %#v", want.id, got.Security)
		}
		if _, ok := got.Responses[want.success]; !ok {
			t.Fatalf("operation %s lacks success %s", want.id, want.success)
		}
	}

	parameters := contract.Operations[1].Parameters
	wantParameters := []string{
		"vms",
		"names",
		"folders",
		"datacenters",
		"hosts",
		"clusters",
		"resource_pools",
		"power_states",
	}
	if len(parameters) != len(wantParameters) {
		t.Fatalf("VM filter count = %d, want %d", len(parameters), len(wantParameters))
	}
	for index, want := range wantParameters {
		got := parameters[index]
		if got.Name != want ||
			got.In != "query" ||
			got.Required ||
			got.Style != "form" ||
			!got.Explode ||
			got.Schema.Type != "array" ||
			!got.Schema.UniqueItems {
			t.Fatalf("VM filter %d mismatch: %#v", index, got)
		}
	}
	summary := contract.Schemas["Vcenter.VM.Summary"]
	if summary.Type != "object" ||
		!reflect.DeepEqual(summary.Required, []string{"name", "power_state", "vm"}) {
		t.Fatalf("unexpected VM summary projection: %#v", summary)
	}
	for _, property := range []string{
		"vm",
		"name",
		"power_state",
		"cpu_count",
		"memory_size_mib",
	} {
		if _, ok := summary.Properties[property]; !ok {
			t.Fatalf("VM summary property %q missing", property)
		}
	}

	var sources struct {
		Repository string            `json:"repository"`
		Commit     string            `json:"repository_commit_sha"`
		SpecPath   string            `json:"spec_path"`
		Blob       string            `json:"spec_blob_sha"`
		License    string            `json:"license"`
		IDs        []string          `json:"operation_ids"`
		Operations []sourceOperation `json:"operations"`
		Derivation string            `json:"derivation"`
	}
	readJSON(t, "docs/official_sources.json", &sources)
	if sources.Repository != "vmware/vcf-api-specs" ||
		sources.Commit != expectedCommit ||
		sources.SpecPath != expectedSpec ||
		sources.Blob != expectedBlob ||
		sources.License != "Apache-2.0" ||
		!reflect.DeepEqual(sources.IDs, expectedOperations) ||
		!strings.Contains(sources.Derivation, "OpenAPI 3.0.3 YAML") {
		t.Fatalf("unexpected official source record: %#v", sources)
	}
	wantPointers := []string{
		"#/paths/~1session/post",
		"#/paths/~1vcenter~1vm/get",
		"#/paths/~1session/delete",
	}
	if len(sources.Operations) != len(expectedOperations) {
		t.Fatalf("official operation count = %d", len(sources.Operations))
	}
	for index, operation := range sources.Operations {
		if operation.OperationID != expectedOperations[index] ||
			operation.Commit != expectedCommit ||
			operation.SpecPath != expectedSpec ||
			operation.Pointer != wantPointers[index] {
			t.Fatalf("official operation %d mismatch: %#v", index, operation)
		}
	}
}

func TestRotationPublishesThenDrains(t *testing.T) {
	scenario := newScenario(t)
	options := allOptions(t)
	scenario.HoldOldTarget = "/api/vcenter/vm?" + expectedQuery(options)
	logPath := t.TempDir() + "/requests.jsonl"
	server := contractmock.Start(
		t,
		"docs/contract.json",
		logPath,
		scenario,
	)
	defer server.Close()

	client, err := NewClient(
		context.Background(),
		Config{
			BaseURL:    server.URL,
			Username:   scenario.Username,
			Password:   scenario.OldPassword,
			HTTPClient: server.Client,
		},
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if got := client.currentTokenForTest(); got != scenario.OldToken {
		t.Fatalf("initial token = %q, want old generation", got)
	}

	type listResult struct {
		vms []VMSummary
		err error
	}
	oldCall := make(chan listResult, 1)
	go func() {
		vms, callErr := client.ListVMs(context.Background(), options)
		oldCall <- listResult{vms: vms, err: callErr}
	}()
	if !server.WaitForSlow(3 * time.Second) {
		t.Fatal("old-generation request did not reach the hold point")
	}

	rotation := make(chan error, 1)
	go func() {
		rotation <- client.RotatePassword(
			context.Background(),
			scenario.NewPassword,
		)
	}()
	waitForToken(t, client, scenario.NewToken)

	select {
	case rotateErr := <-rotation:
		t.Fatalf("rotation returned before the old request drained: %v", rotateErr)
	default:
	}
	beforeRelease, err := contractmock.ReadLog(logPath)
	if err != nil {
		t.Fatalf("read log before release: %v", err)
	}
	for _, record := range beforeRelease {
		if record.OperationID == SessionDeleteOperation {
			t.Fatal("old session was deleted while its request was still held")
		}
	}

	fastVMs, err := client.ListVMs(context.Background(), ListOptions{})
	if err != nil {
		t.Fatalf("queryless list on published generation: %v", err)
	}
	wantFast := summaries(scenario.NewVMs)
	if !reflect.DeepEqual(fastVMs, wantFast) {
		t.Fatalf("new-generation VMs = %#v, want %#v", fastVMs, wantFast)
	}

	server.ReleaseSlow()
	select {
	case result := <-oldCall:
		if result.err != nil {
			t.Fatalf("old-generation list: %v", result.err)
		}
		wantOld := summaries(scenario.OldVMs)
		if !reflect.DeepEqual(result.vms, wantOld) {
			t.Fatalf("old-generation VMs = %#v, want %#v", result.vms, wantOld)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("old-generation request did not complete after release")
	}
	select {
	case rotateErr := <-rotation:
		if rotateErr != nil {
			t.Fatalf("RotatePassword: %v", rotateErr)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("rotation did not retire the drained old generation")
	}

	if err := client.Close(context.Background()); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := client.Close(context.Background()); err != nil {
		t.Fatalf("idempotent Close: %v", err)
	}

	records, err := contractmock.ReadLog(logPath)
	if err != nil {
		t.Fatalf("read final log: %v", err)
	}
	if len(records) != 6 {
		t.Fatalf("request count = %d, want 6: %#v", len(records), records)
	}
	wantOrder := []string{
		SessionCreateOperation,
		VMListOperation,
		SessionCreateOperation,
		VMListOperation,
		SessionDeleteOperation,
		SessionDeleteOperation,
	}
	for index, want := range wantOrder {
		if records[index].OperationID != want {
			t.Fatalf(
				"request %d operation = %q, want %q",
				index,
				records[index].OperationID,
				want,
			)
		}
		assertBodyless(t, records[index])
	}
	if records[0].Method != http.MethodPost ||
		records[0].Target != "/api/session" ||
		!reflect.DeepEqual(
			records[0].Authorization,
			[]string{expectedBasic(scenario.Username, scenario.OldPassword)},
		) ||
		len(records[0].SessionToken) != 0 {
		t.Fatalf("initial session wire mismatch: %#v", records[0])
	}
	if records[1].Method != http.MethodGet ||
		records[1].Target != scenario.HoldOldTarget ||
		!reflect.DeepEqual(records[1].SessionToken, []string{scenario.OldToken}) ||
		len(records[1].Authorization) != 0 {
		t.Fatalf("old list wire mismatch: %#v", records[1])
	}
	if !reflect.DeepEqual(
		records[2].Authorization,
		[]string{expectedBasic(scenario.Username, scenario.NewPassword)},
	) || len(records[2].SessionToken) != 0 {
		t.Fatalf("replacement session wire mismatch: %#v", records[2])
	}
	if records[3].Target != "/api/vcenter/vm" ||
		!reflect.DeepEqual(records[3].SessionToken, []string{scenario.NewToken}) ||
		len(records[3].Authorization) != 0 {
		t.Fatalf("queryless replacement list wire mismatch: %#v", records[3])
	}
	if !reflect.DeepEqual(records[4].SessionToken, []string{scenario.OldToken}) ||
		!records[4].SlowCompletedAtArrival {
		t.Fatalf("old session retired before drain: %#v", records[4])
	}
	if !reflect.DeepEqual(records[5].SessionToken, []string{scenario.NewToken}) {
		t.Fatalf("close deleted the wrong generation: %#v", records[5])
	}
}

func TestTableDrivenFilterWireShape(t *testing.T) {
	scenario := newScenario(t)
	logPath := t.TempDir() + "/requests.jsonl"
	server := contractmock.Start(
		t,
		"docs/contract.json",
		logPath,
		scenario,
	)
	defer server.Close()
	client, err := NewClient(
		context.Background(),
		Config{
			BaseURL:    server.URL,
			Username:   scenario.Username,
			Password:   scenario.OldPassword,
			HTTPClient: server.Client,
		},
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	tests := []struct {
		name    string
		options ListOptions
		target  string
	}{
		{"all_unset", ListOptions{}, "/api/vcenter/vm"},
		{"vms", ListOptions{VMs: []string{"vm-" + randomHex(t)}}, ""},
		{"names", ListOptions{Names: []string{"name +/雪 " + randomHex(t)}}, ""},
		{"folders", ListOptions{Folders: []string{"folder/" + randomHex(t)}}, ""},
		{"datacenters", ListOptions{Datacenters: []string{"dc?" + randomHex(t)}}, ""},
		{"hosts", ListOptions{Hosts: []string{"host&" + randomHex(t)}}, ""},
		{"clusters", ListOptions{Clusters: []string{"cluster=" + randomHex(t)}}, ""},
		{"resource_pools", ListOptions{ResourcePools: []string{"res pool/" + randomHex(t)}}, ""},
		{"power_states", ListOptions{PowerStates: []string{"SUSPENDED"}}, ""},
	}
	for index := range tests {
		if tests[index].target == "" {
			tests[index].target = "/api/vcenter/vm?" +
				expectedQuery(tests[index].options)
		}
		t.Run(tests[index].name, func(t *testing.T) {
			vms, callErr := client.ListVMs(
				context.Background(),
				tests[index].options,
			)
			if callErr != nil {
				t.Fatalf("ListVMs: %v", callErr)
			}
			if !reflect.DeepEqual(vms, summaries(scenario.OldVMs)) {
				t.Fatalf("ListVMs result = %#v", vms)
			}
		})
	}
	if err := client.Close(context.Background()); err != nil {
		t.Fatalf("Close: %v", err)
	}

	records, err := contractmock.ReadLog(logPath)
	if err != nil {
		t.Fatalf("read log: %v", err)
	}
	if len(records) != len(tests)+2 {
		t.Fatalf("request count = %d, want %d", len(records), len(tests)+2)
	}
	for index, test := range tests {
		record := records[index+1]
		if record.OperationID != VMListOperation ||
			record.Method != http.MethodGet ||
			record.Target != test.target ||
			!reflect.DeepEqual(record.SessionToken, []string{scenario.OldToken}) ||
			len(record.Authorization) != 0 {
			t.Fatalf("%s request mismatch: %#v", test.name, record)
		}
		assertBodyless(t, record)
	}
}

func TestTableDrivenLocalValidation(t *testing.T) {
	var attempts atomic.Int64
	httpClient := &http.Client{
		Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			attempts.Add(1)
			return nil, errors.New("network must not be reached")
		}),
	}
	valid := Config{
		BaseURL:    "http://127.0.0.1:9",
		Username:   "svc",
		Password:   "secret",
		HTTPClient: httpClient,
	}
	tests := []struct {
		name string
		ctx  context.Context
		edit func(*Config)
	}{
		{"nil_context", nil, func(*Config) {}},
		{"blank_url", context.Background(), func(c *Config) { c.BaseURL = "" }},
		{"relative_url", context.Background(), func(c *Config) { c.BaseURL = "vc.local" }},
		{"wrong_scheme", context.Background(), func(c *Config) { c.BaseURL = "ftp://vc.local" }},
		{"embedded_user", context.Background(), func(c *Config) { c.BaseURL = "http://u:p@vc.local" }},
		{"path", context.Background(), func(c *Config) { c.BaseURL = "http://vc.local/sdk" }},
		{"query", context.Background(), func(c *Config) { c.BaseURL = "http://vc.local?x=1" }},
		{"fragment", context.Background(), func(c *Config) { c.BaseURL = "http://vc.local#x" }},
		{"blank_username", context.Background(), func(c *Config) { c.Username = " " }},
		{"colon_username", context.Background(), func(c *Config) { c.Username = "bad:name" }},
		{"control_username", context.Background(), func(c *Config) { c.Username = "bad\nname" }},
		{"blank_password", context.Background(), func(c *Config) { c.Password = "\t" }},
		{"control_password", context.Background(), func(c *Config) { c.Password = "bad\rsecret" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			cfg := valid
			test.edit(&cfg)
			_, err := NewClient(test.ctx, cfg)
			var validation *ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("error = %T %v, want *ValidationError", err, err)
			}
		})
	}
	if got := attempts.Load(); got != 0 {
		t.Fatalf("invalid constructors opened %d connections", got)
	}

	scenario := newScenario(t)
	logPath := t.TempDir() + "/requests.jsonl"
	server := contractmock.Start(t, "docs/contract.json", logPath, scenario)
	defer server.Close()
	client, err := NewClient(
		context.Background(),
		Config{
			BaseURL:    server.URL,
			Username:   scenario.Username,
			Password:   scenario.OldPassword,
			HTTPClient: server.Client,
		},
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	baseline, err := contractmock.ReadLog(logPath)
	if err != nil || len(baseline) != 1 {
		t.Fatalf("initial log: records=%d err=%v", len(baseline), err)
	}

	optionTests := []struct {
		name    string
		options ListOptions
	}{
		{"empty_vms", ListOptions{VMs: []string{}}},
		{"duplicate_names", ListOptions{Names: []string{"same", "same"}}},
		{"blank_folder", ListOptions{Folders: []string{" "}}},
		{"empty_datacenters", ListOptions{Datacenters: []string{}}},
		{"duplicate_hosts", ListOptions{Hosts: []string{"h", "h"}}},
		{"blank_cluster", ListOptions{Clusters: []string{""}}},
		{"empty_resource_pools", ListOptions{ResourcePools: []string{}}},
		{"unknown_power_state", ListOptions{PowerStates: []string{"UNKNOWN"}}},
	}
	for _, test := range optionTests {
		t.Run(test.name, func(t *testing.T) {
			_, callErr := client.ListVMs(context.Background(), test.options)
			var validation *ValidationError
			if !errors.As(callErr, &validation) {
				t.Fatalf("error = %T %v, want *ValidationError", callErr, callErr)
			}
		})
	}
	if _, err := client.ListVMs(nil, ListOptions{}); err == nil {
		t.Fatal("nil ListVMs context was accepted")
	}
	for _, password := range []string{"", " ", "bad\nsecret"} {
		if err := client.RotatePassword(context.Background(), password); err == nil {
			t.Fatalf("invalid replacement password %q was accepted", password)
		}
	}
	after, err := contractmock.ReadLog(logPath)
	if err != nil || len(after) != 1 {
		t.Fatalf("local validation generated traffic: records=%d err=%v", len(after), err)
	}
	if err := client.Close(context.Background()); err != nil {
		t.Fatalf("Close: %v", err)
	}
}

func TestTableDrivenResponseValidation(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{"object_not_array", `{}`},
		{"missing_required", `[{"vm":"vm-1","name":"name"}]`},
		{"blank_required", `[{"vm":"","name":"name","power_state":"POWERED_ON"}]`},
		{"unknown_power_state", `[{"vm":"vm-1","name":"name","power_state":"UNKNOWN"}]`},
		{"wrong_optional_type", `[{"vm":"vm-1","name":"name","power_state":"POWERED_ON","cpu_count":false}]`},
		{"unknown_member", `[{"vm":"vm-1","name":"name","power_state":"POWERED_ON","extra":1}]`},
		{"trailing_value", `[{"vm":"vm-1","name":"name","power_state":"POWERED_ON"}] null`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			scenario.ListBody = []byte(test.body)
			logPath := t.TempDir() + "/requests.jsonl"
			server := contractmock.Start(
				t,
				"docs/contract.json",
				logPath,
				scenario,
			)
			defer server.Close()
			client, err := NewClient(
				context.Background(),
				Config{
					BaseURL:    server.URL,
					Username:   scenario.Username,
					Password:   scenario.OldPassword,
					HTTPClient: server.Client,
				},
			)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			_, err = client.ListVMs(context.Background(), ListOptions{})
			var protocol *ProtocolError
			if !errors.As(err, &protocol) ||
				protocol.OperationID != VMListOperation {
				t.Fatalf("error = %T %v, want VM-list ProtocolError", err, err)
			}
			if err := client.Close(context.Background()); err != nil {
				t.Fatalf("Close: %v", err)
			}
			records, readErr := contractmock.ReadLog(logPath)
			if readErr != nil || len(records) != 3 {
				t.Fatalf("request count=%d readErr=%v", len(records), readErr)
			}
		})
	}
}

func TestFailuresAreBoundedAndRedacted(t *testing.T) {
	t.Run("rotation_create_failure_keeps_old_generation", func(t *testing.T) {
		scenario := newScenario(t)
		scenario.NewCreateStatus = http.StatusServiceUnavailable
		scenario.ErrorSecret = "payload-" + randomHex(t)
		logPath := t.TempDir() + "/requests.jsonl"
		server := contractmock.Start(t, "docs/contract.json", logPath, scenario)
		defer server.Close()
		client, err := NewClient(
			context.Background(),
			Config{
				BaseURL:    server.URL,
				Username:   scenario.Username,
				Password:   scenario.OldPassword,
				HTTPClient: server.Client,
			},
		)
		if err != nil {
			t.Fatalf("NewClient: %v", err)
		}
		err = client.RotatePassword(
			context.Background(),
			scenario.NewPassword,
		)
		var apiError *APIError
		if !errors.As(err, &apiError) ||
			apiError.OperationID != SessionCreateOperation ||
			apiError.StatusCode != http.StatusServiceUnavailable {
			t.Fatalf("rotation error = %T %v", err, err)
		}
		assertRedacted(
			t,
			err,
			scenario.OldPassword,
			scenario.NewPassword,
			scenario.OldToken,
			scenario.NewToken,
			scenario.ErrorSecret,
		)
		if got := client.currentTokenForTest(); got != scenario.OldToken {
			t.Fatalf("failed rotation published token %q", got)
		}
		if _, err := client.ListVMs(context.Background(), ListOptions{}); err != nil {
			t.Fatalf("old generation unusable after failed rotation: %v", err)
		}
		if err := client.Close(context.Background()); err != nil {
			t.Fatalf("Close: %v", err)
		}
		records, readErr := contractmock.ReadLog(logPath)
		if readErr != nil {
			t.Fatalf("read log: %v", readErr)
		}
		want := []string{
			SessionCreateOperation,
			SessionCreateOperation,
			VMListOperation,
			SessionDeleteOperation,
		}
		if got := operationIDs(records); !reflect.DeepEqual(got, want) {
			t.Fatalf("operation order = %#v, want %#v", got, want)
		}
		if !reflect.DeepEqual(
			records[3].SessionToken,
			[]string{scenario.OldToken},
		) {
			t.Fatalf("close retired wrong token: %#v", records[3])
		}
	})

	t.Run("list_http_error_not_retried", func(t *testing.T) {
		scenario := newScenario(t)
		scenario.ListStatus = http.StatusServiceUnavailable
		scenario.ErrorSecret = "server-" + randomHex(t)
		logPath := t.TempDir() + "/requests.jsonl"
		server := contractmock.Start(t, "docs/contract.json", logPath, scenario)
		defer server.Close()
		client, err := NewClient(
			context.Background(),
			Config{
				BaseURL:    server.URL,
				Username:   scenario.Username,
				Password:   scenario.OldPassword,
				HTTPClient: server.Client,
			},
		)
		if err != nil {
			t.Fatalf("NewClient: %v", err)
		}
		_, err = client.ListVMs(context.Background(), ListOptions{})
		var apiError *APIError
		if !errors.As(err, &apiError) ||
			apiError.OperationID != VMListOperation ||
			apiError.StatusCode != http.StatusServiceUnavailable {
			t.Fatalf("list error = %T %v", err, err)
		}
		assertRedacted(
			t,
			err,
			scenario.OldPassword,
			scenario.OldToken,
			scenario.ErrorSecret,
		)
		if err := client.Close(context.Background()); err != nil {
			t.Fatalf("Close: %v", err)
		}
		records, readErr := contractmock.ReadLog(logPath)
		if readErr != nil || len(records) != 3 {
			t.Fatalf("bounded request count=%d readErr=%v", len(records), readErr)
		}
	})

	t.Run("redirect_refused", func(t *testing.T) {
		scenario := newScenario(t)
		scenario.InitialCreateStatus = http.StatusTemporaryRedirect
		logPath := t.TempDir() + "/requests.jsonl"
		server := contractmock.Start(t, "docs/contract.json", logPath, scenario)
		defer server.Close()
		callerClient := server.Client
		originalRedirect := callerClient.CheckRedirect
		_, err := NewClient(
			context.Background(),
			Config{
				BaseURL:    server.URL,
				Username:   scenario.Username,
				Password:   scenario.OldPassword,
				HTTPClient: callerClient,
			},
		)
		var apiError *APIError
		if !errors.As(err, &apiError) ||
			apiError.StatusCode != http.StatusTemporaryRedirect {
			t.Fatalf("redirect error = %T %v", err, err)
		}
		if callerClient.CheckRedirect != nil || originalRedirect != nil {
			t.Fatal("NewClient mutated the caller-owned HTTP client")
		}
		records, readErr := contractmock.ReadLog(logPath)
		if readErr != nil || len(records) != 1 {
			t.Fatalf("redirect generated %d requests, readErr=%v", len(records), readErr)
		}
	})

	t.Run("context_cancellation_is_discoverable", func(t *testing.T) {
		scenario := newScenario(t)
		logPath := t.TempDir() + "/requests.jsonl"
		server := contractmock.Start(t, "docs/contract.json", logPath, scenario)
		defer server.Close()
		client, err := NewClient(
			context.Background(),
			Config{
				BaseURL:    server.URL,
				Username:   scenario.Username,
				Password:   scenario.OldPassword,
				HTTPClient: server.Client,
			},
		)
		if err != nil {
			t.Fatalf("NewClient: %v", err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, err = client.ListVMs(ctx, ListOptions{})
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %T %v, want discoverable context.Canceled", err, err)
		}
		assertRedacted(t, err, scenario.OldToken, scenario.OldPassword)
		if err := client.Close(context.Background()); err != nil {
			t.Fatalf("Close: %v", err)
		}
		records, readErr := contractmock.ReadLog(logPath)
		if readErr != nil || len(records) != 2 {
			t.Fatalf("canceled request reached server: count=%d err=%v", len(records), readErr)
		}
	})
}

func TestClosePreventsNewWorkAndIsIdempotent(t *testing.T) {
	scenario := newScenario(t)
	logPath := t.TempDir() + "/requests.jsonl"
	server := contractmock.Start(t, "docs/contract.json", logPath, scenario)
	defer server.Close()
	client, err := NewClient(
		context.Background(),
		Config{
			BaseURL:    server.URL,
			Username:   scenario.Username,
			Password:   scenario.OldPassword,
			HTTPClient: server.Client,
		},
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if err := client.Close(context.Background()); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := client.Close(context.Background()); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	if _, err := client.ListVMs(context.Background(), ListOptions{}); err == nil {
		t.Fatal("closed client accepted new list work")
	}
	if err := client.RotatePassword(
		context.Background(),
		scenario.NewPassword,
	); err == nil {
		t.Fatal("closed client accepted rotation")
	}
	records, err := contractmock.ReadLog(logPath)
	if err != nil || len(records) != 2 {
		t.Fatalf("closed client generated traffic: count=%d err=%v", len(records), err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return function(request)
}

func readJSON(t testing.TB, path string, target any) {
	t.Helper()
	data, err := osReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

// Kept behind a variable so no production package filesystem behavior is
// accidentally exercised by the verifier.
var osReadFile = func(path string) ([]byte, error) {
	return os.ReadFile(path)
}

func newScenario(t testing.TB) contractmock.Scenario {
	t.Helper()
	cpu := int64(6)
	memory := int64(24576)
	return contractmock.Scenario{
		Username:    "svc-雪-" + randomHex(t),
		OldPassword: "old-päss-" + randomHex(t),
		NewPassword: "new-päss-" + randomHex(t),
		OldToken:    "old-token-" + randomHex(t),
		NewToken:    "new-token-" + randomHex(t),
		OldVMs: []contractmock.VM{
			{
				VM:         "vm-old-" + randomHex(t),
				Name:       "old workload",
				PowerState: "POWERED_ON",
				CPUCount:   &cpu,
			},
		},
		NewVMs: []contractmock.VM{
			{
				VM:            "vm-new-" + randomHex(t),
				Name:          "new workload",
				PowerState:    "SUSPENDED",
				MemorySizeMiB: &memory,
			},
		},
		ErrorSecret: "private-" + randomHex(t),
	}
}

func allOptions(t testing.TB) ListOptions {
	t.Helper()
	return ListOptions{
		VMs:           []string{"vm/" + randomHex(t), "vm 雪+" + randomHex(t)},
		Names:         []string{"name?=" + randomHex(t)},
		Folders:       []string{"folder/" + randomHex(t)},
		Datacenters:   []string{"dc&" + randomHex(t)},
		Hosts:         []string{"host #" + randomHex(t)},
		Clusters:      []string{"cluster+" + randomHex(t)},
		ResourcePools: []string{"pool=/" + randomHex(t)},
		PowerStates:   []string{"POWERED_ON", "SUSPENDED"},
	}
}

func randomHex(t testing.TB) string {
	t.Helper()
	value := make([]byte, 8)
	if _, err := rand.Read(value); err != nil {
		t.Fatalf("crypto/rand: %v", err)
	}
	return hex.EncodeToString(value)
}

func expectedBasic(username, password string) string {
	return "Basic " + base64.StdEncoding.EncodeToString(
		[]byte(username+":"+password),
	)
}

func expectedQuery(options ListOptions) string {
	fields := []struct {
		name   string
		values []string
	}{
		{"vms", options.VMs},
		{"names", options.Names},
		{"folders", options.Folders},
		{"datacenters", options.Datacenters},
		{"hosts", options.Hosts},
		{"clusters", options.Clusters},
		{"resource_pools", options.ResourcePools},
		{"power_states", options.PowerStates},
	}
	pairs := make([]string, 0)
	for _, field := range fields {
		for _, value := range field.values {
			pairs = append(
				pairs,
				expectedRFC3986(field.name)+"="+expectedRFC3986(value),
			)
		}
	}
	return strings.Join(pairs, "&")
}

func expectedRFC3986(value string) string {
	const digits = "0123456789ABCDEF"
	var result strings.Builder
	for _, character := range []byte(value) {
		if character >= 'a' && character <= 'z' ||
			character >= 'A' && character <= 'Z' ||
			character >= '0' && character <= '9' ||
			character == '-' || character == '.' ||
			character == '_' || character == '~' {
			result.WriteByte(character)
			continue
		}
		result.WriteByte('%')
		result.WriteByte(digits[character>>4])
		result.WriteByte(digits[character&15])
	}
	return result.String()
}

func waitForToken(t testing.TB, client *Client, token string) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if client.currentTokenForTest() == token {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("replacement token %q was not published", token)
		}
		time.Sleep(time.Millisecond)
	}
}

func summaries(values []contractmock.VM) []VMSummary {
	result := make([]VMSummary, len(values))
	for index, value := range values {
		result[index] = VMSummary{
			VM:            value.VM,
			Name:          value.Name,
			PowerState:    value.PowerState,
			CPUCount:      value.CPUCount,
			MemorySizeMiB: value.MemorySizeMiB,
		}
	}
	return result
}

func assertBodyless(t testing.TB, record contractmock.RequestRecord) {
	t.Helper()
	if !reflect.DeepEqual(record.Accept, []string{"application/json"}) ||
		len(record.ContentType) != 0 ||
		len(record.TransferEncoding) != 0 ||
		record.ContentLength != 0 ||
		record.BodyBase64 != "" {
		t.Fatalf("request is not exact and bodyless: %#v", record)
	}
}

func assertRedacted(t testing.TB, err error, secrets ...string) {
	t.Helper()
	text := fmt.Sprint(err)
	for _, secret := range secrets {
		if secret != "" && strings.Contains(text, secret) {
			t.Fatalf("error text exposed secret %q: %q", secret, text)
		}
	}
}

func operationIDs(records []contractmock.RequestRecord) []string {
	result := make([]string, len(records))
	for index, record := range records {
		result[index] = record.OperationID
	}
	return result
}
