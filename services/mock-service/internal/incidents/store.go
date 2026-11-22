package incidents

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
	"time"
)

const maxChecks = 20

var incidentID = regexp.MustCompile(`^diag-[a-z0-9]+(?:-[a-z0-9]+)*$`)
var observationID = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]*$`)

//go:embed fixtures/health_v1.json
var defaultFixtures []byte

// HealthCheck is one simulated probe with a stable ID for later evidence links.
type HealthCheck struct {
	ID     string `json:"id"`
	Status string `json:"status"`
	Detail string `json:"detail"`
}

// HealthSnapshot is the fixed observation returned for one incident.
type HealthSnapshot struct {
	IncidentID string        `json:"incident_id"`
	Service    string        `json:"service"`
	ObservedAt string        `json:"observed_at"`
	Status     string        `json:"status"`
	Checks     []HealthCheck `json:"checks"`
}

// Store holds validated, immutable synthetic observations.
type Store struct {
	health  map[string]HealthSnapshot
	metrics map[string]MetricsSnapshot
}

// ValidID restricts lookups to synthetic incident IDs, never file paths or URLs.
func ValidID(id string) bool { return incidentID.MatchString(id) }

// validStatus accepts only statuses defined by the incident health contract.
func validStatus(status string) bool {
	switch status {
	case "healthy", "degraded", "unhealthy", "unknown":
		return true
	default:
		return false
	}
}

// LoadHealth rejects a malformed fixture before any snapshot becomes available.
// It retains fixed observation times so tests and replay stay deterministic.
func LoadHealth(data []byte) (*Store, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var snapshots []HealthSnapshot
	if err := decoder.Decode(&snapshots); err != nil {
		return nil, fmt.Errorf("decode health fixtures: %w", err)
	}
	if len(snapshots) == 0 {
		return nil, errors.New("health fixtures are empty")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("health fixtures contain trailing JSON")
	}

	store := &Store{health: make(map[string]HealthSnapshot, len(snapshots))}
	for _, snapshot := range snapshots {
		if !ValidID(snapshot.IncidentID) {
			return nil, fmt.Errorf("invalid incident ID %q", snapshot.IncidentID)
		}
		if _, exists := store.health[snapshot.IncidentID]; exists {
			return nil, fmt.Errorf("duplicate incident ID %q", snapshot.IncidentID)
		}
		if strings.TrimSpace(snapshot.Service) != snapshot.Service || snapshot.Service == "" || len(snapshot.Service) > 64 {
			return nil, fmt.Errorf("invalid service for %q", snapshot.IncidentID)
		}
		if _, err := time.Parse(time.RFC3339, snapshot.ObservedAt); err != nil {
			return nil, fmt.Errorf("invalid observation time for %q", snapshot.IncidentID)
		}
		if !validStatus(snapshot.Status) || snapshot.Checks == nil || len(snapshot.Checks) > maxChecks {
			return nil, fmt.Errorf("invalid health status or checks for %q", snapshot.IncidentID)
		}
		if len(snapshot.Checks) == 0 && snapshot.Status != "unknown" {
			return nil, fmt.Errorf("health without checks must be unknown for %q", snapshot.IncidentID)
		}
		checkIDs := make(map[string]bool, len(snapshot.Checks))
		for _, check := range snapshot.Checks {
			if len(check.ID) > 64 || !observationID.MatchString(check.ID) || checkIDs[check.ID] || !validStatus(check.Status) || strings.TrimSpace(check.Detail) == "" || len(check.Detail) > 256 {
				return nil, fmt.Errorf("invalid health check for %q", snapshot.IncidentID)
			}
			checkIDs[check.ID] = true
		}
		store.health[snapshot.IncidentID] = snapshot
	}
	return store, nil
}

// loadStore combines validated health and metrics fixtures for the same IDs.
// A missing metrics snapshot must be represented by an empty metrics list.
func loadStore(healthData, metricsData []byte) (*Store, error) {
	store, err := LoadHealth(healthData)
	if err != nil {
		return nil, err
	}
	metrics, err := loadMetrics(metricsData)
	if err != nil {
		return nil, err
	}
	if len(metrics) != len(store.health) {
		return nil, errors.New("health and metrics fixtures have different incident IDs")
	}
	for id, snapshot := range store.health {
		metricSnapshot, ok := metrics[id]
		if !ok || metricSnapshot.Service != snapshot.Service || metricSnapshot.ObservedAt != snapshot.ObservedAt {
			return nil, fmt.Errorf("metrics fixture does not match health incident %q", id)
		}
	}
	store.metrics = metrics
	return store, nil
}

// DefaultStore loads the embedded diagnostic snapshots at server startup.
func DefaultStore() (*Store, error) {
	return loadStore(defaultFixtures, defaultMetricFixtures)
}

// Health returns a copy of a snapshot and its checks so callers cannot mutate
// later responses. The bool is false for an unknown incident ID.
func (s *Store) Health(id string) (HealthSnapshot, bool) {
	if s == nil {
		return HealthSnapshot{}, false
	}
	snapshot, ok := s.health[id]
	if !ok {
		return HealthSnapshot{}, false
	}
	snapshot.Checks = append([]HealthCheck{}, snapshot.Checks...)
	return snapshot, true
}
