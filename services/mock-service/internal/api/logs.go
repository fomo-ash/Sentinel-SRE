package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

const defaultLogLimit = 20
const maxLogLimit = 50

// incidentLogsHandler serves only bounded, validated synthetic log entries.
func incidentLogsHandler(store *incidents.Store) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		id := r.PathValue("id")
		if !incidents.ValidID(id) {
			writeLogError(w, http.StatusBadRequest, "invalid_incident_id")
			return
		}
		limit := defaultLogLimit
		values, err := strconv.Atoi(r.URL.Query().Get("limit"))
		if _, present := r.URL.Query()["limit"]; present {
			if len(r.URL.Query()["limit"]) != 1 || err != nil || values < 1 || values > maxLogLimit {
				writeLogError(w, http.StatusBadRequest, "invalid_limit")
				return
			}
			limit = values
		}
		snapshot, ok := store.Logs(id)
		if !ok {
			writeLogError(w, http.StatusNotFound, "incident_not_found")
			return
		}
		if len(snapshot.Entries) > limit {
			snapshot.Entries = snapshot.Entries[:limit]
		}
		_ = json.NewEncoder(w).Encode(snapshot)
	})
}

func writeLogError(w http.ResponseWriter, status int, code string) {
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code})
}
