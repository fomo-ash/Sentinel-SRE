package api

import (
	"encoding/json"
	"net/http"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// NewHandler registers process health, synthetic incident reads, and the one
// loopback-only simulated remediation contract.
// The caller owns the validated store; main binds this handler to loopback.
func NewHandler(store *incidents.Store) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
	mux.Handle("GET /incidents/{id}/health", incidentHealthHandler(store))
	mux.Handle("GET /incidents/{id}/metrics", incidentMetricsHandler(store))
	mux.Handle("GET /incidents/{id}/logs", incidentLogsHandler(store))
	mux.Handle("POST /incidents/{id}/remediations", remediationHandler(store))
	mux.Handle("GET /incidents/{id}/remediations/{action_id}", remediationReceiptHandler(store))
	return mux
}
