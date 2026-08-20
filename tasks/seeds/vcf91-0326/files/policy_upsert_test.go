package vcfapolicy_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	vcfapolicy "vcfa-policy-upsert"
	"vcfa-policy-upsert/internal/vcfamock"
)

const (
	contractPath = "docs/contract.json"
	bearerToken  = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.vcfa-test-token"
	upsertPath   = "/policy/api/policies"
	policyTypeID = "com.vmware.policy.deployment.lease"
)

var uuidV4 = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

// minter hands out predictable policy ids and counts how many it handed out,
// so a client that mints one id per delivery instead of one per upsert is
// visible.
type minter struct {
	mu     sync.Mutex
	minted []string
}

func (m *minter) next() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	id := fmt.Sprintf("11111111-2222-4333-8444-%012d", len(m.minted)+1)
	m.minted = append(m.minted, id)
	return id
}

func (m *minter) all() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]string, len(m.minted))
	copy(out, m.minted)
	return out
}

type env struct {
	mock   *vcfamock.Mock
	minter *minter
	result vcfapolicy.Result
	err    error
}

// posts returns the logged upsert deliveries, in order.
func (e *env) posts() []vcfamock.Recorded {
	var out []vcfamock.Recorded
	for _, entry := range e.mock.Requests() {
		if entry.Operation == "upsertPolicy" {
			out = append(out, entry)
		}
	}
	return out
}

func (e *env) gets() []vcfamock.Recorded {
	var out []vcfamock.Recorded
	for _, entry := range e.mock.Requests() {
		if entry.Operation == "getPolicy" {
			out = append(out, entry)
		}
	}
	return out
}

