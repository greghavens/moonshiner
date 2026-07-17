package api

import (
	"net/http"

	"go-stockroom/domain"
)

type movementRequest struct {
	SKU string `json:"sku"`
	Qty int    `json:"qty"`
}

func (s *Server) listStock(w http.ResponseWriter, r *http.Request) {
	rows, err := s.store.Rows(r.PathValue("id"))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"rows": rows})
}

func (s *Server) restock(w http.ResponseWriter, r *http.Request) {
	s.applyMovement(w, r, domain.MovementRestock)
}

func (s *Server) pick(w http.ResponseWriter, r *http.Request) {
	s.applyMovement(w, r, domain.MovementPick)
}

func (s *Server) applyMovement(w http.ResponseWriter, r *http.Request, kind domain.MovementKind) {
	var body movementRequest
	if err := readJSON(r, &body); err != nil || body.SKU == "" {
		writeJSON(w, http.StatusBadRequest, errBody{Error: "body must be {\"sku\": \"...\", \"qty\": N}"})
		return
	}
	row, err := s.store.ApplyMovement(r.PathValue("id"), kind, body.SKU, body.Qty)
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, row)
}

func (s *Server) listMovements(w http.ResponseWriter, r *http.Request) {
	movements, err := s.store.Movements(r.PathValue("id"))
	if err != nil {
		writeErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"movements": movements})
}
