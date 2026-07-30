package attestdiag_test

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"vcf91-0116/attestdiag"
	"vcf91-0116/internal/contractmock"
)

const contractPath = "../docs/contract.json"

func TestCollectDiagnosisWireAndClassification(t *testing.T) {
	tests := []struct {
		name        string
		active      bool
		truncated   bool
		wantCode    string
		wantSummary string
	}{
		{
			name:        "inactive takes precedence over truncation",
			active:      false,
			truncated:   true,
			wantCode:    attestdiag.DiagnosisTPMInactive,
			wantSummary: attestdiag.SummaryTPMInactive,
		},
		{
			name:        "active with truncated event log",
			active:      true,
			truncated:   true,
			wantCode:    attestdiag.DiagnosisEventLogTruncated,
			wantSummary: attestdiag.SummaryEventLogTruncated,
		},
		{
			name:        "evidence does not establish a cause",
			active:      true,
			truncated:   false,
			wantCode:    attestdiag.DiagnosisUnresolvedReview,
			wantSummary: attestdiag.SummaryUnresolvedReview,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			scenario.Active = test.active
			scenario.EventTruncated = test.truncated
			server := contractmock.Start(t, contractPath, scenario)
			client := newClient(t, server.URL, scenario.SessionID)
			description := "attestation evidence " + runtimeValue(t, "description")

			report, err := attestdiag.CollectDiagnosis(
				context.Background(),
				client,
				scenario.Host,
				description,
			)
			if err != nil {
				t.Fatalf("CollectDiagnosis returned an error: %v", err)
			}
			if report.Host != scenario.Host ||
				report.TPM.TPM != scenario.TPM ||
				report.TPM.Active != test.active ||
				report.EventLog.Truncated != test.truncated ||
				report.SupportBundleTask != scenario.TaskID {
				t.Fatalf("report did not preserve returned evidence: %#v", report)
			}
			if report.Diagnosis.Code != test.wantCode ||
				report.Diagnosis.Summary != test.wantSummary {
				t.Fatalf("diagnosis = %#v, want %q / %q",
					report.Diagnosis, test.wantCode, test.wantSummary)
			}

			records := contractmock.ReadLog(t, server.LogPath)
			if len(records) != 3 {
				t.Fatalf("request count = %d, want 3: %v", len(records), records)
			}
			escapedHost := url.PathEscape(scenario.Host)
			escapedTPM := url.PathEscape(scenario.TPM)
			wantTargets := []string{
				"/api/vcenter/trusted-infrastructure/hosts/" + escapedHost + "/hardware/tpm",
				"/api/vcenter/trusted-infrastructure/hosts/" + escapedHost +
					"/hardware/tpm/" + escapedTPM + "/event-log",
				"/api/appliance/support-bundle?vmw-task=true",
			}
			wantMethods := []string{http.MethodGet, http.MethodGet, http.MethodPost}
			for index := range records {
				if records[index].Method != wantMethods[index] ||
					records[index].RequestURI != wantTargets[index] {
					t.Fatalf("request %d = %s, want %s %s",
						index, records[index], wantMethods[index], wantTargets[index])
				}
				assertCommonHeaders(t, records[index], scenario.SessionID)
			}
			for index := 0; index < 2; index++ {
				if records[index].Body != "" ||
					records[index].ContentLength != 0 ||
					header(records[index], "Content-Type") != "" {
					t.Fatalf("GET request %d unexpectedly carried content: %#v",
						index, records[index])
				}
			}

			wantBody, err := json.Marshal(struct {
				Description string `json:"description"`
				ContentType string `json:"content_type"`
			}{
				Description: description,
				ContentType: "LOGS",
			})
			if err != nil {
				t.Fatal(err)
			}
			bundle := records[2]
			if bundle.Body != string(wantBody) {
				t.Fatalf("bundle body = %q, want %q", bundle.Body, wantBody)
			}
			if bundle.ContentLength != int64(len(wantBody)) {
				t.Fatalf("bundle content length = %d, want %d",
					bundle.ContentLength, len(wantBody))
			}
			if header(bundle, "Content-Type") != "application/json" {
				t.Fatalf("bundle Content-Type = %q", header(bundle, "Content-Type"))
			}
			var members map[string]json.RawMessage
			if err := json.Unmarshal([]byte(bundle.Body), &members); err != nil {
				t.Fatal(err)
			}
			if len(members) != 2 ||
				members["components"] != nil ||
				members["partition"] != nil {
				t.Fatalf("unset bundle members were not omitted: %s", bundle.Body)
			}
		})
	}
}

