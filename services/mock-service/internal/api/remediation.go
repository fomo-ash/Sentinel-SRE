package api

import (
	"encoding/json"
	"io"
	"mime"
	"net/http"

	"github.com/sentinel-sre/mock-service/internal/incidents"
)

const maxRemediationBodyBytes = 4096

func writeRemediationError(w http.ResponseWriter, status int, code string) {
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code})
}

func remediationStatus(err error) (int, string) {
	switch err {
	case incidents.InvalidRemediationRequest:
		return http.StatusBadRequest, "invalid_request"
	case incidents.ApprovalRequired:
		return http.StatusBadRequest, "approval_required"
	case incidents.ApprovalMismatch:
		return http.StatusBadRequest, "approval_mismatch"
	case incidents.IncidentNotFound:
		return http.StatusNotFound, "incident_not_found"
	case incidents.ActionNotAllowed:
		return http.StatusUnprocessableEntity, "action_not_allowed"
	case incidents.ActionIDConflict:
		return http.StatusConflict, "action_id_conflict"
	case incidents.ApprovalAlreadyUsed:
		return http.StatusConflict, "approval_already_used"
	default:
		return http.StatusInternalServerError, "remediation_error"
	}
}

// remediationHandler accepts one narrowly defined local synthetic action.
// The Python workflow must supply its recorded approval. This handler checks
// the record's binding but does not authenticate the claimed human operator.
func remediationHandler(store *incidents.Store) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		id := r.PathValue("id")
		if !incidents.ValidID(id) {
			writeRemediationError(w, http.StatusBadRequest, "invalid_incident_id")
			return
		}
		mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || mediaType != "application/json" {
			writeRemediationError(w, http.StatusBadRequest, "invalid_request")
			return
		}
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxRemediationBodyBytes))
		decoder.DisallowUnknownFields()
		var request incidents.RemediationRequest
		if err := decoder.Decode(&request); err != nil {
			writeRemediationError(w, http.StatusBadRequest, "invalid_request")
			return
		}
		if err := decoder.Decode(new(any)); err != io.EOF || request.IncidentID != id {
			writeRemediationError(w, http.StatusBadRequest, "invalid_request")
			return
		}
		receipt, replayed, err := store.ApplyRemediation(request)
		if err != nil {
			status, code := remediationStatus(err)
			writeRemediationError(w, status, code)
			return
		}
		if !replayed {
			w.WriteHeader(http.StatusCreated)
		}
		_ = json.NewEncoder(w).Encode(receipt)
	})
}

// remediationReceiptHandler supports reconciliation after a lost POST response.
// It reads the ledger only; lookup never repeats an effect.
func remediationReceiptHandler(store *incidents.Store) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		id := r.PathValue("id")
		if !incidents.ValidID(id) {
			writeRemediationError(w, http.StatusBadRequest, "invalid_incident_id")
			return
		}
		actionID := r.PathValue("action_id")
		if !incidents.ValidActionID(actionID) {
			writeRemediationError(w, http.StatusBadRequest, "invalid_action_id")
			return
		}
		if _, exists := store.Health(id); !exists {
			writeRemediationError(w, http.StatusNotFound, "incident_not_found")
			return
		}
		receipt, exists := store.RemediationReceiptFor(id, actionID)
		if !exists {
			writeRemediationError(w, http.StatusNotFound, "action_not_found")
			return
		}
		_ = json.NewEncoder(w).Encode(receipt)
	})
}
