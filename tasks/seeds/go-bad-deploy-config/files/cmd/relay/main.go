package main

import (
	"bufio"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"relayconfig/internal/config"
)

func main() {
	os.Exit(run(os.Args[1:], os.Environ(), os.Stdout, os.Stderr))
}

func run(args, processEnv []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("relay", flag.ContinueOnError)
	flags.SetOutput(stderr)
	configPath := flags.String("config", "", "path to relay JSON configuration")
	envPath := flags.String("env-file", "", "optional generated environment file")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintln(stderr, "unexpected positional arguments")
		return 2
	}

	environ := processEnv
	if *envPath != "" {
		var err error
		environ, err = readEnvFile(*envPath)
		if err != nil {
			fmt.Fprintf(stderr, "startup aborted: %v\n", err)
			return 1
		}
	}

	cfg, err := config.Load(*configPath, environ)
	if err != nil {
		fmt.Fprintf(stderr, "startup aborted: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "relay startup check passed: %s\n", cfg.SafeSummary())
	return 0
}

func readEnvFile(path string) ([]string, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("read env file %q: %w", path, err)
	}
	defer file.Close()

	var environ []string
	scanner := bufio.NewScanner(file)
	for lineNumber := 1; scanner.Scan(); lineNumber++ {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if _, _, ok := strings.Cut(line, "="); !ok {
			return nil, fmt.Errorf("read env file %q: line %d has no '='", path, lineNumber)
		}
		environ = append(environ, line)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read env file %q: %w", path, err)
	}
	return environ, nil
}
