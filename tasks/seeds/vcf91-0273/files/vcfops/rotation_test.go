package vcfops

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// authenticated returns a mock plus a client already holding a first token.
func authenticated(t *testing.T) (*mockServer, *Client) {
	t.Helper()
	m := newMockServer(t)
	m.addUser("svc-ops", "pw-v1", "")

	client := NewClient(m.URL(), m.srv.Client())
	ctx, cancel := waitCtx(t)
	defer cancel()
	if err := client.Authenticate(ctx, Credentials{Username: "svc-ops", Password: "pw-v1"}); err != nil {
		t.Fatalf("Authenticate: %v (request log: %s)", err, m.summary())
	}
	return m, client
}

// TestRotateDoesNotReleaseWhileRequestIsInFlight is the core assertion: while a
// request that captured the old token is still executing, Rotate must not
// release that token.
//
// Rotate is cancelled only after the test observes that the replacement token is
// active. Whatever Rotate did before returning is then exact: an implementation
// that releases early has a releaseToken in the log, and one that drains
// correctly has none.
func TestRotateDoesNotReleaseWhileRequestIsInFlight(t *testing.T) {
	m, client := authenticated(t)

	arrived, release := m.holdNextCurrentUser()

	var (
		userErr  error
		userDone = make(chan struct{})
	)
	go func() {
		defer close(userDone)
		_, userErr = client.CurrentUser(context.Background())
	}()
	<-arrived // the request is now executing inside the mock, holding the old token

	m.addUser("svc-ops", "pw-v2", "")
	rotCtx, rotCancel := context.WithCancel(context.Background())
	var (
		rotErr  error
		rotDone = make(chan struct{})
	)
	go func() {
		defer close(rotDone)
		rotErr = client.Rotate(rotCtx, Credentials{Username: "svc-ops", Password: "pw-v2"})
	}()

	issued := m.waitForIssuedTokens(t, 2)
	waitForActiveToken(t, client, issued[1])

	if got := m.recordsFor("releaseToken"); len(got) != 0 {
		t.Errorf("Rotate released the retired token while a request was still using it; "+
			"the in-flight request must drain first. Request log: %s", m.summary())
	}
	if len(m.recordsFor("acquireToken")) != 2 {
		t.Errorf("Rotate should have acquired a token for the new credentials before draining: %s", m.summary())
	}
	rotCancel()
	<-rotDone
	if !errors.Is(rotErr, context.Canceled) {
		t.Errorf("Rotate = %v, want context.Canceled: it must wait for the retired "+
			"generation to drain, and must not release the retired token when that wait is cut short", rotErr)
	}

	release()
	<-userDone
	if userErr != nil {
		t.Errorf("in-flight request failed: %v (request log: %s)", userErr, m.summary())
	}
	assertNoStrandings(t, m)
}

// TestCloseDrainsThenReleasesActiveToken applies the same retirement rule to
// Close: it must stop new captures immediately, wait for callers already using
// the active token, and release that token only after they finish.
func TestCloseDrainsThenReleasesActiveToken(t *testing.T) {
	m, client := authenticated(t)
	issued := m.issuedTokens()

	arrived, release := m.holdNextCurrentUser()
	var (
		userErr  error
		userDone = make(chan struct{})
	)
	go func() {
		defer close(userDone)
		_, userErr = client.CurrentUser(context.Background())
	}()
	<-arrived

	var (
		closeErr  error
		closeDone = make(chan struct{})
	)
	go func() {
		defer close(closeDone)
		ctx, cancel := waitCtx(t)
		defer cancel()
		closeErr = client.Close(ctx)
	}()
	waitForClosed(t, client)

	if got := m.recordsFor("releaseToken"); len(got) != 0 {
		t.Errorf("Close released the active token while a request was still using it: %s", m.summary())
	}
	if _, err := client.CurrentUser(context.Background()); !errors.Is(err, ErrClosed) {
		t.Errorf("CurrentUser during Close = %v, want ErrClosed", err)
	}

	release()
	<-userDone
	<-closeDone
	if userErr != nil {
		t.Errorf("in-flight request was stranded by Close: %v (request log: %s)", userErr, m.summary())
	}
	if closeErr != nil {
		t.Fatalf("Close: %v (request log: %s)", closeErr, m.summary())
	}
	releases := m.recordsFor("releaseToken")
	if len(releases) != 1 || releases[0].Token != issued[0] {
		t.Errorf("Close releases = %+v, want exactly active token %q: %s", releases, issued[0], m.summary())
	}
	assertNoStrandings(t, m)
}

