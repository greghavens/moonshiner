package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
)

func TestPageQueryOmitsUnsetOptionalParameters(t *testing.T) {
	tests := []struct {
		name   string
		filter Filter
		page   int
		want   string
	}{
		{
			name: "nothing set",
			want: "pageNumber=0",
		},
		{
			name:   "page size only",
			filter: Filter{PageSize: 25},
			page:   3,
			want:   "pageNumber=3&pageSize=25",
		},
		{
			name:   "resource type only",
			filter: Filter{ResourceType: "ESXI"},
			want:   "pageNumber=0&resourceType=ESXI",
		},
		{
			name:   "domain name only",
			filter: Filter{DomainName: "wld-01"},
			want:   "domainName=wld-01&pageNumber=0",
		},
		{
			name: "every field set",
			filter: Filter{
				ResourceName: "esxi-01.vrack.vsphere.local",
				ResourceType: "ESXI",
				DomainName:   "mgmt-domain",
				AccountType:  "USER",
				PageSize:     2,
			},
			want: "accountType=USER&domainName=mgmt-domain&pageNumber=0&pageSize=2&resourceName=esxi-01.vrack.vsphere.local&resourceType=ESXI",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := pageQuery(tt.filter, tt.page).Encode()
			if got != tt.want {
				t.Fatalf("query = %q, want %q", got, tt.want)
			}
			if strings.Contains(got, "=&") || strings.HasSuffix(got, "=") {
				t.Errorf("query %q carries an empty parameter value", got)
			}
		})
	}
}

func TestCreateTokenBodyDropsUnusedCredentialKinds(t *testing.T) {
	tests := []struct {
		name string
		spec tokenCreationSpec
		want string
	}{
		{
			name: "password grant",
			spec: tokenCreationSpec{Username: "administrator@vsphere.local", Password: "secret"},
			want: `{"username":"administrator@vsphere.local","password":"secret"}`,
		},
		{
			name: "api key grant",
			spec: tokenCreationSpec{APIKey: "key"},
			want: `{"apiKey":"key"}`,
		},
		{
			name: "empty spec",
			spec: tokenCreationSpec{},
			want: `{}`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := json.Marshal(tt.spec)
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			if string(got) != tt.want {
				t.Fatalf("body = %s, want %s", got, tt.want)
			}
		})
	}
}

func TestNewRejectsIncompleteConfig(t *testing.T) {
	tests := []struct {
		name string
		cfg  Config
	}{
		{"no base url", Config{Username: "u", Password: "p"}},
		{"blank base url", Config{BaseURL: "   ", Username: "u", Password: "p"}},
		{"no username", Config{BaseURL: "http://127.0.0.1:8080", Password: "p"}},
		{"no password", Config{BaseURL: "http://127.0.0.1:8080", Username: "u"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := New(tt.cfg); err == nil {
				t.Fatal("expected an error")
			}
		})
	}
}

// stub is a minimal hand-rolled endpoint used to drive the client through
// paths the pinned mock does not produce.
type stub struct {
	pages    int
	fail     int // page number answered 401 while the first token is presented
	refreshN atomic.Int64
}

func (s *stub) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/tokens":
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"accessToken":  "first",
				"refreshToken": map[string]string{"id": "rt"},
			})
		case r.Method == http.MethodPatch && r.URL.Path == "/v1/tokens/access-token/refresh":
			s.refreshN.Add(1)
			_ = json.NewEncoder(w).Encode("second")
		case r.Method == http.MethodGet && r.URL.Path == "/v1/credentials":
			page := r.URL.Query().Get("pageNumber")
			if page == s.pageString(s.fail) && r.Header.Get("Authorization") == "Bearer first" {
				w.WriteHeader(http.StatusUnauthorized)
				_ = json.NewEncoder(w).Encode(map[string]string{"errorCode": "TOKEN_EXPIRED"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"elements": []map[string]any{{"id": "cred-" + page}},
				"pageMetadata": map[string]int{
					"pageNumber": 0, "pageSize": 1, "totalElements": s.pages, "totalPages": s.pages,
				},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]string{"errorCode": "NOT_FOUND"})
		}
	}
}

func (s *stub) pageString(n int) string {
	return strconv.Itoa(n)
}

func TestListCredentialsResumesAfterRefresh(t *testing.T) {
	tests := []struct {
		name    string
		pages   int
		failOn  int
		wantIDs []string
	}{
		{"expires on the first page", 3, 0, []string{"cred-0", "cred-1", "cred-2"}},
		{"expires mid listing", 4, 2, []string{"cred-0", "cred-1", "cred-2", "cred-3"}},
		{"expires on the last page", 3, 2, []string{"cred-0", "cred-1", "cred-2"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := &stub{pages: tt.pages, fail: tt.failOn}
			srv := httptest.NewServer(s.handler())
			defer srv.Close()

			c, err := New(Config{BaseURL: srv.URL, Username: "u", Password: "p"})
			if err != nil {
				t.Fatalf("new client: %v", err)
			}
			got, err := c.ListCredentials(context.Background(), Filter{PageSize: 1})
			if err != nil {
				t.Fatalf("ListCredentials: %v", err)
			}
			if len(got) != len(tt.wantIDs) {
				t.Fatalf("collected %d credentials, want %d", len(got), len(tt.wantIDs))
			}
			for i, want := range tt.wantIDs {
				if got[i].ID != want {
					t.Fatalf("credential ids = %v, want %v", got, tt.wantIDs)
				}
			}
			if got := s.refreshN.Load(); got != 1 {
				t.Errorf("refreshed %d times, want 1", got)
			}
		})
	}
}

func TestListCredentialsFailsWhenRefreshDoesNotHelp(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/v1/tokens":
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"accessToken": "first", "refreshToken": map[string]string{"id": "rt"},
			})
		case "/v1/tokens/access-token/refresh":
			_ = json.NewEncoder(w).Encode("second")
		default:
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"errorCode": "TOKEN_EXPIRED"})
		}
	}))
	defer srv.Close()

	c, err := New(Config{BaseURL: srv.URL, Username: "u", Password: "p"})
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	if _, err := c.ListCredentials(context.Background(), Filter{PageSize: 1}); err == nil {
		t.Fatal("expected an error when the refreshed token is refused too")
	}
}
