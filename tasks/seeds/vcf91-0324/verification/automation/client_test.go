package automation_test

import (
	"context"
	"net/url"
	"strings"
	"sync"
	"testing"

	"vcfauto/automation"
	"vcfauto/contract"
	"vcfauto/mock"
	"vcfauto/wire"
)

const (
	testTenant   = "acme-org"
	testAPIToken = "vcfa-api-token-fixture-0123456789"
)

func setup(t *testing.T, expireAfter int) (*automation.Client, *mock.Server, *contract.Contract) {
	t.Helper()
	c, err := contract.Load("../docs/contract.json")
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	srv, err := mock.Start(mock.Options{
		Contract:               c,
		Tenant:                 testTenant,
		APIToken:               testAPIToken,
		Deployments:            mock.Deployments(12),
		CatalogItems:           mock.CatalogItems(5),
		ExpireAccessTokenAfter: expireAfter,
	})
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(srv.Close)

	cl, err := automation.New(automation.Config{
		BaseURL: srv.URL(), Tenant: testTenant, APIToken: testAPIToken,
		Contract: c, Concurrency: 4,
	})
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	return cl, srv, c
}

func TestNewRejectsIncompleteConfig(t *testing.T) {
	c, err := contract.Load("../docs/contract.json")
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	full := automation.Config{
		BaseURL: "https://automation.example.com", Tenant: testTenant,
		APIToken: testAPIToken, Contract: c,
	}

	tests := []struct {
		name    string
		mutate  func(automation.Config) automation.Config
		wantErr bool
	}{
		{name: "complete", mutate: func(c automation.Config) automation.Config { return c }},
		{name: "no base URL", wantErr: true,
			mutate: func(c automation.Config) automation.Config { c.BaseURL = ""; return c }},
		{name: "relative base URL", wantErr: true,
			mutate: func(c automation.Config) automation.Config { c.BaseURL = "/api"; return c }},
		{name: "no tenant", wantErr: true,
			mutate: func(c automation.Config) automation.Config { c.Tenant = ""; return c }},
		{name: "no API token", wantErr: true,
			mutate: func(c automation.Config) automation.Config { c.APIToken = ""; return c }},
		{name: "no contract", wantErr: true,
			mutate: func(c automation.Config) automation.Config { c.Contract = nil; return c }},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, err := automation.New(tc.mutate(full))
			if tc.wantErr != (err != nil) {
				t.Fatalf("New() error = %v, wantErr %v", err, tc.wantErr)
			}
		})
	}
}

// TestListDeploymentsQueryShape checks that options reach the wire as the
// reference describes them, and that unset options do not reach it at all.
func TestListDeploymentsQueryShape(t *testing.T) {
	tests := []struct {
		name string
		opts automation.ListDeploymentsOptions
		want url.Values
	}{
		{
			name: "nothing set sends nothing",
			opts: automation.ListDeploymentsOptions{},
			want: nil,
		},
		{
			name: "page and size",
			opts: automation.ListDeploymentsOptions{
				Page: automation.Set(2), Size: automation.Set(5)},
			want: url.Values{"page": {"2"}, "size": {"5"}},
		},
		{
			name: "a zero that was set is still sent",
			opts: automation.ListDeploymentsOptions{Page: automation.Set(0)},
			want: url.Values{"page": {"0"}},
		},
		{
			name: "an empty string that was set is still sent",
			opts: automation.ListDeploymentsOptions{Search: automation.Set("")},
			want: url.Values{"search": {""}},
		},
		{
			name: "a false that was set is still sent",
			opts: automation.ListDeploymentsOptions{Deleted: automation.Set(false)},
			want: url.Values{"deleted": {"false"}},
		},
		{
			name: "slices become comma-separated lists",
			opts: automation.ListDeploymentsOptions{
				Projects: automation.Set([]string{"proj-0", "proj-1"}),
				Expand:   automation.Set([]string{"resources"}),
			},
			want: url.Values{"projects": {"proj-0,proj-1"}, "expand": {"resources"}},
		},
		{
			name: "everything at once",
			opts: automation.ListDeploymentsOptions{
				Page: automation.Set(1), Size: automation.Set(3),
				Sort: automation.Set("createdAt,DESC"), Search: automation.Set("web"),
				Name: automation.Set("deployment-01"), Deleted: automation.Set(true),
				Status: automation.Set([]string{"CREATE_SUCCESSFUL"}),
			},
			want: url.Values{
				"page": {"1"}, "size": {"3"}, "sort": {"createdAt,DESC"},
				"search": {"web"}, "name": {"deployment-01"}, "deleted": {"true"},
				"status": {"CREATE_SUCCESSFUL"},
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cl, srv, c := setup(t, 0)
			if _, err := cl.ListDeployments(context.Background(), tc.opts); err != nil {
				t.Fatalf("ListDeployments: %v", err)
			}
			got := srv.RequestsFor(contract.OpDeploymentsList)
			if len(got) != 1 {
				t.Fatalf("recorded %d list requests, want 1", len(got))
			}
			if err := wire.Check(got[0], wire.Expectation{
				Operation: contract.OpDeploymentsList,
				Method:    "GET",
				Path:      c.MustOperation(contract.OpDeploymentsList).Path,
				Query:     tc.want,
				NoBody:    true,
				Status:    200,
			}); err != nil {
				t.Error(err)
			}
		})
	}
}

