package api

import (
	"encoding/json"
	"net/http"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// incidentHealthHandler returns a fixture snapshot without changing store state.
// It uses stable JSON error codes so clients can distinguish bad and absent IDs.
func incidentHealthHandler(store *incidents.Store) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		id := r.PathValue("id")
		if !incidents.ValidID(id) {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid_incident_id"})
			return
		}
		snapshot, ok := store.Health(id)
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "incident_not_found"})
			return
		}
		_ = json.NewEncoder(w).Encode(snapshot)
	})
}
