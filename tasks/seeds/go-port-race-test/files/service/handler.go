package service

import (
	"fmt"
	"net/http"
)

// Handler returns the HTTP handler exercised by the integration suite.
func Handler(instance string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /identity", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = fmt.Fprint(w, instance)
	})
	return mux
}