func TestListTPMsExactOptionalQuery(t *testing.T) {
	activeFalse := false
	activeTrue := true
	tests := []struct {
		name    string
		options attestdiag.TPMListOptions
		query   string
	}{
		{name: "all unset"},
		{
			name:    "explicit false",
			options: attestdiag.TPMListOptions{Active: &activeFalse},
			query:   "active=false",
		},
		{
			name:    "exploded major versions preserve order",
			options: attestdiag.TPMListOptions{MajorVersions: []int64{7, 2}},
			query:   "major_versions=7&major_versions=2",
		},
		{
			name: "fields use declaration order",
			options: attestdiag.TPMListOptions{
				Active:        &activeTrue,
				MajorVersions: []int64{2, 7},
			},
			query: "active=true&major_versions=2&major_versions=7",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			scenario.ExpectedListQuery = test.query
			server := contractmock.Start(t, contractPath, scenario)
			client := newClient(t, server.URL, scenario.SessionID)

			items, err := client.ListTPMs(
				context.Background(),
				scenario.Host,
				test.options,
			)
			if err != nil {
				t.Fatalf("ListTPMs returned an error: %v", err)
			}
			if len(items) != 1 || items[0].TPM != scenario.TPM {
				t.Fatalf("ListTPMs returned %#v", items)
			}
			records := contractmock.ReadLog(t, server.LogPath)
			if len(records) != 1 {
				t.Fatalf("request count = %d, want 1", len(records))
			}
			want := "/api/vcenter/trusted-infrastructure/hosts/" +
				url.PathEscape(scenario.Host) + "/hardware/tpm"
			if test.query != "" {
				want += "?" + test.query
			}
			if records[0].RequestURI != want {
				t.Fatalf("request target = %q, want %q", records[0].RequestURI, want)
			}
			if records[0].Body != "" ||
				header(records[0], "Content-Type") != "" {
				t.Fatalf("list GET carried content: %#v", records[0])
			}
		})
	}
}

func TestListTPMsRejectsInvalidOptionsBeforeTraffic(t *testing.T) {
	tests := []struct {
		name    string
		options attestdiag.TPMListOptions
	}{
		{
			name:    "explicitly empty major versions",
			options: attestdiag.TPMListOptions{MajorVersions: []int64{}},
		},
		{
			name:    "nonpositive major version",
			options: attestdiag.TPMListOptions{MajorVersions: []int64{0}},
		},
		{
			name:    "duplicate major version",
			options: attestdiag.TPMListOptions{MajorVersions: []int64{2, 2}},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			server := contractmock.Start(t, contractPath, scenario)
			client := newClient(t, server.URL, scenario.SessionID)
			_, err := client.ListTPMs(
				context.Background(),
				scenario.Host,
				test.options,
			)
			var validation *attestdiag.ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("error = %T %v, want ValidationError", err, err)
			}
			if records := contractmock.ReadLog(t, server.LogPath); len(records) != 0 {
				t.Fatalf("invalid input produced traffic: %v", records)
			}
		})
	}
}

func TestCollectDiagnosisRejectsAmbiguousInventoryBeforeLaterOperations(t *testing.T) {
	tests := []struct {
		name     string
		tpmCount int
	}{
		{name: "no TPM", tpmCount: 0},
		{name: "multiple TPMs", tpmCount: 2},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			scenario.TPMCount = test.tpmCount
			server := contractmock.Start(t, contractPath, scenario)
			client := newClient(t, server.URL, scenario.SessionID)
			_, err := attestdiag.CollectDiagnosis(
				context.Background(),
				client,
				scenario.Host,
				runtimeValue(t, "description"),
			)
			var protocol *attestdiag.ProtocolError
			if !errors.As(err, &protocol) ||
				protocol.OperationID != attestdiag.OperationListTPMs {
				t.Fatalf("error = %T %v, want list ProtocolError", err, err)
			}
			records := contractmock.ReadLog(t, server.LogPath)
			if len(records) != 1 ||
				records[0].Method != http.MethodGet {
				t.Fatalf("ambiguous inventory was not terminal: %v", records)
			}
		})
	}
}

