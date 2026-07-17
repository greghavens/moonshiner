package flagengine

import (
	"fmt"
	"hash/fnv"
	"sync"
	"testing"
)

// refBucket is the bucketing contract shared with the JS and Python SDKs:
// FNV-1a 64 over "<flagKey>:<unitID>", modulo 10000 basis points.
func refBucket(key, id string) int {
	h := fnv.New64a()
	h.Write([]byte(key + ":" + id))
	return int(h.Sum64() % 10000)
}

func TestBucketMatchesCrossSDKContract(t *testing.T) {
	cases := []struct{ key, id string }{
		{"checkout-v2", "user-1"},
		{"checkout-v2", "user-2"},
		{"new-nav", "user-1"},
		{"new-nav", ""},
		{"a", "b"},
		{"emoji-flag", "üser-∆"},
	}
	for _, c := range cases {
		if got, want := Bucket(c.key, c.id), refBucket(c.key, c.id); got != want {
			t.Fatalf("Bucket(%q, %q) = %d, want %d (FNV-1a 64 of key:id mod 10000)", c.key, c.id, got, want)
		}
	}
	// Same user, different flag keys must land in different buckets at least
	// somewhere — the flag key salts the hash.
	diff := false
	for i := 0; i < 20; i++ {
		id := fmt.Sprintf("u%d", i)
		if Bucket("flag-a", id) != Bucket("flag-b", id) {
			diff = true
			break
		}
	}
	if !diff {
		t.Fatal("bucketing ignores the flag key: 20/20 users identical across two flags")
	}
}

func TestRolloutIsExactlyBucketThreshold(t *testing.T) {
	e := New()
	if err := e.AddEnv("prod", ""); err != nil {
		t.Fatal(err)
	}
	const bps = 5000
	if err := e.SetFlag("prod", Flag{Key: "search-v3", On: true, RolloutBPS: bps}); err != nil {
		t.Fatal(err)
	}

	inCount := 0
	for i := 0; i < 2000; i++ {
		id := fmt.Sprintf("user-%d", i)
		r, err := e.Eval("prod", "search-v3", User{ID: id})
		if err != nil {
			t.Fatal(err)
		}
		wantIn := refBucket("search-v3", id) < bps
		if r.Value != wantIn {
			t.Fatalf("user %s: Eval=%v but bucket %d vs threshold %d says %v", id, r.Value, refBucket("search-v3", id), bps, wantIn)
		}
		wantReason := "fallthrough"
		if wantIn {
			wantReason = "rollout"
			inCount++
		}
		if r.Reason != wantReason {
			t.Fatalf("user %s: Reason=%q, want %q", id, r.Reason, wantReason)
		}
	}
	if inCount < 800 || inCount > 1200 {
		t.Fatalf("bucketing badly skewed: %d/2000 users inside a 50%% rollout", inCount)
	}
}

func TestRolloutEdgesAndMonotonicity(t *testing.T) {
	e := New()
	if err := e.AddEnv("prod", ""); err != nil {
		t.Fatal(err)
	}
	if err := e.SetFlag("prod", Flag{Key: "f", On: true, RolloutBPS: 0}); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 50; i++ {
		r, err := e.Eval("prod", "f", User{ID: fmt.Sprintf("u%d", i)})
		if err != nil {
			t.Fatal(err)
		}
		if r.Value {
			t.Fatalf("0 bps rollout let user u%d in", i)
		}
	}
	if err := e.SetFlag("prod", Flag{Key: "f", On: true, RolloutBPS: 10000}); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 50; i++ {
		r, err := e.Eval("prod", "f", User{ID: fmt.Sprintf("u%d", i)})
		if err != nil {
			t.Fatal(err)
		}
		if !r.Value {
			t.Fatalf("10000 bps rollout locked user u%d out", i)
		}
	}

	// Ramping up must only ever ADD users: whoever was in at 3000 stays in at 7000.
	if err := e.SetFlag("prod", Flag{Key: "f", On: true, RolloutBPS: 3000}); err != nil {
		t.Fatal(err)
	}
	inAt30 := map[string]bool{}
	for i := 0; i < 500; i++ {
		id := fmt.Sprintf("user-%d", i)
		r, err := e.Eval("prod", "f", User{ID: id})
		if err != nil {
			t.Fatal(err)
		}
		if r.Value {
			inAt30[id] = true
		}
	}
	if err := e.SetFlag("prod", Flag{Key: "f", On: true, RolloutBPS: 7000}); err != nil {
		t.Fatal(err)
	}
	for id := range inAt30 {
		r, err := e.Eval("prod", "f", User{ID: id})
		if err != nil {
			t.Fatal(err)
		}
		if !r.Value {
			t.Fatalf("user %s was in the 30%% rollout but fell out at 70%% — ramps must be monotone", id)
		}
	}
}

func TestRulesTakePriorityOverRollout(t *testing.T) {
	e := New()
	if err := e.AddEnv("prod", ""); err != nil {
		t.Fatal(err)
	}
	if err := e.SetFlag("prod", Flag{
		Key: "g",
		On:  true,
		Rules: []Rule{{
			Clauses: []Clause{{Attr: "qa", Op: "in", Values: []string{"true"}}},
			Value:   false,
		}},
		RolloutBPS: 10000,
	}); err != nil {
		t.Fatal(err)
	}
	r, err := e.Eval("prod", "g", User{ID: "qa-bot", Attrs: map[string]string{"qa": "true"}})
	if err != nil {
		t.Fatal(err)
	}
	if r.Value != false || r.Reason != "rule:0" {
		t.Fatalf("matching rule must preempt the rollout: got %+v", r)
	}
}

func TestConcurrentEvalAndSetFlag(t *testing.T) {
	e := New()
	if err := e.AddEnv("prod", ""); err != nil {
		t.Fatal(err)
	}
	if err := e.SetFlag("prod", Flag{Key: "hot", On: true, RolloutBPS: 5000}); err != nil {
		t.Fatal(err)
	}

	var wg sync.WaitGroup
	for w := 0; w < 4; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < 200; i++ {
				bps := (i % 11) * 1000
				if err := e.SetFlag("prod", Flag{Key: "hot", On: true, RolloutBPS: bps}); err != nil {
					t.Errorf("concurrent SetFlag: %v", err)
					return
				}
			}
		}(w)
	}
	for r := 0; r < 8; r++ {
		wg.Add(1)
		go func(r int) {
			defer wg.Done()
			for i := 0; i < 500; i++ {
				res, err := e.Eval("prod", "hot", User{ID: fmt.Sprintf("u%d-%d", r, i)})
				if err != nil {
					t.Errorf("concurrent Eval: %v", err)
					return
				}
				if res.Reason != "rollout" && res.Reason != "fallthrough" {
					t.Errorf("unexpected reason under churn: %+v", res)
					return
				}
			}
		}(r)
	}
	wg.Wait()
}