// bodyKeys returns the top-level JSON keys of one request body, sorted, and
// fails if any value was sent as null.
func bodyKeys(t *testing.T, entry vcfamock.Recorded) []string {
	t.Helper()
	var document map[string]json.RawMessage
	if err := json.Unmarshal(entry.Body, &document); err != nil {
		t.Fatalf("request body is not a JSON object: %v (body %q)", err, entry.Body)
	}
	keys := make([]string, 0, len(document))
	for key, value := range document {
		if string(value) == "null" {
			t.Errorf("property %q was sent as null; an unset property is omitted", key)
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func wantKeys(t *testing.T, entry vcfamock.Recorded, want ...string) {
	t.Helper()
	sort.Strings(want)
	got := bodyKeys(t, entry)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("request body properties = %v, want exactly %v (body %s)", got, want, entry.Body)
	}
}

func bodyValue(t *testing.T, entry vcfamock.Recorded, key string) any {
	t.Helper()
	var document map[string]any
	if err := json.Unmarshal(entry.Body, &document); err != nil {
		t.Fatalf("request body is not a JSON object: %v", err)
	}
	return document[key]
}

type testCase struct {
	name      string
	spec      vcfapolicy.PolicySpec
	script    []vcfamock.Action
	seed      map[string]any
	configure func(*vcfapolicy.Config)
	ctx       func(t *testing.T) context.Context
	check     func(t *testing.T, e *env)
}

func TestEnsurePolicy(t *testing.T) {
	t.Parallel()

	cases := []testCase{
		{
			name: "creates a policy that is not there yet",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				id := e.minter.all()[0]
				if e.result.Outcome != vcfapolicy.OutcomeCreated {
					t.Errorf("Outcome = %q, want %q", e.result.Outcome, vcfapolicy.OutcomeCreated)
				}
				if e.result.Attempts != 1 {
					t.Errorf("Attempts = %d, want 1", e.result.Attempts)
				}
				if e.result.PolicyID != id {
					t.Errorf("PolicyID = %q, want the minted id %q", e.result.PolicyID, id)
				}
				wantOperations(t, e, "upsertPolicy", "getPolicy")

				post := e.posts()[0]
				if post.Path != upsertPath {
					t.Errorf("upsert path = %q, want %q", post.Path, upsertPath)
				}
				wantKeys(t, post, "typeId", "id")
				if got := bodyValue(t, post, "typeId"); got != policyTypeID {
					t.Errorf("typeId = %v, want %q", got, policyTypeID)
				}
				if got := bodyValue(t, post, "id"); got != id {
					t.Errorf("id = %v, want %q", got, id)
				}

				get := e.gets()[0]
				if want := upsertPath + "/" + id; get.Path != want {
					t.Errorf("read-back path = %q, want %q", get.Path, want)
				}

				// The upsert response carries no data structure, so everything
				// reported about the stored policy has to come from the read.
				if e.result.Policy.ID != id {
					t.Errorf("Policy.ID = %q, want %q", e.result.Policy.ID, id)
				}
				if e.result.Policy.EnforcementType != "HARD" {
					t.Errorf("Policy.EnforcementType = %q, want the server default %q",
						e.result.Policy.EnforcementType, "HARD")
				}
				if e.result.Policy.CreatedAt == "" || e.result.Policy.OrgID == "" {
					t.Errorf("Policy was not read back from the API: %+v", e.result.Policy)
				}
				wantStoredCount(t, e, 1)
			},
		},
		{
			name: "sends every property the caller set",
			spec: vcfapolicy.PolicySpec{
				TypeID:          policyTypeID,
				Name:            "lease-cap-nonprod",
				Description:     "Non-production leases expire after 7 days.",
				EnforcementType: "SOFT",
				OrgID:           "8f4a2c5e-0d31-4a7b-9a2f-6c1d0e3b7a55",
				ProjectID:       "3c9d1b70-5e42-4f8a-b1c6-2a7e94d0f318",
				OPARegoCriteria: "package vcfa\ndefault allow = true\n",
				Criteria:        map[string]any{"matchExpression": []any{map[string]any{"key": "tag", "operator": "eq", "value": "nonprod"}}},
				ScopeCriteria:   map[string]any{"matchExpression": []any{map[string]any{"key": "project", "operator": "eq", "value": "nonprod"}}},
				Definition:      map[string]any{"leaseGrace": 3, "leaseTermMax": 7},
			},
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				post := e.posts()[0]
				wantKeys(t, post, "typeId", "id", "name", "description", "enforcementType",
					"orgId", "projectId", "opaRegoCriteria", "criteria", "scopeCriteria", "definition")
				if got := bodyValue(t, post, "enforcementType"); got != "SOFT" {
					t.Errorf("enforcementType = %v, want SOFT", got)
				}
				if got := bodyValue(t, post, "name"); got != "lease-cap-nonprod" {
					t.Errorf("name = %v", got)
				}
				definition, _ := bodyValue(t, post, "definition").(map[string]any)
				if definition["leaseTermMax"] != float64(7) {
					t.Errorf("definition = %v, want leaseTermMax 7", definition)
				}
			},
		},
		{
			name: "omits properties the caller left unset instead of sending them empty",
			spec: vcfapolicy.PolicySpec{
				TypeID:        policyTypeID,
				Name:          "lease-cap-nonprod",
				Criteria:      map[string]any{},
				ScopeCriteria: nil,
				Definition:    map[string]any{"leaseTermMax": 7},
			},
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				post := e.posts()[0]
				// description, enforcementType, orgId, projectId and
				// opaRegoCriteria were never set; criteria was set to an empty
				// object. None of them belongs on the wire.
				wantKeys(t, post, "typeId", "id", "name", "definition")

				var document map[string]any
				if err := json.Unmarshal(post.Body, &document); err != nil {
					t.Fatalf("request body: %v", err)
				}
				for _, forbidden := range []string{
					"createdAt", "createdBy", "creator", "lastUpdatedAt",
					"lastUpdatedBy", "lastUpdater", "statistics", "definitionLegend",
				} {
					if _, present := document[forbidden]; present {
						t.Errorf("sent server-owned property %q", forbidden)
					}
				}
			},
		},
		{
			name: "updates in place when the policy is already there",
			seed: map[string]any{
				"id":     "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE",
				"typeId": policyTypeID,
				"name":   "lease-cap-nonprod",
			},
			spec: vcfapolicy.PolicySpec{
				ID:     "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE",
				TypeID: policyTypeID,
				Name:   "lease-cap-nonprod-v2",
			},
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				if e.result.Outcome != vcfapolicy.OutcomeUpdated {
					t.Errorf("Outcome = %q, want %q", e.result.Outcome, vcfapolicy.OutcomeUpdated)
				}
				if minted := e.minter.all(); len(minted) != 0 {
					t.Errorf("minted %v; a caller-supplied id must be used as given", minted)
				}
				if e.result.PolicyID != "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE" {
					t.Errorf("PolicyID = %q", e.result.PolicyID)
				}
				if got := bodyValue(t, e.posts()[0], "id"); got != "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE" {
					t.Errorf("id on the wire = %v", got)
				}
				wantStoredCount(t, e, 1)
				if e.result.Policy.Name != "lease-cap-nonprod-v2" {
					t.Errorf("Policy.Name = %q, want the updated name", e.result.Policy.Name)
				}
			},
		},
		{
			name: "retries a throttled or unavailable upsert without reading back in between",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID, Name: "lease-cap-nonprod"},
			script: []vcfamock.Action{
				{Status: http.StatusTooManyRequests},
				{Status: http.StatusInternalServerError},
				{Status: http.StatusBadGateway},
				{Status: http.StatusServiceUnavailable},
				{Status: http.StatusGatewayTimeout},
			},
			configure: func(cfg *vcfapolicy.Config) { cfg.MaxAttempts = 6 },
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				// A response with a status is never ambiguous, so there is
				// nothing to reconcile between deliveries.
				wantOperations(t, e,
					"upsertPolicy", "upsertPolicy", "upsertPolicy",
					"upsertPolicy", "upsertPolicy", "upsertPolicy", "getPolicy")
				if e.result.Attempts != 6 {
					t.Errorf("Attempts = %d, want 6", e.result.Attempts)
				}
				if e.result.Outcome != vcfapolicy.OutcomeCreated {
					t.Errorf("Outcome = %q, want %q", e.result.Outcome, vcfapolicy.OutcomeCreated)
				}
				posts := e.posts()
				for i, post := range posts[1:] {
					if string(post.Body) != string(posts[0].Body) {
						t.Errorf("delivery %d body = %s, want the same bytes as the first delivery %s",
							i+2, post.Body, posts[0].Body)
					}
				}
				if minted := e.minter.all(); len(minted) != 1 {
					t.Errorf("minted %v; one upsert mints one id however often it is delivered", minted)
				}
				wantStoredCount(t, e, 1)
			},
		},
		{
			name: "does not deliver twice when the first delivery landed but was never acknowledged",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID, Name: "lease-cap-nonprod"},
			// The write is applied and then the connection dies, so the client
			// is left not knowing whether it landed.
			script: []vcfamock.Action{{Commit: true, Drop: true}},
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				wantOperations(t, e, "upsertPolicy", "getPolicy")
				if e.result.Outcome != vcfapolicy.OutcomeRecovered {
					t.Errorf("Outcome = %q, want %q", e.result.Outcome, vcfapolicy.OutcomeRecovered)
				}
				if e.result.Attempts != 1 {
					t.Errorf("Attempts = %d, want 1", e.result.Attempts)
				}
				id := e.minter.all()[0]
				if e.result.PolicyID != id || e.result.Policy.ID != id {
					t.Errorf("PolicyID = %q, Policy.ID = %q, want %q", e.result.PolicyID, e.result.Policy.ID, id)
				}
				wantStoredCount(t, e, 1)
				if _, ok := e.mock.Policies()[id]; !ok {
					t.Errorf("stored policies %v do not include %q", e.mock.Policies(), id)
				}
			},
		},
		{
			name: "delivers again when the lost delivery had not landed",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID, Name: "lease-cap-nonprod"},
			// The connection dies before the write is applied.
			script: []vcfamock.Action{{Drop: true}},
			check: func(t *testing.T, e *env) {
				if e.err != nil {
					t.Fatalf("EnsurePolicy: %v", e.err)
				}
				// Reconcile first, find nothing, then deliver again.
				wantOperations(t, e, "upsertPolicy", "getPolicy", "upsertPolicy", "getPolicy")
				if e.result.Outcome != vcfapolicy.OutcomeCreated {
					t.Errorf("Outcome = %q, want %q", e.result.Outcome, vcfapolicy.OutcomeCreated)
				}
				if e.result.Attempts != 2 {
					t.Errorf("Attempts = %d, want 2", e.result.Attempts)
				}
				posts := e.posts()
				if string(posts[0].Body) != string(posts[1].Body) {
					t.Errorf("second delivery body = %s, want the same bytes as the first %s",
						posts[1].Body, posts[0].Body)
				}
				if minted := e.minter.all(); len(minted) != 1 {
					t.Errorf("minted %v, want one id", minted)
				}
				wantStoredCount(t, e, 1)
			},
		},
		{
			name:   "reports the id when every delivery fails",
			spec:   vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script: []vcfamock.Action{{Status: 503}, {Status: 503}, {Status: 503}},
			configure: func(cfg *vcfapolicy.Config) {
				cfg.MaxAttempts = 3
			},
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) {
					t.Fatalf("error = %v (%T), want *vcfapolicy.APIError", e.err, e.err)
				}
				if apiErr.StatusCode != 503 || apiErr.Operation != "upsertPolicy" ||
					apiErr.Method != http.MethodPost || apiErr.Path != upsertPath {
					t.Errorf("APIError = %+v, want operation upsertPolicy POST %s status 503", apiErr, upsertPath)
				}
				if !strings.Contains(e.err.Error(), "503") {
					t.Errorf("error %q does not name the status", e.err)
				}
				if strings.Contains(e.err.Error(), bearerToken) {
					t.Errorf("error message leaks the bearer token")
				}
				if e.result.PolicyID != e.minter.all()[0] {
					t.Errorf("PolicyID = %q, want the minted id even on failure", e.result.PolicyID)
				}
				if e.result.Attempts != 3 {
					t.Errorf("Attempts = %d, want 3", e.result.Attempts)
				}
				if e.result.Outcome != "" {
					t.Errorf("Outcome = %q, want empty on failure", e.result.Outcome)
				}
				wantOperations(t, e, "upsertPolicy", "upsertPolicy", "upsertPolicy")
				wantStoredCount(t, e, 0)
			},
		},
		{
			name: "defaults to four delivery attempts",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script: []vcfamock.Action{
				{Status: http.StatusServiceUnavailable},
				{Status: http.StatusServiceUnavailable},
				{Status: http.StatusServiceUnavailable},
				{Status: http.StatusServiceUnavailable},
			},
			configure: func(cfg *vcfapolicy.Config) { cfg.MaxAttempts = 0 },
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) || apiErr.StatusCode != http.StatusServiceUnavailable {
					t.Fatalf("error = %v, want a 503 *vcfapolicy.APIError", e.err)
				}
				if e.result.Attempts != 4 {
					t.Errorf("Attempts = %d, want the default 4", e.result.Attempts)
				}
				wantOperations(t, e,
					"upsertPolicy", "upsertPolicy", "upsertPolicy", "upsertPolicy")
			},
		},
		{
			name: "does not retry a rejected upsert",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script: []vcfamock.Action{{
				Status: 400,
				Body: `{"message":"typeId is not a known policy type","detail":"` +
					strings.Repeat("x", 5000) + bearerToken + `"}`,
			}},
			configure: func(cfg *vcfapolicy.Config) { cfg.MaxAttempts = 4 },
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) {
					t.Fatalf("error = %v (%T), want *vcfapolicy.APIError", e.err, e.err)
				}
				if apiErr.StatusCode != 400 {
					t.Errorf("StatusCode = %d, want 400", apiErr.StatusCode)
				}
				wantBody := `{"message":"typeId is not a known policy type","detail":"` +
					strings.Repeat("x", 5000) + bearerToken + `"}`
				if apiErr.Body != wantBody {
					t.Errorf("APIError.Body was not the complete response body (got %d bytes, want %d)",
						len(apiErr.Body), len(wantBody))
				}
				if strings.Contains(e.err.Error(), bearerToken) {
					t.Errorf("error message leaks the bearer token")
				}
				wantOperations(t, e, "upsertPolicy")
				if e.result.Attempts != 1 {
					t.Errorf("Attempts = %d, want 1", e.result.Attempts)
				}
			},
		},
		{
			name:      "does not retry authentication or authorization failures",
			spec:      vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script:    []vcfamock.Action{{Status: http.StatusUnauthorized}},
			configure: func(cfg *vcfapolicy.Config) { cfg.MaxAttempts = 4 },
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) || apiErr.StatusCode != http.StatusUnauthorized {
					t.Fatalf("error = %v, want a 401 *vcfapolicy.APIError", e.err)
				}
				wantOperations(t, e, "upsertPolicy")
				if e.result.Attempts != 1 {
					t.Errorf("Attempts = %d, want 1", e.result.Attempts)
				}
			},
		},
		{
			name:      "does not retry a forbidden upsert",
			spec:      vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script:    []vcfamock.Action{{Status: http.StatusForbidden}},
			configure: func(cfg *vcfapolicy.Config) { cfg.MaxAttempts = 4 },
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) || apiErr.StatusCode != http.StatusForbidden {
					t.Fatalf("error = %v, want a 403 *vcfapolicy.APIError", e.err)
				}
				wantOperations(t, e, "upsertPolicy")
				if e.result.Attempts != 1 {
					t.Errorf("Attempts = %d, want 1", e.result.Attempts)
				}
			},
		},
		{
			name: "reports a read-back response failure",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script: []vcfamock.Action{{
				ReadStatus: http.StatusForbidden,
				ReadBody:   `{"message":"read denied"}`,
			}},
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) {
					t.Fatalf("error = %v (%T), want *vcfapolicy.APIError", e.err, e.err)
				}
				wantPath := upsertPath + "/" + e.result.PolicyID
				if apiErr.Operation != "getPolicy" || apiErr.Method != http.MethodGet ||
					apiErr.Path != wantPath || apiErr.StatusCode != http.StatusForbidden ||
					apiErr.Body != `{"message":"read denied"}` {
					t.Errorf("APIError = %+v, want getPolicy GET %s status 403 with its body",
						apiErr, wantPath)
				}
				if e.result.Attempts != 1 {
					t.Errorf("Attempts = %d, want 1", e.result.Attempts)
				}
				wantOperations(t, e, "upsertPolicy", "getPolicy")
			},
		},
		{
			name: "carries the not-found body when a successful upsert cannot be read back",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script: []vcfamock.Action{{
				ReadStatus: http.StatusNotFound,
				ReadBody:   `{"message":"policy is not visible"}`,
			}},
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) {
					t.Fatalf("error = %v (%T), want *vcfapolicy.APIError", e.err, e.err)
				}
				if apiErr.Operation != "getPolicy" || apiErr.StatusCode != http.StatusNotFound ||
					apiErr.Body != `{"message":"policy is not visible"}` {
					t.Errorf("APIError = %+v, want the getPolicy 404 and its response body", apiErr)
				}
				wantOperations(t, e, "upsertPolicy", "getPolicy")
			},
		},
		{
			name:      "fails on an accepted upsert it cannot resolve",
			spec:      vcfapolicy.PolicySpec{TypeID: policyTypeID},
			script:    []vcfamock.Action{{Status: http.StatusAccepted}},
			configure: func(cfg *vcfapolicy.Config) { cfg.MaxAttempts = 4 },
			check: func(t *testing.T, e *env) {
				var apiErr *vcfapolicy.APIError
				if !errors.As(e.err, &apiErr) {
					t.Fatalf("error = %v (%T), want *vcfapolicy.APIError", e.err, e.err)
				}
				if apiErr.StatusCode != http.StatusAccepted {
					t.Errorf("StatusCode = %d, want 202", apiErr.StatusCode)
				}
				wantOperations(t, e, "upsertPolicy")
			},
		},
		{
			name: "refuses a spec with no policy type before sending anything",
			spec: vcfapolicy.PolicySpec{Name: "lease-cap-nonprod"},
			check: func(t *testing.T, e *env) {
				if e.err == nil {
					t.Fatalf("EnsurePolicy succeeded without a policy type")
				}
				if entries := e.mock.Requests(); len(entries) != 0 {
					t.Errorf("sent %d request(s) for an invalid spec", len(entries))
				}
				if minted := e.minter.all(); len(minted) != 0 {
					t.Errorf("minted %v for an invalid spec", minted)
				}
			},
		},
		{
			name: "refuses to deliver without a minted policy id",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			configure: func(cfg *vcfapolicy.Config) {
				cfg.NewPolicyID = func() string { return "" }
			},
			check: func(t *testing.T, e *env) {
				if e.err == nil {
					t.Fatalf("EnsurePolicy delivered without a policy id")
				}
				if entries := e.mock.Requests(); len(entries) != 0 {
					t.Errorf("sent %d request(s) without a policy id", len(entries))
				}
				if e.result.Attempts != 0 {
					t.Errorf("Attempts = %d, want 0 before any delivery", e.result.Attempts)
				}
			},
		},
		{
			name: "stops on a cancelled context",
			spec: vcfapolicy.PolicySpec{TypeID: policyTypeID},
			ctx: func(t *testing.T) context.Context {
				ctx, cancel := context.WithCancel(context.Background())
				cancel()
				return ctx
			},
			check: func(t *testing.T, e *env) {
				if !errors.Is(e.err, context.Canceled) {
					t.Fatalf("error = %v, want it to wrap context.Canceled", e.err)
				}
				if entries := e.mock.Requests(); len(entries) != 0 {
					t.Errorf("sent %d request(s) under a cancelled context", len(entries))
				}
			},
		},
	}

	for _, testCase := range cases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			e := run(t, testCase)
			testCase.check(t, e)
			checkEveryRequest(t, e)
		})
	}
}

