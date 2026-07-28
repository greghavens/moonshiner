package webhook

import (
	"bytes"
	"io"
	"net/http"
)

// Handler authenticates a webhook before passing it to the application.
type Handler struct {
	verifier *Verifier
	next     http.Handler
}

func NewHandler(verifier *Verifier, next http.Handler) *Handler {
	return &Handler{verifier: verifier, next: next}
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "unable to read webhook body", http.StatusBadRequest)
		return
	}

	if err := h.verifier.Verify(body, r.Header.Get("X-Hub-Signature-256")); err != nil {
		http.Error(w, err.Error(), http.StatusUnauthorized)
		return
	}

	r.Body = io.NopCloser(bytes.NewReader(body))
	h.next.ServeHTTP(w, r)
}
