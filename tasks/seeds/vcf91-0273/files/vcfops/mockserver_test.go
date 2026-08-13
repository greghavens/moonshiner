package vcfops

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// waitTimeout bounds every rendezvous in these tests. A correct implementation
// never approaches it; a serialized or deadlocked one fails here instead of
// hanging until the go test timeout.
const waitTimeout = 5 * time.Second

// requestRecord is one request the mock served, captured as it arrived on the
// wire. Seq is assigned when the handler finishes, so ordering between records
// reflects completion order.
type requestRecord struct {
	Seq         int
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
	Token       string // value after the contract token prefix, "" if absent
	Status      int
}

// jsonBody decodes the recorded request body into a key set plus values.
func (r requestRecord) jsonBody(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	out := map[string]json.RawMessage{}
	if err := json.Unmarshal(r.Body, &out); err != nil {
		t.Fatalf("%s: request body is not a JSON object: %v (body=%q)", r.OperationID, err, r.Body)
	}
	return out
}

// stranding is recorded when the mock observes a retired token being used, or
// released, while a request that captured it is still executing.
type stranding struct {
	Kind   string // "release-while-in-flight" or "request-on-released-token"
	Token  string
	Detail string
}

type mockServer struct {
	t      *testing.T
	srv    *mockHTTPServer
	routes map[string]string // "METHOD /suite-api/..." -> operationId
	prefix string            // contract token prefix

	mu         sync.Mutex
	seq        int
	log        []requestRecord
	creds      map[string]string // username -> password
	authSource map[string]string // username -> required authSource, "" if none
	tokens     map[string]string // token -> username
	revoked    map[string]bool
	inHandler  map[string]int // token -> requests currently executing in the mock
	issued     []string
	strandings []stranding
	nextToken  int

	// holdArrived is closed when the next getCurrentUser request arrives;
	// that request then blocks until holdRelease is closed.
	holdArrived chan struct{}
	holdRelease chan struct{}

	barrier *barrier
}

// newMockServer starts an in-process VCF Operations mock whose routes are built
// from docs/contract.json. It serves only the operations the contract names;
// anything else is a recorded 404.
func newMockServer(t *testing.T) *mockServer {
	t.Helper()
	c := loadContract(t)

	m := &mockServer{
		t:          t,
		routes:     map[string]string{},
		prefix:     c.Security.TokenPrefix,
		creds:      map[string]string{},
		authSource: map[string]string{},
		tokens:     map[string]string{},
		revoked:    map[string]bool{},
		inHandler:  map[string]int{},
	}
	for _, op := range c.Operations {
		m.routes[op.Method+" "+op.fullPath(c.BasePath)] = op.OperationID
	}

	m.srv = newMockHTTPServer(http.HandlerFunc(m.serve))
	t.Cleanup(m.srv.Close)
	return m
}

// URL is the loopback base URL, without the /suite-api base path.
func (m *mockServer) URL() string { return m.srv.URL }

// mockHTTPServer drives the handler through a real http.Client and
// http.RoundTripper without opening a listening socket. This keeps the fixture
// self-contained in sandboxes that prohibit network listeners while preserving
// request construction, transport cancellation, and handler concurrency.
type mockHTTPServer struct {
	URL    string
	client *http.Client
}

func newMockHTTPServer(handler http.Handler) *mockHTTPServer {
	return &mockHTTPServer{
		URL: "http://vcf-operations.test",
		client: &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			result := make(chan *http.Response, 1)
			go func() {
				recorder := httptest.NewRecorder()
				handler.ServeHTTP(recorder, req)
				resp := recorder.Result()
				resp.Request = req
				result <- resp
			}()

			select {
			case resp := <-result:
				return resp, nil
			case <-req.Context().Done():
				return nil, req.Context().Err()
			}
		})},
	}
}

func (s *mockHTTPServer) Client() *http.Client { return s.client }
func (s *mockHTTPServer) Close()               {}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

// addUser registers a credential the mock will accept. A non-empty source makes
// the acquireToken request valid only when it carries that authSource.
func (m *mockServer) addUser(username, password, source string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.creds[username] = password
	m.authSource[username] = source
}

func (m *mockServer) serve(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
		_ = r.Body.Close()
	}

	rec := requestRecord{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	}
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, m.prefix) {
		rec.Token = strings.TrimPrefix(auth, m.prefix)
	}

	operationID, ok := m.routes[r.Method+" "+r.URL.Path]
	if !ok {
		rec.OperationID = "<not-in-contract>"
		rec.Status = http.StatusNotFound
		m.finish(rec)
		http.Error(w, "not served by this contract", http.StatusNotFound)
		return
	}
	rec.OperationID = operationID

	switch operationID {
	case "acquireToken":
		m.handleAcquireToken(w, rec)
	case "getCurrentUser":
		m.handleGetCurrentUser(w, rec)
	case "releaseToken":
		m.handleReleaseToken(w, rec)
	}
}