// TestCloseCancellationLeavesActiveTokenAlive ensures cancellation during the
// drain never strands the request. The client remains closed and deliberately
// leaks the token, matching Rotate's cancellation safety rule.
func TestCloseCancellationLeavesActiveTokenAlive(t *testing.T) {
	m, client := authenticated(t)

	arrived, release := m.holdNextCurrentUser()
	var (
		userErr  error
		userDone = make(chan struct{})
	)
	go func() {
		defer close(userDone)
		_, userErr = client.CurrentUser(context.Background())
	}()
	<-arrived

	closeCtx, cancelClose := context.WithCancel(context.Background())
	var (
		closeErr  error
		closeDone = make(chan struct{})
	)
	go func() {
		defer close(closeDone)
		closeErr = client.Close(closeCtx)
	}()
	waitForClosed(t, client)
	cancelClose()
	<-closeDone

	if !errors.Is(closeErr, context.Canceled) {
		t.Errorf("Close = %v, want context.Canceled", closeErr)
	}
	if got := m.recordsFor("releaseToken"); len(got) != 0 {
		t.Errorf("cancelled Close released the token before its request drained: %s", m.summary())
	}

	release()
	<-userDone
	if userErr != nil {
		t.Errorf("cancelled Close stranded its in-flight request: %v (request log: %s)", userErr, m.summary())
	}
	if got := m.recordsFor("releaseToken"); len(got) != 0 {
		t.Errorf("cancelled Close must leave the token alive, got releases: %s", m.summary())
	}
	if _, err := client.CurrentUser(context.Background()); !errors.Is(err, ErrClosed) {
		t.Errorf("CurrentUser after Close = %v, want ErrClosed", err)
	}
	assertNoStrandings(t, m)
}

// TestRotateDrainsThenReleasesRetiredToken covers the whole rotation: the
// in-flight request completes on the old token, and only then is the old token
// released.
func TestRotateDrainsThenReleasesRetiredToken(t *testing.T) {
	m, client := authenticated(t)

	arrived, release := m.holdNextCurrentUser()

	var (
		user     *User
		userErr  error
		userDone = make(chan struct{})
	)
	go func() {
		defer close(userDone)
		user, userErr = client.CurrentUser(context.Background())
	}()
	<-arrived

	m.addUser("svc-ops", "pw-v2", "")
	var (
		rotErr  error
		rotDone = make(chan struct{})
	)
	go func() {
		defer close(rotDone)
		ctx, cancel := waitCtx(t)
		defer cancel()
		rotErr = client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "pw-v2"})
	}()

	// Rotate has acquired the replacement token and can only be draining now.
	m.waitForRecord(t, "the replacement token to be acquired", func(r requestRecord) bool {
		return r.OperationID == "acquireToken" && r.Seq > 1
	})

	release()
	<-userDone
	<-rotDone

	if userErr != nil {
		t.Fatalf("in-flight request was stranded by the rotation: %v (request log: %s)", userErr, m.summary())
	}
	if user == nil || user.Username != "svc-ops" {
		t.Errorf("in-flight request returned %+v, want the svc-ops user", user)
	}
	if rotErr != nil {
		t.Fatalf("Rotate: %v (request log: %s)", rotErr, m.summary())
	}
	assertNoStrandings(t, m)

	issued := m.issuedTokens()
	if len(issued) != 2 {
		t.Fatalf("mock issued %d tokens, want 2: %s", len(issued), m.summary())
	}

	releases := m.recordsFor("releaseToken")
	if len(releases) != 1 {
		t.Fatalf("got %d releaseToken requests, want exactly 1 (the retired token): %s", len(releases), m.summary())
	}
	if releases[0].Token != issued[0] {
		t.Errorf("released token %q, want the retired token %q", releases[0].Token, issued[0])
	}

	inFlight := m.recordsFor("getCurrentUser")
	if len(inFlight) != 1 {
		t.Fatalf("got %d getCurrentUser requests, want 1: %s", len(inFlight), m.summary())
	}
	if releases[0].Seq < inFlight[0].Seq {
		t.Errorf("releaseToken completed (#%d) before the in-flight request it had to wait for (#%d): %s",
			releases[0].Seq, inFlight[0].Seq, m.summary())
	}
}

