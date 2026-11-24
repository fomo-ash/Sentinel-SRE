package incidents

import (
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

// approvedRemediationRequest models an approval already recorded by the Python
// workflow. These tests exercise only synthetic, process-local observations.
func approvedRemediationRequest() RemediationRequest {
	return RemediationRequest{
		ActionID:            "action-test-one",
		IncidentID:          "diag-db-auth",
		Service:             "api",
		Action:              RestoreSimulatedDBLogin,
		ProposalID:          "proposal-test-one",
		ProposalFingerprint: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		Approval: ApprovalRecord{
			DecisionID:          "decision-test-one",
			ProposalID:          "proposal-test-one",
			ProposalFingerprint: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			IncidentID:          "diag-db-auth",
			Service:             "api",
			Decision:            "approve",
			OperatorID:          "local-reviewer",
			DecidedAt:           time.Now().UTC().Add(-time.Minute).Format(time.RFC3339Nano),
		},
	}
}

func TestApplyRemediationRecordsOneEffectAndPreservesFixtures(t *testing.T) {
	store, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	before, _ := store.Health("diag-db-auth")
	metricsBefore, _ := store.Metrics("diag-db-auth")
	logsBefore, _ := store.Logs("diag-db-auth")
	unrelated, _ := store.Health("diag-healthy")
	request := approvedRemediationRequest()
	receipt, replayed, err := store.ApplyRemediation(request)
	if err != nil || replayed {
		t.Fatalf("first action: receipt=%#v replayed=%v err=%v", receipt, replayed, err)
	}
	if receipt.Status != "applied" || receipt.ApprovalReference != request.Approval.DecisionID {
		t.Fatalf("unexpected receipt: %#v", receipt)
	}
	after, _ := store.Health("diag-db-auth")
	if after.Status != "healthy" || len(after.Checks) != 1 || after.Checks[0].ID != restoredDBLoginCheckID {
		t.Fatalf("unexpected post-action health: %#v", after)
	}
	if after.ObservedAt != receipt.AppliedAt {
		t.Fatalf("health observation %q differs from receipt %q", after.ObservedAt, receipt.AppliedAt)
	}
	oldTime, _ := time.Parse(time.RFC3339, before.ObservedAt)
	newTime, err := time.Parse(time.RFC3339Nano, after.ObservedAt)
	if err != nil || !newTime.After(oldTime) {
		t.Fatalf("post-action observation is not newer: %q, err=%v", after.ObservedAt, err)
	}
	if store.health["diag-db-auth"].Status != before.Status || store.health["diag-db-auth"].Checks[0].ID != before.Checks[0].ID {
		t.Fatal("embedded health fixture was changed")
	}
	metricsAfter, _ := store.Metrics("diag-db-auth")
	logsAfter, _ := store.Logs("diag-db-auth")
	if !reflect.DeepEqual(metricsAfter, metricsBefore) || !reflect.DeepEqual(logsAfter, logsBefore) {
		t.Fatal("mock action changed metric or log fixtures")
	}
	again, _ := store.Health("diag-healthy")
	if again.Status != unrelated.Status || again.ObservedAt != unrelated.ObservedAt {
		t.Fatal("unrelated incident health changed")
	}
	// Returned slices must not let a caller mutate the overlay in the store.
	after.Checks[0].Detail = "tampered"
	copy, _ := store.Health("diag-db-auth")
	if copy.Checks[0].Detail == "tampered" {
		t.Fatal("caller mutated the stored overlay")
	}

	replayedReceipt, replayed, err := store.ApplyRemediation(request)
	if err != nil || !replayed || replayedReceipt != receipt {
		t.Fatalf("replay changed result: %#v replayed=%v err=%v", replayedReceipt, replayed, err)
	}
	lookup, ok := store.RemediationReceiptFor("diag-db-auth", request.ActionID)
	if !ok || lookup != receipt {
		t.Fatalf("receipt lookup failed: %#v found=%v", lookup, ok)
	}
	if _, ok := store.RemediationReceiptFor("diag-healthy", request.ActionID); ok {
		t.Fatal("receipt leaked to another incident")
	}

	// A fresh process creates a new store from the same embedded baseline.
	fresh, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	reset, _ := fresh.Health("diag-db-auth")
	if reset.Status != before.Status || reset.ObservedAt != before.ObservedAt {
		t.Fatal("fresh store retained the previous process's overlay")
	}
	if _, ok := fresh.RemediationReceiptFor("diag-db-auth", request.ActionID); ok {
		t.Fatal("fresh store retained the previous process's receipt")
	}
}

func TestApplyRemediationRejectsConflictsAndMissingApproval(t *testing.T) {
	store, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	request := approvedRemediationRequest()
	if _, _, err := store.ApplyRemediation(request); err != nil {
		t.Fatal(err)
	}
	changed := request
	changed.Approval.OperatorID = "other-reviewer"
	if _, _, err := store.ApplyRemediation(changed); err != ActionIDConflict {
		t.Fatalf("same action ID with changed request: %v", err)
	}
	newID := request
	newID.ActionID = "action-other-id"
	if _, _, err := store.ApplyRemediation(newID); err != ApprovalAlreadyUsed {
		t.Fatalf("reused approval with a new action ID: %v", err)
	}
	for name, modify := range map[string]func(*RemediationRequest){
		"bad action ID":       func(r *RemediationRequest) { r.ActionID = "../outside" },
		"missing approval":    func(r *RemediationRequest) { r.Approval = ApprovalRecord{} },
		"rejected decision":   func(r *RemediationRequest) { r.Approval.Decision = "reject" },
		"wrong fingerprint":   func(r *RemediationRequest) { r.Approval.ProposalFingerprint = "b" + r.Approval.ProposalFingerprint[1:] },
		"wrong incident":      func(r *RemediationRequest) { r.IncidentID = "diag-healthy" },
		"unknown incident":    func(r *RemediationRequest) { r.IncidentID = "diag-absent" },
		"wrong service":       func(r *RemediationRequest) { r.Service = "worker" },
		"unlisted action":     func(r *RemediationRequest) { r.Action = "restart_service" },
		"invalid proposal ID": func(r *RemediationRequest) { r.ProposalID = "../proposal" },
		"invalid time":        func(r *RemediationRequest) { r.Approval.DecidedAt = "yesterday" },
		"future decision": func(r *RemediationRequest) {
			r.Approval.DecidedAt = time.Now().UTC().Add(time.Hour).Format(time.RFC3339Nano)
		},
	} {
		t.Run(name, func(t *testing.T) {
			candidate := approvedRemediationRequest()
			candidate.ActionID = "action-" + strings.ReplaceAll(name, " ", "-")
			modify(&candidate)
			if _, _, err := store.ApplyRemediation(candidate); err == nil {
				t.Fatal("accepted invalid remediation request")
			}
		})
	}
}

func TestConcurrentExactReplaysApplyOnlyOnce(t *testing.T) {
	store, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	request := approvedRemediationRequest()
	const workers = 12
	results := make(chan struct {
		receipt  RemediationReceipt
		replayed bool
		err      error
	}, workers)
	var group sync.WaitGroup
	for range workers {
		group.Add(1)
		go func() {
			defer group.Done()
			receipt, replayed, err := store.ApplyRemediation(request)
			results <- struct {
				receipt  RemediationReceipt
				replayed bool
				err      error
			}{receipt, replayed, err}
		}()
	}
	group.Wait()
	close(results)
	newEffects := 0
	var first RemediationReceipt
	for result := range results {
		if result.err != nil {
			t.Fatal(result.err)
		}
		if !result.replayed {
			newEffects++
		}
		if first.ActionID == "" {
			first = result.receipt
		} else if result.receipt != first {
			t.Fatalf("concurrent replay returned different receipts: %#v != %#v", result.receipt, first)
		}
	}
	if newEffects != 1 {
		t.Fatalf("applied %d effects, want one", newEffects)
	}
}
