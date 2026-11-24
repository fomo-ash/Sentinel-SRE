package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// approvedHTTPAction stands in for a decision already recorded by the local
// Python workflow. The HTTP tests never contact a real infrastructure target.
func approvedHTTPAction() incidents.RemediationRequest {
	return incidents.RemediationRequest{
		ActionID:            "action-http-one",
		IncidentID:          "diag-db-auth",
		Service:             "api",
		Action:              incidents.RestoreSimulatedDBLogin,
		ProposalID:          "proposal-http-one",
		ProposalFingerprint: strings.Repeat("a", 64),
		Approval: incidents.ApprovalRecord{
			DecisionID:          "decision-http-one",
			ProposalID:          "proposal-http-one",
			ProposalFingerprint: strings.Repeat("a", 64),
			IncidentID:          "diag-db-auth",
			Service:             "api",
			Decision:            "approve",
			OperatorID:          "local-reviewer",
			DecidedAt:           time.Now().UTC().Add(-time.Minute).Format(time.RFC3339Nano),
		},
	}
}

func postAction(t *testing.T, handler http.Handler, path string, request incidents.RemediationRequest) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	httpRequest := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(body))
	httpRequest.Header.Set("Content-Type", "application/json")
	handler.ServeHTTP(recorder, httpRequest)
	return recorder
}

func TestRemediationHTTPReceiptAndExactReplay(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	request := approvedHTTPAction()
	path := "/incidents/diag-db-auth/remediations"
	created := postAction(t, handler, path, request)
	if created.Code != http.StatusCreated || created.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("new action status=%d content-type=%q body=%s", created.Code, created.Header().Get("Content-Type"), created.Body.String())
	}
	var receipt incidents.RemediationReceipt
	if err := json.Unmarshal(created.Body.Bytes(), &receipt); err != nil ||
		receipt.ActionID != request.ActionID || receipt.Status != "applied" ||
		receipt.ApprovalReference != request.Approval.DecisionID {
		t.Fatalf("invalid receipt: %#v decode=%v", receipt, err)
	}
	replayed := postAction(t, handler, path, request)
	if replayed.Code != http.StatusOK || replayed.Body.String() != created.Body.String() {
		t.Fatalf("exact replay changed receipt: status=%d body=%s", replayed.Code, replayed.Body.String())
	}
	lookup := httptest.NewRecorder()
	handler.ServeHTTP(lookup, httptest.NewRequest(http.MethodGet, path+"/"+request.ActionID, nil))
	if lookup.Code != http.StatusOK || lookup.Body.String() != created.Body.String() {
		t.Fatalf("receipt lookup failed: status=%d body=%s", lookup.Code, lookup.Body.String())
	}
	request.Approval.OperatorID = "other-reviewer"
	conflict := postAction(t, handler, path, request)
	if conflict.Code != http.StatusConflict || !strings.Contains(conflict.Body.String(), `"action_id_conflict"`) {
		t.Fatalf("conflicting replay: status=%d body=%s", conflict.Code, conflict.Body.String())
	}
	request = approvedHTTPAction()
	request.ActionID = "action-http-other"
	usedApproval := postAction(t, handler, path, request)
	if usedApproval.Code != http.StatusConflict || !strings.Contains(usedApproval.Body.String(), `"approval_already_used"`) {
		t.Fatalf("approval reused for new action: status=%d body=%s", usedApproval.Code, usedApproval.Body.String())
	}
}

func TestRemediationHTTPRejectsInvalidRequests(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	for _, tc := range []struct {
		name   string
		path   string
		modify func(*incidents.RemediationRequest)
		status int
		code   string
	}{
		{"missing approval", "/incidents/diag-db-auth/remediations", func(r *incidents.RemediationRequest) { r.Approval = incidents.ApprovalRecord{} }, 400, "approval_required"},
		{"rejected", "/incidents/diag-db-auth/remediations", func(r *incidents.RemediationRequest) { r.Approval.Decision = "reject" }, 400, "approval_mismatch"},
		{"mismatched approval", "/incidents/diag-db-auth/remediations", func(r *incidents.RemediationRequest) { r.Approval.ProposalFingerprint = strings.Repeat("b", 64) }, 400, "approval_mismatch"},
		{"unknown incident", "/incidents/diag-absent/remediations", func(r *incidents.RemediationRequest) {
			r.IncidentID = "diag-absent"
			r.Approval.IncidentID = "diag-absent"
		}, 404, "incident_not_found"},
		{"other fixture", "/incidents/diag-healthy/remediations", func(r *incidents.RemediationRequest) {
			r.IncidentID = "diag-healthy"
			r.Approval.IncidentID = "diag-healthy"
		}, 422, "action_not_allowed"},
		{"wrong service", "/incidents/diag-db-auth/remediations", func(r *incidents.RemediationRequest) { r.Service = "worker"; r.Approval.Service = "worker" }, 422, "action_not_allowed"},
		{"unlisted action", "/incidents/diag-db-auth/remediations", func(r *incidents.RemediationRequest) { r.Action = "restart_service" }, 422, "action_not_allowed"},
		{"path mismatch", "/incidents/diag-healthy/remediations", func(r *incidents.RemediationRequest) {}, 400, "invalid_request"},
		{"bad path ID", "/incidents/not-synthetic/remediations", func(r *incidents.RemediationRequest) {}, 400, "invalid_incident_id"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			request := approvedHTTPAction()
			request.ActionID = "action-" + strings.ReplaceAll(tc.name, " ", "-")
			tc.modify(&request)
			response := postAction(t, handler, tc.path, request)
			if response.Code != tc.status || !strings.Contains(response.Body.String(), `"`+tc.code+`"`) {
				t.Fatalf("status=%d body=%s, want %d %s", response.Code, response.Body.String(), tc.status, tc.code)
			}
		})
	}
	for _, tc := range []struct {
		name        string
		body        string
		contentType string
	}{
		{"malformed", `{`, "application/json"},
		{"unknown field", `{"action_id":"action-unknown","extra":true}`, "application/json"},
		{"trailing JSON", `{} {}`, "application/json"},
		{"oversized", strings.Repeat("x", maxRemediationBodyBytes+1), "application/json"},
		{"wrong media type", `{}`, "text/plain"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/incidents/diag-db-auth/remediations", strings.NewReader(tc.body))
			request.Header.Set("Content-Type", tc.contentType)
			handler.ServeHTTP(recorder, request)
			if recorder.Code != http.StatusBadRequest || !strings.Contains(recorder.Body.String(), `"invalid_request"`) {
				t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
			}
		})
	}
}

func TestRemediationReceiptLookupRejectsOtherTargetsAndMethods(t *testing.T) {
	store, err := incidents.DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	for _, tc := range []struct {
		method string
		path   string
		status int
		code   string
	}{
		{"GET", "/incidents/diag-db-auth/remediations/action-missing", 404, "action_not_found"},
		{"GET", "/incidents/diag-absent/remediations/action-missing", 404, "incident_not_found"},
		{"GET", "/incidents/diag-db-auth/remediations/bad", 400, "invalid_action_id"},
		{"PUT", "/incidents/diag-db-auth/remediations", 405, ""},
		{"POST", "/incidents/diag-db-auth/remediations/action-missing", 405, ""},
	} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, nil))
		if recorder.Code != tc.status || (tc.code != "" && !strings.Contains(recorder.Body.String(), `"`+tc.code+`"`)) {
			t.Fatalf("%s %s: status=%d body=%s", tc.method, tc.path, recorder.Code, recorder.Body.String())
		}
	}
}
