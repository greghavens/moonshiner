package httpapi

import (
	"net/http"

	"example.com/correlation-boundary/internal/correlation"
	"example.com/correlation-boundary/internal/jobs"
	"example.com/correlation-boundary/internal/telemetry"
)

type ExportHandler struct {
	Queue jobs.Enqueuer
	Log   *telemetry.Recorder
}

func (h ExportHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	account := r.URL.Query().Get("account")
	if account == "" {
		http.Error(w, "account is required", http.StatusBadRequest)
		return
	}

	job := jobs.Job{
		Account:       account,
		CorrelationID: correlation.FromContext(r.Context()),
	}
	if err := h.Queue.Enqueue(job); err != nil {
		http.Error(w, "enqueue export", http.StatusServiceUnavailable)
		return
	}

	h.Log.Record(r.Context(), "export.accepted", account)
	w.WriteHeader(http.StatusAccepted)
}

func ExportRoute(source correlation.Source, queue jobs.Enqueuer, log *telemetry.Recorder) http.Handler {
	return correlation.Boundary(source)(ExportHandler{Queue: queue, Log: log})
}
