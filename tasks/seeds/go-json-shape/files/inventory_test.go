package inventory

import (
	"bytes"
	"encoding/json"
	"reflect"
	"testing"
)

func wireMap(t *testing.T, data []byte) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("payload is not valid JSON: %v\n%s", err, data)
	}
	return m
}

func TestEncodeEmitsEmptyTagArrayNotNull(t *testing.T) {
	data, err := Encode(Component{ID: "web"})
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	m := wireMap(t, data)
	v, ok := m["tags"]
	if !ok {
		t.Fatalf("payload %s has no tags key; contract requires an array", data)
	}
	arr, isArr := v.([]any)
	if !isArr {
		t.Fatalf("tags encoded as %v (%T); contract requires a JSON array", v, v)
	}
	if len(arr) != 0 {
		t.Fatalf("tags = %v, want empty array for a tagless component", arr)
	}
}

func TestEncodeOmitsEmptyNotes(t *testing.T) {
	data, err := Encode(Component{ID: "web", Tags: []string{"edge"}})
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	if _, ok := wireMap(t, data)["notes"]; ok {
		t.Fatalf("payload %s carries a notes key for an empty note", data)
	}
	data, err = Encode(Component{ID: "web", Tags: []string{"edge"}, Notes: "canary"})
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	if got := wireMap(t, data)["notes"]; got != "canary" {
		t.Fatalf("notes = %v, want %q", got, "canary")
	}
}

func TestDecodedNullTagsReencodeAsArray(t *testing.T) {
	c, err := Decode([]byte(`{"id":"db","tags":null}`))
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	data, err := Encode(c)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	arr, isArr := wireMap(t, data)["tags"].([]any)
	if !isArr || len(arr) != 0 {
		t.Fatalf("re-encoded tags in %s, want empty array", data)
	}
}

func TestRoundTripIsStable(t *testing.T) {
	original := []byte(`{"id":"db","tags":["primary","pinned"],"notes":"blue"}`)
	c1, err := Decode(original)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	out1, err := Encode(c1)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	c2, err := Decode(out1)
	if err != nil {
		t.Fatalf("Decode of own output: %v", err)
	}
	if !reflect.DeepEqual(c1, c2) {
		t.Fatalf("round trip changed the record: %+v vs %+v", c1, c2)
	}
	out2, err := Encode(c2)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	if !bytes.Equal(out1, out2) {
		t.Fatalf("encoding is not stable across round trips: %s vs %s", out1, out2)
	}
}

func TestGetHandsOutIndependentCopies(t *testing.T) {
	cache := NewCache()
	if _, err := cache.Sync([]byte(`{"id":"api","tags":["canary","edge"]}`)); err != nil {
		t.Fatalf("Sync: %v", err)
	}
	a, ok := cache.Get("api")
	if !ok {
		t.Fatal("synced record missing from cache")
	}
	a.Tags[0] = "stomped"
	b, _ := cache.Get("api")
	if b.Tags[0] != "canary" {
		t.Fatalf("mutating a returned record changed the cached copy: tags = %v", b.Tags)
	}
}

func TestSyncResultIsIndependentOfCache(t *testing.T) {
	cache := NewCache()
	got, err := cache.Sync([]byte(`{"id":"queue","tags":["durable","fanout"]}`))
	if err != nil {
		t.Fatalf("Sync: %v", err)
	}
	got.Tags[1] = "stomped"
	cached, ok := cache.Get("queue")
	if !ok {
		t.Fatal("synced record missing from cache")
	}
	if cached.Tags[1] != "fanout" {
		t.Fatalf("mutating the Sync result changed the cached copy: tags = %v", cached.Tags)
	}
}
