package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

func TestIncidentLogsReadOnlyContract(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	for _, tc := range []struct {
		method  string
		path    string
		status  int
		code    string
		entries int
	}{
		{"GET", "/incidents/diag-degraded/logs", http.StatusOK, "", 3},
		{"GET", "/incidents/diag-degraded/logs?limit=1", http.StatusOK, "", 1},
		{"GET", "/incidents/diag-missing/logs", http.StatusOK, "", 0},
		{"GET", "/incidents/diag-absent/logs", http.StatusNotFound, "incident_not_found", 0},
		{"GET", "/incidents/bad/logs", http.StatusBadRequest, "invalid_incident_id", 0},
		{"GET", "/incidents/diag-degraded/logs?limit=0", http.StatusBadRequest, "invalid_limit", 0},
		{"GET", "/incidents/diag-degraded/logs?limit=51", http.StatusBadRequest, "invalid_limit", 0},
		{"GET", "/incidents/diag-degraded/logs?limit=x", http.StatusBadRequest, "invalid_limit", 0},
		{"GET", "/incidents/diag-degraded/logs?limit=1&limit=2", http.StatusBadRequest, "invalid_limit", 0},
		{"POST", "/incidents/diag-degraded/logs", http.StatusMethodNotAllowed, "", 0},
	} {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, nil))
			if recorder.Code != tc.status {
				t.Fatalf("status=%d, want %d; body=%s", recorder.Code, tc.status, recorder.Body.String())
			}
			if tc.method != "GET" {
				return
			}
			if recorder.Header().Get("Content-Type") != "application/json" {
				t.Fatalf("content type=%q", recorder.Header().Get("Content-Type"))
			}
			if tc.code != "" {
				var body map[string]string
				if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body["error"] != tc.code {
					t.Fatalf("error body=%s, decode error=%v", recorder.Body.String(), err)
				}
				return
			}
			var body incidents.LogSnapshot
			if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body.IncidentID == "" || body.Service == "" || body.ObservedAt == "" || body.Entries == nil || len(body.Entries) != tc.entries {
				t.Fatalf("log body=%s, decode error=%v", recorder.Body.String(), err)
			}
		})
	}
}
