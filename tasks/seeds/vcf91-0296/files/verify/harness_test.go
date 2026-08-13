// This file is part of the protected harness. Do not edit it.
package verify

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"vcfopsnetinv/internal/opsnet"
	"vcfopsnetinv/internal/opsnetmock"
)

// baseTime anchors the fake clock. Nothing about the suite depends on the wall
// clock, so a token expiry computed from it is reproducible.
var baseTime = time.Date(2026, time.May, 20, 9, 0, 0, 0, time.UTC)

// fakeClock advances by a fixed step once per completed HTTP round trip. That
// makes "how much time has passed" a function of how many requests the client
// has made, rather than of how fast the test machine is.
type fakeClock struct {
	mu    sync.Mutex
	base  time.Time
	step  time.Duration
	ticks int
}

func newFakeClock(base time.Time, step time.Duration) *fakeClock {
	return &fakeClock{base: base, step: step}
}

func (c *fakeClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.base.Add(time.Duration(c.ticks) * c.step)
}

func (c *fakeClock) tick() {
	c.mu.Lock()
	c.ticks++
	c.mu.Unlock()
}

// tickingTransport advances the fake clock after every round trip.
type tickingTransport struct {
	base  http.RoundTripper
	clock *fakeClock
}

func (t *tickingTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	resp, err := t.base.RoundTrip(r)
	t.clock.tick()
	return resp, err
}

func newHTTPClient(clock *fakeClock) *http.Client {
	return &http.Client{Transport: &tickingTransport{base: http.DefaultTransport, clock: clock}}
}

// --- log helpers ---------------------------------------------------------

func opsOf(log []opsnetmock.Entry) []string {
	out := make([]string, len(log))
	for i, e := range log {
		out[i] = e.OperationID
	}
	return out
}

func entriesFor(log []opsnetmock.Entry, opID string) []opsnetmock.Entry {
	var out []opsnetmock.Entry
	for _, e := range log {
		if e.OperationID == opID {
			out = append(out, e)
		}
	}
	return out
}

// runLengths collapses an operation sequence into "op xN" segments so a test
// failure reads as a shape rather than a wall of repeated names.
func runLengths(ops []string) []string {
	var out []string
	for i := 0; i < len(ops); {
		j := i
		for j < len(ops) && ops[j] == ops[i] {
			j++
		}
		if j-i == 1 {
			out = append(out, ops[i])
		} else {
			out = append(out, ops[i]+" x"+itoa(j-i))
		}
		i = j
	}
	return out
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var digits []byte
	for n > 0 {
		digits = append([]byte{byte('0' + n%10)}, digits...)
		n /= 10
	}
	return string(digits)
}

// --- JSON body helpers ---------------------------------------------------

// jsonRaw is an alias so helpers can accept maps produced by decodeObject.
type jsonRaw = json.RawMessage

func unquote(raw json.RawMessage) (string, error) {
	var s string
	err := json.Unmarshal(raw, &s)
	return s, err
}

func decodeObject(t *testing.T, what string, body []byte) map[string]json.RawMessage {
	t.Helper()
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(body, &obj); err != nil {
		t.Fatalf("%s: body is not a JSON object: %v (body=%q)", what, err, string(body))
	}
	return obj
}

func sortedKeys(obj map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// assertKeys fails when the key set differs from want. This is how the suite
// checks that unset optional fields were omitted: a field that was sent with an
// empty value shows up as an extra key.
func assertKeys(t *testing.T, what string, got, want []string) {
	t.Helper()
	g, w := append([]string(nil), got...), append([]string(nil), want...)
	sort.Strings(g)
	sort.Strings(w)
	if strings.Join(g, ",") != strings.Join(w, ",") {
		t.Errorf("%s: keys present on the wire = [%s], want exactly [%s]",
			what, strings.Join(g, ","), strings.Join(w, ","))
	}
}

// assertNoEmptyStrings fails when any property of obj is the empty JSON string.
// The contract requires omission, not "".
func assertNoEmptyStrings(t *testing.T, what string, obj map[string]json.RawMessage) {
	t.Helper()
	for _, k := range sortedKeys(obj) {
		if string(obj[k]) == `""` {
			t.Errorf("%s: property %q was sent as \"\"; the contract requires unset optional fields to be omitted", what, k)
		}
	}
}

// --- expected shapes derived from the client configuration ---------------

// wantCreateBodyKeys is the exact UserCredential key set for these credentials.
func wantCreateBodyKeys(creds opsnet.Credentials) []string {
	keys := []string{"username", "password"}
	if creds.DomainType != "" {
		keys = append(keys, "domain")
	}
	return keys
}

// wantDomainKeys is the exact Domain key set for these credentials.
func wantDomainKeys(creds opsnet.Credentials) []string {
	keys := []string{"domain_type"}
	if creds.DomainValue != "" {
		keys = append(keys, "value")
	}
	return keys
}

// wantListQueryKeys is the exact listApplications query key set. modifiedAfter
// is never expected: this client never sets it.
func wantListQueryKeys(cfg opsnet.Config, firstPage bool) []string {
	var keys []string
	if cfg.PageSize > 0 {
		keys = append(keys, "size")
	}
	if !firstPage {
		keys = append(keys, "cursor")
	}
	return keys
}

// wantDetailQueryKeys is the exact getApplicationById query key set.
func wantDetailQueryKeys(cfg opsnet.Config) []string {
	var keys []string
	if cfg.FetchMemberCounts {
		keys = append(keys, "fetch_member_counts")
	}
	if cfg.FetchUpdateStatus {
		keys = append(keys, "fetch_update_status")
	}
	return keys
}
