// Package dashfeed is the edge service behind the ops dashboard. It relays
// live numbers from the internal stats backend and renders downloadable
// reports on demand.
package dashfeed

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
)

// RenderFunc produces a report body. In production it talks to the
// warehouse; tests inject their own.
type RenderFunc func(ctx context.Context, name string) ([]byte, error)

// Server is the dashboard edge API.
type Server struct {
	statsBase string
	render    RenderFunc
	client    *http.Client
	mux       *http.ServeMux
}

// New wires the edge service against a stats backend base URL and a report
// renderer.
func New(statsBase string, render RenderFunc) *Server {
	s := &Server{
		statsBase: statsBase,
		render:    render,
		// The stats backend is one small box; a single keep-alive
		// connection per edge pod is all it wants from us.
		client: &http.Client{Transport: &http.Transport{MaxConnsPerHost: 1}},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /summary", s.handleSummary)
	mux.HandleFunc("GET /report/{name}", s.handleReport)
	s.mux = mux
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

// handleSummary proxies the current stats blob for the dashboard header.
func (s *Server) handleSummary(w http.ResponseWriter, r *http.Request) {
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, s.statsBase+"/stats", nil)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "bad stats url"})
		return
	}
	resp, err := s.client.Do(req)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": "stats backend unreachable"})
		return
	}
	if resp.StatusCode != http.StatusOK {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": "stats backend degraded"})
		return
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": "stats backend unreachable"})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(raw)
}

// handleReport renders a report without blocking the serve loop on the
// renderer: the work runs in its own goroutine and the request context
// decides how long we wait for it.
func (s *Server) handleReport(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := r.PathValue("name")

	done := make(chan struct{})
	var data []byte
	var renderErr error
	go func() {
		defer close(done)
		data, renderErr = s.render(ctx, name)
		if renderErr != nil {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(data)
	}()

	select {
	case <-done:
		if renderErr != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "render failed"})
		}
	case <-ctx.Done():
		writeJSON(w, http.StatusGatewayTimeout, map[string]any{"error": "report timed out"})
		<-done // let the render finish so we never leak the goroutine
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
