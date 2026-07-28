SHELL := /bin/sh

GO ?= go
SCHEMA := api/events.json
GO_OUTPUT := internal/eventwire/types.gen.go
DOC_OUTPUT := docs/events.md
GENERATOR := $(GO) run ./cmd/eventgen

.PHONY: generate check-generated test ci

generate:
	$(GENERATOR) -schema $(SCHEMA) -go-out $(GO_OUTPUT) -doc-out $(DOC_OUTPUT)

# BUG: a check must not repair drift, and release source archives do not contain
# Git metadata. Generate candidates away from the source tree before comparing.
check-generated:
	@$(MAKE) generate
	@git diff --exit-code -- $(GO_OUTPUT) $(DOC_OUTPUT)

test:
	$(GO) test -count=1 ./...

ci: check-generated test
