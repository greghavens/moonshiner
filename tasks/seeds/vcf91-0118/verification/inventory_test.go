package vcenter_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"sync"
	"testing"

	vcenter "vcf91-0118"
	"vcf91-0118/internal/contractmock"
)

func TestNewClientRejectsInvalidConfig(t *testing.T) {
	valid := func() vcenter.Config {
		return vcenter.Config{
			BaseURL:          "https://vcenter.example.test",
			AccessToken:      "access",
			SubjectToken:     "subject",
			SubjectTokenType: "urn:ietf:params:oauth:token-type:jwt",
			HTTPClient:       http.DefaultClient,
		}
	}

	tests := []struct {
		name   string
		mutate func(*vcenter.Config)
	}{
		{name: "base URL", mutate: func(c *vcenter.Config) { c.BaseURL = "" }},
		{name: "access token", mutate: func(c *vcenter.Config) { c.AccessToken = "" }},
		{name: "subject token", mutate: func(c *vcenter.Config) { c.SubjectToken = "" }},
		{name: "subject token type", mutate: func(c *vcenter.Config) { c.SubjectTokenType = "" }},
		{name: "relative URL", mutate: func(c *vcenter.Config) { c.BaseURL = "/vcenter" }},
		{name: "unsupported URL scheme", mutate: func(c *vcenter.Config) { c.BaseURL = "ftp://vcenter.example.test" }},
		{name: "URL with api path", mutate: func(c *vcenter.Config) { c.BaseURL = "https://vcenter.example.test/api" }},
		{name: "URL with query", mutate: func(c *vcenter.Config) { c.BaseURL = "https://vcenter.example.test?x=1" }},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg := valid()
			tt.mutate(&cfg)
			if _, err := vcenter.NewClient(cfg); err == nil {
				t.Fatal("NewClient returned nil error")
			}
		})
	}
}

func TestInventoryRefreshesInPlaceAndSortsEveryResponse(t *testing.T) {
	server := contractmock.New()
	defer server.Close()
	client := newClient(t, server)

	want := expectedInventory()
	for run := 1; run <= 2; run++ {
		got, err := client.Inventory(context.Background())
		if err != nil {
			t.Fatalf("Inventory run %d: %v", run, err)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("Inventory run %d returned unstable/unsorted output\n got: %#v\nwant: %#v", run, got, want)
		}

		if run == 1 {
			assertFirstRunLog(t, server.Requests())
		}
	}

	log := server.Requests()
	counts := map[string]int{}
	for _, req := range log {
		if req.OperationID == "" {
			t.Fatalf("client called an operation outside docs/contract.json: %s %s", req.Method, req.Path)
		}
		counts[req.OperationID]++
	}
	wantCounts := map[string]int{
		contractmock.OperationVMList:     2,
		contractmock.OperationHostList:   3,
		contractmock.OperationTokenIssue: 1,
	}
	if !reflect.DeepEqual(counts, wantCounts) {
		t.Fatalf("operation counts = %v, want %v", counts, wantCounts)
	}
}

func TestTypedContractErrors(t *testing.T) {
	tests := []struct {
		name   string
		option contractmock.Option
		check  func(*testing.T, error)
	}{
		{
			name:   "OAuth token error",
			option: contractmock.WithTokenFailure(),
			check: func(t *testing.T, err error) {
				t.Helper()
				var got *vcenter.TokenError
				if !errors.As(err, &got) {
					t.Fatalf("error = %T %v, want *TokenError", err, err)
				}
				if got.StatusCode != http.StatusBadRequest ||
					got.Code != "invalid_grant" ||
					got.Description != "the subject credential expired" ||
					got.URI != "https://developer.broadcom.com/xapis/vsphere-automation-api/9.1/" {
					t.Fatalf("TokenError = %#v", got)
				}
			},
		},
		{
			name:   "second collection 401",
			option: contractmock.WithRejectedRotatedAccess(),
			check: func(t *testing.T, err error) {
				t.Helper()
				var got *vcenter.APIError
				if !errors.As(err, &got) {
					t.Fatalf("error = %T %v, want *APIError", err, err)
				}
				wantMessages := []vcenter.Message{{
					ID:             "com.vmware.vapi.endpoint.method.authentication.required",
					DefaultMessage: "Authentication required.",
					Args:           []string{},
				}}
				if got.StatusCode != http.StatusUnauthorized ||
					got.ErrorType != "UNAUTHENTICATED" ||
					!reflect.DeepEqual(got.Messages, wantMessages) {
					t.Fatalf("APIError = %#v, want complete message envelope %#v", got, wantMessages)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := contractmock.New(tt.option)
			defer server.Close()
			client := newClient(t, server)
			_, err := client.Inventory(context.Background())
			if err == nil {
				t.Fatal("Inventory returned nil error")
			}
			tt.check(t, err)
		})
	}
}

func TestConcurrentCallersShareRefreshUnderRace(t *testing.T) {
	server := contractmock.New()
	defer server.Close()
	client := newClient(t, server)

	const callers = 12
	start := make(chan struct{})
	errs := make(chan error, callers)
	var wg sync.WaitGroup
	wg.Add(callers)
	for i := 0; i < callers; i++ {
		go func() {
			defer wg.Done()
			<-start
			got, err := client.Inventory(context.Background())
			if err == nil && !reflect.DeepEqual(got, expectedInventory()) {
				err = fmt.Errorf("unsorted inventory: %#v", got)
			}
			errs <- err
		}()
	}
	close(start)
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}

	refreshes := 0
	for _, req := range server.Requests() {
		if req.OperationID == contractmock.OperationTokenIssue {
			refreshes++
		}
	}
	if refreshes != 1 {
		t.Fatalf("token exchanges = %d, want 1", refreshes)
	}
}