// TestEnsurePolicyStopsWhileWaitingToRetry cancels the context between
// deliveries; the client must not deliver again.
func TestEnsurePolicyStopsWhileWaitingToRetry(t *testing.T) {
	t.Parallel()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	mock := vcfamock.New(t, contractPath, vcfamock.Action{Status: 503, After: cancel})
	ids := &minter{}
	client, err := vcfapolicy.New(vcfapolicy.Config{
		BaseURL:     mock.URL(),
		Token:       bearerToken,
		HTTPClient:  loopbackClient(),
		MaxAttempts: 4,
		RetryDelay:  func(int) time.Duration { return time.Hour },
		NewPolicyID: ids.next,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	result, err := client.EnsurePolicy(ctx, vcfapolicy.PolicySpec{TypeID: policyTypeID})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want it to wrap context.Canceled", err)
	}
	if result.Attempts != 1 {
		t.Errorf("Attempts = %d, want 1", result.Attempts)
	}
	e := &env{mock: mock, minter: ids, result: result, err: err}
	wantOperations(t, e, "upsertPolicy")
	checkEveryRequest(t, e)
}

// TestEnsurePolicyConcurrent exercises the defaults - a client of its own and
// randomly minted ids - from many goroutines at once.
func TestEnsurePolicyConcurrent(t *testing.T) {
	t.Parallel()

	const workers = 12
	mock := vcfamock.New(t, contractPath)
	client, err := vcfapolicy.New(vcfapolicy.Config{BaseURL: mock.URL(), Token: bearerToken})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	results := make([]vcfapolicy.Result, workers)
	errs := make([]error, workers)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			results[i], errs[i] = client.EnsurePolicy(context.Background(), vcfapolicy.PolicySpec{
				TypeID: policyTypeID,
				Name:   fmt.Sprintf("lease-cap-%02d", i),
			})
		}(i)
	}
	wg.Wait()

	seen := map[string]bool{}
	for i, result := range results {
		if errs[i] != nil {
			t.Fatalf("worker %d: %v", i, errs[i])
		}
		if result.Outcome != vcfapolicy.OutcomeCreated {
			t.Errorf("worker %d Outcome = %q, want created", i, result.Outcome)
		}
		if !uuidV4.MatchString(result.PolicyID) {
			t.Errorf("worker %d PolicyID = %q, want a random version 4 UUID", i, result.PolicyID)
		}
		if seen[result.PolicyID] {
			t.Errorf("worker %d reused policy id %q", i, result.PolicyID)
		}
		seen[result.PolicyID] = true
	}
	if stored := mock.Policies(); len(stored) != workers {
		t.Errorf("stored %d policies, want %d", len(stored), workers)
	}
	checkEveryRequest(t, &env{mock: mock, minter: &minter{}})
}

