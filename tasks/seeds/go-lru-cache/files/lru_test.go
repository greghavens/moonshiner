package lru

import (
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
)

type eviction struct {
	key   string
	value int
}

func collectEvictions(evs *[]eviction, mu *sync.Mutex) func(string, int) {
	return func(k string, v int) {
		mu.Lock()
		*evs = append(*evs, eviction{k, v})
		mu.Unlock()
	}
}

func TestNewRejectsNonPositiveCapacity(t *testing.T) {
	if _, err := New[string, int](0, nil); err == nil {
		t.Fatal("capacity 0 accepted")
	}
	if _, err := New[string, int](-5, nil); err == nil {
		t.Fatal("negative capacity accepted")
	}
	if _, err := New[string, int](1, nil); err != nil {
		t.Fatalf("capacity 1 rejected: %v", err)
	}
}

func TestGetMissReturnsZeroValue(t *testing.T) {
	c, _ := New[string, int](4, nil)
	if v, ok := c.Get("absent"); ok || v != 0 {
		t.Fatalf("Get on empty cache = (%v, %v), want (0, false)", v, ok)
	}
}

func TestPutGetLen(t *testing.T) {
	c, _ := New[string, int](4, nil)
	c.Put("a", 1)
	c.Put("b", 2)
	if got := c.Len(); got != 2 {
		t.Fatalf("Len = %d, want 2", got)
	}
	if v, ok := c.Get("a"); !ok || v != 1 {
		t.Fatalf("Get(a) = (%v, %v), want (1, true)", v, ok)
	}
	if v, ok := c.Get("b"); !ok || v != 2 {
		t.Fatalf("Get(b) = (%v, %v), want (2, true)", v, ok)
	}
}

func TestEvictsLeastRecentlyUsedNotOldestInserted(t *testing.T) {
	var evs []eviction
	var mu sync.Mutex
	c, _ := New[string, int](2, collectEvictions(&evs, &mu))
	c.Put("a", 1)
	c.Put("b", 2)
	if _, ok := c.Get("a"); !ok { // "a" is now more recent than "b"
		t.Fatal("Get(a) missed")
	}
	c.Put("c", 3) // must displace "b", the least recently USED
	if _, ok := c.Get("b"); ok {
		t.Fatal("b should have been evicted; insertion-order eviction is wrong")
	}
	if _, ok := c.Get("a"); !ok {
		t.Fatal("a was evicted even though it was recently used")
	}
	c.Put("d", 4) // recency now: d > a > c, so "c" goes
	if _, ok := c.Get("c"); ok {
		t.Fatal("c should have been evicted after d was inserted")
	}
	mu.Lock()
	defer mu.Unlock()
	want := []eviction{{"b", 2}, {"c", 3}}
	if len(evs) != len(want) {
		t.Fatalf("evictions = %v, want %v", evs, want)
	}
	for i := range want {
		if evs[i] != want[i] {
			t.Fatalf("eviction %d = %v, want %v (callback must receive the displaced key AND its value, in order)", i, evs[i], want[i])
		}
	}
}

func TestOverwriteUpdatesValuePromotesAndDoesNotEvict(t *testing.T) {
	var evs []eviction
	var mu sync.Mutex
	c, _ := New[string, int](2, collectEvictions(&evs, &mu))
	c.Put("a", 1)
	c.Put("b", 2)
	c.Put("a", 9) // overwrite: no eviction, no callback, "a" becomes most recent
	mu.Lock()
	if len(evs) != 0 {
		t.Fatalf("overwriting an existing key fired the eviction callback: %v", evs)
	}
	mu.Unlock()
	if got := c.Len(); got != 2 {
		t.Fatalf("Len after overwrite = %d, want 2", got)
	}
	if v, _ := c.Get("a"); v != 9 {
		t.Fatalf("Get(a) after overwrite = %d, want 9", v)
	}
	c.Put("c", 3) // "b" is LRU because the overwrite promoted "a"
	if _, ok := c.Get("b"); ok {
		t.Fatal("b survived; overwrite must promote the key to most-recent")
	}
	if _, ok := c.Get("a"); !ok {
		t.Fatal("a was evicted despite being promoted by the overwrite")
	}
}

func TestRemove(t *testing.T) {
	var evs []eviction
	var mu sync.Mutex
	c, _ := New[string, int](2, collectEvictions(&evs, &mu))
	c.Put("a", 1)
	if !c.Remove("a") {
		t.Fatal("Remove(existing) = false, want true")
	}
	if c.Remove("a") {
		t.Fatal("Remove(missing) = true, want false")
	}
	if got := c.Len(); got != 0 {
		t.Fatalf("Len after remove = %d, want 0", got)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(evs) != 0 {
		t.Fatalf("explicit Remove must not fire the eviction callback, got %v", evs)
	}
}

func TestRemovedSlotIsReusable(t *testing.T) {
	c, _ := New[string, int](2, nil)
	c.Put("a", 1)
	c.Put("b", 2)
	c.Remove("a")
	c.Put("c", 3)
	c.Put("b", 20)
	if got := c.Len(); got != 2 {
		t.Fatalf("Len = %d, want 2", got)
	}
	if v, ok := c.Get("c"); !ok || v != 3 {
		t.Fatalf("Get(c) = (%v, %v), want (3, true)", v, ok)
	}
	if v, ok := c.Get("b"); !ok || v != 20 {
		t.Fatalf("Get(b) = (%v, %v), want (20, true)", v, ok)
	}
}

func TestWorksWithOtherKeyValueTypes(t *testing.T) {
	c, _ := New[int, string](2, nil)
	c.Put(404, "not found")
	c.Put(500, "server error")
	if v, ok := c.Get(404); !ok || v != "not found" {
		t.Fatalf("Get(404) = (%q, %v)", v, ok)
	}
	c.Put(200, "ok")
	if _, ok := c.Get(500); ok {
		t.Fatal("500 should have been evicted (404 was more recently used)")
	}
}

func TestConcurrentAccessAccountsForEveryEntry(t *testing.T) {
	const capacity = 64
	const workers = 8
	const perWorker = 500

	var evicted int64
	c, err := New[string, int](capacity, func(string, int) {
		atomic.AddInt64(&evicted, 1)
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWorker; i++ {
				key := fmt.Sprintf("g%d-k%d", w, i)
				c.Put(key, i)
				c.Get(key)
				c.Get(fmt.Sprintf("g%d-k%d", w, i/2))
			}
		}(w)
	}
	wg.Wait()

	if got := c.Len(); got != capacity {
		t.Fatalf("Len after heavy load = %d, want exactly capacity %d", got, capacity)
	}
	total := int(atomic.LoadInt64(&evicted)) + c.Len()
	if total != workers*perWorker {
		t.Fatalf("evicted(%d) + resident(%d) = %d, want %d — entries were lost or double-evicted under concurrency",
			atomic.LoadInt64(&evicted), c.Len(), total, workers*perWorker)
	}
}
