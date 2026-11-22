package incidents

import (
	"strings"
	"testing"
)

// TestDefaultStoreAndDefensiveCopy checks fixture loading and response isolation.
func TestDefaultStoreAndDefensiveCopy(t *testing.T) {
	store, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	snapshot, ok := store.Health("diag-healthy")
	if !ok || snapshot.Status != "healthy" || len(snapshot.Checks) != 1 {
		t.Fatalf("unexpected snapshot: %#v, found=%v", snapshot, ok)
	}
	snapshot.Checks[0].Detail = "changed"
	again, _ := store.Health("diag-healthy")
	if again.Checks[0].Detail == "changed" {
		t.Fatal("caller mutated stored checks")
	}
	missing, ok := store.Health("diag-missing")
	if !ok || missing.Status != "unknown" || missing.Checks == nil || len(missing.Checks) != 0 {
		t.Fatalf("missing-data fixture was not preserved: %#v", missing)
	}
}

// TestLoadHealthRejectsInvalidFixtures covers whole-file and record failures.
func TestLoadHealthRejectsInvalidFixtures(t *testing.T) {
	valid := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"unknown","checks":[]}]`
	if _, err := LoadHealth([]byte(valid)); err != nil {
		t.Fatalf("valid missing-data fixture rejected: %v", err)
	}
	cases := map[string]string{
		"duplicate ID":           `[` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `,` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `]`,
		"bad ID":                 strings.Replace(valid, "diag-one", "../etc", 1),
		"bad time":               strings.Replace(valid, "2026-01-01T00:00:00Z", "yesterday", 1),
		"unknown status":         strings.Replace(valid, `"status":"unknown"`, `"status":"down"`, 1),
		"healthy without checks": strings.Replace(valid, `"status":"unknown"`, `"status":"healthy"`, 1),
		"missing checks":         strings.Replace(valid, `,"checks":[]`, "", 1),
		"unknown field":          strings.Replace(valid, `"checks":[]`, `"checks":[],"cause":"database"`, 1),
		"trailing JSON":          valid + `{}`,
		"empty fixture":          `[]`,
	}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := LoadHealth([]byte(data)); err == nil {
				t.Fatalf("accepted invalid fixture: %s", data)
			}
		})
	}
}

// TestLoadHealthRejectsDuplicateChecks keeps evidence references unambiguous.
func TestLoadHealthRejectsDuplicateChecks(t *testing.T) {
	data := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"healthy","checks":[{"id":"ready","status":"healthy","detail":"ok"},{"id":"ready","status":"healthy","detail":"ok"}]}]`
	if _, err := LoadHealth([]byte(data)); err == nil {
		t.Fatal("accepted duplicate check IDs")
	}
}

// TestLoadHealthRejectsUnsafeCheckID keeps future evidence references stable.
func TestLoadHealthRejectsUnsafeCheckID(t *testing.T) {
	data := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"healthy","checks":[{"id":"bad/id","status":"healthy","detail":"ok"}]}]`
	if _, err := LoadHealth([]byte(data)); err == nil {
		t.Fatal("accepted unsafe check ID")
	}
}

// TestLoadStoreRejectsMismatchedSnapshots keeps both diagnostic routes aligned.
func TestLoadStoreRejectsMismatchedSnapshots(t *testing.T) {
	for name, metrics := range map[string]string{
		"missing ID":    `[{"incident_id":"diag-other","service":"api","observed_at":"2026-01-01T00:00:00Z","metrics":[]}]`,
		"wrong service": `[{"incident_id":"diag-one","service":"worker","observed_at":"2026-01-01T00:00:00Z","metrics":[]}]`,
		"wrong time":    `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:01:00Z","metrics":[]}]`,
	} {
		t.Run(name, func(t *testing.T) {
			health := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"unknown","checks":[]}]`
			if _, err := loadStore([]byte(health), []byte(metrics)); err == nil {
				t.Fatal("accepted inconsistent diagnostic snapshots")
			}
		})
	}
}
