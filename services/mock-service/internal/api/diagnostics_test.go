package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

func TestIncidentHealthReadOnlyContract(t *testing.T) {
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
		{"GET", "/incidents/diag-healthy/health", http.StatusOK, ""},
		{"GET", "/incidents/diag-missing/health", http.StatusOK, ""},
		{"GET", "/incidents/diag-absent/health", http.StatusNotFound, "incident_not_found"},
		{"GET", "/incidents/bad/health", http.StatusBadRequest, "invalid_incident_id"},
		{"POST", "/incidents/diag-healthy/health", http.StatusMethodNotAllowed, ""},
	} {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, nil))
			if recorder.Code != tc.code {
				t.Fatalf("status=%d, want %d", recorder.Code, tc.code)
			}
			if tc.method == "GET" && recorder.Header().Get("Content-Type") != "application/json" {
				t.Fatalf("content type=%q", recorder.Header().Get("Content-Type"))
			}
			if tc.error != "" {
				var body map[string]string
				if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body["error"] != tc.error {
					t.Fatalf("error body=%s, decode error=%v", recorder.Body.String(), err)
				}
			}
			if tc.path == "/incidents/diag-healthy/health" && tc.method == "GET" {
				var body incidents.HealthSnapshot
				if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body.IncidentID != "diag-healthy" || body.Service != "api" || body.ObservedAt == "" || len(body.Checks) != 1 {
					t.Fatalf("health body=%s, decode error=%v", recorder.Body.String(), err)
				}
			}
			if tc.path == "/incidents/diag-missing/health" && tc.method == "GET" {
				var body incidents.HealthSnapshot
				if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil || body.Status != "unknown" || body.Checks == nil || len(body.Checks) != 0 {
					t.Fatalf("missing-data body=%s, decode error=%v", recorder.Body.String(), err)
				}
			}
		})
	}
}
