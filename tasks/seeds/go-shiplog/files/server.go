// Package shiplog is the deploy-event log service: CI pipelines POST an
// event per deployment and dashboards read them back by id.
package shiplog

import (
	"encoding/json"
	"net/http"
	"strconv"
	"sync"
)

// Event is one recorded deployment.
type Event struct {
	ID      int    `json:"id"`
	Service string `json:"service"`
	Version string `json:"version"`
	Status  string `json:"status"`
}

var validStatus = map[string]bool{
	"started":   true,
	"succeeded": true,
	"failed":    true,
}

// Server is the HTTP API. Zero external deps; storage is in-memory.
type Server struct {
	mu     sync.Mutex
	nextID int
	events map[int]Event
	mux    *http.ServeMux
}

// NewServer returns a ready-to-mount handler.
func NewServer() *Server {
	s := &Server{nextID: 1, events: map[int]Event{}}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /events", s.handleCreate)
	mux.HandleFunc("GET /events/{id}", s.handleGet)
	mux.HandleFunc("GET /healthz", s.handleHealth)
	s.mux = mux
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *Server) handleCreate(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Service string `json:"service"`
		Version string `json:"version"`
		Status  string `json:"status"`
	}
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "malformed JSON body"})
		return
	}
	if in.Service == "" || in.Version == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "service and version are required"})
		return
	}
	if !validStatus[in.Status] {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "status must be started, succeeded or failed"})
		return
	}

	s.mu.Lock()
	ev := Event{ID: s.nextID, Service: in.Service, Version: in.Version, Status: in.Status}
	s.events[ev.ID] = ev
	s.nextID++
	s.mu.Unlock()

	writeJSON(w, http.StatusCreated, ev)
}

func (s *Server) handleGet(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "no such event"})
		return
	}
	s.mu.Lock()
	ev, ok := s.events[id]
	s.mu.Unlock()
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "no such event"})
		return
	}
	writeJSON(w, http.StatusOK, ev)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
