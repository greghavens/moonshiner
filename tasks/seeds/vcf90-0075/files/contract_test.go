package maintenancechange

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	"vcf90-0075/internal/contractmock"
)

const (
	wantTag      = "9.0.0.0"
	wantCommit   = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	wantSpecPath = "specifications/vcf-operations/vcf-operations-openapi.json"
	wantOpID     = "createMaintenanceSchedules"
)

func TestProtectedContractProvenance(t *testing.T) {
	t.Parallel()

	contractData, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		DerivedFrom struct {
			Tag      string `json:"repository_tag"`
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
		} `json:"derived_from"`
		Servers []struct {
			URL string `json:"url"`
		} `json:"servers"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractData, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.DerivedFrom.Tag != wantTag || contract.DerivedFrom.Commit != wantCommit || contract.DerivedFrom.SpecPath != wantSpecPath {
		t.Fatalf("contract provenance = %#v", contract.DerivedFrom)
	}
	if len(contract.Servers) != 1 || contract.Servers[0].URL != "/suite-api" {
		t.Fatalf("contract servers = %#v", contract.Servers)
	}
	if len(contract.Operations) != 1 {
		t.Fatalf("contract operations = %#v", contract.Operations)
	}
	operation := contract.Operations[0]
	if operation.OperationID != wantOpID || operation.Method != "POST" || operation.Path != "/api/maintenanceschedules" {
		t.Fatalf("contract operation = %#v", operation)
	}

	sourcesData, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		Repository struct {
			Tag    string `json:"tag"`
			Commit string `json:"commit_sha"`
		} `json:"repository"`
		Specification struct {
			Path string `json:"path"`
		} `json:"specification"`
		Operations []struct {
			OperationID string `json:"operationId"`
		} `json:"operations"`
		Derivation string `json:"derivation"`
	}
	if err := json.Unmarshal(sourcesData, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Repository.Tag != wantTag || sources.Repository.Commit != wantCommit || sources.Specification.Path != wantSpecPath {
		t.Fatalf("official source provenance is not pinned to VCF 9.0: %#v", sources)
	}
	if len(sources.Operations) != 1 || sources.Operations[0].OperationID != wantOpID {
		t.Fatalf("official source operations = %#v", sources.Operations)
	}
	if !strings.Contains(sources.Derivation, "not derived from a rendered documentation page or from the 9.1 revision") {
		t.Fatalf("derivation statement does not distinguish the pinned specification: %q", sources.Derivation)
	}
}

func TestApplyReportsPartialChangeAndExactWire(t *testing.T) {
	t.Parallel()

	fixture, err := contractmock.New(contractmock.Config{FailAt: 1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = fixture.Close() })
	runtime := fixture.Runtime()

	client, err := NewClient(Config{
		BaseURL:     fixture.URL(),
		AccessToken: runtime.AccessToken,
		HTTPClient:  fixture.Client(),
	})
	if err != nil {
		t.Fatal(err)
	}
	recurrence := int32(2)
	expireRuns := int32(5)
	startDate := "08/20/2026"
	changes := []MaintenanceScheduleSpec{
		{
			Key: runtime.KeyPrefix + "-first",
			Schedule: Schedule{
				Hour:            2,
				MinuteOfTheHour: 15,
				Duration:        90,
				ScheduleType:    "DAILY",
				Recurrence:      &recurrence,
				ExpireRuns:      &expireRuns,
			},
		},
		{
			Key: runtime.KeyPrefix + "-second",
			Schedule: Schedule{
				Hour:            4,
				MinuteOfTheHour: 0,
				Duration:        45,
				ScheduleType:    "ONCE",
				StartDate:       &startDate,
			},
		},
		{
			Key:      runtime.KeyPrefix + "-must-not-run",
			Schedule: Schedule{Hour: 5, Duration: 30, ScheduleType: "ONCE"},
		},
	}

	report, applyErr := client.Apply(context.Background(), changes)
	var apiErr *APIError
	if !errors.As(applyErr, &apiErr) {
		t.Fatalf("Apply error = %T %v, want *APIError", applyErr, applyErr)
	}
	if apiErr.OperationID != wantOpID || apiErr.StatusCode != 422 {
		t.Fatalf("APIError = %#v", apiErr)
	}
	if len(report.Results) != 2 {
		t.Fatalf("attempted results = %d, want 2: %#v", len(report.Results), report.Results)
	}
	first := report.Results[0]
	if first.Index != 0 || first.Key != changes[0].Key || first.Status != StepSucceeded || first.Created == nil {
		t.Fatalf("first result lost successful outcome: %#v", first)
	}
	if first.Created.ID != "00000000-0000-4000-8000-000000000001" || first.Created.Key != changes[0].Key || !reflect.DeepEqual(first.Created.Schedule, changes[0].Schedule) {
		t.Fatalf("first created schedule = %#v", first.Created)
	}
	second := report.Results[1]
	if second.Index != 1 || second.Key != changes[1].Key || second.Status != StepFailed || second.Created != nil {
		t.Fatalf("failed result = %#v", second)
	}

	requests := fixture.Requests()
	if len(requests) != 2 {
		t.Fatalf("requests = %d, want 2 (stop after failure)", len(requests))
	}
	wantBodies := [][]byte{
		[]byte(fmt.Sprintf(`{"key":%q,"schedule":{"hour":2,"minuteOfTheHour":15,"duration":90,"scheduleType":"DAILY","recurrence":2,"expireRuns":5}}`, changes[0].Key)),
		[]byte(fmt.Sprintf(`{"key":%q,"schedule":{"hour":4,"minuteOfTheHour":0,"duration":45,"scheduleType":"ONCE","startDate":"08/20/2026"}}`, changes[1].Key)),
	}
	for index, request := range requests {
		if request.Method != "POST" || request.Path != "/suite-api/api/maintenanceschedules" || request.RawQuery != "" {
			t.Errorf("request[%d] target = %s %s?%s", index, request.Method, request.Path, request.RawQuery)
		}
		if request.Header.Get("Authorization") != runtime.AccessToken {
			t.Errorf("request[%d] Authorization = %q", index, request.Header.Get("Authorization"))
		}
		if request.Header.Get("Accept") != "application/json" || request.Header.Get("Content-Type") != "application/json" {
			t.Errorf("request[%d] media headers = Accept %q Content-Type %q", index, request.Header.Get("Accept"), request.Header.Get("Content-Type"))
		}
		if len(request.TransferEncoding) != 0 {
			t.Errorf("request[%d] unexpectedly chunked: %#v", index, request.TransferEncoding)
		}
		if !reflect.DeepEqual(request.Body, wantBodies[index]) {
			t.Errorf("request[%d] body\n got: %s\nwant: %s", index, request.Body, wantBodies[index])
		}
	}

	var firstWire map[string]any
	if err := json.Unmarshal(requests[0].Body, &firstWire); err != nil {
		t.Fatal(err)
	}
	if _, present := firstWire["id"]; present {
		t.Error("server-generated id was sent in a create request")
	}
	scheduleWire := firstWire["schedule"].(map[string]any)
	for _, omitted := range []string{
		"dayOfTheMonth", "daysOfTheMonth", "weeksOfTheMonth", "daysOfTheWeek",
		"month", "months", "startDate", "expirationDate", "timeZone",
	} {
		if value, present := scheduleWire[omitted]; present {
			t.Errorf("unset optional field %q was sent as %#v", omitted, value)
		}
	}
}

func TestApplySuccessAndOmissionTable(t *testing.T) {
	t.Parallel()

	text := func(value string) *string { return &value }
	integer := func(value int32) *int32 { return &value }
	cases := []struct {
		name       string
		schedule   Schedule
		wantSuffix string
	}{
		{
			name: "required fields only",
			schedule: Schedule{
				Hour: 0, MinuteOfTheHour: 0, Duration: 1, ScheduleType: "ONCE",
			},
			wantSuffix: `{"hour":0,"minuteOfTheHour":0,"duration":1,"scheduleType":"ONCE"}`,
		},
		{
			name: "selected optional fields",
			schedule: Schedule{
				Hour: 23, MinuteOfTheHour: 59, Duration: 120, ScheduleType: "WEEKLY",
				Recurrence: integer(1), DaysOfTheWeek: []string{"SATURDAY", "SUNDAY"},
				TimeZone: text("America/Chicago"), ExpirationDate: text("12/31/2026"),
			},
			wantSuffix: `{"hour":23,"minuteOfTheHour":59,"duration":120,"scheduleType":"WEEKLY","recurrence":1,"daysOfTheWeek":["SATURDAY","SUNDAY"],"expirationDate":"12/31/2026","timeZone":"America/Chicago"}`,
		},
		{
			name: "legacy optional scalars including explicit zeros",
			schedule: Schedule{
				Hour: 6, MinuteOfTheHour: 30, Duration: 15, ScheduleType: "MONTHLY",
				DayOfTheMonth: integer(0), Month: integer(0), StartDate: text("01/02/2027"),
				TimeZone: text(""), ExpireRuns: integer(0),
			},
			wantSuffix: `{"hour":6,"minuteOfTheHour":30,"duration":15,"scheduleType":"MONTHLY","dayOfTheMonth":0,"month":0,"startDate":"01/02/2027","timeZone":"","expireRuns":0}`,
		},
		{
			name: "remaining optional collections",
			schedule: Schedule{
				Hour: 6, MinuteOfTheHour: 30, Duration: 15, ScheduleType: "MONTHLY",
				DaysOfTheMonth: []string{"1", "LAST"}, WeeksOfTheMonth: []string{"FIRST", "LAST"},
				DaysOfTheWeek: []string{"MONDAY"}, Months: []int32{1, 12},
			},
			wantSuffix: `{"hour":6,"minuteOfTheHour":30,"duration":15,"scheduleType":"MONTHLY","daysOfTheMonth":["1","LAST"],"weeksOfTheMonth":["FIRST","LAST"],"daysOfTheWeek":["MONDAY"],"months":[1,12]}`,
		},
		{
			name:       "unknown enum member is accepted",
			schedule:   Schedule{Hour: 1, Duration: 1, ScheduleType: "UNKNOWN"},
			wantSuffix: `{"hour":1,"minuteOfTheHour":0,"duration":1,"scheduleType":"UNKNOWN"}`,
		},
		{
			name:       "yearly enum member is accepted",
			schedule:   Schedule{Hour: 1, Duration: 1, ScheduleType: "YEARLY"},
			wantSuffix: `{"hour":1,"minuteOfTheHour":0,"duration":1,"scheduleType":"YEARLY"}`,
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			fixture, err := contractmock.New(contractmock.Config{FailAt: -1})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = fixture.Close() })
			runtime := fixture.Runtime()
			client, err := NewClient(Config{BaseURL: fixture.URL(), AccessToken: runtime.AccessToken, HTTPClient: fixture.Client()})
			if err != nil {
				t.Fatal(err)
			}
			key := runtime.KeyPrefix + "-table"
			report, err := client.Apply(context.Background(), []MaintenanceScheduleSpec{{Key: key, Schedule: testCase.schedule}})
			if err != nil {
				t.Fatal(err)
			}
			if len(report.Results) != 1 || report.Results[0].Status != StepSucceeded || report.Results[0].Created == nil {
				t.Fatalf("report = %#v", report)
			}
			if !reflect.DeepEqual(report.Results[0].Created.Schedule, testCase.schedule) {
				t.Fatalf("created schedule = %#v, want %#v", report.Results[0].Created.Schedule, testCase.schedule)
			}
			requests := fixture.Requests()
			if len(requests) != 1 {
				t.Fatalf("requests = %d", len(requests))
			}
			want := []byte(fmt.Sprintf(`{"key":%q,"schedule":%s}`, key, testCase.wantSuffix))
			if !reflect.DeepEqual(requests[0].Body, want) {
				t.Fatalf("body\n got: %s\nwant: %s", requests[0].Body, want)
			}
		})
	}
}

func TestValidationStopsBeforeTrafficTable(t *testing.T) {
	t.Parallel()

	fixture, err := contractmock.New(contractmock.Config{FailAt: -1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = fixture.Close() })
	client, err := NewClient(Config{BaseURL: fixture.URL(), AccessToken: fixture.Runtime().AccessToken, HTTPClient: fixture.Client()})
	if err != nil {
		t.Fatal(err)
	}
	zero := int32(0)
	negative := int32(-1)
	cases := []struct {
		name string
		spec MaintenanceScheduleSpec
	}{
		{"empty key", MaintenanceScheduleSpec{Schedule: validSchedule()}},
		{"key too long", MaintenanceScheduleSpec{Key: strings.Repeat("x", 201), Schedule: validSchedule()}},
		{"unicode key too long", MaintenanceScheduleSpec{Key: strings.Repeat("é", 201), Schedule: validSchedule()}},
		{"duration below minimum", MaintenanceScheduleSpec{Key: "bad-duration", Schedule: Schedule{Duration: 0, ScheduleType: "ONCE"}}},
		{"negative duration", MaintenanceScheduleSpec{Key: "negative-duration", Schedule: Schedule{Duration: -1, ScheduleType: "ONCE"}}},
		{"unknown schedule type", MaintenanceScheduleSpec{Key: "bad-type", Schedule: Schedule{Duration: 1, ScheduleType: "SOMEDAY"}}},
		{"recurrence below minimum", MaintenanceScheduleSpec{Key: "bad-recurrence", Schedule: Schedule{Duration: 1, ScheduleType: "DAILY", Recurrence: &zero}}},
		{"negative recurrence", MaintenanceScheduleSpec{Key: "negative-recurrence", Schedule: Schedule{Duration: 1, ScheduleType: "DAILY", Recurrence: &negative}}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			report, err := client.Apply(context.Background(), []MaintenanceScheduleSpec{testCase.spec})
			if err == nil {
				t.Fatal("Apply succeeded")
			}
			if len(report.Results) != 1 || report.Results[0].Status != StepFailed || report.Results[0].Index != 0 {
				t.Fatalf("report = %#v", report)
			}
		})
	}
	if got := len(fixture.Requests()); got != 0 {
		t.Fatalf("invalid changes made %d requests", got)
	}
}

func TestNewClientTable(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name   string
		config Config
	}{
		{"empty URL", Config{AccessToken: "token"}},
		{"relative URL", Config{BaseURL: "/appliance", AccessToken: "token"}},
		{"unsupported scheme", Config{BaseURL: "ftp://ops.example.test", AccessToken: "token"}},
		{"missing hostname", Config{BaseURL: "http://:8080", AccessToken: "token"}},
		{"credentials", Config{BaseURL: "https://user:pass@ops.example.test", AccessToken: "token"}},
		{"base path", Config{BaseURL: "https://ops.example.test/suite-api", AccessToken: "token"}},
		{"query", Config{BaseURL: "https://ops.example.test/?x=1", AccessToken: "token"}},
		{"empty query", Config{BaseURL: "https://ops.example.test?", AccessToken: "token"}},
		{"fragment", Config{BaseURL: "https://ops.example.test/#section", AccessToken: "token"}},
		{"empty fragment", Config{BaseURL: "https://ops.example.test#", AccessToken: "token"}},
		{"empty token", Config{BaseURL: "https://ops.example.test"}},
		{"blank unicode token", Config{BaseURL: "https://ops.example.test", AccessToken: "\u00a0"}},
		{"token space", Config{BaseURL: "https://ops.example.test", AccessToken: "bad token"}},
		{"token tab", Config{BaseURL: "https://ops.example.test", AccessToken: "bad\ttoken"}},
		{"token newline", Config{BaseURL: "https://ops.example.test", AccessToken: "bad\ntoken"}},
		{"token carriage return", Config{BaseURL: "https://ops.example.test", AccessToken: "bad\rtoken"}},
		{"token vertical tab", Config{BaseURL: "https://ops.example.test", AccessToken: "bad\vtoken"}},
		{"token form feed", Config{BaseURL: "https://ops.example.test", AccessToken: "bad\ftoken"}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if _, err := NewClient(testCase.config); err == nil {
				t.Fatal("NewClient succeeded")
			}
		})
	}
}

func TestNewClientAcceptsOriginsWithoutMakingARequest(t *testing.T) {
	t.Parallel()

	calls := 0
	httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls++
		return nil, errors.New("unexpected request")
	})}
	for _, baseURL := range []string{"http://ops.example.test", "https://ops.example.test/", "HTTP://ops.example.test"} {
		client, err := NewClient(Config{BaseURL: baseURL, AccessToken: "token-value", HTTPClient: httpClient})
		if err != nil || client == nil {
			t.Errorf("NewClient(%q) = %#v, %v", baseURL, client, err)
		}
	}
	if calls != 0 {
		t.Fatalf("construction made %d HTTP requests", calls)
	}
}

func TestValidationAfterSuccessPreservesBothAttemptedSteps(t *testing.T) {
	t.Parallel()

	fixture, err := contractmock.New(contractmock.Config{FailAt: -1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = fixture.Close() })
	runtime := fixture.Runtime()
	client, err := NewClient(Config{BaseURL: fixture.URL(), AccessToken: runtime.AccessToken, HTTPClient: fixture.Client()})
	if err != nil {
		t.Fatal(err)
	}
	changes := []MaintenanceScheduleSpec{
		{Key: runtime.KeyPrefix + "-valid", Schedule: validSchedule()},
		{Key: runtime.KeyPrefix + "-invalid", Schedule: Schedule{Duration: 0, ScheduleType: "ONCE"}},
		{Key: runtime.KeyPrefix + "-must-not-run", Schedule: validSchedule()},
	}
	report, err := client.Apply(context.Background(), changes)
	if err == nil {
		t.Fatal("Apply succeeded")
	}
	if len(report.Results) != 2 || report.Results[0].Status != StepSucceeded || report.Results[0].Created == nil ||
		report.Results[1].Index != 1 || report.Results[1].Key != changes[1].Key || report.Results[1].Status != StepFailed || report.Results[1].Created != nil {
		t.Fatalf("report = %#v", report)
	}
	if got := len(fixture.Requests()); got != 1 {
		t.Fatalf("requests = %d, want only the first valid step", got)
	}
}

func TestUnicodeKeyLimitCountsCharacters(t *testing.T) {
	t.Parallel()

	fixture, err := contractmock.New(contractmock.Config{FailAt: -1})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = fixture.Close() })
	client, err := NewClient(Config{BaseURL: fixture.URL(), AccessToken: fixture.Runtime().AccessToken, HTTPClient: fixture.Client()})
	if err != nil {
		t.Fatal(err)
	}
	key := strings.Repeat("é", 200)
	report, err := client.Apply(context.Background(), []MaintenanceScheduleSpec{{Key: key, Schedule: validSchedule()}})
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Results) != 1 || report.Results[0].Status != StepSucceeded || report.Results[0].Key != key {
		t.Fatalf("report = %#v", report)
	}
}

func TestResponseFailureContractTable(t *testing.T) {
	t.Parallel()

	validScheduleJSON := `{"hour":0,"minuteOfTheHour":0,"duration":1,"scheduleType":"ONCE"}`
	validBody := `{"id":"created-id","key":"response-test","schedule":` + validScheduleJSON + `}`
	cases := []struct {
		name        string
		status      int
		contentType string
		body        string
		wantAPI     bool
	}{
		{"status must be exactly 201", http.StatusOK, "application/json", validBody, true},
		{"missing media type", http.StatusCreated, "", validBody, false},
		{"wrong media type", http.StatusCreated, "text/plain", validBody, false},
		{"malformed JSON", http.StatusCreated, "application/json", `{`, false},
		{"trailing JSON", http.StatusCreated, "application/json", validBody + `{}`, false},
		{"missing key", http.StatusCreated, "application/json", `{"schedule":` + validScheduleJSON + `}`, false},
		{"null key", http.StatusCreated, "application/json", `{"key":null,"schedule":` + validScheduleJSON + `}`, false},
		{"missing schedule", http.StatusCreated, "application/json", `{"key":"response-test"}`, false},
		{"missing hour", http.StatusCreated, "application/json", `{"key":"response-test","schedule":{"minuteOfTheHour":0,"duration":1,"scheduleType":"ONCE"}}`, false},
		{"missing minute", http.StatusCreated, "application/json", `{"key":"response-test","schedule":{"hour":0,"duration":1,"scheduleType":"ONCE"}}`, false},
		{"missing duration", http.StatusCreated, "application/json", `{"key":"response-test","schedule":{"hour":0,"minuteOfTheHour":0,"scheduleType":"ONCE"}}`, false},
		{"missing schedule type", http.StatusCreated, "application/json", `{"key":"response-test","schedule":{"hour":0,"minuteOfTheHour":0,"duration":1}}`, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			client, err := NewClient(Config{
				BaseURL: "https://ops.example.test", AccessToken: "token",
				HTTPClient: newResponseClient(t, testCase.status, testCase.contentType, testCase.body),
			})
			if err != nil {
				t.Fatal(err)
			}
			report, applyErr := client.Apply(context.Background(), []MaintenanceScheduleSpec{{Key: "response-test", Schedule: validSchedule()}})
			if applyErr == nil {
				t.Fatal("Apply succeeded")
			}
			if len(report.Results) != 1 || report.Results[0].Status != StepFailed || report.Results[0].Created != nil {
				t.Fatalf("report = %#v", report)
			}
			if testCase.wantAPI {
				var apiErr *APIError
				if !errors.As(applyErr, &apiErr) || apiErr.OperationID != wantOpID || apiErr.StatusCode != testCase.status {
					t.Fatalf("error = %T %v", applyErr, applyErr)
				}
				return
			}
			var protocolErr *ProtocolError
			if !errors.As(applyErr, &protocolErr) || protocolErr.OperationID != wantOpID {
				t.Fatalf("error = %T %v", applyErr, applyErr)
			}
		})
	}
}

func TestCompleteResponseIsPreserved(t *testing.T) {
	t.Parallel()

	body := `{"id":"created-id","key":"server-key","schedule":{"hour":7,"minuteOfTheHour":8,"duration":9,"scheduleType":"YEARLY","recurrence":2,"dayOfTheMonth":0,"daysOfTheMonth":["LAST"],"weeksOfTheMonth":["SECOND"],"daysOfTheWeek":["TUESDAY"],"month":0,"months":[2,11],"startDate":"02/03/2027","expirationDate":"04/05/2028","timeZone":"UTC","expireRuns":0}}`
	client, err := NewClient(Config{
		BaseURL: "https://ops.example.test", AccessToken: "token",
		HTTPClient: newResponseClient(t, http.StatusCreated, "application/json; charset=utf-8", body),
	})
	if err != nil {
		t.Fatal(err)
	}
	report, err := client.Apply(context.Background(), []MaintenanceScheduleSpec{{Key: "request-key", Schedule: validSchedule()}})
	if err != nil {
		t.Fatal(err)
	}
	integer := func(value int32) *int32 { return &value }
	text := func(value string) *string { return &value }
	want := &MaintenanceSchedule{
		ID: "created-id", Key: "server-key",
		Schedule: Schedule{
			Hour: 7, MinuteOfTheHour: 8, Duration: 9, ScheduleType: "YEARLY",
			Recurrence: integer(2), DayOfTheMonth: integer(0), DaysOfTheMonth: []string{"LAST"},
			WeeksOfTheMonth: []string{"SECOND"}, DaysOfTheWeek: []string{"TUESDAY"},
			Month: integer(0), Months: []int32{2, 11}, StartDate: text("02/03/2027"),
			ExpirationDate: text("04/05/2028"), TimeZone: text("UTC"), ExpireRuns: integer(0),
		},
	}
	if len(report.Results) != 1 || report.Results[0].Status != StepSucceeded || !reflect.DeepEqual(report.Results[0].Created, want) {
		t.Fatalf("created = %#v, want %#v", report.Results, want)
	}
}

func TestTransportAndContextErrorsArePreserved(t *testing.T) {
	t.Parallel()

	t.Run("transport", func(t *testing.T) {
		sentinel := errors.New("transport sentinel")
		client, err := NewClient(Config{
			BaseURL: "https://ops.example.test", AccessToken: "token",
			HTTPClient: &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) { return nil, sentinel })},
		})
		if err != nil {
			t.Fatal(err)
		}
		report, applyErr := client.Apply(context.Background(), []MaintenanceScheduleSpec{{Key: "transport", Schedule: validSchedule()}})
		if !errors.Is(applyErr, sentinel) {
			t.Fatalf("error = %T %v, want wrapped sentinel", applyErr, applyErr)
		}
		if len(report.Results) != 1 || report.Results[0].Status != StepFailed {
			t.Fatalf("report = %#v", report)
		}
	})

	t.Run("already canceled context", func(t *testing.T) {
		calls := 0
		client, err := NewClient(Config{
			BaseURL: "https://ops.example.test", AccessToken: "token",
			HTTPClient: &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
				calls++
				return nil, errors.New("unexpected transport call")
			})},
		})
		if err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		report, applyErr := client.Apply(ctx, []MaintenanceScheduleSpec{{Key: "context", Schedule: validSchedule()}})
		if !errors.Is(applyErr, context.Canceled) {
			t.Fatalf("error = %T %v, want context.Canceled", applyErr, applyErr)
		}
		if len(report.Results) != 0 || calls != 0 {
			t.Fatalf("report = %#v, transport calls = %d", report, calls)
		}
	})
}

func TestEmptyChangeSet(t *testing.T) {
	calls := 0
	client, err := NewClient(Config{
		BaseURL: "https://ops.example.test", AccessToken: "token",
		HTTPClient: &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			calls++
			return nil, errors.New("unexpected request")
		})},
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	report, err := client.Apply(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	if report.Results == nil || len(report.Results) != 0 {
		t.Fatalf("empty report results = %#v, want non-nil empty slice", report.Results)
	}
	if calls != 0 {
		t.Fatalf("empty change set made %d requests", calls)
	}
}

func TestNilHTTPClientUsesDefaultClient(t *testing.T) {
	oldDefault := http.DefaultClient
	t.Cleanup(func() { http.DefaultClient = oldDefault })

	calls := 0
	http.DefaultClient = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		body := `{"id":"default-client","key":"default-client","schedule":{"hour":1,"minuteOfTheHour":0,"duration":30,"scheduleType":"ONCE"}}`
		return &http.Response{
			StatusCode: http.StatusCreated,
			Header:     http.Header{"Content-Type": []string{"application/json"}},
			Body:       io.NopCloser(strings.NewReader(body)),
			Request:    request,
		}, nil
	})}
	client, err := NewClient(Config{BaseURL: "https://ops.example.test", AccessToken: "token"})
	if err != nil {
		t.Fatal(err)
	}
	report, err := client.Apply(context.Background(), []MaintenanceScheduleSpec{{Key: "default-client", Schedule: validSchedule()}})
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 || len(report.Results) != 1 || report.Results[0].Status != StepSucceeded {
		t.Fatalf("default client calls = %d, report = %#v", calls, report)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func newResponseClient(t *testing.T, status int, contentType, body string) *http.Client {
	t.Helper()
	return &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.Method != http.MethodPost || request.URL.Scheme != "https" || request.URL.Host != "ops.example.test" ||
			request.URL.Path != "/suite-api/api/maintenanceschedules" || request.URL.RawQuery != "" {
			t.Errorf("request target = %s %s", request.Method, request.URL.String())
		}
		header := make(http.Header)
		if contentType != "" {
			header.Set("Content-Type", contentType)
		}
		return &http.Response{
			StatusCode: status,
			Header:     header,
			Body:       io.NopCloser(strings.NewReader(body)),
			Request:    request,
		}, nil
	})}
}

func validSchedule() Schedule {
	return Schedule{Hour: 1, MinuteOfTheHour: 0, Duration: 30, ScheduleType: "ONCE"}
}
