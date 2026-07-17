package eventbus

import (
	"fmt"
	"sync"
	"testing"
)

func TestConcurrentPublishersGetDenseDistinctOffsets(t *testing.T) {
	b := NewBus()
	const writers, perWriter = 8, 100

	offsets := make([][]int64, writers)
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWriter; i++ {
				off, err := b.Publish("firehose", []byte(fmt.Sprintf("w%d-%d", w, i)))
				if err != nil {
					t.Errorf("concurrent Publish: %v", err)
					return
				}
				offsets[w] = append(offsets[w], off)
			}
		}(w)
	}
	wg.Wait()

	total := int64(writers * perWriter)
	if n := b.Len("firehose"); n != total {
		t.Fatalf("Len = %d, want %d", n, total)
	}
	seen := make(map[int64]bool, total)
	for w := range offsets {
		prev := int64(-1)
		for _, off := range offsets[w] {
			if off < 0 || off >= total {
				t.Fatalf("offset %d out of range [0,%d)", off, total)
			}
			if seen[off] {
				t.Fatalf("offset %d handed out twice", off)
			}
			seen[off] = true
			if off <= prev {
				t.Fatalf("a single publisher's offsets must increase: got %d after %d", off, prev)
			}
			prev = off
		}
	}
	if int64(len(seen)) != total {
		t.Fatalf("saw %d distinct offsets, want %d (offsets must be dense)", len(seen), total)
	}
}

func TestConcurrentGroupsEachDrainTheFullLog(t *testing.T) {
	b := NewBus()
	const writers, perWriter = 4, 100
	const total = writers * perWriter

	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWriter; i++ {
				if _, err := b.Publish("firehose", []byte("payload")); err != nil {
					t.Errorf("concurrent Publish: %v", err)
					return
				}
			}
		}(w)
	}
	wg.Wait()

	// Four groups drain the same static log concurrently. Each must see
	// every offset exactly once, in order, using poll+ack batches.
	var cg sync.WaitGroup
	for g := 0; g < 4; g++ {
		cg.Add(1)
		go func(g int) {
			defer cg.Done()
			group := fmt.Sprintf("group-%d", g)
			next := int64(0)
			for {
				evs, err := b.Poll("firehose", group, 7)
				if err != nil {
					t.Errorf("%s: Poll: %v", group, err)
					return
				}
				if len(evs) == 0 {
					break
				}
				for _, ev := range evs {
					if ev.Offset != next {
						t.Errorf("%s: got offset %d, want %d (contiguous, in order)", group, ev.Offset, next)
						return
					}
					next++
				}
				if err := b.Ack("firehose", group, evs[len(evs)-1].Offset); err != nil {
					t.Errorf("%s: Ack: %v", group, err)
					return
				}
			}
			if next != total {
				t.Errorf("%s consumed %d events, want %d", group, next, total)
			}
		}(g)
	}
	cg.Wait()
}

func TestPollAckChurnWhilePublishing(t *testing.T) {
	b := NewBus()
	const writers, perWriter = 4, 50
	const total = writers * perWriter
	const groups = 3

	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < perWriter; i++ {
				if _, err := b.Publish("stream", []byte("x")); err != nil {
					t.Errorf("concurrent Publish: %v", err)
					return
				}
			}
		}()
	}
	// Consumers churn while publishers run: a bounded number of poll+ack
	// rounds, no completion requirement yet.
	counts := make([]int64, groups)
	for g := 0; g < groups; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			group := fmt.Sprintf("g%d", g)
			for round := 0; round < 40; round++ {
				evs, err := b.Poll("stream", group, 5)
				if err != nil {
					t.Errorf("%s: Poll: %v", group, err)
					return
				}
				if len(evs) == 0 {
					continue
				}
				counts[g] += int64(len(evs))
				if err := b.Ack("stream", group, evs[len(evs)-1].Offset); err != nil {
					t.Errorf("%s: Ack: %v", group, err)
					return
				}
			}
		}(g)
	}
	wg.Wait()

	// Everything is published now; each group drains the remainder and the
	// grand total per group must be exactly the number of publishes.
	for g := 0; g < groups; g++ {
		group := fmt.Sprintf("g%d", g)
		for {
			evs, err := b.Poll("stream", group, 64)
			if err != nil {
				t.Fatalf("%s: drain Poll: %v", group, err)
			}
			if len(evs) == 0 {
				break
			}
			counts[g] += int64(len(evs))
			if err := b.Ack("stream", group, evs[len(evs)-1].Offset); err != nil {
				t.Fatalf("%s: drain Ack: %v", group, err)
			}
		}
		if counts[g] != total {
			t.Fatalf("%s consumed %d events in total, want %d (no loss, no duplicates)", group, counts[g], total)
		}
	}
}