// TestNewRequestsProceedDuringDrain checks that the drain only holds up the
// rotation, not the callers: a request issued while Rotate is waiting must go
// through rather than queue behind it.
func TestNewRequestsProceedDuringDrain(t *testing.T) {
	m, client := authenticated(t)

	arrived, release := m.holdNextCurrentUser()

	var (
		heldErr  error
		heldDone = make(chan struct{})
	)
	go func() {
		defer close(heldDone)
		_, heldErr = client.CurrentUser(context.Background())
	}()
	<-arrived

	m.addUser("svc-ops", "pw-v2", "")
	var (
		rotErr  error
		rotDone = make(chan struct{})
	)
	go func() {
		defer close(rotDone)
		ctx, cancel := waitCtx(t)
		defer cancel()
		rotErr = client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "pw-v2"})
	}()

	m.waitForRecord(t, "the replacement token to be acquired", func(r requestRecord) bool {
		return r.OperationID == "acquireToken" && r.Seq > 1
	})

	// This caller arrives mid-drain. It must not block behind the rotation.
	// Which generation it lands on depends on how far Rotate has got, and either
	// is correct; what matters is that it completes.
	ctx, cancel := waitCtx(t)
	defer cancel()
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Fatalf("request issued while Rotate was draining did not go through: %v (request log: %s)", err, m.summary())
	}

	release()
	<-heldDone
	<-rotDone

	if heldErr != nil {
		t.Errorf("in-flight request was stranded: %v", heldErr)
	}
	if rotErr != nil {
		t.Errorf("Rotate: %v (request log: %s)", rotErr, m.summary())
	}
	assertNoStrandings(t, m)
}

// TestConcurrentRequestsAreNotSerialized guards against fixing the drain with a
// lock held across HTTP calls. The mock holds requests until several are
// executing at once, which a serialized client can never satisfy.
func TestConcurrentRequestsAreNotSerialized(t *testing.T) {
	const concurrency = 4

	m, client := authenticated(t)
	serialized := m.expectConcurrentCurrentUsers(concurrency)

	var (
		wg   sync.WaitGroup
		mu   sync.Mutex
		errs []error
	)
	for range concurrency {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, cancel := waitCtx(t)
			defer cancel()
			if _, err := client.CurrentUser(ctx); err != nil {
				mu.Lock()
				errs = append(errs, err)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()

	if serialized() {
		t.Errorf("the mock never saw %d requests executing at once: the client serializes its "+
			"HTTP calls behind a single lock", concurrency)
	}
	for _, err := range errs {
		t.Errorf("concurrent request failed: %v", err)
	}
}

// TestRotateWithoutInFlightRequestsReleasesImmediately covers the ordinary case:
// nothing to drain, so the retired token goes away right after the swap.
func TestRotateWithoutInFlightRequestsReleasesImmediately(t *testing.T) {
	m, client := authenticated(t)

	ctx, cancel := waitCtx(t)
	defer cancel()
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Fatalf("CurrentUser: %v", err)
	}

	m.addUser("svc-ops", "pw-v2", "")
	if err := client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "pw-v2"}); err != nil {
		t.Fatalf("Rotate: %v (request log: %s)", err, m.summary())
	}

	issued := m.issuedTokens()
	releases := m.recordsFor("releaseToken")
	if len(releases) != 1 {
		t.Fatalf("got %d releaseToken requests, want 1: %s", len(releases), m.summary())
	}
	if releases[0].Token != issued[0] {
		t.Errorf("released %q, want the retired token %q", releases[0].Token, issued[0])
	}
	assertNoStrandings(t, m)

	// New callers are on the replacement generation.
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Fatalf("CurrentUser after rotation: %v", err)
	}
	users := m.recordsFor("getCurrentUser")
	if got, want := users[len(users)-1].Token, issued[1]; got != want {
		t.Errorf("request after rotation used token %q, want the replacement %q", got, want)
	}
}

// TestRotateFailureKeepsCurrentGeneration checks that a rotation that cannot
// authenticate leaves the working token alone.
func TestRotateFailureKeepsCurrentGeneration(t *testing.T) {
	m, client := authenticated(t)

	ctx, cancel := waitCtx(t)
	defer cancel()

	err := client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "wrong-password"})
	if err == nil {
		t.Fatalf("Rotate with bad credentials returned nil: %s", m.summary())
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 401 {
		t.Errorf("Rotate error = %v, want an *APIError with status 401", err)
	}
	if got := m.recordsFor("releaseToken"); len(got) != 0 {
		t.Errorf("a failed rotation released a token: %s", m.summary())
	}

	// The original generation still works.
	if _, err := client.CurrentUser(ctx); err != nil {
		t.Errorf("CurrentUser after a failed rotation: %v", err)
	}
	assertNoStrandings(t, m)
}