func (m *mockServer) handleAcquireToken(w http.ResponseWriter, rec requestRecord) {
	var req struct {
		Username   *string `json:"username"`
		Password   *string `json:"password"`
		AuthSource *string `json:"authSource"`
	}
	if err := json.Unmarshal(rec.Body, &req); err != nil || req.Username == nil || req.Password == nil {
		m.deny(w, rec, http.StatusUnauthorized, "malformed username-password")
		return
	}

	m.mu.Lock()
	want, known := m.creds[*req.Username]
	wantSource := m.authSource[*req.Username]
	m.mu.Unlock()

	gotSource := ""
	if req.AuthSource != nil {
		gotSource = *req.AuthSource
	}
	if !known || want != *req.Password || wantSource != gotSource {
		m.deny(w, rec, http.StatusUnauthorized, "authentication failed")
		return
	}

	m.mu.Lock()
	m.nextToken++
	token := fmt.Sprintf("ops-token-%d", m.nextToken)
	m.tokens[token] = *req.Username
	m.issued = append(m.issued, token)
	m.mu.Unlock()

	rec.Status = http.StatusOK
	m.finish(rec)
	writeJSON(w, http.StatusOK, map[string]any{
		"token":     token,
		"validity":  int64(21600000),
		"expiresAt": "2026-05-13T14:19:58Z",
		"roles":     []string{"Administrator"},
	})
}

func (m *mockServer) handleGetCurrentUser(w http.ResponseWriter, rec requestRecord) {
	m.mu.Lock()
	username, live := m.tokens[rec.Token]
	revoked := m.revoked[rec.Token]
	if !live || revoked {
		m.mu.Unlock()
		if revoked {
			m.recordStranding(stranding{
				Kind:   "request-on-released-token",
				Token:  rec.Token,
				Detail: "getCurrentUser presented a token that had already been released",
			})
		}
		m.deny(w, rec, http.StatusUnauthorized, "token missing, unknown, or already released")
		return
	}
	m.inHandler[rec.Token]++
	arrived, release := m.holdArrived, m.holdRelease
	m.holdArrived, m.holdRelease = nil, nil
	b := m.barrier
	m.mu.Unlock()

	defer func() {
		m.mu.Lock()
		m.inHandler[rec.Token]--
		m.mu.Unlock()
	}()

	if release != nil {
		close(arrived)
		<-release
	}
	if b != nil {
		b.arrive()
	}

	// Re-check after the request was held: if the client released this token
	// while this request was executing, the request has been stranded.
	m.mu.Lock()
	stillRevoked := m.revoked[rec.Token]
	m.mu.Unlock()
	if stillRevoked {
		m.recordStranding(stranding{
			Kind:   "request-on-released-token",
			Token:  rec.Token,
			Detail: "in-flight getCurrentUser resumed after its token was released",
		})
		m.deny(w, rec, http.StatusUnauthorized, "token released while request was in flight")
		return
	}

	rec.Status = http.StatusOK
	m.finish(rec)
	writeJSON(w, http.StatusOK, map[string]any{
		"id":        "6f2a1b40-3f5e-4a2f-9a1e-000000000001",
		"username":  username,
		"firstName": "VCF",
		"lastName":  "Operator",
		"enabled":   true,
		"roleNames": []string{"Administrator"},
	})
}

func (m *mockServer) handleReleaseToken(w http.ResponseWriter, rec requestRecord) {
	m.mu.Lock()
	_, live := m.tokens[rec.Token]
	if !live || m.revoked[rec.Token] {
		m.mu.Unlock()
		m.deny(w, rec, http.StatusUnauthorized, "token missing, unknown, or already released")
		return
	}
	inFlight := m.inHandler[rec.Token]
	m.revoked[rec.Token] = true
	m.mu.Unlock()

	if inFlight > 0 {
		m.recordStranding(stranding{
			Kind:   "release-while-in-flight",
			Token:  rec.Token,
			Detail: fmt.Sprintf("releaseToken arrived while %d request(s) were still executing on this token", inFlight),
		})
	}

	rec.Status = http.StatusOK
	m.finish(rec)
	w.WriteHeader(http.StatusOK)
}

func (m *mockServer) deny(w http.ResponseWriter, rec requestRecord, status int, msg string) {
	rec.Status = status
	m.finish(rec)
	http.Error(w, msg, status)
}

// finish appends rec to the request log, assigning its completion sequence.
func (m *mockServer) finish(rec requestRecord) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	rec.Seq = m.seq
	m.log = append(m.log, rec)
}

func (m *mockServer) recordStranding(s stranding) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.strandings = append(m.strandings, s)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

