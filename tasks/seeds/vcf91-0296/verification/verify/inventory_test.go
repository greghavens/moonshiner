// This file is part of the protected harness. Do not edit it.
package verify

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"vcfopsnetinv/internal/opsnet"
	"vcfopsnetinv/internal/opsnetmock"
)

var localCreds = opsnet.Credentials{
	Username:   "svc-inventory@local",
	Password:   "S3cret-Pass!",
	DomainType: opsnet.DomainLocal,
}

// scenario is one row of the acceptance table.
type scenario struct {
	name string
	// clockStep is how far the fake clock advances per HTTP round trip.
	clockStep time.Duration
	// mockOpts builds the mock options; now is the shared fake clock.
	mockOpts func(now func() time.Time) opsnetmock.Options
	// tune adjusts the default client configuration.
	tune func(cfg *opsnet.Config)
	// wantCollectErr expects CollectInventory to fail.
	wantCollectErr bool
	// check runs after Close.
	check func(t *testing.T, env *env)
}

// env is what a scenario's check sees.
type env struct {
	cfg      opsnet.Config
	srv      *opsnetmock.Server
	client   *opsnet.Client
	inv      *opsnet.Inventory
	closeErr error
}

func scenarios() []scenario {
	return []scenario{
		{
			// Baseline: no token trouble at all. Pins the operation sequence
			// (list every page first, then fetch details) and proves the
			// optional parameters this configuration does not set are absent.
			name: "happy_path_local_domain",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23}
			},
			check: func(t *testing.T, env *env) {
				assertOpShape(t, env.srv, []string{
					"create", "listApplications x3", "getApplicationById x23", "delete",
				})
				assertInventory(t, env)
				if got := env.srv.Unauthorized(); got != 0 {
					t.Errorf("mock served %d 401 responses, want 0 in the untroubled case", got)
				}
				if got := env.client.TokenCreates(); got != 1 {
					t.Errorf("TokenCreates() = %d, want 1", got)
				}
				for _, app := range env.inv.Applications {
					if app.TierCount != 0 || app.MemberCount != 0 {
						t.Errorf("%s: member counts populated without fetch_member_counts", app.EntityID)
						break
					}
				}
			},
		},
		{
			// No domain configured: the whole Domain object must be absent from
			// the UserCredential body, not sent as {} or with empty strings.
			name: "credentials_without_domain_omit_domain_object",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23}
			},
			tune: func(cfg *opsnet.Config) {
				cfg.Credentials = opsnet.Credentials{Username: "admin@local", Password: "pw-1"}
			},
			check: func(t *testing.T, env *env) { assertInventory(t, env) },
		},
		{
			// LDAP: Domain.value is meaningful and must be present.
			name: "ldap_domain_sends_domain_value",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23}
			},
			tune: func(cfg *opsnet.Config) {
				cfg.Credentials = opsnet.Credentials{
					Username:    "netops",
					Password:    "pw-2",
					DomainType:  opsnet.DomainLDAP,
					DomainValue: "corp.example.net",
				}
			},
			check: func(t *testing.T, env *env) { assertInventory(t, env) },
		},
		{
			// Only one of the two detail flags is requested, so only one may
			// appear on the wire.
			name: "one_detail_flag_requested",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23}
			},
			tune: func(cfg *opsnet.Config) { cfg.FetchMemberCounts = true },
			check: func(t *testing.T, env *env) {
				assertInventory(t, env)
				for _, app := range env.inv.Applications {
					if app.MemberCount == 0 {
						t.Errorf("%s: member_count not decoded although fetch_member_counts was set", app.EntityID)
						break
					}
					if app.UpdateStatus != "" {
						t.Errorf("%s: update_status = %q without fetch_update_status", app.EntityID, app.UpdateStatus)
						break
					}
				}
			},
		},
		{
			name: "both_detail_flags_requested",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23}
			},
			tune: func(cfg *opsnet.Config) {
				cfg.FetchMemberCounts = true
				cfg.FetchUpdateStatus = true
			},
			check: func(t *testing.T, env *env) {
				assertInventory(t, env)
				for _, app := range env.inv.Applications {
					if app.UpdateStatus == "" {
						t.Errorf("%s: update_status not decoded although fetch_update_status was set", app.EntityID)
						break
					}
				}
			},
		},
		{
			// PageSize 0 means "let the server default apply", so size must be
			// absent rather than sent as size=0 or size=10.
			name: "unset_page_size_omits_size_parameter",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23, DefaultPageSize: 10}
			},
			tune: func(cfg *opsnet.Config) { cfg.PageSize = 0 },
			check: func(t *testing.T, env *env) {
				assertOpShape(t, env.srv, []string{
					"create", "listApplications x3", "getApplicationById x23", "delete",
				})
				assertInventory(t, env)
			},
		},
		{
			// The token dies after two pages. The third page must be retried
			// from the cursor it already had; pages one and two must not be
			// fetched again.
			name: "token_expires_during_pagination",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{
					Now:           now,
					Applications:  23,
					RequestBudget: budgetForFirstToken(2),
				}
			},
			check: func(t *testing.T, env *env) {
				assertOpShape(t, env.srv, []string{
					"create", "listApplications x3", "create", "listApplications",
					"getApplicationById x23", "delete",
				})
				assertInventory(t, env)
				if got := env.srv.Unauthorized(); got != 1 {
					t.Errorf("mock served %d 401 responses, want exactly 1", got)
				}
				if got := env.srv.TokensIssued(); got != 2 {
					t.Errorf("mock issued %d tokens, want 2 (initial login plus one refresh)", got)
				}
				if got := env.client.TokenCreates(); got != 2 {
					t.Errorf("TokenCreates() = %d, want 2", got)
				}
			},
		},
		{
			// The token dies mid-way through the concurrent detail phase, so
			// several workers see 401 at once. They must collapse onto a single
			// refresh: a worker that finds the token already replaced reuses the
			// replacement instead of minting another.
			name: "token_expires_during_concurrent_details",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{
					Now:           now,
					Applications:  23,
					RequestBudget: budgetForFirstToken(8),
				}
			},
			tune: func(cfg *opsnet.Config) { cfg.DetailConcurrency = 4 },
			check: func(t *testing.T, env *env) {
				assertInventory(t, env)
				if got := env.srv.TokensIssued(); got != 2 {
					t.Errorf("mock issued %d tokens, want exactly 2: concurrent 401s must collapse onto one refresh", got)
				}
				if got := env.client.TokenCreates(); got != 2 {
					t.Errorf("TokenCreates() = %d, want 2", got)
				}
				if got := env.srv.Unauthorized(); got < 1 {
					t.Errorf("mock served %d 401 responses, want at least 1: the scenario never exercised expiry", got)
				}
			},
		},
		{
			// The client is told to refresh while the token still has more than
			// RefreshSkew left, so it must never see a 401 at all. Token.expiry
			// is epoch milliseconds (see docs/contract.json): reading it as
			// seconds puts expiry in the far future and no refresh happens.
			//
			// The fake clock advances 1s per round trip and the first token
			// lives 20s, so with a 6.5s skew the refresh is due immediately
			// before the 15th round trip - the 11th detail request.
			name:      "token_refreshed_proactively_before_expiry",
			clockStep: time.Second,
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{
					Now:          now,
					Applications: 23,
					TokenTTL: func(index int) time.Duration {
						if index == 1 {
							return 20 * time.Second
						}
						return 10000 * time.Second
					},
				}
			},
			tune: func(cfg *opsnet.Config) { cfg.RefreshSkew = 6500 * time.Millisecond },
			check: func(t *testing.T, env *env) {
				assertOpShape(t, env.srv, []string{
					"create", "listApplications x3", "getApplicationById x10",
					"create", "getApplicationById x13", "delete",
				})
				assertInventory(t, env)
				if got := env.srv.Unauthorized(); got != 0 {
					t.Errorf("mock served %d 401 responses, want 0: the refresh should have happened before expiry", got)
				}
				if got := env.srv.TokensIssued(); got != 2 {
					t.Errorf("mock issued %d tokens, want 2", got)
				}
			},
		},
		{
			// Bad credentials must surface as an error immediately, with no
			// retry storm against the login operation.
			name: "rejected_credentials_fail_fast",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{Now: now, Applications: 23, RejectCredentials: true}
			},
			wantCollectErr: true,
			check: func(t *testing.T, env *env) {
				assertOpShape(t, env.srv, []string{"create"})
				if got := env.srv.TokensIssued(); got != 0 {
					t.Errorf("mock issued %d tokens, want 0", got)
				}
				if env.closeErr != nil {
					t.Errorf("Close() = %v, want nil when the client holds no token", env.closeErr)
				}
			},
		},
		{
			// Every token is dead on arrival. The client must refresh at most
			// once per request and then give up, rather than looping forever.
			name: "every_token_dead_on_arrival_gives_up",
			mockOpts: func(now func() time.Time) opsnetmock.Options {
				return opsnetmock.Options{
					Now:           now,
					Applications:  23,
					RequestBudget: func(int) int { return 0 },
				}
			},
			wantCollectErr: true,
			check: func(t *testing.T, env *env) {
				assertOpShape(t, env.srv, []string{
					"create", "listApplications", "create", "listApplications", "delete",
				})
				if got := env.srv.TokensIssued(); got != 2 {
					t.Errorf("mock issued %d tokens, want exactly 2: at most one refresh per failed request", got)
				}
			},
		},
	}
}

