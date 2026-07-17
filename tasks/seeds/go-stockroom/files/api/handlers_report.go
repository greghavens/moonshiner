package api

import (
	"net/http"
	"strconv"

	"go-stockroom/domain"
)

// availabilityReport serves the ops dashboard: rows ranked by available
// quantity. ?top=N limits the result.
func (s *Server) availabilityReport(w http.ResponseWriter, r *http.Request) {
	top := 0
	if raw := r.URL.Query().Get("top"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 {
			writeJSON(w, http.StatusBadRequest, errBody{Error: "top must be a positive integer"})
			return
		}
		top = n
	}
	warehouse := r.PathValue("id")
	rows, err := s.store.Rows(warehouse)
	if err != nil {
		writeErr(w, err)
		return
	}
	report := domain.AvailabilityReport(rows, top)
	writeJSON(w, http.StatusOK, map[string]any{"warehouse": warehouse, "rows": report})
}

func (s *Server) ledger(w http.ResponseWriter, r *http.Request) {
	entries, err := s.store.Ledger(r.PathValue("id"))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries})
}

func (s *Server) lock(w http.ResponseWriter, r *http.Request) {
	if err := s.store.Lock(r.PathValue("id")); err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "locked"})
}

func (s *Server) unlock(w http.ResponseWriter, r *http.Request) {
	if err := s.store.Unlock(r.PathValue("id")); err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "unlocked"})
}
