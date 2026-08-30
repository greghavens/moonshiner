package contracttest

import (
	"context"
	"reflect"
	"strings"
	"testing"
	"time"

	"example.com/vcf/restoredrill/drill"
	"example.com/vcf/restoredrill/internal/mocklcm"
)

func rep(name string, n int) []string {
	out := make([]string, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, name)
	}
	return out
}

func flat(groups ...[]string) []string {
	var out []string
	for _, g := range groups {
		out = append(out, g...)
	}
	return out
}

type scenario struct {
	name string
	plan map[string]any
	mock mocklcm.Options
	// wantErr is a substring of the error the drill must return. Empty means
	// the drill must carry the plan out and return a report.
	wantErr string
	// wantSeq is every request the drill must make, in order, named by the
	// operationId of the contract route it matched.
	wantSeq []string
	// wantReport is the report the drill must produce.
	wantReport map[string]any
	// wire asserts the exact shape of the recorded requests.
	wire func(t *testing.T, s *mocklcm.Server)
}

func TestRestoreDrillContract(t *testing.T) {
	t.Parallel()

	scenarios := []scenario{
		{
			name: "every optional value supplied",
			plan: map[string]any{
				"scope":                "FLEET",
				"correlationId":        "drill-2026-02-11",
				"encryptionPassphrase": "cor-rect-horse",
				"components": []any{
					map[string]any{
						"componentType": "vidb",
						"window": map[string]any{
							"start": "2026-02-10T00:00:00Z",
							"end":   "2026-02-11T00:00:00Z",
						},
					},
					map[string]any{"componentType": "opscp"},
				},
			},
			mock: mocklcm.Options{PollsBeforeTerminal: 3},
			wantSeq: flat(
				[]string{"getComponents"},
				[]string{"getComponentsBackups", "backupRestoreComponentsAction"}, rep("getTask", 3),
				[]string{"getComponentsBackups", "backupRestoreComponentsAction"}, rep("getTask", 3),
				[]string{"fetchComponentStatuses"},
			),
			wantReport: map[string]any{
				"outcome": "succeeded",
				"scope":   "FLEET",
				"components": []any{
					map[string]any{
						"componentType": "vidb", "componentId": vidbID, "version": "9.1.0.0",
						"outcome": "restored",
						"backup": map[string]any{
							"name":  "2026-02-10T02-00-00Z",
							"path":  "/backups/vidb/2026-02-10T02-00-00Z",
							"point": "2026-02-10T14-30-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000001", "taskStatus": "SUCCEEDED",
						"postRestoreStatus": "Running",
					},
					map[string]any{
						"componentType": "opscp", "componentId": opscpID, "version": "9.1.0.0",
						"outcome": "restored",
						"backup": map[string]any{
							"name": "2026-02-10T03-15-00Z",
							"path": "/backups/opscp/2026-02-10T03-15-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000002", "taskStatus": "SUCCEEDED",
						"postRestoreStatus": "Running",
					},
				},
			},
			wire: func(t *testing.T, s *mocklcm.Server) {
				requireQuery(t, s.Requests("getComponents")[0], map[string]string{"scope": "FLEET"})

				backups := s.Requests("getComponentsBackups")
				requireQuery(t, backups[0], map[string]string{
					"componentId": vidbID,
					"periodStart": "2026-02-10T00:00:00Z",
					"periodEnd":   "2026-02-11T00:00:00Z",
				})
				// opscp has no window, so neither bound is sent.
				requireQuery(t, backups[1], map[string]string{"componentId": opscpID})

				actions := s.Requests("backupRestoreComponentsAction")
				requireBody(t, actions[0], `{
					"actionType": "ComponentsRestoreSpec",
					"components": [{
						"componentId": "`+vidbID+`",
						"componentType": "vidb",
						"path": "/backups/vidb/2026-02-10T02-00-00Z",
						"point": "2026-02-10T14-30-00Z"
					}],
					"encryptionPassphrase": "cor-rect-horse"
				}`)
				// The opscp backup lists no restore points, so point is absent.
				requireBody(t, actions[1], `{
					"actionType": "ComponentsRestoreSpec",
					"components": [{
						"componentId": "`+opscpID+`",
						"componentType": "opscp",
						"path": "/backups/opscp/2026-02-10T03-15-00Z"
					}],
					"encryptionPassphrase": "cor-rect-horse"
				}`)
				for _, action := range actions {
					if got := action.Header.Get("X-Correlation-Id"); got != "drill-2026-02-11" {
						t.Errorf("request %d X-Correlation-Id is %q, want %q",
							action.Seq, got, "drill-2026-02-11")
					}
				}
				// Only backupRestoreComponentsAction declares the header.
				for _, rec := range s.Log() {
					if rec.OperationID == "backupRestoreComponentsAction" {
						continue
					}
					if got := rec.Header.Get("X-Correlation-Id"); got != "" {
						t.Errorf("request %d (%s) sent X-Correlation-Id, which that operation does not declare",
							rec.Seq, rec.OperationID)
					}
				}

				requireBody(t, s.Requests("fetchComponentStatuses")[0],
					`{"componentIds": ["`+vidbID+`", "`+opscpID+`"]}`)
			},
		},
		{
			name: "no optional value supplied",
			plan: map[string]any{
				"components": []any{map[string]any{"componentType": "opscp"}},
			},
			mock: mocklcm.Options{PollsBeforeTerminal: 2},
			wantSeq: flat(
				[]string{"getComponents", "getComponentsBackups", "backupRestoreComponentsAction"},
				rep("getTask", 2),
				[]string{"fetchComponentStatuses"},
			),
			wantReport: map[string]any{
				"outcome": "succeeded",
				"components": []any{
					map[string]any{
						"componentType": "opscp", "componentId": opscpID, "version": "9.1.0.0",
						"outcome": "restored",
						"backup": map[string]any{
							"name": "2026-02-10T03-15-00Z",
							"path": "/backups/opscp/2026-02-10T03-15-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000001", "taskStatus": "SUCCEEDED",
						"postRestoreStatus": "Running",
					},
				},
			},
			wire: func(t *testing.T, s *mocklcm.Server) {
				// The plan sets no scope, so there is no query string at all.
				requireQuery(t, s.Requests("getComponents")[0], map[string]string{})
				requireQuery(t, s.Requests("getComponentsBackups")[0],
					map[string]string{"componentId": opscpID})
				requireBody(t, s.Requests("backupRestoreComponentsAction")[0], `{
					"actionType": "ComponentsRestoreSpec",
					"components": [{
						"componentId": "`+opscpID+`",
						"componentType": "opscp",
						"path": "/backups/opscp/2026-02-10T03-15-00Z"
					}]
				}`)
				requireNoHeaderAnywhere(t, s, "X-Correlation-Id")
			},
		},
		{
			name: "a failed restore halts the drill",
			plan: map[string]any{
				"scope": "FLEET",
				"components": []any{
					map[string]any{"componentType": "vidb"},
					map[string]any{"componentType": "opscp"},
					map[string]any{"componentType": "vcfops"},
				},
			},
			mock: mocklcm.Options{
				PollsBeforeTerminal: 2,
				Restores: map[string]mocklcm.TaskOutcome{
					opscpID: {
						Status:      "FAILED",
						FailedStage: "restore-data",
						Errors: []mocklcm.Message{
							{ID: "com.broadcom.lcm.restore.datastore.full", DefaultMessage: "Datastore has no free space"},
							{ID: "com.broadcom.lcm.restore.rollback", DefaultMessage: "Rolled back to the pre-restore snapshot"},
						},
					},
				},
			},
			wantSeq: flat(
				[]string{"getComponents"},
				[]string{"getComponentsBackups", "backupRestoreComponentsAction"}, rep("getTask", 2),
				[]string{"getComponentsBackups", "backupRestoreComponentsAction"}, rep("getTask", 2),
				[]string{"fetchComponentStatuses"},
			),
			wantReport: map[string]any{
				"outcome": "failed",
				"scope":   "FLEET",
				"components": []any{
					map[string]any{
						"componentType": "vidb", "componentId": vidbID, "version": "9.1.0.0",
						"outcome": "restored",
						"backup": map[string]any{
							"name":  "2026-02-10T02-00-00Z",
							"path":  "/backups/vidb/2026-02-10T02-00-00Z",
							"point": "2026-02-10T14-30-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000001", "taskStatus": "SUCCEEDED",
						"postRestoreStatus": "Running",
					},
					map[string]any{
						"componentType": "opscp", "componentId": opscpID, "version": "9.1.0.0",
						"outcome": "failed",
						"backup": map[string]any{
							"name": "2026-02-10T03-15-00Z",
							"path": "/backups/opscp/2026-02-10T03-15-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000002", "taskStatus": "FAILED",
						"failure": map[string]any{
							"taskId":      "00000000-0000-4000-8000-000000000002",
							"failedStage": "restore-data",
							"errors": []any{
								map[string]any{
									"id":             "com.broadcom.lcm.restore.datastore.full",
									"defaultMessage": "Datastore has no free space",
								},
								map[string]any{
									"id":             "com.broadcom.lcm.restore.rollback",
									"defaultMessage": "Rolled back to the pre-restore snapshot",
								},
							},
						},
					},
					map[string]any{
						"componentType": "vcfops", "componentId": vcfopsID, "version": "9.1.0.0",
						"outcome": "skipped",
					},
				},
			},
			wire: func(t *testing.T, s *mocklcm.Server) {
				// Nothing at all is sent for the component the drill never reached.
				for _, rec := range s.Log() {
					if strings.Contains(string(rec.Body), vcfopsID) {
						t.Errorf("request %d (%s) mentions the component the drill never reached",
							rec.Seq, rec.OperationID)
					}
					if rec.Query.Get("componentId") == vcfopsID {
						t.Errorf("request %d (%s) looked up backups for the component the drill never reached",
							rec.Seq, rec.OperationID)
					}
				}
				// The status check covers only what was actually restored.
				requireBody(t, s.Requests("fetchComponentStatuses")[0],
					`{"componentIds": ["`+vidbID+`"]}`)
			},
		},
		{
			name: "the first restore fails so nothing is restored",
			plan: map[string]any{
				"scope": "FLEET",
				"components": []any{
					map[string]any{"componentType": "vidb"},
					map[string]any{"componentType": "opscp"},
				},
			},
			mock: mocklcm.Options{
				PollsBeforeTerminal: 2,
				Restores: map[string]mocklcm.TaskOutcome{
					vidbID: {
						Status: "CANCELED",
						Errors: []mocklcm.Message{
							{ID: "com.broadcom.lcm.restore.canceled", DefaultMessage: "Restore canceled by an operator"},
						},
					},
				},
			},
			wantSeq: flat(
				[]string{"getComponents", "getComponentsBackups", "backupRestoreComponentsAction"},
				rep("getTask", 2),
			),
			wantReport: map[string]any{
				"outcome": "failed",
				"scope":   "FLEET",
				"components": []any{
					map[string]any{
						"componentType": "vidb", "componentId": vidbID, "version": "9.1.0.0",
						"outcome": "failed",
						"backup": map[string]any{
							"name":  "2026-02-10T02-00-00Z",
							"path":  "/backups/vidb/2026-02-10T02-00-00Z",
							"point": "2026-02-10T14-30-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000001", "taskStatus": "CANCELED",
						"failure": map[string]any{
							"taskId": "00000000-0000-4000-8000-000000000001",
							"errors": []any{
								map[string]any{
									"id":             "com.broadcom.lcm.restore.canceled",
									"defaultMessage": "Restore canceled by an operator",
								},
							},
						},
					},
					map[string]any{
						"componentType": "opscp", "componentId": opscpID, "version": "9.1.0.0",
						"outcome": "skipped",
					},
				},
			},
			wire: func(t *testing.T, s *mocklcm.Server) {
				if got := s.Requests("fetchComponentStatuses"); len(got) != 0 {
					t.Errorf("nothing was restored, so there are no component statuses to fetch; got %d request(s)",
						len(got))
				}
			},
		},
		{
			name: "a restored component that did not come back running",
			plan: map[string]any{
				"scope":      "FLEET",
				"components": []any{map[string]any{"componentType": "vcfops"}},
			},
			mock: mocklcm.Options{
				PollsBeforeTerminal: 2,
				Statuses:            map[string]string{vcfopsID: "NotRunning"},
			},
			wantSeq: flat(
				[]string{"getComponents", "getComponentsBackups", "backupRestoreComponentsAction"},
				rep("getTask", 2),
				[]string{"fetchComponentStatuses"},
			),
			wantReport: map[string]any{
				"outcome": "failed",
				"scope":   "FLEET",
				"components": []any{
					map[string]any{
						"componentType": "vcfops", "componentId": vcfopsID, "version": "9.1.0.0",
						"outcome": "restored",
						"backup": map[string]any{
							"name":  "2026-02-10T04-40-00Z",
							"path":  "/backups/vcfops/2026-02-10T04-40-00Z",
							"point": "2026-02-10T04-40-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000001", "taskStatus": "SUCCEEDED",
						"postRestoreStatus": "NotRunning",
					},
				},
			},
		},
		{
			name: "a long running task is polled to a terminal state",
			plan: map[string]any{
				"scope":      "FLEET",
				"components": []any{map[string]any{"componentType": "vidb"}},
			},
			mock: mocklcm.Options{PollsBeforeTerminal: 6},
			wantSeq: flat(
				[]string{"getComponents", "getComponentsBackups", "backupRestoreComponentsAction"},
				rep("getTask", 6),
				[]string{"fetchComponentStatuses"},
			),
			wantReport: map[string]any{
				"outcome": "succeeded",
				"scope":   "FLEET",
				"components": []any{
					map[string]any{
						"componentType": "vidb", "componentId": vidbID, "version": "9.1.0.0",
						"outcome": "restored",
						"backup": map[string]any{
							"name":  "2026-02-10T02-00-00Z",
							"path":  "/backups/vidb/2026-02-10T02-00-00Z",
							"point": "2026-02-10T14-30-00Z",
						},
						"taskId": "00000000-0000-4000-8000-000000000001", "taskStatus": "SUCCEEDED",
						"postRestoreStatus": "Running",
					},
				},
			},
		},
		{
			name: "a one-sided window sends only the bound that is set",
			plan: map[string]any{
				"scope": "FLEET",
				"components": []any{map[string]any{
					"componentType": "vcfops",
					"window":        map[string]any{"end": "2026-02-11T00:00:00Z"},
				}},
			},
			mock: mocklcm.Options{PollsBeforeTerminal: 2},
			wantSeq: flat(
				[]string{"getComponents", "getComponentsBackups", "backupRestoreComponentsAction"},
				rep("getTask", 2),
				[]string{"fetchComponentStatuses"},
			),
			wire: func(t *testing.T, s *mocklcm.Server) {
				requireQuery(t, s0(t, s, "getComponentsBackups"), map[string]string{
					"componentId": vcfopsID,
					"periodEnd":   "2026-02-11T00:00:00Z",
				})
			},
		},
		{
			name: "a component with no matching backup makes the run impossible",
			plan: map[string]any{
				"scope":      "FLEET",
				"components": []any{map[string]any{"componentType": "vcfops"}},
			},
			mock: mocklcm.Options{
				PollsBeforeTerminal: 2,
				Backups:             []mocklcm.Backup{},
			},
			wantErr: "vcfops",
			wantSeq: []string{
				"getComponents",
				"getComponentsBackups",
			},
		},
		{
			name: "a planned component the inventory does not carry",
			plan: map[string]any{
				"scope":      "FLEET",
				"components": []any{map[string]any{"componentType": "vcfa"}},
			},
			mock:    mocklcm.Options{PollsBeforeTerminal: 2},
			wantErr: "vcfa",
			wantSeq: []string{"getComponents"},
		},
	}

	for _, sc := range scenarios {
		sc := sc
		t.Run(sc.name, func(t *testing.T) {
			t.Parallel()
			server := startMock(t, sc.mock)
			planPath := writePlan(t, sc.plan)

			ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
			defer cancel()

			report, err := drill.Run(ctx, drill.Options{
				PlanPath:     planPath,
				ContractPath: contractPath,
				BaseURL:      server.URL,
				Token:        token,
				PollInterval: 2 * time.Millisecond,
				PollTimeout:  30 * time.Second,
			})

			switch {
			case sc.wantErr != "":
				if err == nil {
					t.Fatalf("the drill should not have been able to run, got report %v", asJSON(t, report))
				}
				if !strings.Contains(err.Error(), sc.wantErr) {
					t.Errorf("error %q does not mention %q", err, sc.wantErr)
				}
			case err != nil:
				t.Fatalf("the drill returned an error for a plan it could carry out: %v", err)
			}

			requireNoViolations(t, server)
			requireAuthorized(t, server)

			if got := server.Sequence(); !reflect.DeepEqual(got, sc.wantSeq) {
				t.Errorf("request sequence is\n  %v\nwant\n  %v", got, sc.wantSeq)
			}
			if sc.wantReport != nil {
				requireEqualJSON(t, "report", asJSON(t, report), asJSON(t, sc.wantReport))
			}
			if sc.wire != nil {
				sc.wire(t, server)
			}
		})
	}
}