// TestNewRejectsIncompleteConfig covers the configuration the client cannot
// work without.
func TestNewRejectsIncompleteConfig(t *testing.T) {
	t.Parallel()

	for _, testCase := range []struct {
		name string
		cfg  vcfapolicy.Config
	}{
		{"no base URL", vcfapolicy.Config{Token: bearerToken}},
		{"no token", vcfapolicy.Config{BaseURL: "https://automation.example.com"}},
		{"neither", vcfapolicy.Config{}},
	} {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			if _, err := vcfapolicy.New(testCase.cfg); err == nil {
				t.Fatalf("New(%+v) succeeded, want an error", testCase.cfg)
			}
		})
	}
}

// TestMockServesOnlyContractOperations proves the loopback service is pinned to
// docs/contract.json and answers nothing it does not name.
func TestMockServesOnlyContractOperations(t *testing.T) {
	t.Parallel()

	mock := vcfamock.New(t, contractPath)
	client := loopbackClient()
	for _, path := range []string{
		"/policy/api/policies?$top=10",
		"/catalog/api/items",
		"/deployment/api/deployments",
		"/policy/api/policyTypes",
	} {
		response, err := client.Get(mock.URL() + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Errorf("GET %s = %d, want 404 from an operation the contract does not name",
				path, response.StatusCode)
		}
	}
	for _, entry := range mock.Requests() {
		if !entry.Rejected {
			t.Errorf("%s %s was served as %q, want it rejected", entry.Method, entry.Path, entry.Operation)
		}
	}
}

