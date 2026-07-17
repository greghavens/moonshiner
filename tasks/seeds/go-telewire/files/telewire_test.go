// Acceptance suite for the telewire frame encoder.
//
// Pinned behavior: exact frame bytes (field order, decimal ts/value,
// two-digit lowercase hex flags, the four escape rules), append semantics
// on the caller's buffer, error identities, and — the point of the
// ticket — an allocation budget on the hot path via testing.AllocsPerRun.
//
// Run: go test -race -timeout 120s ./...
package telewire

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

// ---- deterministic input generator ----------------------------------------

type lcg struct{ s uint32 }

func (l *lcg) next() uint32 {
	l.s = l.s*1664525 + 1013904223
	return l.s
}

func (l *lcg) below(n uint32) uint32 { return l.next() % n }

var devicePool = []string{
	"pump-a", "rack|7", `sensor\9`, "belt-3", "chiller-2", "dock\ngate",
	"mixer-1", "oven|two", "press\r4",
}

var metricPool = []string{"rpm", "temp-c", "kw|load", `psi\raw`, "hz", "amps"}

func genReadings(n int, seed uint32) []Reading {
	rng := &lcg{s: seed}
	rs := make([]Reading, n)
	for i := range rs {
		rs[i] = Reading{
			TS:     1_700_000_000_000 + int64(rng.below(1_000_000_000)),
			Device: devicePool[rng.below(uint32(len(devicePool)))],
			Metric: metricPool[rng.below(uint32(len(metricPool)))],
			Value:  int64(rng.next()) - int64(rng.next()),
			Flags:  uint8(rng.next()),
		}
	}
	return rs
}

// ---- test-local oracle (independent of the implementation) ----------------

