package incidents

import (
	"regexp"
	"strings"
	"time"
)

const RestoreSimulatedDBLogin = "restore_simulated_db_login"

const restoredDBLoginCheckID = "db_login_restored"

var actionIDPattern = regexp.MustCompile(`^action-[a-z0-9]+(?:-[a-z0-9]+)*$`)
var proposalIDPattern = regexp.MustCompile(`^proposal-[a-z0-9]+(?:-[a-z0-9]+)*$`)
var decisionIDPattern = regexp.MustCompile(`^decision-[a-z0-9]+(?:-[a-z0-9]+)*$`)
var fingerprintPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

// RemediationFailure is a fixed error code suitable for a local JSON response.
// It never includes request text or an operator identifier.
type RemediationFailure string

func (f RemediationFailure) Error() string { return string(f) }

const (
	InvalidRemediationRequest RemediationFailure = "invalid_request"
	ApprovalRequired          RemediationFailure = "approval_required"
	ApprovalMismatch          RemediationFailure = "approval_mismatch"
	IncidentNotFound          RemediationFailure = "incident_not_found"
	ActionNotAllowed          RemediationFailure = "action_not_allowed"
	ActionIDConflict          RemediationFailure = "action_id_conflict"
	ApprovalAlreadyUsed       RemediationFailure = "approval_already_used"
)

// ApprovalRecord carries the decision fields checkpointed by the Python
// workflow. The mock service checks their shape and binding but cannot prove
// their origin or authenticate the operator; Phase 7 adds access control.
type ApprovalRecord struct {
	DecisionID          string `json:"decision_id"`
	ProposalID          string `json:"proposal_id"`
	ProposalFingerprint string `json:"proposal_fingerprint"`
	IncidentID          string `json:"incident_id"`
	Service             string `json:"service"`
	Decision            string `json:"decision"`
	OperatorID          string `json:"operator_id"`
	DecidedAt           string `json:"decided_at"`
}

// RemediationRequest names one synthetic action and the recorded approval for it.
// No target URL, shell command, database credential, or free-form action is used.
type RemediationRequest struct {
	ActionID            string         `json:"action_id"`
	IncidentID          string         `json:"incident_id"`
	Service             string         `json:"service"`
	Action              string         `json:"action"`
	ProposalID          string         `json:"proposal_id"`
	ProposalFingerprint string         `json:"proposal_fingerprint"`
	Approval            ApprovalRecord `json:"approval"`
}

// RemediationReceipt records the single in-memory effect associated with an ID.
// Replayed requests return this exact value, including its original timestamp.
type RemediationReceipt struct {
	ActionID            string `json:"action_id"`
	IncidentID          string `json:"incident_id"`
	Service             string `json:"service"`
	Action              string `json:"action"`
	ProposalFingerprint string `json:"proposal_fingerprint"`
	ApprovalReference   string `json:"approval_reference"`
	Status              string `json:"status"`
	AppliedAt           string `json:"applied_at"`
}

type appliedAction struct {
	request RemediationRequest
	receipt RemediationReceipt
}

// ValidActionID restricts receipt lookups and effects to synthetic action IDs.
func ValidActionID(id string) bool {
	return len(id) <= 128 && actionIDPattern.MatchString(id)
}

func validApproval(request RemediationRequest) RemediationFailure {
	a := request.Approval
	if a.DecisionID == "" || a.Decision == "" || a.OperatorID == "" || a.DecidedAt == "" {
		return ApprovalRequired
	}
	if len(a.DecisionID) > 128 || !decisionIDPattern.MatchString(a.DecisionID) ||
		len(a.OperatorID) > 128 || strings.TrimSpace(a.OperatorID) != a.OperatorID ||
		a.Decision != "approve" ||
		a.ProposalID != request.ProposalID ||
		a.ProposalFingerprint != request.ProposalFingerprint ||
		a.IncidentID != request.IncidentID || a.Service != request.Service {
		return ApprovalMismatch
	}
	decidedAt, err := time.Parse(time.RFC3339Nano, a.DecidedAt)
	if err != nil || decidedAt.After(time.Now().UTC()) {
		return ApprovalMismatch
	}
	return ""
}