func run(t *testing.T, testCase testCase) *env {
	t.Helper()

	mock := vcfamock.New(t, contractPath, testCase.script...)
	if testCase.seed != nil {
		mock.Seed(testCase.seed)
	}
	ids := &minter{}
	cfg := vcfapolicy.Config{
		BaseURL:     mock.URL(),
		Token:       bearerToken,
		HTTPClient:  loopbackClient(),
		MaxAttempts: 4,
		RetryDelay:  func(int) time.Duration { return 0 },
		NewPolicyID: ids.next,
	}
	if testCase.configure != nil {
		testCase.configure(&cfg)
	}
	client, err := vcfapolicy.New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	ctx := context.Background()
	if testCase.ctx != nil {
		ctx = testCase.ctx(t)
	}
	result, err := client.EnsurePolicy(ctx, testCase.spec)
	return &env{mock: mock, minter: ids, result: result, err: err}
}

// loopbackClient never reuses a connection, so a dropped connection is a
// dropped delivery rather than something the transport quietly replays.
func loopbackClient() *http.Client {
	return &http.Client{Transport: &http.Transport{DisableKeepAlives: true}}
}

func wantOperations(t *testing.T, e *env, want ...string) {
	t.Helper()
	got := e.mock.Operations()
	if !reflect.DeepEqual(got, want) {
		t.Errorf("operations = %v, want %v", got, want)
	}
}

