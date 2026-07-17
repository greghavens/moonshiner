package dupfinder

import (
	"bytes"
	"fmt"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

// makeHashClass writes 12 files of identical size (one size class): four
// distinct contents, three copies each.
func makeHashClass(t *testing.T, root string) {
	t.Helper()
	for i := 0; i < 12; i++ {
		content := bytes.Repeat([]byte{byte('A' + i%4)}, 2048)
		writeFile(t, root, fmt.Sprintf("class/f%02d.bin", i), content)
	}
}

func TestHashingIsBoundedAndActuallyParallel(t *testing.T) {
	root := t.TempDir()
	makeHashClass(t, root)

	const workers = 3
	var (
		mu            sync.Mutex
		inflight      int
		peak          int
		starts, ends  int
		gate          = make(chan struct{})
		releaseOnce   sync.Once
	)
	opts := Options{
		Workers: workers,
		Progress: func(stage Stage, path string) {
			switch stage {
			case StageHashStart:
				mu.Lock()
				starts++
				inflight++
				if inflight > peak {
					peak = inflight
				}
				reached := inflight >= workers
				mu.Unlock()
				if reached {
					releaseOnce.Do(func() { close(gate) })
				}
				// Hold this hash open until all workers are busy at once
				// (generous fallback so a broken pool still terminates).
				select {
				case <-gate:
				case <-time.After(8 * time.Second):
				}
			case StageHashEnd:
				mu.Lock()
				ends++
				inflight--
				mu.Unlock()
			}
		},
	}

	rep := find(t, root, opts)

	select {
	case <-gate:
	default:
		t.Error("the pool never had Workers files hashing simultaneously — hashing is serialized (per file or per size class)")
	}
	mu.Lock()
	defer mu.Unlock()
	if peak > workers {
		t.Errorf("concurrent hashes peaked at %d, above the Workers=%d bound", peak, workers)
	}
	if starts != 12 || ends != 12 {
		t.Errorf("Progress start/end counts = %d/%d, want 12/12 (every candidate hashed exactly once, every start matched by an end)", starts, ends)
	}
	if len(rep.Groups) != 4 {
		t.Errorf("got %d groups, want 4 (four distinct contents, three copies each)", len(rep.Groups))
	}
	for _, g := range rep.Groups {
		if g.Size != 2048 || len(g.Paths) != 3 {
			t.Errorf("bad group %+v, want size 2048 with 3 paths", g)
		}
	}
}

func TestReportIdenticalAcrossWorkerCounts(t *testing.T) {
	root := t.TempDir()
	trio := []byte(strings.Repeat("t", 512))
	pair := []byte(strings.Repeat("p", 1024))
	writeFile(t, root, "x/one.dat", trio)
	writeFile(t, root, "y/two.dat", trio)
	writeFile(t, root, "z/three.dat", trio)
	writeFile(t, root, "p/left.bin", pair)
	writeFile(t, root, "q/right.bin", pair)
	writeFile(t, root, "lonely.txt", []byte("no twin anywhere"))
	writeFile(t, root, "decoy-a.dat", []byte(strings.Repeat("a", 512)))  // same size as trio,
	writeFile(t, root, "decoy-b.dat", []byte(strings.Repeat("b", 512))) // both content-unique

	serial := find(t, root, Options{Workers: 1})
	wide := find(t, root, Options{Workers: 8})
	again := find(t, root, Options{Workers: 8})
	if !reflect.DeepEqual(serial, wide) || !reflect.DeepEqual(wide, again) {
		t.Fatalf("reports differ across runs/worker counts:\nserial: %+v\nwide:   %+v\nagain:  %+v", serial, wide, again)
	}
	if wantWaste := int64(512*2 + 1024*1); serial.WastedBytes != wantWaste {
		t.Fatalf("WastedBytes = %d, want %d", serial.WastedBytes, wantWaste)
	}
	if len(serial.Groups) != 2 {
		t.Fatalf("got %d groups, want 2 (decoys share a size but not content)", len(serial.Groups))
	}
}
