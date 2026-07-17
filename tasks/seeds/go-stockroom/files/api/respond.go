package api

import (
	"encoding/json"
	"errors"
	"net/http"

	"go-stockroom/domain"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

type errBody struct {
	Error string `json:"error"`
}

// writeErr maps domain sentinels onto HTTP statuses.
func writeErr(w http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	switch {
	case errors.Is(err, domain.ErrUnknownWarehouse), errors.Is(err, domain.ErrUnknownSKU):
		status = http.StatusNotFound
	case errors.Is(err, domain.ErrWarehouseExists),
		errors.Is(err, domain.ErrInsufficientStock),
		errors.Is(err, domain.ErrLocked),
		errors.Is(err, domain.ErrNotLocked):
		status = http.StatusConflict
	case errors.Is(err, domain.ErrInvalidQty):
		status = http.StatusBadRequest
	}
	writeJSON(w, status, errBody{Error: err.Error()})
}

// readJSON decodes a small JSON body, strictly.
func readJSON(r *http.Request, dst any) error {
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1<<16))
	dec.DisallowUnknownFields()
	return dec.Decode(dst)
}
