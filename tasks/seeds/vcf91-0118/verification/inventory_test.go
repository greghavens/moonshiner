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
			BaseURL:            "https://vcenter.example.test",
			AccessToken:        "access",
			SubjectToken:       "subject",
			SubjectTokenType:   "SAML2",
			Audience:           "vcenter.example.test",
			RequestedTokenType: "JWT_ID",
			HTTPClient:         http.DefaultClient,
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
		{name: "audience", mutate: func(c *vcenter.Config) { c.Audience = "" }},
		{name: "requested token type", mutate: func(c *vcenter.Config) { c.RequestedTokenType = "" }},
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

func TestHostSortingBeyondLiveSingleHostContractCoverage(t *testing.T) {
	server := contractmock.New(contractmock.WithAdditionalHostForSortingCoverage())
	defer server.Close()
	client := newClient(t, server)

	got, err := client.Inventory(context.Background())
	if err != nil {
		t.Fatalf("Inventory: %v", err)
	}
	if len(got.Hosts) != 2 || got.Hosts[0].ID != "host-01" || got.Hosts[1].ID != "host-12" {
		t.Fatalf("Hosts = %#v, want contract-only host followed by live-shaped host", got.Hosts)
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
		{contractmock.OperationHostList, contractmock.RotatedAccessToken},
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
		refresh.Form.Get("audience") != contractmock.Audience ||
		refresh.Form.Get("requested_token_type") != contractmock.RequestedTokenType ||
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
		BaseURL:            server.URL(),
		AccessToken:        contractmock.InitialAccessToken,
		SubjectToken:       contractmock.SubjectToken,
		SubjectTokenType:   contractmock.SubjectTokenType,
		Audience:           contractmock.Audience,
		RequestedTokenType: contractmock.RequestedTokenType,
		HTTPClient:         server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func expectedInventory() vcenter.Inventory {
	on := "POWERED_ON"
	uuid := "312680c6-8a28-4302-90c6-319869516823"
	return vcenter.Inventory{
		VMs: []vcenter.VM{
			{ID: "vm-19", Name: "sddcm01", PowerState: "POWERED_ON", CPUCount: int64Ptr(4), MemorySizeMiB: int64Ptr(16384)},
			{ID: "vm-20", Name: "vc01", PowerState: "POWERED_ON", CPUCount: int64Ptr(4), MemorySizeMiB: int64Ptr(21504)},
			{ID: "vm-28", Name: "nsx01a", PowerState: "POWERED_ON", CPUCount: int64Ptr(6), MemorySizeMiB: int64Ptr(24576)},
			{ID: "vm-33", Name: "vcf-msr01-nxpxf", PowerState: "POWERED_ON", CPUCount: int64Ptr(4), MemorySizeMiB: int64Ptr(10240)},
			{ID: "vm-34", Name: "vcf-msr01-5ghdn", PowerState: "POWERED_ON", CPUCount: int64Ptr(8), MemorySizeMiB: int64Ptr(24576)},
			{ID: "vm-35", Name: "vcf-msr01-x6j88", PowerState: "POWERED_ON", CPUCount: int64Ptr(8), MemorySizeMiB: int64Ptr(24576)},
			{ID: "vm-36", Name: "vcf-msr01-6zpgq", PowerState: "POWERED_ON", CPUCount: int64Ptr(8), MemorySizeMiB: int64Ptr(24576)},
			{ID: "vm-37", Name: "vcf01", PowerState: "POWERED_ON", CPUCount: int64Ptr(4), MemorySizeMiB: int64Ptr(16384)},
			{ID: "vm-38", Name: "vcf-proxy01", PowerState: "POWERED_ON", CPUCount: int64Ptr(4), MemorySizeMiB: int64Ptr(16384)},
			{ID: "vm-39", Name: "vcf-lic01", PowerState: "POWERED_ON", CPUCount: int64Ptr(2), MemorySizeMiB: int64Ptr(4096)},
			{ID: "vm-43", Name: "vcf-asr01-szwjz", PowerState: "POWERED_ON", CPUCount: int64Ptr(8), MemorySizeMiB: int64Ptr(98304)},
		},
		Hosts: []vcenter.Host{{
			ID: "host-12", Name: "esx01.vcf.lab", ConnectionState: "CONNECTED", PowerState: &on, HostUUID: &uuid,
		}},
	}
}

func int64Ptr(value int64) *int64 { return &value }
