package fetch

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"testing"
	"time"
)

func TestFastLookupSucceeds(t *testing.T) {
	c := New(func(id string) (Profile, error) {
		return Profile{ID: id, DisplayName: "Ada", Email: "ada@corp.example"}, nil
	})
	p, err := c.Get(context.Background(), "u-100")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if p.ID != "u-100" || p.DisplayName != "Ada" {
		t.Fatalf("wrong profile: %+v", p)
	}
}

func TestBackendErrorPropagates(t *testing.T) {
	backendErr := errors.New("directory shard offline")
	c := New(func(id string) (Profile, error) {
		return Profile{}, backendErr
	})
	_, err := c.Get(context.Background(), "u-1")
	if !errors.Is(err, backendErr) {
		t.Fatalf("err = %v, want the backend error", err)
	}
}

func TestExpiredContextReturnsImmediately(t *testing.T) {
	block := make(chan struct{})
	defer close(block)
	c := New(func(id string) (Profile, error) {
		<-block
		return Profile{ID: id}, nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	done := make(chan error, 1)
	go func() {
		_, err := c.Get(ctx, "u-2")
		done <- err
	}()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("err = %v, want context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Get did not return for an already-cancelled context")
	}
}

func TestAbandonedLookupsDoNotAccumulate(t *testing.T) {
	const calls = 25
	release := make(chan struct{})
	c := New(func(id string) (Profile, error) {
		<-release // simulate the slow backend the caller gave up on
		return Profile{ID: id}, nil
	})
	before := runtime.NumGoroutine()
	for i := 0; i < calls; i++ {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if _, err := c.Get(ctx, fmt.Sprintf("u-%d", i)); err == nil {
			t.Fatal("expected an error when the context is already cancelled")
		}
	}
	close(release) // backend finally answers all of them
	deadline := time.Now().Add(3 * time.Second)
	for {
		extra := runtime.NumGoroutine() - before
		if extra <= 0 {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("%d background lookups are still running long after the backend answered", extra)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
