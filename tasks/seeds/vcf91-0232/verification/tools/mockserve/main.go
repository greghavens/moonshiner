// Command mockserve runs the loopback SDDC LCM mock so the fleet run can be
// driven by hand. It builds its routes from docs/contract.json and contacts no
// VMware endpoint.
package main

import (
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"example.com/vcf/fleetlcm/internal/mocklcm"
)

func main() {
	var (
		contract  = flag.String("contract", "docs/contract.json", "derived REST contract to serve")
		tokens    = flag.String("tokens", "tok-alpha,tok-beta", "comma separated access tokens the service accepts, in order")
		tokenUses = flag.Int("token-uses", 3, "authenticated requests each token serves before it expires; 0 means never")
		scenario  = flag.String("scenario", "succeed", "task scenario: succeed, retry or fail")
		logPath   = flag.String("log", "", "write the request log here on exit")
	)
	flag.Parse()

	var script mocklcm.TaskScript
	switch *scenario {
	case "succeed":
		script = mocklcm.DefaultTaskScript()
	case "retry":
		script = mocklcm.FailThenSucceedScript()
	case "fail":
		script = mocklcm.TerminalFailureScript()
	default:
		fmt.Fprintf(os.Stderr, "mockserve: unknown scenario %q\n", *scenario)
		os.Exit(2)
	}

	m, err := mocklcm.New(mocklcm.Config{
		ContractPath: *contract,
		Tokens:       strings.Split(*tokens, ","),
		TokenUses:    *tokenUses,
		Inventory:    mocklcm.DefaultInventory(),
		Depot:        mocklcm.DefaultDepot(),
		Task:         script,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "mockserve: %v\n", err)
		os.Exit(2)
	}
	fmt.Printf("mockserve: listening on %s (scenario %s)\n", m.URL(), *scenario)
	fmt.Printf("mockserve: tokens %s, %d use(s) each\n", *tokens, *tokenUses)

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig

	if *logPath != "" {
		if err := m.WriteLog(*logPath); err != nil {
			fmt.Fprintf(os.Stderr, "mockserve: write log: %v\n", err)
		}
	}
	for _, v := range m.Violations() {
		fmt.Fprintf(os.Stderr, "mockserve: contract violation: %s\n", v)
	}
	_ = m.Close()
}
