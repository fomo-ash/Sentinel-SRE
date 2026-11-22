package incidents

import (
	"strings"
	"testing"
)

// TestDefaultMetricsAndDefensiveCopy verifies loaded and missing observations.
func TestDefaultMetricsAndDefensiveCopy(t *testing.T) {
	store, err := DefaultStore()
	if err != nil {
		t.Fatal(err)
	}
	snapshot, ok := store.Metrics("diag-degraded")
	if !ok || snapshot.Service != "api" || len(snapshot.Metrics) != 2 {
		t.Fatalf("unexpected metrics snapshot: %#v, found=%v", snapshot, ok)
	}
	snapshot.Metrics[0].Value = 999
	again, _ := store.Metrics("diag-degraded")
	if again.Metrics[0].Value == 999 {
		t.Fatal("caller mutated stored metrics")
	}
	missing, ok := store.Metrics("diag-missing")
	if !ok || missing.Metrics == nil || len(missing.Metrics) != 0 {
		t.Fatalf("missing metrics should be an empty list: %#v", missing)
	}
	if _, ok := store.Metrics("diag-absent"); ok {
		t.Fatal("unknown incident returned metrics")
	}
}

// TestLoadMetricsRejectsInvalidFixtures covers schema and bounded data errors.
func TestLoadMetricsRejectsInvalidFixtures(t *testing.T) {
	valid := `[{"incident_id":"diag-one","service":"api","observed_at":"2026-01-01T00:00:00Z","metrics":[{"id":"errors","name":"http_error_ratio","value":0,"unit":"ratio","window":"5m"}]}]`
	if _, err := loadMetrics([]byte(valid)); err != nil {
		t.Fatalf("valid zero metric rejected: %v", err)
	}
	for name, data := range map[string]string{
		"bad JSON":         `{`,
		"empty fixture":    `[]`,
		"duplicate ID":     `[` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `,` + strings.TrimSuffix(strings.TrimPrefix(valid, "["), "]") + `]`,
		"bad incident":     strings.Replace(valid, "diag-one", "../etc", 1),
		"bad time":         strings.Replace(valid, "2026-01-01T00:00:00Z", "yesterday", 1),
		"missing metrics":  strings.Replace(valid, `,"metrics":[{"id":"errors","name":"http_error_ratio","value":0,"unit":"ratio","window":"5m"}]`, "", 1),
		"duplicate metric": strings.Replace(valid, `"window":"5m"}]`, `"window":"5m"},{"id":"errors","name":"http_error_ratio","value":0,"unit":"ratio","window":"5m"}]`, 1),
		"bad name":         strings.Replace(valid, "http_error_ratio", "has spaces", 1),
		"bad metric ID":    strings.Replace(valid, `"id":"errors"`, `"id":"bad/id"`, 1),
		"bad unit":         strings.Replace(valid, `"unit":"ratio"`, `"unit":""`, 1),
		"bad window":       strings.Replace(valid, `"window":"5m"`, `"window":"0s"`, 1),
		"unknown field":    strings.Replace(valid, `"window":"5m"`, `"window":"5m","cause":"database"`, 1),
		"trailing JSON":    valid + `{}`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := loadMetrics([]byte(data)); err == nil {
				t.Fatalf("accepted invalid metrics fixture: %s", data)
			}
		})
	}
}
