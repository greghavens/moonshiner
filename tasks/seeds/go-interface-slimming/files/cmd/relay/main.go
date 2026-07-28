package main

import (
	"example.com/go-interface-slimming/internal/dispatch"
	"example.com/go-interface-slimming/internal/httpapi"
	"example.com/go-interface-slimming/internal/worker"
)

func main() {
	service := dispatch.NewMemoryService(nil)
	_ = httpapi.New(service)
	_ = worker.New(service, 4)
}