// TestConcurrentRotationsAndRequests runs rotations against a stream of requests
// under the race detector. No request may ever be stranded on a released token.
func TestConcurrentRotationsAndRequests(t *testing.T) {
	const (
		rotations = 5
		readers   = 6
	)

	m, client := authenticated(t)

	stop := make(chan struct{})
	var (
		wg  sync.WaitGroup
		mu  sync.Mutex
		bad []error
	)

	for range readers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				ctx, cancel := waitCtx(t)
				_, err := client.CurrentUser(ctx)
				cancel()
				if err != nil {
					mu.Lock()
					bad = append(bad, err)
					mu.Unlock()
					return
				}
			}
		}()
	}

	for i := range rotations {
		password := "pw-rot-" + string(rune('a'+i))
		m.addUser("svc-ops", password, "")
		ctx, cancel := waitCtx(t)
		err := client.Rotate(ctx, Credentials{Username: "svc-ops", Password: password})
		cancel()
		if err != nil {
			close(stop)
			wg.Wait()
			t.Fatalf("Rotate #%d: %v (request log: %s)", i+1, err, m.summary())
		}
	}
	close(stop)
	wg.Wait()

	for _, err := range bad {
		t.Errorf("request failed during concurrent rotation: %v", err)
	}
	assertNoStrandings(t, m)

	ctx, cancel := waitCtx(t)
	defer cancel()
	if err := client.Close(ctx); err != nil {
		t.Errorf("Close: %v", err)
	}
}

// TestFailedRequestStillReleasesItsGeneration checks the accounting on the error
// path: a request that captured a generation and then failed must still let go
// of it, or the next rotation waits on a drain that can never finish.
func TestFailedRequestStillReleasesItsGeneration(t *testing.T) {
	m, client := authenticated(t)

	arrived, release := m.holdNextCurrentUser()

	reqCtx, abort := context.WithCancel(context.Background())
	var (
		userErr  error
		userDone = make(chan struct{})
	)
	go func() {
		defer close(userDone)
		_, userErr = client.CurrentUser(reqCtx)
	}()
	<-arrived

	abort() // the caller walks away while the request is in flight
	<-userDone
	if userErr == nil {
		t.Fatalf("CurrentUser returned nil after its context was cancelled")
	}

	// Let the mock finish with the abandoned request so a later release cannot be
	// mistaken for one issued while a request was still executing.
	release()
	m.waitForNoRequestsInFlight(t)

	m.addUser("svc-ops", "pw-v2", "")
	ctx, cancel := waitCtx(t)
	defer cancel()
	if err := client.Rotate(ctx, Credentials{Username: "svc-ops", Password: "pw-v2"}); err != nil {
		t.Fatalf("Rotate after a failed request: %v; the failed request never released its "+
			"generation, so the drain could not finish (request log: %s)", err, m.summary())
	}

	issued := m.issuedTokens()
	releases := m.recordsFor("releaseToken")
	if len(releases) != 1 {
		t.Fatalf("got %d releaseToken requests, want 1: %s", len(releases), m.summary())
	}
	if releases[0].Token != issued[0] {
		t.Errorf("released %q, want the retired token %q", releases[0].Token, issued[0])
	}
	assertNoStrandings(t, m)
}

func assertNoStrandings(t *testing.T, m *mockServer) {
	t.Helper()
	for _, s := range m.strandingReports() {
		t.Errorf("the mock observed a stranded request [%s] on token %q: %s", s.Kind, s.Token, s.Detail)
	}
}

func waitForActiveToken(t *testing.T, client *Client, token string) {
	t.Helper()
	deadline := time.Now().Add(waitTimeout)
	for {
		client.mu.Lock()
		active := client.active
		got := ""
		if active != nil {
			got = active.token
		}
		client.mu.Unlock()
		if got == token {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for active token %q; current token is %q", token, got)
		}
		time.Sleep(time.Millisecond)
	}
}

func waitForClosed(t *testing.T, client *Client) {
	t.Helper()
	deadline := time.Now().Add(waitTimeout)
	for {
		client.mu.Lock()
		closed := client.closed
		client.mu.Unlock()
		if closed {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("timed out waiting for Close to retire the active generation")
		}
		time.Sleep(time.Millisecond)
	}
}
