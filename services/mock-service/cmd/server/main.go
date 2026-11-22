package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/sentinel-sre/mock-service/internal/api"
	"github.com/sentinel-sre/mock-service/internal/incidents"
)

// main validates embedded fixtures before serving read-only routes on loopback.
func main() {
	store, err := incidents.DefaultStore()
	if err != nil {
		log.Fatalf("load synthetic health fixtures: %v", err)
	}
	port := os.Getenv("SENTINEL_MOCK_SERVICE_PORT")
	if port == "" {
		port = "8080"
	}
	server := &http.Server{
		Addr:              "127.0.0.1:" + port,
		Handler:           api.NewHandler(store),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("mock service listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}
