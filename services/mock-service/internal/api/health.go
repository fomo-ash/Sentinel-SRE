package api

import (
	"encoding/json"
	"net/http"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// NewHandler exposes only local, read-only mock service routes.
func NewHandler(store *incidents.Store) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
	mux.Handle("GET /incidents/{id}/health", incidentHealthHandler(store))
	return mux
}
