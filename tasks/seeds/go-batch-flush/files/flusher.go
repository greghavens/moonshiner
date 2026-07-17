// Package flusher batches metric points before shipping them to the
// timeseries sink. Points are flushed when the batch fills up or when the
// flush interval elapses, whichever comes first, so dashboards stay fresh
// even when a service emits only a trickle of points.
package flusher

import "time"

// Point is one metric sample.
type Point struct {
	Name  string
	Value float64
}

// Flusher accumulates points and ships them in batches.
type Flusher struct {
	in       chan Point
	stop     chan struct{}
	done     chan struct{}
	max      int
	interval time.Duration
	sink     func([]Point)
}

// New starts a flusher that ships batches of up to max points at least
// every interval, calling sink from its own goroutine.
func New(max int, interval time.Duration, sink func([]Point)) *Flusher {
	f := &Flusher{
		in:       make(chan Point),
		stop:     make(chan struct{}),
		done:     make(chan struct{}),
		max:      max,
		interval: interval,
		sink:     sink,
	}
	go f.loop()
	return f
}

// Add queues one point for delivery.
func (f *Flusher) Add(p Point) {
	f.in <- p
}

// Close flushes whatever is buffered and stops the background goroutine.
func (f *Flusher) Close() {
	close(f.stop)
	<-f.done
}

func (f *Flusher) loop() {
	defer close(f.done)
	var batch []Point
	flush := func() {
		if len(batch) == 0 {
			return
		}
		f.sink(batch)
		batch = nil
	}
	for {
		select {
		case p := <-f.in:
			batch = append(batch, p)
			if len(batch) >= f.max {
				flush()
			}
		case <-time.Tick(f.interval):
			flush()
		case <-f.stop:
			flush()
			return
		}
	}
}
