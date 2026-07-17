package hitstats

import (
	"fmt"
	"sync"
	"testing"
)

func TestSequentialCounting(t *testing.T) {
	s := New()
	s.Record("/api/users")
	s.Record("/api/users")
	s.Record("/healthz")
	if got := s.Total("/api/users"); got != 2 {
		t.Fatalf("Total(/api/users) = %d, want 2", got)
	}
	if got := s.Total("/api/orders"); got != 0 {
		t.Fatalf("Total(/api/orders) = %d, want 0", got)
	}
}

func TestConcurrentHandlers(t *testing.T) {
	s := New()
	routes := []string{"/api/users", "/api/orders", "/healthz", "/metrics"}
	const perRoute = 50
	var wg sync.WaitGroup
	for _, route := range routes {
		for i := 0; i < perRoute; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				s.Record(route)
			}()
		}
	}
	wg.Wait()
	for _, route := range routes {
		if got := s.Total(route); got != perRoute {
			t.Fatalf("Total(%s) = %d, want %d", route, got, perRoute)
		}
	}
}

func TestSnapshotWhileTrafficFlows(t *testing.T) {
	s := New()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			route := fmt.Sprintf("/api/v%d", i%2)
			for j := 0; j < 100; j++ {
				s.Record(route)
			}
		}(i)
	}
	snapDone := make(chan struct{})
	go func() {
		defer close(snapDone)
		for i := 0; i < 50; i++ {
			s.Snapshot()
		}
	}()
	wg.Wait()
	<-snapDone
	snap := s.Snapshot()
	if snap["/api/v0"] != 400 || snap["/api/v1"] != 400 {
		t.Fatalf("final snapshot = %v, want 400 hits on each route", snap)
	}
}

func TestResetClearsCounters(t *testing.T) {
	s := New()
	s.Record("/api/users")
	s.Reset()
	if got := s.Total("/api/users"); got != 0 {
		t.Fatalf("Total after Reset = %d, want 0", got)
	}
}