func TestEventLogEnumAndByteValidation(t *testing.T) {
	validBytes := base64.StdEncoding.EncodeToString([]byte(runtimeValue(t, "event")))
	tests := []struct {
		name      string
		eventType string
		algorithm string
		data      string
		pcr       string
	}{
		{
			name:      "unknown event type",
			eventType: "UNKNOWN",
			algorithm: "SHA256",
			data:      validBytes,
			pcr:       validBytes,
		},
		{
			name:      "unknown bank algorithm",
			eventType: "EFI_TCG2_EVENT_LOG_FORMAT_TCG_2",
			algorithm: "SHA1",
			data:      validBytes,
			pcr:       validBytes,
		},
		{
			name:      "event data is not base64",
			eventType: "EFI_TCG2_EVENT_LOG_FORMAT_TCG_2",
			algorithm: "SHA256",
			data:      "%%%",
			pcr:       validBytes,
		},
		{
			name:      "PCR value is not base64",
			eventType: "EFI_TCG2_EVENT_LOG_FORMAT_TCG_2",
			algorithm: "SHA256",
			data:      validBytes,
			pcr:       "%%%",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			scenario.EventType = test.eventType
			scenario.BankAlgorithm = test.algorithm
			scenario.EventData = &test.data
			scenario.PCRs = map[string]string{"0": test.pcr}
			server := contractmock.Start(t, contractPath, scenario)
			client := newClient(t, server.URL, scenario.SessionID)
			_, err := client.GetTPMEventLog(
				context.Background(),
				scenario.Host,
				scenario.TPM,
			)
			var protocol *attestdiag.ProtocolError
			if !errors.As(err, &protocol) ||
				protocol.OperationID != attestdiag.OperationGetTPMEventLog {
				t.Fatalf("error = %T %v, want event-log ProtocolError", err, err)
			}
		})
	}
}

func TestBundleOptionsValidationBeforeTraffic(t *testing.T) {
	blank := " "
	tests := []struct {
		name    string
		options attestdiag.BundleOptions
	}{
		{
			name:    "empty components map",
			options: attestdiag.BundleOptions{Components: map[string][]string{}},
		},
		{
			name: "empty component list",
			options: attestdiag.BundleOptions{
				Components: map[string][]string{"vcenter": {}},
			},
		},
		{
			name: "blank component",
			options: attestdiag.BundleOptions{
				Components: map[string][]string{"vcenter": {" "}},
			},
		},
		{
			name:    "blank partition",
			options: attestdiag.BundleOptions{Partition: &blank},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			scenario := newScenario(t)
			server := contractmock.Start(t, contractPath, scenario)
			client := newClient(t, server.URL, scenario.SessionID)
			_, err := client.CreateLogBundle(
				context.Background(),
				runtimeValue(t, "description"),
				test.options,
			)
			var validation *attestdiag.ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("error = %T %v, want ValidationError", err, err)
			}
			if records := contractmock.ReadLog(t, server.LogPath); len(records) != 0 {
				t.Fatalf("invalid bundle input produced traffic: %v", records)
			}
		})
	}
}

func TestConfigurationValidationIsLocal(t *testing.T) {
	tests := []struct {
		name string
		cfg  attestdiag.Config
	}{
		{
			name: "base URL is not API root",
			cfg: attestdiag.Config{
				BaseURL:   "http://127.0.0.1:1/other",
				SessionID: "session",
				Timeout:   time.Second,
			},
		},
		{
			name: "base URL has query",
			cfg: attestdiag.Config{
				BaseURL:   "http://127.0.0.1:1/api?mode=test",
				SessionID: "session",
				Timeout:   time.Second,
			},
		},
		{
			name: "session is header unsafe",
			cfg: attestdiag.Config{
				BaseURL:   "http://127.0.0.1:1/api",
				SessionID: "session\nvalue",
				Timeout:   time.Second,
			},
		},
		{
			name: "timeout is nonpositive",
			cfg: attestdiag.Config{
				BaseURL:   "http://127.0.0.1:1/api",
				SessionID: "session",
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := attestdiag.NewClient(test.cfg)
			var validation *attestdiag.ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("error = %T %v, want ValidationError", err, err)
			}
		})
	}
}