// ---- test-facing accessors ----------------------------------------------

// records returns a copy of the request log in completion order.
func (m *mockServer) records() []requestRecord {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]requestRecord, len(m.log))
	copy(out, m.log)
	return out
}

// recordsFor returns the logged requests for one operationId.
func (m *mockServer) recordsFor(operationID string) []requestRecord {
	var out []requestRecord
	for _, r := range m.records() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

func (m *mockServer) strandingReports() []stranding {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]stranding, len(m.strandings))
	copy(out, m.strandings)
	return out
}

// issuedTokens returns every token acquireToken minted, in order.
func (m *mockServer) issuedTokens() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]string, len(m.issued))
	copy(out, m.issued)
	return out
}

func (m *mockServer) waitForIssuedTokens(t *testing.T, n int) []string {
	t.Helper()
	deadline := time.Now().Add(waitTimeout)
	for {
		issued := m.issuedTokens()
		if len(issued) >= n {
			return issued
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %d issued tokens; got %d: %s", n, len(issued), m.summary())
		}
		time.Sleep(time.Millisecond)
	}
}

// holdNextCurrentUser makes the next getCurrentUser request block inside the
// mock. The returned channel closes once that request has arrived; the returned
// func lets it finish.
func (m *mockServer) holdNextCurrentUser() (arrived <-chan struct{}, release func()) {
	a := make(chan struct{})
	r := make(chan struct{})
	m.mu.Lock()
	m.holdArrived, m.holdRelease = a, r
	m.mu.Unlock()

	var once sync.Once
	return a, func() { once.Do(func() { close(r) }) }
}

// expectConcurrentCurrentUsers makes getCurrentUser requests block until n of
// them are executing at once, so a client that serializes its HTTP calls cannot
// satisfy it. The returned func reports whether the mock gave up waiting.
func (m *mockServer) expectConcurrentCurrentUsers(n int) (serialized func() bool) {
	b := &barrier{n: n, ch: make(chan struct{})}
	m.mu.Lock()
	m.barrier = b
	m.mu.Unlock()
	return b.missed
}

// waitForRecord blocks until a logged request satisfies pred.
func (m *mockServer) waitForRecord(t *testing.T, what string, pred func(requestRecord) bool) requestRecord {
	t.Helper()
	deadline := time.Now().Add(waitTimeout)
	for {
		for _, r := range m.records() {
			if pred(r) {
				return r
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out after %s waiting for %s; request log so far: %s", waitTimeout, what, m.summary())
		}
		time.Sleep(time.Millisecond)
	}
}

// waitForNoRequestsInFlight blocks until no request is still executing inside
// the mock, so a test can tell the difference between "the client gave up" and
// "the server has finished with it".
func (m *mockServer) waitForNoRequestsInFlight(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(waitTimeout)
	for {
		m.mu.Lock()
		busy := 0
		for _, n := range m.inHandler {
			busy += n
		}
		m.mu.Unlock()
		if busy == 0 {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out after %s waiting for the mock to finish serving; request log: %s", waitTimeout, m.summary())
		}
		time.Sleep(time.Millisecond)
	}
}

// summary renders the request log for failure messages.
func (m *mockServer) summary() string {
	var b strings.Builder
	for _, r := range m.records() {
		fmt.Fprintf(&b, "\n  #%d %s %s -> %d token=%q body=%q", r.Seq, r.Method, r.Path, r.Status, r.Token, r.Body)
	}
	if b.Len() == 0 {
		return " (empty)"
	}
	return b.String()
}

// ---- helpers -------------------------------------------------------------

// barrierTimeout bounds how long the mock waits for concurrent requests to
// gather before giving up and letting them through, so a client that serializes
// its HTTP calls fails the assertion instead of hanging.
const barrierTimeout = 2 * time.Second

type barrier struct {
	n  int
	ch chan struct{}

	mu       sync.Mutex
	count    int
	closed   bool
	timedOut bool
}

func (b *barrier) open(timedOut bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		return
	}
	b.closed = true
	b.timedOut = timedOut
	close(b.ch)
}

// arrive blocks until b.n requests are executing at once, or until the barrier
// times out.
func (b *barrier) arrive() {
	b.mu.Lock()
	b.count++
	reached := b.count >= b.n
	b.mu.Unlock()

	if reached {
		b.open(false)
	}
	select {
	case <-b.ch:
	case <-time.After(barrierTimeout):
		b.open(true)
	}
}

// missed reports whether the barrier gave up before enough requests gathered.
func (b *barrier) missed() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.timedOut
}

// waitCtx returns a context bounded by waitTimeout.
func waitCtx(t *testing.T) (context.Context, context.CancelFunc) {
	t.Helper()
	return context.WithTimeout(context.Background(), waitTimeout)
}