// TestCatalogRequestBodyShape checks the JSON body, where the reference marks
// every field optional and so an unset field must simply not be there.
func TestCatalogRequestBodyShape(t *testing.T) {
	tests := []struct {
		name string
		req  automation.CatalogItemRequest
		want string
	}{
		{
			name: "nothing set is a bare object",
			req:  automation.CatalogItemRequest{},
			want: `{}`,
		},
		{
			name: "one field",
			req:  automation.CatalogItemRequest{DeploymentName: automation.Set("web")},
			want: `{"deploymentName":"web"}`,
		},
		{
			name: "a zero that was set is still sent",
			req:  automation.CatalogItemRequest{BulkRequestCount: automation.Set(0)},
			want: `{"bulkRequestCount":0}`,
		},
		{
			name: "an empty string that was set is still sent",
			req:  automation.CatalogItemRequest{Reason: automation.Set("")},
			want: `{"reason":""}`,
		},
		{
			name: "nested inputs",
			req: automation.CatalogItemRequest{
				ProjectID: automation.Set("proj-0"),
				Inputs:    automation.Set(map[string]any{"cpu": float64(2)}),
			},
			want: `{"projectId":"proj-0","inputs":{"cpu":2}}`,
		},
		{
			name: "everything at once",
			req: automation.CatalogItemRequest{
				DeploymentName: automation.Set("web"), ProjectID: automation.Set("proj-0"),
				Version: automation.Set("v2.0"), Reason: automation.Set("because"),
				BulkRequestCount: automation.Set(2),
			},
			want: `{"deploymentName":"web","projectId":"proj-0","version":"v2.0",` +
				`"reason":"because","bulkRequestCount":2}`,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cl, srv, _ := setup(t, 0)
			if _, err := cl.RequestCatalogItem(context.Background(), mock.CatalogItemID(0), tc.req); err != nil {
				t.Fatalf("RequestCatalogItem: %v", err)
			}
			got := srv.RequestsFor(contract.OpCatalogItemsRequest)
			if len(got) != 1 {
				t.Fatalf("recorded %d requests, want 1", len(got))
			}
			if err := wire.Check(got[0], wire.Expectation{
				Operation: contract.OpCatalogItemsRequest,
				Method:    "POST",
				JSONBody:  tc.want,
				Status:    200,
			}); err != nil {
				t.Error(err)
			}
		})
	}
}

func TestTokenExchangeWireShape(t *testing.T) {
	cl, srv, _ := setup(t, 0)
	if _, err := cl.ListDeployments(context.Background(), automation.ListDeploymentsOptions{}); err != nil {
		t.Fatalf("ListDeployments: %v", err)
	}
	got := srv.RequestsFor(contract.OpAuthToken)
	if len(got) != 1 {
		t.Fatalf("recorded %d token exchanges, want 1", len(got))
	}
	if err := wire.Check(got[0], wire.Expectation{
		Operation:     contract.OpAuthToken,
		Method:        "POST",
		AbsentHeaders: []string{"Authorization"},
		FormBody: url.Values{
			"grant_type":    {"refresh_token"},
			"refresh_token": {testAPIToken},
		},
		Status: 200,
	}); err != nil {
		t.Error(err)
	}
	if ct := got[0].Header.Get("Content-Type"); !strings.HasPrefix(ct, contract.ContentTypeForm) {
		t.Errorf("Content-Type = %q, want %q", ct, contract.ContentTypeForm)
	}
}

func TestTokenIsFetchedOnceAndReused(t *testing.T) {
	cl, srv, _ := setup(t, 0)
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		if _, err := cl.ListDeployments(ctx, automation.ListDeploymentsOptions{}); err != nil {
			t.Fatalf("ListDeployments: %v", err)
		}
	}
	if n := len(srv.RequestsFor(contract.OpAuthToken)); n != 1 {
		t.Errorf("token exchanges = %d, want 1: a token that still works must be reused", n)
	}
}

