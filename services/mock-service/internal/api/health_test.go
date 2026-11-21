package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

func TestHealth(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	NewHandler(store).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/health", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d", recorder.Code)
	}
	if !strings.Contains(recorder.Body.String(), `"status":"ok"`) {
		t.Fatalf("body = %s", recorder.Body.String())
	}
}
