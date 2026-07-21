package integration_test

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"example.com/go-port-race-test/internal/testserver"
	"example.com/go-port-race-test/service"
)

func TestServerUsesInjectedListener(t *testing.T) {
	for i := 0; i < 4; i++ {
		i := i
		t.Run(fmt.Sprintf("instance-%d", i), func(t *testing.T) {
			t.Parallel()

			reserved, err := net.Listen("tcp4", "127.0.0.1:0")
			if err != nil {
				t.Fatalf("reserve loopback address: %v", err)
			}
			listener := newHandoffListener(t, reserved)
			t.Cleanup(listener.cleanup)

			wantAddress := reserved.Addr().String()
			instance := fmt.Sprintf("worker-%d", i)
			running, err := testserver.Start(listener, service.Handler(instance))
			if err != nil {
				t.Fatalf("Start() with reserved listener: %v", err)
			}
			t.Cleanup(func() {
				if err := running.Close(); err != nil {
					t.Errorf("close server: %v", err)
				}
			})

			if got := running.Addr(); got != wantAddress {
				t.Fatalf("Addr() = %q, want exact injected address %q", got, wantAddress)
			}

			transport := &http.Transport{DisableKeepAlives: true}
			client := &http.Client{Transport: transport, Timeout: 5 * time.Second}
			t.Cleanup(client.CloseIdleConnections)

			response, err := client.Get("http://" + wantAddress + "/identity")
			if err != nil {
				t.Fatalf("GET injected listener address: %v", err)
			}
			t.Cleanup(func() { _ = response.Body.Close() })
			body, err := io.ReadAll(response.Body)
			if err != nil {
				t.Fatalf("read response: %v", err)
			}
			if response.StatusCode != http.StatusOK {
				t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusOK)
			}
			if got := string(body); got != instance {
				t.Fatalf("body = %q, want %q", got, instance)
			}
		})
	}
}

// handoffListener models a process waiting for a test to drop its reservation.
// Closing the injected listener before its first Accept immediately hands the
// address to that process, turning the otherwise timing-dependent close/rebind
// window into a deterministic integration test.
type handoffListener struct {
	net.Listener

	network string
	address string
	once    sync.Once

	mu         sync.Mutex
	competitor net.Listener
	closeErr   error
	accepted   atomic.Bool
}

func newHandoffListener(t *testing.T, listener net.Listener) *handoffListener {
	t.Helper()
	return &handoffListener{
		Listener: listener,
		network:  listener.Addr().Network(),
		address:  listener.Addr().String(),
	}
}

func (l *handoffListener) Accept() (net.Conn, error) {
	l.accepted.Store(true)
	return l.Listener.Accept()
}

func (l *handoffListener) Close() error {
	l.once.Do(func() {
		if err := l.Listener.Close(); err != nil {
			l.closeErr = err
			return
		}
		if l.accepted.Load() {
			return
		}
		competitor, err := net.Listen(l.network, l.address)
		if err != nil {
			l.closeErr = fmt.Errorf("competitor claim %s: %w", l.address, err)
			return
		}
		l.mu.Lock()
		l.competitor = competitor
		l.mu.Unlock()
	})
	return l.closeErr
}

func (l *handoffListener) cleanup() {
	_ = l.Close()
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.competitor != nil {
		_ = l.competitor.Close()
		l.competitor = nil
	}
}
