package incidents

import (
	"strings"
	"testing"
)

func TestDefaultLogsAndDefensiveCopy(t *testing.T) {
	store, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	snapshot, ok := store.Logs("diag-degraded")
	if !ok || len(snapshot.Entries) != 3 || snapshot.Entries[0].ID == "" {
		t.Fatalf("unexpected log snapshot: %#v, found=%v", snapshot, ok)
	}
	snapshot.Entries[0].Message = "changed"
	again, _ := store.Logs("diag-degraded")
	if again.Entries[0].Message == "changed" {
		t.Fatal("caller mutated stored logs")
	}
	missing, ok := store.Logs("diag-missing")
	if !ok || missing.Entries == nil || len(missing.Entries) != 0 {
		t.Fatalf("missing-data fixture was not preserved: %#v", missing)
	}
}

func TestLoadLogsRejectsInvalidFixtures(t *testing.T) {
	valid := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","entries":[{"id":"ready","timestamp":"2025-12-31T23:59:00Z","component":"api","level":"info","message":"Ready."}]}]`
	if _, err := loadLogs([]byte(valid)); err != nil {
		t.Fatalf("valid log fixture rejected: %v", err)
	}
	entry := `{"id":"ready","timestamp":"2025-12-31T23:59:00Z","component":"api","level":"info","message":"Ready."}`
	cases := map[string]string{
		"duplicate ID":       `[` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `,` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `]`,
		"bad incident ID":    strings.Replace(valid, "diag-one", "../etc", 1),
		"missing entries":    strings.Replace(valid, `,"entries":[`+entry+`]`, "", 1),
		"duplicate entry":    strings.Replace(valid, `"entries":[`+entry+`]`, `"entries":[`+entry+`,`+entry+`]`, 1),
		"bad timestamp":      strings.Replace(valid, "2025-12-31T23:59:00Z", "yesterday", 1),
		"future entry":       strings.Replace(valid, "2025-12-31T23:59:00Z", "2026-01-01T00:01:00Z", 1),
		"unknown level":      strings.Replace(valid, `"level":"info"`, `"level":"fatal"`, 1),
		"unknown field":      strings.Replace(valid, `"message":"Ready."`, `"message":"Ready.","cause":"database"`, 1),
		"trailing JSON":      valid + `{}`,
		"empty fixture":      `[]`,
		"mismatched service": strings.Replace(valid, `"service":"api"`, `"service":"worker"`, 1),
	}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			if name == "mismatched service" {
				health := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","status":"unknown","checks":[]}]`
				metrics := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","metrics":[]}]`
				if _, err := loadStoreWithLogs([]byte(health), []byte(metrics), []byte(data)); err == nil {
					t.Fatal("accepted mismatched log service")
				}
				return
			}
			if _, err := loadLogs([]byte(data)); err == nil {
				t.Fatalf("accepted invalid fixture: %s", data)
			}
		})
	}
}