func TestCollectInventoryAgainstContract(t *testing.T) {
	for _, tc := range scenarios() {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			clock := newFakeClock(baseTime, tc.clockStep)
			srv := opsnetmock.New(tc.mockOpts(clock.now))
			t.Cleanup(srv.Close)

			cfg := opsnet.Config{
				BaseURL:           srv.URL(),
				Credentials:       localCreds,
				HTTPClient:        newHTTPClient(clock),
				Now:               clock.now,
				PageSize:          10,
				DetailConcurrency: 1,
			}
			if tc.tune != nil {
				tc.tune(&cfg)
			}

			client, err := opsnet.New(cfg)
			if err != nil {
				t.Fatalf("New() = %v, want a usable client", err)
			}
			if got := srv.Log(); len(got) != 0 {
				t.Fatalf("New() issued %d requests; it must perform no I/O", len(got))
			}

			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()

			inv, collectErr := client.CollectInventory(ctx)
			switch {
			case tc.wantCollectErr && collectErr == nil:
				t.Fatal("CollectInventory() succeeded, want an error")
			case tc.wantCollectErr:
				if inv != nil {
					t.Errorf("CollectInventory() returned an inventory alongside error %v, want nil", collectErr)
				}
			case collectErr != nil:
				t.Fatalf("CollectInventory() = %v", collectErr)
			case inv == nil:
				t.Fatal("CollectInventory() returned a nil inventory and a nil error")
			}

			closeErr := client.Close(ctx)

			e := &env{cfg: cfg, srv: srv, client: client, inv: inv, closeErr: closeErr}
			if !tc.wantCollectErr {
				if closeErr != nil {
					t.Errorf("Close() = %v", closeErr)
				}
				assertWireShape(t, cfg, srv)
				assertAuthenticatedRequests(t, srv)
				assertNoWorkLost(t, srv)
			}
			if tc.check != nil {
				tc.check(t, e)
			}
		})
	}
}

