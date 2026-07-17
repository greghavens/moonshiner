package registry

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

func TestSnapshotIsIsolatedFromLaterUpdates(t *testing.T) {
	r := New()
	r.Update("auth", Status{State: "up", Detail: "2 replicas"})
	snap := r.Snapshot()
	r.Update("auth", Status{State: "down", Detail: "probe timeout"})
	r.Update("billing", Status{State: "up"})
	if len(snap) != 1 {
		t.Fatalf("snapshot grew after later updates: %v", snap)
	}
	if got := snap["auth"]; got.State != "up" || got.Detail != "2 replicas" {
		t.Fatalf("snapshot changed after a later update: %+v", got)
	}
}

func TestSnapshotEditsDoNotTouchRegistry(t *testing.T) {
	r := New()
	r.Update("auth", Status{State: "up"})
	snap := r.Snapshot()
	snap["auth"] = Status{State: "corrupted"}
	snap["ghost"] = Status{State: "up"}
	if got, ok := r.Get("auth"); !ok || got.State != "up" {
		t.Fatalf("registry saw a snapshot edit: %+v", got)
	}
	if _, ok := r.Get("ghost"); ok {
		t.Fatal("registry saw a key added to a snapshot")
	}
}

func TestUpdateVisibility(t *testing.T) {
	r := New()
	r.Update("queue", Status{State: "up", Detail: "depth 3"})
	r.Update("queue", Status{State: "degraded", Detail: "depth 9000"})
	got, ok := r.Get("queue")
	if !ok || got.State != "degraded" || got.Detail != "depth 9000" {
		t.Fatalf("Get after update = %+v, %v", got, ok)
	}
	snap := r.Snapshot()
	if got := snap["queue"]; got.State != "degraded" {
		t.Fatalf("fresh snapshot missed the latest update: %+v", got)
	}
}

func TestWatchersRunInOrderWithPayload(t *testing.T) {
	r := New()
	var calls []string
	r.Watch(func(name string, s Status) { calls = append(calls, "first:"+name+"="+s.State) })
	r.Watch(func(name string, s Status) { calls = append(calls, "second:"+name+"="+s.State) })
	r.Update("auth", Status{State: "up"})
	r.Update("auth", Status{State: "down"})
	want := []string{"first:auth=up", "second:auth=up", "first:auth=down", "second:auth=down"}
	if len(calls) != len(want) {
		t.Fatalf("watcher calls = %v, want %v", calls, want)
	}
	for i := range want {
		if calls[i] != want[i] {
			t.Fatalf("watcher calls = %v, want %v", calls, want)
		}
	}
}

func TestWatcherMayReadRegistry(t *testing.T) {
	r := New()
	seen := make(chan string, 1)
	r.Watch(func(name string, s Status) {
		if got, ok := r.Get(name); ok {
			seen <- name + "=" + got.State
		} else {
			seen <- name + "=missing"
		}
	})
	done := make(chan struct{})
	go func() {
		r.Update("auth", Status{State: "up"})
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Update never returned while a watcher read the registry")
	}
	if got := <-seen; got != "auth=up" {
		t.Fatalf("watcher observed %q, want %q", got, "auth=up")
	}
}

func TestConcurrentSnapshotsAndUpdates(t *testing.T) {
	r := New()
	for i := 0; i < 8; i++ {
		r.Update(fmt.Sprintf("svc-%d", i), Status{State: "up"})
	}
	start := make(chan struct{})
	var wg sync.WaitGroup
	for u := 0; u < 4; u++ {
		wg.Add(1)
		go func(u int) {
			defer wg.Done()
			<-start
			for i := 0; i < 200; i++ {
				name := fmt.Sprintf("svc-%d", (u+i)%8)
				r.Update(name, Status{State: "up", Detail: fmt.Sprintf("round %d", i)})
			}
		}(u)
	}
	for s := 0; s < 4; s++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			for i := 0; i < 200; i++ {
				snap := r.Snapshot()
				total := 0
				for _, st := range snap {
					if st.State != "" {
						total++
					}
				}
				if total > 8 {
					t.Errorf("snapshot has %d services, max is 8", total)
					return
				}
			}
		}()
	}
	close(start)
	wg.Wait()
}
