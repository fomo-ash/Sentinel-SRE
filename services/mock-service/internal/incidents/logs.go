package incidents

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
)

const maxLogEntriesPerIncident = 50

//go:embed fixtures/logs_v1.json
var defaultLogFixtures []byte

// LogEntry is one fixed, synthetic observation with a stable evidence ID.
type LogEntry struct {
	ID        string `json:"id"`
	Timestamp string `json:"timestamp"`
	Component string `json:"component"`
	Level     string `json:"level"`
	Message   string `json:"message"`
}

// LogSnapshot groups log observations for one incident at a fixed fixture time.
type LogSnapshot struct {
	IncidentID string     `json:"incident_id"`
	Service    string     `json:"service"`
	ObservedAt string     `json:"observed_at"`
	Entries    []LogEntry `json:"entries"`
}

// loadLogs rejects malformed or unbounded data before it can be served.
func loadLogs(data []byte) (map[string]LogSnapshot, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var snapshots []LogSnapshot
	if err := decoder.Decode(&snapshots); err != nil {
		return nil, fmt.Errorf("decode log fixtures: %w", err)
	}
	if len(snapshots) == 0 || len(snapshots) > 100 {
		return nil, errors.New("log fixture count is out of bounds")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("log fixtures contain trailing JSON")
	}

	result := make(map[string]LogSnapshot, len(snapshots))
	for _, snapshot := range snapshots {
		if !ValidID(snapshot.IncidentID) {
			return nil, fmt.Errorf("invalid log incident ID %q", snapshot.IncidentID)
		}
		if _, exists := result[snapshot.IncidentID]; exists {
			return nil, fmt.Errorf("duplicate log incident ID %q", snapshot.IncidentID)
		}
		if strings.TrimSpace(snapshot.Service) != snapshot.Service || snapshot.Service == "" || len(snapshot.Service) > 64 {
			return nil, fmt.Errorf("invalid log service for %q", snapshot.IncidentID)
		}
		observedAt, err := time.Parse(time.RFC3339, snapshot.ObservedAt)
		if err != nil {
			return nil, fmt.Errorf("invalid log observation time for %q", snapshot.IncidentID)
		}
		if snapshot.Entries == nil || len(snapshot.Entries) > maxLogEntriesPerIncident {
			return nil, fmt.Errorf("invalid log entries for %q", snapshot.IncidentID)
		}
		seen := make(map[string]bool, len(snapshot.Entries))
		for _, entry := range snapshot.Entries {
			entryTime, err := time.Parse(time.RFC3339, entry.Timestamp)
			if len(entry.ID) > 64 || !observationID.MatchString(entry.ID) || seen[entry.ID] || err != nil || entryTime.After(observedAt) {
				return nil, fmt.Errorf("invalid log ID or time for %q", snapshot.IncidentID)
			}
			seen[entry.ID] = true
			if strings.TrimSpace(entry.Component) != entry.Component || entry.Component == "" || len(entry.Component) > 64 {
				return nil, fmt.Errorf("invalid log component for %q", snapshot.IncidentID)
			}
			switch entry.Level {
			case "debug", "info", "warn", "error":
			default:
				return nil, fmt.Errorf("invalid log level for %q", snapshot.IncidentID)
			}
			if strings.TrimSpace(entry.Message) == "" || len(entry.Message) > 256 {
				return nil, fmt.Errorf("invalid log message for %q", snapshot.IncidentID)
			}
		}
		result[snapshot.IncidentID] = snapshot
	}
	return result, nil
}

// Logs returns a defensive copy, or false when the incident is unknown.
func (s *Store) Logs(id string) (LogSnapshot, bool) {
	if s == nil {
		return LogSnapshot{}, false
	}
	snapshot, ok := s.logs[id]
	if !ok {
		return LogSnapshot{}, false
	}
	snapshot.Entries = append([]LogEntry{}, snapshot.Entries...)
	return snapshot, true
}
