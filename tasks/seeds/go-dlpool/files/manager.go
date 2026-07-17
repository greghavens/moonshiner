// Package dlpool is a small batched download manager. Files are fetched a
// batch at a time — the next batch starts only once the previous one has
// fully drained — with per-file retries and a progress table that the
// status endpoint can snapshot while a run is in flight.
package dlpool

import "sync"

// Fetcher retrieves one named file. It is injected so the manager stays
// transport-agnostic (HTTP mirror, object store, test double...).
type Fetcher func(name string) ([]byte, error)

// Progress is the per-file state the manager tracks during a run.
type Progress struct {
	Status   string // "queued", "fetching", "done" or "failed"
	Bytes    int
	Attempts int
	Err      string
}

// Manager coordinates one download run at a time.
type Manager struct {
	fetch Fetcher
	opts  Options

	mu       sync.Mutex
	delay    delayState
	progress map[string]Progress

	wg sync.WaitGroup
}

// New returns a Manager using the given fetcher and options; zero option
// fields fall back to the defaults documented on Options.
func New(fetch Fetcher, opts Options) *Manager {
	opts = withDefaults(opts)
	return &Manager{
		fetch:    fetch,
		opts:     opts,
		delay:    delayState{next: opts.BaseDelay},
		progress: make(map[string]Progress),
	}
}

// Download fetches every named file, BatchSize at a time, and returns the
// final per-file report once everything has settled.
func (m *Manager) Download(names []string) map[string]Progress {
	m.mu.Lock()
	for _, name := range names {
		m.progress[name] = Progress{Status: "queued"}
	}
	m.mu.Unlock()

	// Register every download up front so a straggler can't be missed.
	m.wg.Add(len(names))
	for _, batch := range chunk(names, m.opts.BatchSize) {
		for _, name := range batch {
			go m.fetchOne(name)
		}
		// Drain the batch before the next one starts.
		m.wg.Wait()
	}
	return m.Report()
}

// Report returns a point-in-time copy of per-file progress. The status
// endpoint polls this while a run is live.
func (m *Manager) Report() map[string]Progress {
	out := make(map[string]Progress, len(m.progress))
	for name, p := range m.progress {
		out[name] = p
	}
	return out
}

func chunk(names []string, size int) [][]string {
	var out [][]string
	for start := 0; start < len(names); start += size {
		out = append(out, names[start:min(start+size, len(names))])
	}
	return out
}
