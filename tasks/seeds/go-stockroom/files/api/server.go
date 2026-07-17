// Package api is the HTTP layer over the store and domain packages.
package api

import (
	"net/http"

	"go-stockroom/store"
)

type Server struct {
	store *store.Store
}

// NewServer wires the JSON API around a store.
func NewServer(st *store.Store) http.Handler {
	s := &Server{store: st}
	mux := http.NewServeMux()

	mux.HandleFunc("POST /warehouses", s.createWarehouse)
	mux.HandleFunc("GET /warehouses", s.listWarehouses)

	mux.HandleFunc("GET /warehouses/{id}/stock", s.listStock)
	mux.HandleFunc("POST /warehouses/{id}/restock", s.restock)
	mux.HandleFunc("POST /warehouses/{id}/pick", s.pick)
	mux.HandleFunc("GET /warehouses/{id}/movements", s.listMovements)

	mux.HandleFunc("GET /warehouses/{id}/report", s.availabilityReport)
	mux.HandleFunc("GET /warehouses/{id}/ledger", s.ledger)
	mux.HandleFunc("POST /warehouses/{id}/lock", s.lock)
	mux.HandleFunc("DELETE /warehouses/{id}/lock", s.unlock)

	return mux
}

func (s *Server) createWarehouse(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ID string `json:"id"`
	}
	if err := readJSON(r, &body); err != nil || body.ID == "" {
		writeJSON(w, http.StatusBadRequest, errBody{Error: "body must be {\"id\": \"...\"}"})
		return
	}
	if err := s.store.AddWarehouse(body.ID); err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"id": body.ID})
}

func (s *Server) listWarehouses(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"warehouses": s.store.Warehouses()})
}