// budgetForFirstToken lets the first issued token serve n authenticated
// requests and leaves every later token unlimited.
func budgetForFirstToken(n int) func(int) int {
	return func(index int) int {
		if index == 1 {
			return n
		}
		return -1
	}
}

func assertOpShape(t *testing.T, srv *opsnetmock.Server, want []string) {
	t.Helper()
	got := runLengths(opsOf(srv.Log()))
	if strings.Join(got, ", ") != strings.Join(want, ", ") {
		t.Errorf("operation sequence:\n got: %s\nwant: %s", render(got), render(want))
	}
}

// render keeps a runaway sequence from burying the failure it explains.
func render(segments []string) string {
	const limit = 20
	if len(segments) <= limit {
		return strings.Join(segments, ", ")
	}
	return strings.Join(segments[:limit], ", ") +
		", ... (" + itoa(len(segments)) + " segments in total)"
}

// assertInventory checks the collected result against the fixture inventory.
func assertInventory(t *testing.T, env *env) {
	t.Helper()
	if env.inv == nil {
		t.Fatal("no inventory to check")
	}
	wantIDs := env.srv.EntityIDs()
	if len(env.inv.Applications) != len(wantIDs) {
		t.Fatalf("collected %d applications, want %d", len(env.inv.Applications), len(wantIDs))
	}
	for i, app := range env.inv.Applications {
		if app.EntityID != wantIDs[i] {
			t.Fatalf("Applications[%d].EntityID = %q, want %q: results must keep the pagination order",
				i, app.EntityID, wantIDs[i])
		}
		if want := env.srv.NameFor(app.EntityID); app.Name != want {
			t.Errorf("Applications[%d].Name = %q, want %q", i, app.Name, want)
		}
		if app.EntityType != "Application" {
			t.Errorf("Applications[%d].EntityType = %q, want %q", i, app.EntityType, "Application")
		}
	}
	if env.inv.TotalCount != len(wantIDs) {
		t.Errorf("TotalCount = %d, want %d", env.inv.TotalCount, len(wantIDs))
	}
	wantPages := len(entriesFor(env.srv.Log(), "listApplications"))
	for _, e := range entriesFor(env.srv.Log(), "listApplications") {
		if e.Status != http.StatusOK {
			wantPages--
		}
	}
	if env.inv.Pages != wantPages {
		t.Errorf("Pages = %d, want %d (the number of listApplications responses that succeeded)", env.inv.Pages, wantPages)
	}
}

