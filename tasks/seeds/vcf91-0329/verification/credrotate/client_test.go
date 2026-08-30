package credrotate_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"moonshiner.local/vcf91-0329/credrotate"
	"moonshiner.local/vcf91-0329/internal/contractmock"
)

const (
	contractPath   = "../docs/contract.json"
	sourcesPath    = "../docs/official_sources.json"
	apiVersion     = "2021-07-15"
	refreshToken   = "rt-provider-admin"
	cloudAccountID = "vsphere-cloud-account-7a1c"
	trackerID      = "req-9f21e0"
)

func pointer[T any](value T) *T { return &value }

func startMock(t *testing.T, opts contractmock.Options) *contractmock.Server {
	t.Helper()
	opts.ContractPath = contractPath
	if opts.RefreshToken == "" {
		opts.RefreshToken = refreshToken
	}
	if opts.APIVersion == "" {
		opts.APIVersion = apiVersion
	}
	if opts.CloudAccountID == "" {
		opts.CloudAccountID = cloudAccountID
	}
	if opts.TrackerID == "" {
		opts.TrackerID = trackerID
	}
	server, err := contractmock.Start(opts)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server) *credrotate.Client {
	t.Helper()
	client, err := credrotate.NewClient(credrotate.Config{
		BaseURL:         server.URL(),
		RefreshToken:    refreshToken,
		APIVersion:      apiVersion,
		HTTPClient:      server.Client(),
		PollInterval:    time.Millisecond,
		MaxPollAttempts: 20,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func readLog(t *testing.T, server *contractmock.Server) []contractmock.RequestRecord {
	t.Helper()
	records, err := server.ReadLog()
	if err != nil {
		t.Fatalf("read request log: %v", err)
	}
	return records
}

func recordsFor(t *testing.T, server *contractmock.Server, operationID string) []contractmock.RequestRecord {
	t.Helper()
	records, err := server.RecordsFor(operationID)
	if err != nil {
		t.Fatalf("read request log: %v", err)
	}
	return records
}

func operationSequence(records []contractmock.RequestRecord) []string {
	out := make([]string, 0, len(records))
	for _, record := range records {
		out = append(out, record.OperationID)
	}
	return out
}

// canonicalJSON reparses a JSON document and re-serializes it with sorted
// object members, so comparisons constrain the member set and the values
// without constraining serialization order or whitespace.
func canonicalJSON(t *testing.T, label, raw string) string {
	t.Helper()
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("%s is not valid JSON: %v (%q)", label, err, raw)
	}
	if decoder.More() {
		t.Fatalf("%s carries trailing data: %q", label, raw)
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("%s cannot be re-encoded: %v", label, err)
	}
	return string(encoded)
}

func topLevelKeys(t *testing.T, label, raw string) []string {
	t.Helper()
	var object map[string]json.RawMessage
	if err := json.Unmarshal([]byte(raw), &object); err != nil {
		t.Fatalf("%s is not a JSON object: %v (%q)", label, err, raw)
	}
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func assertQueryIsAPIVersionOnly(t *testing.T, record contractmock.RequestRecord) {
	t.Helper()
	want := "apiVersion=" + apiVersion
	if record.RawQuery != want {
		t.Errorf("%s query = %q, want exactly %q", record.OperationID, record.RawQuery, want)
	}
}

func assertHeader(t *testing.T, record contractmock.RequestRecord, name, want string) {
	t.Helper()
	values := record.Headers.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s %s header = %v, want exactly [%q]", record.OperationID, name, values, want)
	}
}

// TestRotateCredentialsWireShape pins the exact wire shape of the mutation,
// most importantly that an unset optional member is absent from the body
// rather than serialized as an empty or zero value, and that a member set to
// an explicit zero value is present and carries it.
func TestRotateCredentialsWireShape(t *testing.T) {
	const certificate = "-----BEGIN CERTIFICATE-----\nMIIB\"vc\"<test>\n-----END CERTIFICATE-----\n"

	base := func() credrotate.UpdateCloudAccountInput {
		return credrotate.UpdateCloudAccountInput{
			Name:                   "vc-prod-01",
			CloudAccountProperties: map[string]string{"hostName": "vc.loopback.test", "dcId": "onprem"},
			Regions: []credrotate.Region{
				{Name: "Datacenter-A", ExternalRegionID: "Datacenter:datacenter-2"},
			},
			PrivateKeyID: pointer("svc-rotation@vsphere.local"),
			PrivateKey:   pointer("new-secret-value"),
		}
	}

	tests := []struct {
		name     string
		mutate   func(*credrotate.UpdateCloudAccountInput)
		wantKeys []string
		wantBody string
	}{
		{
			name:   "unset optional members are omitted",
			mutate: func(*credrotate.UpdateCloudAccountInput) {},
			wantKeys: []string{
				"cloudAccountProperties", "name", "privateKey", "privateKeyId", "regions",
			},
			wantBody: `{
				"name": "vc-prod-01",
				"cloudAccountProperties": {"hostName": "vc.loopback.test", "dcId": "onprem"},
				"regions": [{"name": "Datacenter-A", "externalRegionId": "Datacenter:datacenter-2"}],
				"privateKeyId": "svc-rotation@vsphere.local",
				"privateKey": "new-secret-value"
			}`,
		},
		{
			name: "explicit zero values are present",
			mutate: func(input *credrotate.UpdateCloudAccountInput) {
				input.Description = pointer("")
				input.CreateDefaultZones = pointer(false)
				input.CustomProperties = pointer(map[string]string{})
				input.Tags = pointer([]credrotate.Tag{})
				input.AssociatedCloudAccountIDs = pointer([]string{})
				input.AssociatedMobilityCloudAccountIDs = pointer(map[string]string{})
			},
			wantKeys: []string{
				"associatedCloudAccountIds", "associatedMobilityCloudAccountIds",
				"cloudAccountProperties", "createDefaultZones", "customProperties",
				"description", "name", "privateKey", "privateKeyId", "regions", "tags",
			},
			wantBody: `{
				"name": "vc-prod-01",
				"description": "",
				"cloudAccountProperties": {"hostName": "vc.loopback.test", "dcId": "onprem"},
				"regions": [{"name": "Datacenter-A", "externalRegionId": "Datacenter:datacenter-2"}],
				"privateKeyId": "svc-rotation@vsphere.local",
				"privateKey": "new-secret-value",
				"associatedCloudAccountIds": [],
				"associatedMobilityCloudAccountIds": {},
				"customProperties": {},
				"createDefaultZones": false,
				"tags": []
			}`,
		},
		{
			name: "nested optional members follow the same rule",
			mutate: func(input *credrotate.UpdateCloudAccountInput) {
				input.CreateDefaultZones = pointer(true)
				input.Tags = pointer([]credrotate.Tag{
					{Key: "owner", Value: "platform"},
					{Key: "rotated", Value: "2026-08-11", ID: pointer("tag-7")},
				})
				input.CertificateInfo = &credrotate.CertificateInfo{Certificate: certificate}
				input.AssociatedCloudAccountIDs = pointer([]string{"nsx-account-1"})
			},
			wantKeys: []string{
				"associatedCloudAccountIds", "certificateInfo", "cloudAccountProperties",
				"createDefaultZones", "name", "privateKey", "privateKeyId", "regions", "tags",
			},
			wantBody: `{
				"name": "vc-prod-01",
				"cloudAccountProperties": {"hostName": "vc.loopback.test", "dcId": "onprem"},
				"regions": [{"name": "Datacenter-A", "externalRegionId": "Datacenter:datacenter-2"}],
				"privateKeyId": "svc-rotation@vsphere.local",
				"privateKey": "new-secret-value",
				"associatedCloudAccountIds": ["nsx-account-1"],
				"createDefaultZones": true,
				"tags": [
					{"key": "owner", "value": "platform"},
					{"key": "rotated", "value": "2026-08-11", "id": "tag-7"}
				],
				"certificateInfo": {"certificate": "-----BEGIN CERTIFICATE-----\nMIIB\"vc\"<test>\n-----END CERTIFICATE-----\n"}
			}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := startMock(t, contractmock.Options{CloudAccountName: "vc-prod-01"})
			client := newClient(t, server)

			input := base()
			test.mutate(&input)

			result, err := client.RotateCredentials(context.Background(), cloudAccountID, input)
			if err != nil {
				t.Fatalf("RotateCredentials: %v", err)
			}
			if result.Account.ID != cloudAccountID {
				t.Errorf("account id = %q, want %q", result.Account.ID, cloudAccountID)
			}
			if result.Tracker.Status != credrotate.StatusFinished {
				t.Errorf("tracker status = %q, want %q", result.Tracker.Status, credrotate.StatusFinished)
			}
			if result.Reauthentications != 0 {
				t.Errorf("reauthentications = %d, want 0", result.Reauthentications)
			}
			if effects := server.Effects(); effects != 1 {
				t.Errorf("accepted mutations = %d, want 1", effects)
			}

			records := readLog(t, server)
			wantSequence := []string{
				credrotate.OperationRetrieveAuthToken,
				credrotate.OperationUpdateCloudAccountAsync,
				credrotate.OperationGetRequestTracker,
				credrotate.OperationGetCloudAccount,
			}
			if got := operationSequence(records); !reflect.DeepEqual(got, wantSequence) {
				t.Fatalf("operation sequence = %v, want %v", got, wantSequence)
			}
			for _, record := range records {
				if record.Status < 200 || record.Status >= 300 {
					t.Fatalf("%s answered %d: %s", record.OperationID, record.Status, record.Body)
				}
				assertHeader(t, record, "Accept", "application/json")
				assertQueryIsAPIVersionOnly(t, record)
			}

			login := records[0]
			if auth := login.Headers.Values("Authorization"); len(auth) != 0 {
				t.Errorf("retrieveAuthToken carried Authorization %v, want none", auth)
			}
			wantLogin := canonicalJSON(t, "want login body", `{"refreshToken":"`+refreshToken+`"}`)
			if got := canonicalJSON(t, "login body", login.Body); got != wantLogin {
				t.Errorf("login body = %s, want %s", got, wantLogin)
			}

			patch := records[1]
			if want := "/iaas/api/cloud-accounts/" + cloudAccountID; patch.Path != want {
				t.Errorf("mutation path = %q, want %q", patch.Path, want)
			}
			assertHeader(t, patch, "Content-Type", "application/json")
			assertHeader(t, patch, "Authorization", "Bearer tok-1")
			if got, want := topLevelKeys(t, "mutation body", patch.Body), test.wantKeys; !reflect.DeepEqual(got, want) {
				t.Errorf("mutation body members = %v, want %v", got, want)
			}
			if got, want := canonicalJSON(t, "mutation body", patch.Body), canonicalJSON(t, "want body", test.wantBody); got != want {
				t.Errorf("mutation body = %s\nwant %s", got, want)
			}
			if stored := server.LastPatchBody(); stored != patch.Body {
				t.Errorf("stored mutation body = %q, want %q", stored, patch.Body)
			}

			tracker := records[2]
			if want := "/iaas/api/request-tracker/" + trackerID; tracker.Path != want {
				t.Errorf("tracker path = %q, want %q", tracker.Path, want)
			}
			assertHeader(t, tracker, "Authorization", "Bearer tok-1")

			readBack := records[3]
			if want := "/iaas/api/cloud-accounts/" + cloudAccountID; readBack.Path != want {
				t.Errorf("read-back path = %q, want %q", readBack.Path, want)
			}
			assertHeader(t, readBack, "Authorization", "Bearer tok-1")
			if readBack.Body != "" {
				t.Errorf("read-back carried a body %q, want none", readBack.Body)
			}
		})
	}
}

func TestRotateCredentialsPollsUntilTerminal(t *testing.T) {
	server := startMock(t, contractmock.Options{
		TrackerScript: []contractmock.TrackerState{
			{Status: credrotate.StatusInProgress, Progress: 10, Message: "Validating credentials"},
			{Status: credrotate.StatusInProgress, Progress: 55, Message: "Applying credentials"},
			{Status: credrotate.StatusFinished, Progress: 100, Message: "Completed"},
		},
	})
	client := newClient(t, server)

	result, err := client.RotateCredentials(context.Background(), cloudAccountID, validInput())
	if err != nil {
		t.Fatalf("RotateCredentials: %v", err)
	}
	if result.Tracker.Status != credrotate.StatusFinished || result.Tracker.Progress != 100 {
		t.Errorf("tracker = %+v, want FINISHED at 100", result.Tracker)
	}
	if result.Tracker.ID != trackerID {
		t.Errorf("tracker id = %q, want %q", result.Tracker.ID, trackerID)
	}
	if polls := recordsFor(t, server, credrotate.OperationGetRequestTracker); len(polls) != 3 {
		t.Errorf("tracker polls = %d, want 3", len(polls))
	}
	if effects := server.Effects(); effects != 1 {
		t.Errorf("accepted mutations = %d, want 1", effects)
	}

	sequence := operationSequence(readLog(t, server))
	want := []string{
		credrotate.OperationRetrieveAuthToken,
		credrotate.OperationUpdateCloudAccountAsync,
		credrotate.OperationGetRequestTracker,
		credrotate.OperationGetRequestTracker,
		credrotate.OperationGetRequestTracker,
		credrotate.OperationGetCloudAccount,
	}
	if !reflect.DeepEqual(sequence, want) {
		t.Errorf("operation sequence = %v, want %v", sequence, want)
	}
}

func TestRotateCredentialsTrackerFailed(t *testing.T) {
	const message = "Credential validation failed against vc.loopback.test"
	server := startMock(t, contractmock.Options{
		TrackerScript: []contractmock.TrackerState{
			{Status: credrotate.StatusInProgress, Progress: 20, Message: "Validating credentials"},
			{Status: credrotate.StatusFailed, Progress: 100, Message: message},
		},
	})
	client := newClient(t, server)

	_, err := client.RotateCredentials(context.Background(), cloudAccountID, validInput())
	var trackerErr *credrotate.TrackerError
	if !errors.As(err, &trackerErr) {
		t.Fatalf("error = %v, want a *credrotate.TrackerError", err)
	}
	if trackerErr.Status != credrotate.StatusFailed {
		t.Errorf("tracker error status = %q, want %q", trackerErr.Status, credrotate.StatusFailed)
	}
	if trackerErr.Message != message {
		t.Errorf("tracker error message = %q, want %q", trackerErr.Message, message)
	}
	if trackerErr.RequestID != trackerID {
		t.Errorf("tracker error request id = %q, want %q", trackerErr.RequestID, trackerID)
	}
	if reads := recordsFor(t, server, credrotate.OperationGetCloudAccount); len(reads) != 0 {
		t.Errorf("read-back requests = %d, want 0 after a failed rotation", len(reads))
	}
}

func TestRotateCredentialsPollBudgetExhausted(t *testing.T) {
	server := startMock(t, contractmock.Options{
		TrackerScript: []contractmock.TrackerState{
			{Status: credrotate.StatusInProgress, Progress: 5, Message: "Validating credentials"},
		},
	})
	client, err := credrotate.NewClient(credrotate.Config{
		BaseURL:         server.URL(),
		RefreshToken:    refreshToken,
		APIVersion:      apiVersion,
		HTTPClient:      server.Client(),
		PollInterval:    time.Millisecond,
		MaxPollAttempts: 3,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	_, err = client.RotateCredentials(context.Background(), cloudAccountID, validInput())
	var trackerErr *credrotate.TrackerError
	if !errors.As(err, &trackerErr) {
		t.Fatalf("error = %v, want a *credrotate.TrackerError", err)
	}
	if trackerErr.Status != credrotate.StatusInProgress {
		t.Errorf("tracker error status = %q, want %q", trackerErr.Status, credrotate.StatusInProgress)
	}
	if polls := recordsFor(t, server, credrotate.OperationGetRequestTracker); len(polls) != 3 {
		t.Errorf("tracker polls = %d, want exactly MaxPollAttempts (3)", len(polls))
	}
	if reads := recordsFor(t, server, credrotate.OperationGetCloudAccount); len(reads) != 0 {
		t.Errorf("read-back requests = %d, want 0", len(reads))
	}
}

func TestRejectsInvalidInputBeforeAnyRequest(t *testing.T) {
	tests := []struct {
		name   string
		id     string
		mutate func(*credrotate.UpdateCloudAccountInput)
	}{
		{name: "empty cloud account id", id: "", mutate: func(*credrotate.UpdateCloudAccountInput) {}},
		{name: "empty name", id: cloudAccountID, mutate: func(in *credrotate.UpdateCloudAccountInput) { in.Name = "" }},
		{name: "nil cloud account properties", id: cloudAccountID, mutate: func(in *credrotate.UpdateCloudAccountInput) { in.CloudAccountProperties = nil }},
		{name: "no regions", id: cloudAccountID, mutate: func(in *credrotate.UpdateCloudAccountInput) { in.Regions = nil }},
		{name: "region without a name", id: cloudAccountID, mutate: func(in *credrotate.UpdateCloudAccountInput) {
			in.Regions = []credrotate.Region{{ExternalRegionID: "Datacenter:datacenter-2"}}
		}},
		{name: "region without an external id", id: cloudAccountID, mutate: func(in *credrotate.UpdateCloudAccountInput) {
			in.Regions = []credrotate.Region{{Name: "Datacenter-A"}}
		}},
		{name: "tag without a key", id: cloudAccountID, mutate: func(in *credrotate.UpdateCloudAccountInput) {
			in.Tags = pointer([]credrotate.Tag{{Value: "platform"}})
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := startMock(t, contractmock.Options{})
			client := newClient(t, server)

			input := validInput()
			test.mutate(&input)

			_, err := client.RotateCredentials(context.Background(), test.id, input)
			if !errors.Is(err, credrotate.ErrInvalidInput) {
				t.Fatalf("error = %v, want one wrapping credrotate.ErrInvalidInput", err)
			}
			if records := readLog(t, server); len(records) != 0 {
				t.Fatalf("rejected input still issued %d request(s): %v", len(records), operationSequence(records))
			}
		})
	}
}

func TestServesOnlyContractOperations(t *testing.T) {
	server := startMock(t, contractmock.Options{})
	client := newClient(t, server)

	allowed := server.OperationIDs()
	want := []string{
		credrotate.OperationGetCloudAccount,
		credrotate.OperationGetRequestTracker,
		credrotate.OperationRetrieveAuthToken,
		credrotate.OperationUpdateCloudAccountAsync,
	}
	sort.Strings(want)
	if !reflect.DeepEqual(allowed, want) {
		t.Fatalf("contract allow-list = %v, want %v", allowed, want)
	}

	if _, err := client.RotateCredentials(context.Background(), cloudAccountID, validInput()); err != nil {
		t.Fatalf("RotateCredentials: %v", err)
	}

	permitted := map[string]bool{}
	for _, id := range allowed {
		permitted[id] = true
	}
	for index, record := range readLog(t, server) {
		if !permitted[record.OperationID] {
			t.Errorf("request %d (%s %s) matched no contract operation", index, record.Method, record.Path)
		}
	}

	for _, probe := range []struct{ method, path string }{
		{http.MethodGet, "/iaas/api/cloud-accounts"},
		{http.MethodGet, "/iaas/api/login"},
		{http.MethodDelete, "/iaas/api/cloud-accounts/" + cloudAccountID},
		{http.MethodPost, "/iaas/api/cloud-accounts-vsphere"},
	} {
		request, err := http.NewRequest(probe.method, server.URL()+probe.path, nil)
		if err != nil {
			t.Fatalf("build probe: %v", err)
		}
		request.Header.Set("Accept", "application/json")
		response, err := server.Client().Do(request)
		if err != nil {
			t.Fatalf("probe %s %s: %v", probe.method, probe.path, err)
		}
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Errorf("probe %s %s = %d, want 404 from a contract-pinned mock",
				probe.method, probe.path, response.StatusCode)
		}
	}
}

// TestInFlightRequestsSurviveCredentialRotation is the core of the scenario:
// the bearer token is revoked while eight callers are in flight. Every caller
// must still succeed on the new token, and the whole fleet must collapse into
// a single re-authentication rather than stampeding retrieveAuthToken.
func TestInFlightRequestsSurviveCredentialRotation(t *testing.T) {
	const callers = 8
	server := startMock(t, contractmock.Options{
		CloudAccountName:      "vc-prod-01",
		RevokeAfterAuthorized: 3,
	})
	client := newClient(t, server)

	accounts := make([]credrotate.CloudAccount, callers)
	errs := make([]error, callers)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			accounts[i], errs[i] = client.GetCloudAccount(context.Background(), cloudAccountID)
		}(i)
	}
	close(start)
	wg.Wait()

	for i := 0; i < callers; i++ {
		if errs[i] != nil {
			t.Errorf("caller %d was stranded on the old secret: %v", i, errs[i])
			continue
		}
		if accounts[i].ID != cloudAccountID {
			t.Errorf("caller %d read account %q, want %q", i, accounts[i].ID, cloudAccountID)
		}
	}

	logins := recordsFor(t, server, credrotate.OperationRetrieveAuthToken)
	if len(logins) != 2 {
		t.Errorf("retrieveAuthToken calls = %d, want exactly 2 (one lazy acquisition, one shared refresh)", len(logins))
	}
	if effects := server.Effects(); effects != 0 {
		t.Errorf("accepted mutations = %d, want 0", effects)
	}

	rejected := 0
	for _, record := range readLog(t, server) {
		if record.OperationID == credrotate.OperationRetrieveAuthToken {
			if auth := record.Headers.Values("Authorization"); len(auth) != 0 {
				t.Errorf("retrieveAuthToken carried Authorization %v, want none", auth)
			}
			continue
		}
		switch record.Authorization() {
		case "Bearer tok-1", "Bearer tok-2":
		default:
			t.Errorf("%s sent Authorization %q, want a whole bearer token",
				record.OperationID, record.Authorization())
		}
		if record.Status == http.StatusUnauthorized {
			rejected++
		}
	}
	if rejected == 0 {
		t.Error("no request was ever refused with 401, so the revocation the callers " +
			"had to survive never reached them")
	}
}

func TestRotationSurvivesRevocationDuringPolling(t *testing.T) {
	server := startMock(t, contractmock.Options{
		CloudAccountName: "vc-prod-01",
		// The mutation and the first poll consume the budget, so the second
		// poll is the first request to meet the revoked token.
		RevokeAfterAuthorized: 2,
		TrackerScript: []contractmock.TrackerState{
			{Status: credrotate.StatusInProgress, Progress: 10, Message: "Validating credentials"},
			{Status: credrotate.StatusInProgress, Progress: 60, Message: "Applying credentials"},
			{Status: credrotate.StatusFinished, Progress: 100, Message: "Completed"},
		},
	})
	client := newClient(t, server)

	result, err := client.RotateCredentials(context.Background(), cloudAccountID, validInput())
	if err != nil {
		t.Fatalf("rotation was stranded by its own token revocation: %v", err)
	}
	if result.Tracker.Status != credrotate.StatusFinished {
		t.Errorf("tracker status = %q, want %q", result.Tracker.Status, credrotate.StatusFinished)
	}
	if result.Account.ID != cloudAccountID {
		t.Errorf("account id = %q, want %q", result.Account.ID, cloudAccountID)
	}
	if result.Reauthentications != 1 {
		t.Errorf("reauthentications = %d, want 1", result.Reauthentications)
	}
	if effects := server.Effects(); effects != 1 {
		t.Errorf("accepted mutations = %d, want 1; a 401 retry must not duplicate the rotation", effects)
	}
	if logins := recordsFor(t, server, credrotate.OperationRetrieveAuthToken); len(logins) != 2 {
		t.Errorf("retrieveAuthToken calls = %d, want 2", len(logins))
	}

	rejected := 0
	for _, record := range readLog(t, server) {
		if record.Status == http.StatusUnauthorized {
			rejected++
		}
	}
	if rejected != 1 {
		t.Errorf("401 responses = %d, want exactly 1", rejected)
	}
}

// A 401 on the mutation is safe to replay because the contract guarantees
// that an unauthorized request was rejected before any update was applied.
// This pins the requirement for PATCH itself, rather than only for the GETs
// exercised by the polling and concurrent-reader tests.
func TestRotationReplaysRejectedMutationExactlyOnce(t *testing.T) {
	server := startMock(t, contractmock.Options{
		CloudAccountName:      "vc-prod-01",
		RevokeAfterAuthorized: 1,
	})
	client := newClient(t, server)

	// Acquire tok-1 and have the service revoke it after this successful read.
	if _, err := client.GetCloudAccount(context.Background(), cloudAccountID); err != nil {
		t.Fatalf("prime bearer token: %v", err)
	}

	result, err := client.RotateCredentials(context.Background(), cloudAccountID, validInput())
	if err != nil {
		t.Fatalf("rotation was stranded by a 401 on PATCH: %v", err)
	}
	if result.Reauthentications != 1 {
		t.Errorf("reauthentications = %d, want 1", result.Reauthentications)
	}
	if effects := server.Effects(); effects != 1 {
		t.Errorf("accepted mutations = %d, want exactly 1", effects)
	}
	if logins := recordsFor(t, server, credrotate.OperationRetrieveAuthToken); len(logins) != 2 {
		t.Errorf("retrieveAuthToken calls = %d, want 2", len(logins))
	}

	patches := recordsFor(t, server, credrotate.OperationUpdateCloudAccountAsync)
	if len(patches) != 2 {
		t.Fatalf("PATCH attempts = %d, want one rejected attempt and one replay", len(patches))
	}
	if patches[0].Status != http.StatusUnauthorized || patches[1].Status != http.StatusAccepted {
		t.Errorf("PATCH statuses = [%d %d], want [401 202]", patches[0].Status, patches[1].Status)
	}
	if patches[0].Authorization() != "Bearer tok-1" || patches[1].Authorization() != "Bearer tok-2" {
		t.Errorf("PATCH authorizations = [%q %q], want old then replacement token",
			patches[0].Authorization(), patches[1].Authorization())
	}
	if patches[0].Method != patches[1].Method ||
		patches[0].Path != patches[1].Path ||
		patches[0].RawQuery != patches[1].RawQuery ||
		patches[0].Body != patches[1].Body ||
		patches[0].ContentLength != patches[1].ContentLength {
		t.Errorf("replayed PATCH did not preserve the rejected request: first=%+v replay=%+v",
			patches[0], patches[1])
	}
}

// The read-back is part of the rotation just as much as the mutation and the
// tracker polls, so it must also recover when its bearer token is revoked.
func TestRotationSurvivesRevocationBeforeReadBack(t *testing.T) {
	server := startMock(t, contractmock.Options{
		CloudAccountName:      "vc-prod-01",
		RevokeAfterAuthorized: 2,
	})
	client := newClient(t, server)

	result, err := client.RotateCredentials(context.Background(), cloudAccountID, validInput())
	if err != nil {
		t.Fatalf("rotation was stranded by a 401 on read-back: %v", err)
	}
	if result.Account.ID != cloudAccountID {
		t.Errorf("account id = %q, want %q", result.Account.ID, cloudAccountID)
	}
	if result.Reauthentications != 1 {
		t.Errorf("reauthentications = %d, want 1", result.Reauthentications)
	}
	if effects := server.Effects(); effects != 1 {
		t.Errorf("accepted mutations = %d, want exactly 1", effects)
	}
	if logins := recordsFor(t, server, credrotate.OperationRetrieveAuthToken); len(logins) != 2 {
		t.Errorf("retrieveAuthToken calls = %d, want 2", len(logins))
	}

	reads := recordsFor(t, server, credrotate.OperationGetCloudAccount)
	if len(reads) != 2 {
		t.Fatalf("read-back attempts = %d, want one rejected attempt and one replay", len(reads))
	}
	if reads[0].Status != http.StatusUnauthorized || reads[1].Status != http.StatusOK {
		t.Errorf("read-back statuses = [%d %d], want [401 200]", reads[0].Status, reads[1].Status)
	}
	if reads[0].Authorization() != "Bearer tok-1" || reads[1].Authorization() != "Bearer tok-2" {
		t.Errorf("read-back authorizations = [%q %q], want old then replacement token",
			reads[0].Authorization(), reads[1].Authorization())
	}
}

// TestContractProvenance reads only local files. It asserts that the contract
// declares its reference-documentation origin and that every operation it
// names is traceable to a recorded developer.broadcom.com page.
func TestContractProvenance(t *testing.T) {
	var contract struct {
		OfficialSource struct {
			Kind          string `json:"kind"`
			Statement     string `json:"statement"`
			SourcesIndex  string `json:"sources_index"`
			ReferenceRoot string `json:"reference_root"`
			FetchedAt     string `json:"fetched_at"`
		} `json:"x-official-source"`
		Paths map[string]map[string]struct {
			OperationID string `json:"operationId"`
			SourceURL   string `json:"x-source-url"`
		} `json:"paths"`
	}
	readJSON(t, contractPath, &contract)

	if contract.OfficialSource.Kind != "reference-documentation" {
		t.Errorf("x-official-source.kind = %q, want %q",
			contract.OfficialSource.Kind, "reference-documentation")
	}
	statement := strings.ToLower(contract.OfficialSource.Statement)
	if !strings.Contains(statement, "not a published specification") {
		t.Errorf("x-official-source.statement must say plainly that the source is reference "+
			"documentation rather than a published specification, got %q", contract.OfficialSource.Statement)
	}
	if contract.OfficialSource.SourcesIndex != "docs/official_sources.json" {
		t.Errorf("x-official-source.sources_index = %q, want %q",
			contract.OfficialSource.SourcesIndex, "docs/official_sources.json")
	}

	var sources struct {
		SourceKind string `json:"source_kind"`
		Pages      []struct {
			URL         string `json:"url"`
			OperationID string `json:"operationId"`
			FetchedAt   string `json:"fetched_at"`
		} `json:"pages"`
	}
	readJSON(t, sourcesPath, &sources)

	if len(sources.Pages) == 0 {
		t.Fatal("official_sources.json records no pages")
	}
	date := regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
	documented := map[string]string{}
	for i, page := range sources.Pages {
		if !strings.HasPrefix(page.URL, "https://developer.broadcom.com/xapis/") {
			t.Errorf("page %d url = %q, want a developer.broadcom.com xAPIs page", i, page.URL)
		}
		if !date.MatchString(page.FetchedAt) {
			t.Errorf("page %d (%s) fetched_at = %q, want YYYY-MM-DD", i, page.URL, page.FetchedAt)
		}
		if page.OperationID != "" {
			documented[page.OperationID] = page.URL
		}
	}

	for path, item := range contract.Paths {
		for method, operation := range item {
			label := fmt.Sprintf("%s %s", strings.ToUpper(method), path)
			if operation.OperationID == "" {
				t.Errorf("%s declares no operationId", label)
				continue
			}
			url, ok := documented[operation.OperationID]
			if !ok {
				t.Errorf("%s (%s) has no page recorded in official_sources.json", label, operation.OperationID)
				continue
			}
			if operation.SourceURL != url {
				t.Errorf("%s x-source-url = %q, but official_sources.json records %q",
					label, operation.SourceURL, url)
			}
		}
	}
}

func readJSON(t *testing.T, path string, into any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}

func validInput() credrotate.UpdateCloudAccountInput {
	return credrotate.UpdateCloudAccountInput{
		Name:                   "vc-prod-01",
		CloudAccountProperties: map[string]string{"hostName": "vc.loopback.test"},
		Regions: []credrotate.Region{
			{Name: "Datacenter-A", ExternalRegionID: "Datacenter:datacenter-2"},
		},
		PrivateKeyID: pointer("svc-rotation@vsphere.local"),
		PrivateKey:   pointer("new-secret-value"),
	}
}
