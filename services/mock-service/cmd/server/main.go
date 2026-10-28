package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/sentinel-sre/mock-service/internal/api"
)

func main() {
	port := os.Getenv("SENTINEL_MOCK_SERVICE_PORT")
	if port == "" {
		port = "8080"
	}
	server := &http.Server{
		Addr:              "127.0.0.1:" + port,
		Handler:           api.NewHandler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("mock service listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}

