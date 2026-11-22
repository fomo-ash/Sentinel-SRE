package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// TestIncidentMetricsReadOnlyContract checks response shape and fixed errors.
func TestIncidentMetricsReadOnlyContract(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	for _, tc := range []struct {
		method string
		path   string
		code   int
		error  string
	}{
		{"GET", "/incidents/diag-degraded/metrics", http.StatusOK, ""},
		{"GET", "/incidents/diag-missing/metrics", http.StatusOK, ""},
		{"GET", "/incidents/diag-absent/metrics", http.StatusNotFound, "incident_not_found"},
		{"GET", "/incidents/bad/metrics", http.StatusBadRequest, "invalid_incident_id"},
		{"HEAD", "/incidents/diag-degraded/metrics", http.StatusOK, ""},
		{"POST", "/incidents/diag-degraded/metrics", http.StatusMethodNotAllowed, ""},
	} {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, nil))
			if recorder.Code != tc.code {
				t.Fatalf("status=%d, want %d", recorder.Code, tc.code)
			}
			if tc.method != "GET" {
				return
			}
			if recorder.Header().Get("Content-Type") != "application/json" {
				t.Fatalf("content type=%q", recorder.Header().Get("Content-Type"))
			}
			if tc.error != "" {
				var body map[string]string
				if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body["error"] != tc.error {
					t.Fatalf("error body=%s, decode error=%v", recorder.Body.String(), err)
				}
				return
			}
			var body incidents.MetricsSnapshot
			if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body.IncidentID == "" || body.Service == "" || body.ObservedAt == "" || body.Metrics == nil {
				t.Fatalf("metrics body=%s, decode error=%v", recorder.Body.String(), err)
			}
			if tc.path == "/incidents/diag-degraded/metrics" {
				if body.IncidentID != "diag-degraded" || len(body.Metrics) != 2 || body.Metrics[0].Window != "5m" {
					t.Fatalf("degraded metrics body=%s", recorder.Body.String())
				}
			}
			if tc.path == "/incidents/diag-missing/metrics" {
				if body.IncidentID != "diag-missing" || len(body.Metrics) != 0 {
					t.Fatalf("missing-data body=%s", recorder.Body.String())
				}
			}
		})
	}
}
