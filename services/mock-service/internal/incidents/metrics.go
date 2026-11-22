package incidents

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"regexp"
	"strings"
	"time"
)

const maxMetricsPerIncident = 50

var metricName = regexp.MustCompile(`^[a-zA-Z_:][a-zA-Z0-9_:]*$`)

//go:embed fixtures/metrics_v1.json
var defaultMetricFixtures []byte

// Metric is one fixed, numeric observation. Window is a duration string such
// as "5m" so the JSON response does not expose Go's nanosecond representation.
type Metric struct {
	ID     string  `json:"id"`
	Name   string  `json:"name"`
	Value  float64 `json:"value"`
	Unit   string  `json:"unit"`
	Window string  `json:"window"`
}

// MetricsSnapshot groups observations for one incident at a fixed fixture time.
// An empty Metrics slice means this fixture has no metrics, not measured zero.
type MetricsSnapshot struct {
	IncidentID string   `json:"incident_id"`
	Service    string   `json:"service"`
	ObservedAt string   `json:"observed_at"`
	Metrics    []Metric `json:"metrics"`
}

// loadMetrics rejects malformed fixture data before it can be served.
// IDs may recur across incidents, but must be unique within one snapshot.
func loadMetrics(data []byte) (map[string]MetricsSnapshot, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var snapshots []MetricsSnapshot
	if err := decoder.Decode(&snapshots); err != nil {
		return nil, fmt.Errorf("decode metrics fixtures: %w", err)
	}
	if len(snapshots) == 0 || len(snapshots) > 100 {
		return nil, errors.New("metrics fixture count is out of bounds")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("metrics fixtures contain trailing JSON")
	}

	result := make(map[string]MetricsSnapshot, len(snapshots))
	for _, snapshot := range snapshots {
		if !ValidID(snapshot.IncidentID) {
			return nil, fmt.Errorf("invalid metrics incident ID %q", snapshot.IncidentID)
		}
		if _, exists := result[snapshot.IncidentID]; exists {
			return nil, fmt.Errorf("duplicate metrics incident ID %q", snapshot.IncidentID)
		}
		if strings.TrimSpace(snapshot.Service) != snapshot.Service || snapshot.Service == "" || len(snapshot.Service) > 64 {
			return nil, fmt.Errorf("invalid metrics service for %q", snapshot.IncidentID)
		}
		if _, err := time.Parse(time.RFC3339, snapshot.ObservedAt); err != nil {
			return nil, fmt.Errorf("invalid metrics time for %q", snapshot.IncidentID)
		}
		if snapshot.Metrics == nil || len(snapshot.Metrics) > maxMetricsPerIncident {
			return nil, fmt.Errorf("invalid metrics list for %q", snapshot.IncidentID)
		}
		seen := make(map[string]bool, len(snapshot.Metrics))
		for _, metric := range snapshot.Metrics {
			if len(metric.ID) > 64 || !observationID.MatchString(metric.ID) || seen[metric.ID] {
				return nil, fmt.Errorf("invalid or duplicate metric ID for %q", snapshot.IncidentID)
			}
			seen[metric.ID] = true
			if !metricName.MatchString(metric.Name) || len(metric.Name) > 128 {
				return nil, fmt.Errorf("invalid metric name for %q", snapshot.IncidentID)
			}
			if metric.Unit == "" || len(metric.Unit) > 32 || strings.TrimSpace(metric.Unit) != metric.Unit {
				return nil, fmt.Errorf("invalid metric unit for %q", snapshot.IncidentID)
			}
			if math.IsNaN(metric.Value) || math.IsInf(metric.Value, 0) {
				return nil, fmt.Errorf("non-finite metric value for %q", snapshot.IncidentID)
			}
			window, err := time.ParseDuration(metric.Window)
			if err != nil || window <= 0 {
				return nil, fmt.Errorf("invalid metric window for %q", snapshot.IncidentID)
			}
		}
		result[snapshot.IncidentID] = snapshot
	}
	return result, nil
}

// Metrics returns a defensive copy so callers cannot change later responses.
// A false result means the incident ID is not in the validated fixture store.
func (s *Store) Metrics(id string) (MetricsSnapshot, bool) {
	if s == nil {
		return MetricsSnapshot{}, false
	}
	snapshot, ok := s.metrics[id]
	if !ok {
		return MetricsSnapshot{}, false
	}
	snapshot.Metrics = append([]Metric{}, snapshot.Metrics...)
	return snapshot, true
}