// assertNoWorkLost is the core "refresh without losing work" check: nothing that
// already succeeded is redone, and anything that failed on an expired token is
// retried at the same position with a fresh token.
func assertNoWorkLost(t *testing.T, srv *opsnetmock.Server) {
	t.Helper()
	log := srv.Log()

	lists := entriesFor(log, "listApplications")
	seenCursor := map[string]int{}
	for _, e := range lists {
		if e.Status != http.StatusOK {
			continue
		}
		cursor := e.Query.Get("cursor")
		if prev, dup := seenCursor[cursor]; dup {
			t.Errorf("listApplications fetched cursor %q successfully twice (requests #%d and #%d): a page already collected was refetched",
				cursor, prev, e.Seq)
		}
		seenCursor[cursor] = e.Seq
	}
	for i, e := range lists {
		if e.Status != http.StatusUnauthorized {
			continue
		}
		if i+1 >= len(lists) {
			t.Errorf("listApplications request #%d got 401 and was never retried", e.Seq)
			continue
		}
		next := lists[i+1]
		if next.Query.Has("cursor") != e.Query.Has("cursor") || next.Query.Get("cursor") != e.Query.Get("cursor") {
			t.Errorf("listApplications request #%d got 401 at cursor %q, but the next page request #%d used cursor %q: pagination must resume from the same cursor, not restart",
				e.Seq, e.Query.Get("cursor"), next.Seq, next.Query.Get("cursor"))
		}
		if next.TokenIndex <= e.TokenIndex {
			t.Errorf("listApplications request #%d retried with token #%d after a 401 on token #%d: the retry must use a fresh token",
				next.Seq, next.TokenIndex, e.TokenIndex)
		}
	}

	details := entriesFor(log, "getApplicationById")
	fetched := map[string]int{}
	for _, e := range details {
		if e.Status != http.StatusOK {
			continue
		}
		id := strings.TrimPrefix(e.Path, appPathPrefix)
		if prev, dup := fetched[id]; dup {
			t.Errorf("getApplicationById fetched %s successfully twice (requests #%d and #%d)", id, prev, e.Seq)
		}
		fetched[id] = e.Seq
	}
	for _, id := range srv.EntityIDs() {
		if _, ok := fetched[id]; !ok {
			t.Errorf("getApplicationById never succeeded for %s: work was lost", id)
		}
	}
	for _, e := range details {
		if e.Status != http.StatusUnauthorized {
			continue
		}
		id := strings.TrimPrefix(e.Path, appPathPrefix)
		retrySeq, ok := fetched[id]
		if !ok || retrySeq <= e.Seq {
			t.Errorf("getApplicationById request #%d for %s got 401 and was never retried successfully", e.Seq, id)
			continue
		}
		var retry opsnetmock.Entry
		for _, c := range details {
			if c.Seq == retrySeq {
				retry = c
			}
		}
		if retry.TokenIndex <= e.TokenIndex {
			t.Errorf("getApplicationById request #%d retried %s with token #%d after a 401 on token #%d: the retry must use a fresh token",
				retry.Seq, id, retry.TokenIndex, e.TokenIndex)
		}
	}
}