func TestExpiryHandling(t *testing.T) {
	tests := []struct {
		name          string
		expireAfter   int
		calls         int
		wantExchanges int
	}{
		{name: "no expiry", expireAfter: 0, calls: 4, wantExchanges: 1},
		{name: "expires every other call", expireAfter: 2, calls: 4, wantExchanges: 2},
		{name: "expires every call", expireAfter: 1, calls: 4, wantExchanges: 4},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cl, srv, _ := setup(t, tc.expireAfter)
			ctx := context.Background()
			for i := 0; i < tc.calls; i++ {
				if _, err := cl.ListDeployments(ctx, automation.ListDeploymentsOptions{}); err != nil {
					t.Fatalf("call %d: %v", i, err)
				}
			}
			if n := len(srv.RequestsFor(contract.OpAuthToken)); n != tc.wantExchanges {
				t.Errorf("token exchanges = %d, want %d", n, tc.wantExchanges)
			}
		})
	}
}

func TestCollectDeployments(t *testing.T) {
	tests := []struct {
		name        string
		expireAfter int
		size        int
	}{
		{name: "no expiry", expireAfter: 0, size: 4},
		{name: "expiry mid-walk", expireAfter: 2, size: 4},
		{name: "expiry every page", expireAfter: 1, size: 3},
		{name: "single page", expireAfter: 0, size: 20},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cl, _, _ := setup(t, tc.expireAfter)
			got, err := cl.CollectDeployments(context.Background(),
				automation.ListDeploymentsOptions{Page: automation.Set(0), Size: automation.Set(tc.size)})
			if err != nil {
				t.Fatalf("CollectDeployments: %v", err)
			}
			if len(got) != 12 {
				t.Fatalf("collected %d, want 12", len(got))
			}
			for i, d := range got {
				if d.ID != mock.DeploymentID(i) {
					t.Fatalf("index %d = %q, want %q", i, d.ID, mock.DeploymentID(i))
				}
			}
		})
	}
}

func TestCollectDeploymentDetailsIsOrderedAndRaceFree(t *testing.T) {
	cl, _, _ := setup(t, 3)
	ids := make([]string, 9)
	for i := range ids {
		ids[i] = mock.DeploymentID(i)
	}
	got, err := cl.CollectDeploymentDetails(context.Background(), ids, automation.GetDeploymentOptions{})
	if err != nil {
		t.Fatalf("CollectDeploymentDetails: %v", err)
	}
	for i, d := range got {
		if d.ID != ids[i] {
			t.Errorf("index %d = %q, want %q", i, d.ID, ids[i])
		}
	}
}

// TestConcurrentCallsShareOneToken drives the client from several goroutines
// at once, which is where a token cache without a lock shows up under -race.
func TestConcurrentCallsShareOneToken(t *testing.T) {
	cl, srv, _ := setup(t, 0)
	ctx := context.Background()

	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := cl.ListDeployments(ctx, automation.ListDeploymentsOptions{}); err != nil {
				t.Errorf("ListDeployments: %v", err)
			}
		}()
	}
	wg.Wait()

	if n := len(srv.RequestsFor(contract.OpAuthToken)); n != 1 {
		t.Errorf("token exchanges = %d, want 1", n)
	}
}

func TestGetDeploymentNotFound(t *testing.T) {
	cl, _, _ := setup(t, 0)
	_, err := cl.GetDeployment(context.Background(), "no-such-id", automation.GetDeploymentOptions{})
	if err == nil {
		t.Fatal("GetDeployment() = nil error, want a not-found error")
	}
	var apiErr *automation.APIError
	if !asAPIError(err, &apiErr) {
		t.Fatalf("error is %T, want *automation.APIError", err)
	}
	if apiErr.StatusCode != 404 {
		t.Errorf("StatusCode = %d, want 404", apiErr.StatusCode)
	}
}

func asAPIError(err error, target **automation.APIError) bool {
	for err != nil {
		if e, ok := err.(*automation.APIError); ok {
			*target = e
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

func TestListCatalogItems(t *testing.T) {
	cl, _, _ := setup(t, 0)
	page, err := cl.ListCatalogItems(context.Background(), automation.ListCatalogItemsOptions{
		Page: automation.Set(0), Size: automation.Set(2),
	})
	if err != nil {
		t.Fatalf("ListCatalogItems: %v", err)
	}
	if len(page.Content) != 2 {
		t.Fatalf("content = %d, want 2", len(page.Content))
	}
	if page.Content[0].ID != mock.CatalogItemID(0) {
		t.Errorf("first item = %q, want %q", page.Content[0].ID, mock.CatalogItemID(0))
	}
	if page.TotalElements != 5 {
		t.Errorf("TotalElements = %d, want 5", page.TotalElements)
	}
}