func TestTransportErrorRedactsUnderlyingText(t *testing.T) {
	session := runtimeValue(t, "session")
	secretText := session + "-" + runtimeValue(t, "transport")
	httpClient := &http.Client{
		Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return nil, fmt.Errorf("dial failed with %s", secretText)
		}),
	}
	client, err := attestdiag.NewClient(attestdiag.Config{
		BaseURL:    "http://127.0.0.1:1/api",
		SessionID:  session,
		Timeout:    time.Second,
		HTTPClient: httpClient,
	})
	if err != nil {
		t.Fatalf("NewClient returned an error: %v", err)
	}
	_, err = client.ListTPMs(
		context.Background(),
		runtimeValue(t, "host"),
		attestdiag.TPMListOptions{},
	)
	var transport *attestdiag.TransportError
	if !errors.As(err, &transport) {
		t.Fatalf("error = %T %v, want TransportError", err, err)
	}
	if strings.Contains(err.Error(), session) ||
		strings.Contains(err.Error(), secretText) {
		t.Fatalf("transport error leaked sensitive text: %q", err)
	}
}

func assertCommonHeaders(
	t *testing.T,
	record contractmock.RequestRecord,
	sessionID string,
) {
	t.Helper()
	if header(record, "Accept") != "application/json" {
		t.Fatalf("%s Accept = %q", record, header(record, "Accept"))
	}
	if header(record, "vmware-api-session-id") != sessionID {
		t.Fatalf("%s session header mismatch", record)
	}
	if header(record, "Authorization") != "" {
		t.Fatalf("%s sent Authorization", record)
	}
}

func header(record contractmock.RequestRecord, name string) string {
	return http.Header(record.Headers).Get(name)
}

func newClient(t *testing.T, baseURL string, sessionID string) *attestdiag.Client {
	t.Helper()
	client, err := attestdiag.NewClient(attestdiag.Config{
		BaseURL:   baseURL,
		SessionID: sessionID,
		Timeout:   2 * time.Second,
	})
	if err != nil {
		t.Fatalf("NewClient returned an error: %v", err)
	}
	return client
}

func newScenario(t *testing.T) contractmock.Scenario {
	t.Helper()
	eventData := base64.StdEncoding.EncodeToString(
		[]byte(runtimeValue(t, "event-data")),
	)
	pcrValue := base64.StdEncoding.EncodeToString(
		[]byte(runtimeValue(t, "pcr")),
	)
	return contractmock.Scenario{
		SessionID:      runtimeValue(t, "session"),
		Host:           "esx /Ω-" + runtimeValue(t, "host"),
		TPM:            "tpm /β-" + runtimeValue(t, "tpm"),
		SecondaryTPM:   runtimeValue(t, "tpm-secondary"),
		TaskID:         runtimeValue(t, "task"),
		Active:         true,
		MajorVersion:   2,
		MinorVersion:   0,
		TPMCount:       1,
		EventType:      "EFI_TCG2_EVENT_LOG_FORMAT_TCG_2",
		EventData:      &eventData,
		EventTruncated: false,
		BankAlgorithm:  "SHA256",
		PCRs:           map[string]string{"0": pcrValue},
	}
}

var fallbackCounter atomic.Uint64

func runtimeValue(t testing.TB, prefix string) string {
	t.Helper()
	var bytes [8]byte
	if _, err := rand.Read(bytes[:]); err == nil {
		return prefix + "-" + hex.EncodeToString(bytes[:])
	}
	return fmt.Sprintf("%s-%d", prefix, fallbackCounter.Add(1))
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return function(request)
}
