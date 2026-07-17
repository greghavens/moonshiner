package flusher

import (
	"testing"
	"time"
)

func collectSink(flushes chan []Point) func([]Point) {
	return func(pts []Point) {
		cp := append([]Point(nil), pts...)
		flushes <- cp
	}
}

func TestFullBatchShipsImmediately(t *testing.T) {
	flushes := make(chan []Point, 4)
	f := New(5, time.Hour, collectSink(flushes))
	defer f.Close()
	for i := 0; i < 5; i++ {
		f.Add(Point{Name: "cpu", Value: float64(i)})
	}
	select {
	case batch := <-flushes:
		if len(batch) != 5 {
			t.Fatalf("first batch has %d points, want 5", len(batch))
		}
	case <-time.After(2 * time.Second):
		t.Fatal("full batch never reached the sink")
	}
}

func TestTrickleTrafficStillFlushesOnInterval(t *testing.T) {
	flushes := make(chan []Point, 32)
	f := New(1000, 200*time.Millisecond, collectSink(flushes))
	defer f.Close()
	feed := time.NewTicker(20 * time.Millisecond)
	defer feed.Stop()
	deadline := time.After(1500 * time.Millisecond)
	sent := 0
	for {
		select {
		case <-feed.C:
			f.Add(Point{Name: "req_latency_ms", Value: float64(sent)})
			sent++
		case batch := <-flushes:
			if len(batch) == 0 {
				t.Fatal("sink received an empty batch")
			}
			return
		case <-deadline:
			t.Fatalf("sent %d points over 1.5s of steady traffic and the sink never saw a batch (flush interval is 200ms)", sent)
		}
	}
}

func TestCloseDeliversBufferedPoints(t *testing.T) {
	flushes := make(chan []Point, 4)
	f := New(100, time.Hour, collectSink(flushes))
	f.Add(Point{Name: "mem", Value: 1})
	f.Add(Point{Name: "mem", Value: 2})
	f.Add(Point{Name: "mem", Value: 3})
	f.Close()
	select {
	case batch := <-flushes:
		if len(batch) != 3 {
			t.Fatalf("final batch has %d points, want 3", len(batch))
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Close returned without delivering the buffered points")
	}
}

func TestCloseWithNothingBuffered(t *testing.T) {
	flushes := make(chan []Point, 4)
	f := New(10, time.Hour, collectSink(flushes))
	f.Close()
	select {
	case batch := <-flushes:
		t.Fatalf("sink called with %d points, expected no flush at all", len(batch))
	default:
	}
}