func wantStoredCount(t *testing.T, e *env, want int) {
	t.Helper()
	if stored := e.mock.Policies(); len(stored) != want {
		t.Errorf("stored %d policies %v, want %d", len(stored), stored, want)
	}
}

// checkEveryRequest applies the contract's wire rules to every request the
// mock saw, whatever the case was exercising.
func checkEveryRequest(t *testing.T, e *env) {
	t.Helper()
	for i, entry := range e.mock.Requests() {
		if entry.Rejected {
			t.Errorf("request %d: %s %s matches no operation in the contract",
				i, entry.Method, entry.Path)
			continue
		}
		if got := entry.Header.Get("Authorization"); got != "Bearer "+bearerToken {
			t.Errorf("request %d (%s): Authorization = %q, want %q",
				i, entry.Operation, got, "Bearer "+bearerToken)
		}
		if entry.RawQuery != "" {
			t.Errorf("request %d (%s): query = %q, want none; an unrequested query parameter is absent",
				i, entry.Operation, entry.RawQuery)
		}
		if values := entry.Header.Values("Accept"); len(values) != 0 {
			t.Errorf("request %d (%s): sent Accept: %v, which the contract does not name",
				i, entry.Operation, values)
		}
		allowedHeaders := map[string]bool{
			"Authorization":   true,
			"User-Agent":      true, // supplied by net/http when the client leaves it unset
			"Accept-Encoding": true, // supplied by net/http's default compression support
			"Connection":      true, // supplied by the test transport when keep-alives are disabled
			"Content-Length":  true, // supplied by net/http for any body whose length it knows
		}
		if entry.Operation == "upsertPolicy" {
			allowedHeaders["Content-Type"] = true
		}
		for name, values := range entry.Header {
			if !allowedHeaders[name] {
				t.Errorf("request %d (%s): sent undocumented header %s: %v",
					i, entry.Operation, name, values)
			}
		}
		if got := entry.Header.Get("User-Agent"); got != "" && got != "Go-http-client/1.1" {
			t.Errorf("request %d (%s): set User-Agent to %q", i, entry.Operation, got)
		}
		if got := entry.Header.Get("Accept-Encoding"); got != "" && got != "gzip" {
			t.Errorf("request %d (%s): set Accept-Encoding to %q", i, entry.Operation, got)
		}
		switch entry.Operation {
		case "upsertPolicy":
			if got := entry.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("request %d: Content-Type = %q, want application/json", i, got)
			}
		case "getPolicy":
			if got := entry.Header.Get("Content-Type"); got != "" {
				t.Errorf("request %d: read sent Content-Type %q; it has no body", i, got)
			}
			if len(entry.Body) != 0 {
				t.Errorf("request %d: read sent a body %q", i, entry.Body)
			}
		}
	}
}