func TestContextCancellationIsDiscoverable(t *testing.T) {
	server := contractmock.New()
	defer server.Close()
	client := newClient(t, server)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := client.Inventory(ctx)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Inventory error = %v, want errors.Is(context.Canceled)", err)
	}
}

func assertFirstRunLog(t *testing.T, got []contractmock.Request) {
	t.Helper()
	want := []struct {
		operation string
		session   string
	}{
		{contractmock.OperationVMList, contractmock.InitialAccessToken},
		{contractmock.OperationHostList, contractmock.InitialAccessToken},
		{contractmock.OperationTokenIssue, ""},
		{contractmock.OperationHostList, "access-2"},
	}
	if len(got) != len(want) {
		t.Fatalf("first-run request count = %d, want %d: %#v", len(got), len(want), got)
	}
	for i, tt := range want {
		if got[i].OperationID != tt.operation || got[i].SessionID != tt.session {
			t.Errorf("request[%d] = operation %q session %q, want %q %q",
				i, got[i].OperationID, got[i].SessionID, tt.operation, tt.session)
		}
	}

	refresh := got[2]
	if refresh.Authorization != "Bearer "+contractmock.SubjectToken {
		t.Errorf("refresh Authorization = %q", refresh.Authorization)
	}
	if refresh.Accept != "application/json" {
		t.Errorf("refresh Accept = %q, want application/json", refresh.Accept)
	}
	if refresh.Form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:token-exchange" ||
		refresh.Form.Get("subject_token") != contractmock.SubjectToken ||
		refresh.Form.Get("subject_token_type") != contractmock.SubjectTokenType {
		t.Errorf("refresh form does not match pinned token exchange: %v", refresh.Form)
	}

	// The VM request appears once: a whole-workflow retry would make this two.
	vmRequests := 0
	for _, req := range got {
		if req.OperationID == contractmock.OperationVMList {
			vmRequests++
		}
	}
	if vmRequests != 1 {
		t.Fatalf("VM collection requests = %d, want 1; completed work was repeated", vmRequests)
	}
}

func newClient(t *testing.T, server *contractmock.Server) *vcenter.Client {
	t.Helper()
	client, err := vcenter.NewClient(vcenter.Config{
		BaseURL:          server.URL(),
		AccessToken:      contractmock.InitialAccessToken,
		SubjectToken:     contractmock.SubjectToken,
		SubjectTokenType: contractmock.SubjectTokenType,
		HTTPClient:       server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func expectedInventory() vcenter.Inventory {
	cpu4, cpu8 := int64(4), int64(8)
	mem8, mem16 := int64(8192), int64(16384)
	on, off := "POWERED_ON", "POWERED_OFF"
	uuidA := "11111111-1111-1111-1111-111111111111"
	uuidZ := "99999999-9999-9999-9999-999999999999"
	return vcenter.Inventory{
		VMs: []vcenter.VM{
			{ID: "vm-101", Name: "build-runner", PowerState: "POWERED_ON", CPUCount: &cpu4, MemorySizeMiB: &mem8},
			{ID: "vm-909", Name: "release-db", PowerState: "POWERED_OFF", CPUCount: &cpu8, MemorySizeMiB: &mem16},
		},
		Hosts: []vcenter.Host{
			{ID: "host-120", Name: "esx-a.example.test", ConnectionState: "CONNECTED", PowerState: &on, HostUUID: &uuidA},
			{ID: "host-880", Name: "esx-z.example.test", ConnectionState: "DISCONNECTED", PowerState: &off, HostUUID: &uuidZ},
		},
	}
}
