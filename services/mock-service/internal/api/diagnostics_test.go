package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// TestIncidentHealthReadOnlyContract checks reads, errors, and method rejection.
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

// TestIncidentHealthReflectsOnlyTheApprovedMockOverlay verifies that the new
// health observation is scoped to one synthetic incident. The original fixture
// and the process health route stay independent of the simulated effect.
func TestIncidentHealthReflectsOnlyTheApprovedMockOverlay(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	read := func(path string) incidents.HealthSnapshot {
		t.Helper()
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusOK {
			t.Fatalf("GET %s: status=%d body=%s", path, recorder.Code, recorder.Body.String())
		}
		var snapshot incidents.HealthSnapshot
		if err := json.Unmarshal(recorder.Body.Bytes(), &snapshot); err != nil {
			t.Fatal(err)
		}
		return snapshot
	}
	before := read("/incidents/diag-db-auth/health")
	if before.Status != "unhealthy" || before.Checks[0].ID != "database_auth" {
		t.Fatalf("unexpected baseline health: %#v", before)
	}
	request := approvedHTTPAction()
	response := postAction(t, handler, "/incidents/diag-db-auth/remediations", request)
	if response.Code != http.StatusCreated {
		t.Fatalf("simulated action status=%d body=%s", response.Code, response.Body.String())
	}
	after := read("/incidents/diag-db-auth/health")
	if after.Status != "healthy" || after.Checks[0].ID != "db_login_restored" || after.ObservedAt == before.ObservedAt {
		t.Fatalf("post-action health does not reflect overlay: %#v", after)
	}
	if storeHealth, _ := store.Health("diag-healthy"); storeHealth.Status != "healthy" {
		t.Fatalf("unrelated fixture changed: %#v", storeHealth)
	}
	process := httptest.NewRecorder()
	handler.ServeHTTP(process, httptest.NewRequest(http.MethodGet, "/health", nil))
	if process.Code != http.StatusOK || process.Body.String() != "{\"status\":\"ok\"}\n" {
		t.Fatalf("process health changed: status=%d body=%s", process.Code, process.Body.String())
	}
}
