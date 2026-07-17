package thumbwarm

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestPendingFiltersAlreadyWarm(t *testing.T) {
	have := map[string]int{"alps.jpg": 100, "cliff.jpg": 300}
	got := Pending(have, []string{"alps.jpg", "bay.jpg", "cliff.jpg", "dune.jpg"})
	if len(got) != 2 || got[0] != "bay.jpg" || got[1] != "dune.jpg" {
		t.Fatalf("Pending = %v, want [bay.jpg dune.jpg]", got)
	}
}

func TestWarmAllRendersEveryPhotoOnce(t *testing.T) {
	names := []string{
		"alps.jpg", "bay.jpg", "cliff.jpg", "dune.jpg",
		"elm.jpg", "fjord.jpg", "glen.jpg", "heath.jpg",
	}
	sizes, err := WarmAll(context.Background(), time.Minute, names,
		func(ctx context.Context, name string) (int, error) {
			return len(name) * 10, nil
		})
	if err != nil {
		t.Fatalf("WarmAll: %v", err)
	}
	if len(sizes) != len(names) {
		t.Fatalf("WarmAll warmed %d of %d photos: %v", len(sizes), len(names), sizes)
	}
	for _, n := range names {
		if sizes[n] != len(n)*10 {
			t.Errorf("sizes[%q] = %d, want %d", n, sizes[n], len(n)*10)
		}
	}
}

func TestWarmAllPassesBudgetDeadlineToRenderer(t *testing.T) {
	sizes, err := WarmAll(context.Background(), time.Minute, []string{"alps.jpg"},
		func(ctx context.Context, name string) (int, error) {
			if _, ok := ctx.Deadline(); !ok {
				return 0, errors.New("renderer context has no deadline")
			}
			return 1, nil
		})
	if err != nil {
		t.Fatalf("WarmAll: %v", err)
	}
	if sizes["alps.jpg"] != 1 {
		t.Fatalf("sizes = %v, want alps.jpg rendered", sizes)
	}
}

func TestWarmAllReportsFirstRenderProblem(t *testing.T) {
	names := []string{"alps.jpg", "bay.jpg", "corrupt.jpg", "dune.jpg", "elm.jpg"}
	sizes, err := WarmAll(context.Background(), time.Minute, names,
		func(ctx context.Context, name string) (int, error) {
			if name == "corrupt.jpg" {
				return 0, errors.New("corrupt.jpg: bad JPEG header")
			}
			return len(name), nil
		})
	if err == nil {
		t.Fatal("WarmAll swallowed the render failure")
	}
	if !strings.Contains(err.Error(), "corrupt.jpg") {
		t.Fatalf("WarmAll error %q should name the failing photo", err)
	}
	if len(sizes) != 4 {
		t.Fatalf("WarmAll warmed %d photos, want the 4 good ones: %v", len(sizes), sizes)
	}
	if _, ok := sizes["corrupt.jpg"]; ok {
		t.Fatalf("WarmAll recorded a size for the failing photo: %v", sizes)
	}
}
