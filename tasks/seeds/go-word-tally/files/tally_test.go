package tally

import (
	"fmt"
	"reflect"
	"testing"
)

func TestTokenizeNormalizes(t *testing.T) {
	got := Tokenize("GET /health: OK, latency=2ms (fast!)")
	want := []string{"get", "/health", "ok", "latency=2ms", "fast"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Tokenize = %v, want %v", got, want)
	}
}

func TestCountSingleShard(t *testing.T) {
	counts := Count([][]string{{"error error warn", "warn error"}})
	if counts["error"] != 3 || counts["warn"] != 2 {
		t.Fatalf("counts = %v, want error:3 warn:2", counts)
	}
}

func TestCountManyShards(t *testing.T) {
	const shardCount = 8
	const linesPerShard = 200
	shards := make([][]string, shardCount)
	for s := range shards {
		lines := make([]string, linesPerShard)
		for i := range lines {
			lines[i] = fmt.Sprintf("request handled status=ok shard%d", s)
		}
		shards[s] = lines
	}
	counts := Count(shards)
	if got := counts["request"]; got != shardCount*linesPerShard {
		t.Fatalf("counts[request] = %d, want %d", got, shardCount*linesPerShard)
	}
	if got := counts["status=ok"]; got != shardCount*linesPerShard {
		t.Fatalf("counts[status=ok] = %d, want %d", got, shardCount*linesPerShard)
	}
	for s := 0; s < shardCount; s++ {
		key := fmt.Sprintf("shard%d", s)
		if got := counts[key]; got != linesPerShard {
			t.Fatalf("counts[%s] = %d, want %d", key, got, linesPerShard)
		}
	}
}

func TestCountEmpty(t *testing.T) {
	counts := Count(nil)
	if len(counts) != 0 {
		t.Fatalf("Count(nil) = %v, want empty", counts)
	}
}
