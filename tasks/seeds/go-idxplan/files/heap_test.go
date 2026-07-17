package idxplan

import (
	"errors"
	"maps"
	"testing"
)

func TestInsertAssignsMonotonicIDsFromOne(t *testing.T) {
	h := NewHeap()
	for want := uint64(1); want <= 3; want++ {
		if got := h.Insert(map[string]string{"sku": "X"}); got != want {
			t.Fatalf("Insert #%d returned id %d", want, got)
		}
	}
	if h.Len() != 3 {
		t.Fatalf("Len = %d, want 3", h.Len())
	}
}

func TestDeletedIDsAreNeverReused(t *testing.T) {
	h := NewHeap()
	h.Insert(map[string]string{"n": "1"})
	id2 := h.Insert(map[string]string{"n": "2"})
	h.Insert(map[string]string{"n": "3"})
	if err := h.Delete(id2); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if got := h.Insert(map[string]string{"n": "4"}); got != 4 {
		t.Fatalf("Insert after delete returned id %d, want 4", got)
	}
	if h.Len() != 3 {
		t.Fatalf("Len = %d, want 3", h.Len())
	}
}

func TestGetReturnsLiveRecordsOnly(t *testing.T) {
	h := NewHeap()
	id := h.Insert(map[string]string{"sku": "P-1", "wh": "OSL"})
	rec, ok := h.Get(id)
	if !ok || rec["sku"] != "P-1" || rec["wh"] != "OSL" {
		t.Fatalf("Get = %v, %v", rec, ok)
	}
	if err := h.Delete(id); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, ok := h.Get(id); ok {
		t.Fatal("Get after Delete still returns the record")
	}
}

func TestHeapIsDefensiveAboutCallerMaps(t *testing.T) {
	h := NewHeap()
	in := map[string]string{"sku": "P-1"}
	id := h.Insert(in)
	in["sku"] = "scribbled"
	rec, _ := h.Get(id)
	if rec["sku"] != "P-1" {
		t.Fatal("mutating the map passed to Insert changed the stored record")
	}
	rec["sku"] = "also-scribbled"
	again, _ := h.Get(id)
	if again["sku"] != "P-1" {
		t.Fatal("mutating a map returned by Get changed the stored record")
	}
}

func TestUpdateReplacesTheWholeRecord(t *testing.T) {
	h := NewHeap()
	id := h.Insert(map[string]string{"sku": "P-1", "wh": "OSL", "qty": "9"})
	if err := h.Update(id, map[string]string{"sku": "P-1", "wh": "BER"}); err != nil {
		t.Fatalf("Update: %v", err)
	}
	rec, _ := h.Get(id)
	want := map[string]string{"sku": "P-1", "wh": "BER"}
	if !maps.Equal(rec, want) {
		t.Fatalf("record after Update = %v, want %v (full replacement)", rec, want)
	}
}

func TestUpdateAndDeleteOnMissingIDs(t *testing.T) {
	h := NewHeap()
	id := h.Insert(map[string]string{"sku": "P-1"})
	if err := h.Delete(id); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if err := h.Delete(id); !errors.Is(err, ErrNotFound) {
		t.Fatalf("second Delete = %v, want ErrNotFound", err)
	}
	if err := h.Update(id, map[string]string{"sku": "P-2"}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Update on deleted id = %v, want ErrNotFound", err)
	}
	if err := h.Update(999, map[string]string{"sku": "P-2"}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Update on unknown id = %v, want ErrNotFound", err)
	}
}