var escaper = strings.NewReplacer(`\`, `\\`, "|", `\|`, "\n", `\n`, "\r", `\r`)

func oracleFrame(r Reading) string {
	return fmt.Sprintf("%d|%s|%s|%d|%02x\n",
		r.TS, escaper.Replace(r.Device), escaper.Replace(r.Metric), r.Value, r.Flags)
}

// ---- behavior --------------------------------------------------------------

func TestFrameLayoutExact(t *testing.T) {
	cases := []struct {
		r    Reading
		want string
	}{
		{Reading{TS: 1720000000123, Device: "pump-a", Metric: "rpm", Value: 1450, Flags: 0x00},
			"1720000000123|pump-a|rpm|1450|00\n"},
		{Reading{TS: -5, Device: "belt-3", Metric: "temp-c", Value: -273, Flags: 0xff},
			"-5|belt-3|temp-c|-273|ff\n"},
		{Reading{TS: 0, Device: "rack|7", Metric: "kw|load", Value: 0, Flags: 0x0a},
			"0|rack\\|7|kw\\|load|0|0a\n"},
		{Reading{TS: 9, Device: `sensor\9`, Metric: `psi\raw`, Value: 7, Flags: 0x10},
			"9|sensor\\\\9|psi\\\\raw|7|10\n"},
		{Reading{TS: 3, Device: "dock\ngate", Metric: "hz", Value: 60, Flags: 0x2b},
			"3|dock\\ngate|hz|60|2b\n"},
		{Reading{TS: 4, Device: "press\r4", Metric: "amps", Value: 12, Flags: 0x07},
			"4|press\\r4|amps|12|07\n"},
	}
	for _, c := range cases {
		got, err := AppendFrame(nil, c.r)
		if err != nil {
			t.Fatalf("AppendFrame(%+v): %v", c.r, err)
		}
		if string(got) != c.want {
			t.Fatalf("frame mismatch for %+v:\n got  %q\n want %q", c.r, got, c.want)
		}
	}
}

func TestErrorsLeaveDstUntouched(t *testing.T) {
	dst := []byte("keep")
	out, err := AppendFrame(dst, Reading{Metric: "rpm"})
	if !errors.Is(err, ErrEmptyDevice) {
		t.Fatalf("want ErrEmptyDevice, got %v", err)
	}
	if string(out) != "keep" {
		t.Fatalf("dst changed on error: %q", out)
	}
	out, err = AppendFrame(dst, Reading{Device: "pump-a"})
	if !errors.Is(err, ErrEmptyMetric) {
		t.Fatalf("want ErrEmptyMetric, got %v", err)
	}
	if string(out) != "keep" {
		t.Fatalf("dst changed on error: %q", out)
	}
}

func TestAppendSemantics(t *testing.T) {
	r1 := Reading{TS: 1, Device: "pump-a", Metric: "rpm", Value: 2, Flags: 1}
	r2 := Reading{TS: 3, Device: "belt-3", Metric: "hz", Value: 4, Flags: 2}

	buf := []byte("prefix:")
	buf, err := AppendFrame(buf, r1)
	if err != nil {
		t.Fatal(err)
	}
	buf, err = AppendFrame(buf, r2)
	if err != nil {
		t.Fatal(err)
	}
	want := "prefix:" + oracleFrame(r1) + oracleFrame(r2)
	if string(buf) != want {
		t.Fatalf("append chain mismatch:\n got  %q\n want %q", buf, want)
	}

	// spare capacity must be usable without disturbing earlier content
	base := make([]byte, 0, 256)
	base, _ = AppendFrame(base, r1)
	snapshot := string(base)
	if _, err := AppendFrame(base, r2); err != nil {
		t.Fatal(err)
	}
	if string(base) != snapshot {
		t.Fatalf("earlier frame bytes disturbed: %q", base)
	}
}

func TestMatchesOracleAcrossGeneratedReadings(t *testing.T) {
	for i, r := range genReadings(2000, 20260713) {
		got, err := AppendFrame(nil, r)
		if err != nil {
			t.Fatalf("reading %d: %v", i, err)
		}
		if want := oracleFrame(r); string(got) != want {
			t.Fatalf("reading %d (%+v):\n got  %q\n want %q", i, r, got, want)
		}
	}
}

func TestConcurrentCallersWithOwnBuffers(t *testing.T) {
	rs := genReadings(300, 7)
	want := make([]string, len(rs))
	for i, r := range rs {
		want[i] = oracleFrame(r)
	}
	done := make(chan error, 4)
	for g := 0; g < 4; g++ {
		go func() {
			var buf []byte
			for i, r := range rs {
				buf = buf[:0]
				var err error
				buf, err = AppendFrame(buf, r)
				if err != nil {
					done <- err
					return
				}
				if string(buf) != want[i] {
					done <- fmt.Errorf("goroutine saw wrong bytes at reading %d", i)
					return
				}
			}
			done <- nil
		}()
	}
	for g := 0; g < 4; g++ {
		if err := <-done; err != nil {
			t.Fatal(err)
		}
	}
}

// ---- the perf gate ----------------------------------------------------------

func TestAllocBudgetOnHotPath(t *testing.T) {
	// Budget arithmetic (perf-seed policy: document the margin): an
	// encoder that appends straight into the caller's buffer allocates
	// nothing in steady state — the warm-up call AllocsPerRun makes
	// grows buf to its final capacity, and we keep that capacity across
	// runs. Budget: 8 allocations per 512-frame batch, generous headroom
	// over 0-1. The Sprintf-plus-ReplaceAll shape costs ~6 allocations
	// per frame (~3000 per batch, ~400x over budget), so it trips this
	// immediately; the whole gate runs in milliseconds either way.
	rs := genReadings(512, 99)
	buf := make([]byte, 0, 1<<15)
	avg := testing.AllocsPerRun(20, func() {
		b := buf[:0]
		for _, r := range rs {
			var err error
			b, err = AppendFrame(b, r)
			if err != nil {
				t.Fatal(err)
			}
		}
		buf = b
	})
	if avg > 8 {
		t.Fatalf("allocation budget exceeded: %.0f allocs per 512-frame batch (budget 8) — the encoder is allocating per frame", avg)
	}
}
