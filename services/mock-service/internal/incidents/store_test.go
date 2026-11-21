package incidents

import (
	"strings"
	"testing"
)

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

func TestLoadHealthRejectsInvalidFixtures(t *testing.T) {
	valid := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"healthy","checks":[]}]`
	cases := map[string]string{
		"duplicate ID":   `[` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `,` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `]`,
		"bad ID":         strings.Replace(valid, "diag-one", "../etc", 1),
		"bad time":       strings.Replace(valid, "2026-01-01T00:00:00Z", "yesterday", 1),
		"unknown status": strings.Replace(valid, `"status":"healthy"`, `"status":"down"`, 1),
		"missing checks": strings.Replace(valid, `,"checks":[]`, "", 1),
		"unknown field":  strings.Replace(valid, `"checks":[]`, `"checks":[],"cause":"database"`, 1),
		"trailing JSON":  valid + `{}`,
		"empty fixture":  `[]`,
	}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := LoadHealth([]byte(data)); err == nil {
				t.Fatalf("accepted invalid fixture: %s", data)
			}
		})
	}
}

func TestLoadHealthRejectsDuplicateChecks(t *testing.T) {
	data := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"healthy","checks":[{"id":"ready","status":"healthy","detail":"ok"},{"id":"ready","status":"healthy","detail":"ok"}]}]`
	if _, err := LoadHealth([]byte(data)); err == nil {
		t.Fatal("accepted duplicate check IDs")
	}
}
