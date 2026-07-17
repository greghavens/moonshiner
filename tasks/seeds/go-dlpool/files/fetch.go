package dlpool

// fetchOne runs the retry loop for a single file and records progress as
// it goes. Download always runs it on its own goroutine.
func (m *Manager) fetchOne(name string) {
	defer m.wg.Done()
	var lastErr error
	for attempt := 1; attempt <= m.opts.Attempts; attempt++ {
		m.setProgress(name, Progress{Status: "fetching", Attempts: attempt})
		data, err := m.fetch(name)
		if err == nil {
			m.setProgress(name, Progress{
				Status:   "done",
				Bytes:    len(data),
				Attempts: attempt,
			})
			return
		}
		lastErr = err
		if attempt < m.opts.Attempts {
			m.opts.Sleep(m.nextDelay())
		}
	}
	m.setProgress(name, Progress{
		Status:   "failed",
		Attempts: m.opts.Attempts,
		Err:      lastErr.Error(),
	})
}

func (m *Manager) setProgress(name string, p Progress) {
	m.mu.Lock()
	m.progress[name] = p
	m.mu.Unlock()
}
