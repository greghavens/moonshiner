// Command mockserve runs the SDDC LCM mock on loopback so the drill can be
// driven by hand. It builds its routes from docs/contract.json and prints the
// requests it received when it is stopped.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"example.com/vcf/restoredrill/internal/mocklcm"
)

func main() {
	contract := flag.String("contract", "docs/contract.json", "path to the derived REST contract")
	token := flag.String("token", "drill-token", "bearer token the mock demands")
	polls := flag.Int("polls", 3, "how many polls a task answers before it settles")
	fail := flag.String("fail", "", "componentType whose restore task should fail")
	flag.Parse()

	restores := map[string]mocklcm.TaskOutcome{}
	for _, c := range mocklcm.SampleComponents() {
		if *fail != "" && c.ComponentType == *fail {
			restores[c.ID] = mocklcm.TaskOutcome{
				Status:      "FAILED",
				FailedStage: "restore-data",
				Errors: []mocklcm.Message{{
					ID:             "com.broadcom.lcm.restore.datastore.full",
					DefaultMessage: "Datastore has no free space",
				}},
			}
		}
	}

	server, err := mocklcm.Start(mocklcm.Options{
		ContractPath:        *contract,
		Token:               *token,
		Components:          mocklcm.SampleComponents(),
		Backups:             mocklcm.SampleBackups(),
		Restores:            restores,
		PollsBeforeTerminal: *polls,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "mockserve: %v\n", err)
		os.Exit(1)
	}
	defer server.Close()

	fmt.Printf("mock SDDC LCM listening on %s\n", server.URL)
	fmt.Printf("bearer token: %s\n", *token)
	fmt.Println("press Ctrl-C to stop and print the request log")

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop

	fmt.Println()
	for _, rec := range server.Log() {
		line := map[string]any{
			"seq":       rec.Seq,
			"operation": rec.OperationID,
			"method":    rec.Method,
			"path":      rec.Path,
		}
		if rec.RawQuery != "" {
			line["query"] = rec.RawQuery
		}
		if len(rec.Body) > 0 {
			var body any
			if err := json.Unmarshal(rec.Body, &body); err == nil {
				line["body"] = body
			}
		}
		if v := rec.Header.Get("X-Correlation-Id"); v != "" {
			line["correlationId"] = v
		}
		if rec.Violation != "" {
			line["violation"] = rec.Violation
		}
		encoded, _ := json.Marshal(line)
		fmt.Println(string(encoded))
	}
	for _, v := range server.Violations() {
		fmt.Fprintf(os.Stderr, "contract violation: %s\n", v)
	}
}