// ApplyRemediation atomically records one receipt and one health overlay.
// The immutable fixture remains available for fresh store instances. The returned
// replay flag lets the HTTP handler distinguish a new effect from an exact retry.
func (s *Store) ApplyRemediation(request RemediationRequest) (RemediationReceipt, bool, error) {
	if s == nil || !ValidActionID(request.ActionID) {
		return RemediationReceipt{}, false, InvalidRemediationRequest
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	if prior, exists := s.actions[request.ActionID]; exists {
		if prior.request == request {
			return prior.receipt, true, nil
		}
		return RemediationReceipt{}, false, ActionIDConflict
	}
	if !ValidID(request.IncidentID) {
		return RemediationReceipt{}, false, InvalidRemediationRequest
	}
	base, exists := s.health[request.IncidentID]
	if !exists {
		return RemediationReceipt{}, false, IncidentNotFound
	}
	if request.IncidentID != "diag-db-auth" || request.Service != "api" ||
		base.Service != request.Service || request.Action != RestoreSimulatedDBLogin {
		return RemediationReceipt{}, false, ActionNotAllowed
	}
	if len(request.ProposalID) > 128 || !proposalIDPattern.MatchString(request.ProposalID) ||
		!fingerprintPattern.MatchString(request.ProposalFingerprint) {
		return RemediationReceipt{}, false, InvalidRemediationRequest
	}
	if reason := validApproval(request); reason != "" {
		return RemediationReceipt{}, false, reason
	}
	if _, used := s.approvalUses[request.Approval.DecisionID]; used {
		return RemediationReceipt{}, false, ApprovalAlreadyUsed
	}

	// Keep the synthetic post-action observation later than the frozen fixture,
	// even when a test host's clock is set earlier than the fixture timestamp.
	applied := time.Now().UTC()
	fixtureTime, _ := time.Parse(time.RFC3339, base.ObservedAt)
	if !applied.After(fixtureTime) {
		applied = fixtureTime.Add(time.Nanosecond)
	}
	appliedAt := applied.Format(time.RFC3339Nano)
	receipt := RemediationReceipt{
		ActionID:            request.ActionID,
		IncidentID:          request.IncidentID,
		Service:             request.Service,
		Action:              request.Action,
		ProposalFingerprint: request.ProposalFingerprint,
		ApprovalReference:   request.Approval.DecisionID,
		Status:              "applied",
		AppliedAt:           appliedAt,
	}
	overlay := HealthSnapshot{
		IncidentID: request.IncidentID,
		Service:    request.Service,
		ObservedAt: appliedAt,
		Status:     "healthy",
		Checks: []HealthCheck{{
			ID:     restoredDBLoginCheckID,
			Status: "healthy",
			Detail: "The simulated API database login check passed after the approved local action.",
		}},
	}
	if s.actions == nil {
		s.actions = make(map[string]appliedAction)
	}
	if s.healthOverlay == nil {
		s.healthOverlay = make(map[string]HealthSnapshot)
	}
	if s.approvalUses == nil {
		s.approvalUses = make(map[string]string)
	}
	// Ledger, approval use, and overlay change under the same lock, so replay
	// and health readers see a complete action or the original fixture.
	s.actions[request.ActionID] = appliedAction{request: request, receipt: receipt}
	s.approvalUses[request.Approval.DecisionID] = request.ActionID
	s.healthOverlay[request.IncidentID] = overlay
	return receipt, false, nil
}

// RemediationReceiptFor returns only a receipt belonging to the requested
// incident, preventing an action ID from disclosing another incident's result.
func (s *Store) RemediationReceiptFor(incidentID, actionID string) (RemediationReceipt, bool) {
	if s == nil {
		return RemediationReceipt{}, false
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	prior, exists := s.actions[actionID]
	if !exists || prior.receipt.IncidentID != incidentID {
		return RemediationReceipt{}, false
	}
	return prior.receipt, true
}
