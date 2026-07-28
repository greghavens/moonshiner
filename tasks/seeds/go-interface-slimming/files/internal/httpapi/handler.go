package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"example.com/go-interface-slimming/internal/dispatch"
)

const statusClientClosedRequest = 499

type Handler struct {
	service dispatch.Service
}

func New(service dispatch.Service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	id, cancelRoute, ok := route(r.URL.Path)
	if !ok {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}

	switch {
	case !cancelRoute && r.Method == http.MethodGet:
		run, err := h.service.GetRun(r.Context(), id)
		if err != nil {
			writeServiceError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, run)
	case cancelRoute && r.Method == http.MethodPost:
		if err := h.service.CancelRun(r.Context(), id); err != nil {
			writeServiceError(w, err)
			return
		}
		writeJSON(w, http.StatusAccepted, map[string]string{"status": "cancelling"})
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func route(path string) (id string, cancel bool, ok bool) {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) == 2 && parts[0] == "runs" && parts[1] != "" {
		return parts[1], false, true
	}
	if len(parts) == 3 && parts[0] == "runs" && parts[1] != "" && parts[2] == "cancel" {
		return parts[1], true, true
	}
	return "", false, false
}

func writeServiceError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, dispatch.ErrNotFound):
		writeError(w, http.StatusNotFound, "run not found")
	case errors.Is(err, dispatch.ErrConflict):
		writeError(w, http.StatusConflict, "run state conflict")
	case errors.Is(err, context.Canceled):
		writeError(w, statusClientClosedRequest, "request cancelled")
	default:
		writeError(w, http.StatusInternalServerError, "internal error")
	}
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
