package mockvcf

import (
	"bytes"
	"compress/gzip"
	"net/http"
	"strings"
	"sync"
)

// The scenario: a workload-domain cluster expansion in sfo-w01 failed. The task's
// own top-level error says only that the expansion failed. The reason lives two
// levels down in the failed sub-task's nested errors, and the corroborating log
// lines live inside the SoS support bundle, keyed by reference token.
const (
	ClusterResourceID = "9d1a2f4c-6b77-4f1e-9c2a-31b0e5d64a10"
	DomainName        = "sfo-w01"
	ClusterName       = "sfo-w01-cl01"

	FailedTaskID = "8f0c1b2e-4a55-4d90-b3f1-2c7e6a9d4b83"
	OlderTaskID  = "3ac9d017-51ee-4a2b-8f60-0d4e19b7c5aa"
	BundleID     = "b7e41d29-0c86-4f3a-9a51-6e2d78bc4013"

	HostResourceID = "c04e7b31-9d8a-4a6f-bb12-5f7ac3e08d99"
	OtherVCID      = "1f6b8c40-2ea9-4d77-90b3-8c5d1e42af07"
)

// bundleLog is the plain text the support bundle carries. Lines that a reference
// token can be traced to are tagged [ref=TOKEN]. The final line carries a token
// that belongs to an unrelated task and must not be treated as evidence.
const bundleLog = `2026-03-11T04:19:52.104Z INFO  sddc-manager domainmanager: task 8f0c1b2e-4a55-4d90-b3f1-2c7e6a9d4b83 CLUSTER_EXPANSION started for cluster sfo-w01-cl01
2026-03-11T04:20:31.559Z INFO  sddc-manager domainmanager: host validation passed for esx-04.sfo.rainpole.io
2026-03-11T04:21:16.882Z WARN  esx-04.sfo.rainpole.io ntpd[2118]: no server suitable for synchronization found [ref=P8M2ZD]
2026-03-11T04:21:44.037Z ERROR esx-04.sfo.rainpole.io hostd[2431]: clock offset 412s exceeds the vSAN cluster tolerance of 300s [ref=LX4T9B]
2026-03-11T04:22:07.115Z ERROR sddc-manager domainmanager: sfo-w01-vc01.rainpole.io rejected the host add for esx-04.sfo.rainpole.io [ref=RVK7QW]
2026-03-11T04:22:07.980Z ERROR sddc-manager domainmanager: rolling back the partial cluster expansion failed [ref=RB55CN]
2026-03-11T04:22:09.412Z ERROR sddc-manager domainmanager: task 8f0c1b2e-4a55-4d90-b3f1-2c7e6a9d4b83 marked FAILED [ref=A1B2C3]
2026-03-11T04:25:00.000Z INFO  vcf-ops collector: nightly inventory sync complete
2026-03-11T04:26:13.771Z WARN  sfo-m01-vc01.rainpole.io vpxd[9004]: certificate expires in 21 days [ref=ZZ0000]
`

var (
	bundleOnce  sync.Once
	bundleBytes []byte
)

// BundleBytes returns the exact octet-stream body exportSupportBundleByID serves.
// A test that wants to assert a digest should hash these bytes rather than a
// hard-coded constant, so the assertion stays true across Go releases.
func BundleBytes() []byte {
	bundleOnce.Do(func() {
		var buf bytes.Buffer
		zw := gzip.NewWriter(&buf)
		zw.Name = "sos-sfo-w01-2026-03-11.log"
		if _, err := zw.Write([]byte(bundleLog)); err != nil {
			panic(err)
		}
		if err := zw.Close(); err != nil {
			panic(err)
		}
		bundleBytes = buf.Bytes()
	})
	return append([]byte(nil), bundleBytes...)
}

// BundleLog returns the decompressed bundle text, for tests that want to derive
// expected evidence lines rather than restate them.
func BundleLog() string { return bundleLog }

type handlerFunc func(s *Server, w http.ResponseWriter, r *http.Request, vars map[string]string, body []byte) int

var handlers = map[string]handlerFunc{
	"getTasks":                hGetTasks,
	"getTask":                 hGetTask,
	"getNotifications":        hGetNotifications,
	"startSupportBundle":      hStartSupportBundle,
	"getSupportBundleStatus":  hGetSupportBundleStatus,
	"exportSupportBundleByID": hExportSupportBundle,
}

// failedTask is the full Task getTask serves. The generic top-level error is the
// bait: the actionable reason is nested under the failed sub-task.
func failedTask() map[string]any {
	return map[string]any{
		"id":                  FailedTaskID,
		"name":                "Add hosts to cluster " + ClusterName,
		"type":                "CLUSTER_EXPANSION",
		"status":              "FAILED",
		"creationTimestamp":   "2026-03-11T04:19:51.980Z",
		"completionTimestamp": "2026-03-11T04:22:09.500Z",
		"isRetryable":         true,
		"resources": []any{
			map[string]any{"resourceId": ClusterResourceID, "type": "CLUSTER", "name": ClusterName},
			map[string]any{"resourceId": HostResourceID, "type": "HOST", "fqdn": "esx-04.sfo.rainpole.io"},
		},
		"errors": []any{
			map[string]any{
				"errorCode":      "CLUSTER_EXPANSION_FAILED",
				"errorType":      "OPERATION",
				"message":        "Cluster expansion failed. Inspect the sub-tasks for details.",
				"referenceToken": "A1B2C3",
			},
		},
		"subTasks": []any{
			map[string]any{
				"name":                "Validate host configuration",
				"type":                "VALIDATION",
				"description":         "Validate the candidate hosts against the cluster profile",
				"status":              "SUCCESSFUL",
				"creationTimestamp":   "2026-03-11T04:19:52.100Z",
				"completionTimestamp": "2026-03-11T04:20:31.600Z",
			},
			map[string]any{
				"name":                "Add hosts to vSphere cluster",
				"type":                "HOST_ADD",
				"description":         "Add the validated hosts to " + ClusterName,
				"status":              "FAILED",
				"creationTimestamp":   "2026-03-11T04:20:32.010Z",
				"completionTimestamp": "2026-03-11T04:22:07.200Z",
				"errors": []any{
					map[string]any{
						"errorCode":      "VSPHERE_HOST_ADD_FAILED",
						"errorType":      "OPERATION",
						"message":        "Host addition to the cluster did not complete.",
						"referenceToken": "RVK7QW",
						"nestedErrors": []any{
							map[string]any{
								"errorCode": "VSPHERE_REJECTED_HOST_ADD",
								"errorType": "OPERATION",
								"message":   "vCenter rejected the host add request.",
								// Repeating the task token exercises first-occurrence
								// de-duplication across the whole task trail.
								"referenceToken": "A1B2C3",
							},
							map[string]any{
								"errorCode":      "ESX_TIME_SYNC_DRIFT",
								"errorType":      "VALIDATION",
								"message":        "Host esx-04.sfo.rainpole.io is outside the vSAN time synchronisation tolerance.",
								"referenceToken": "P8M2ZD",
								"causes": []any{
									map[string]any{"type": "TimeSyncException", "message": "offset 412s, tolerance 300s"},
								},
								"nestedErrors": []any{
									map[string]any{
										"errorCode":      "NTP_SERVER_UNREACHABLE",
										"errorType":      "CONNECTIVITY",
										"message":        "ESXi host esx-04.sfo.rainpole.io cannot reach any of its configured NTP servers (ntp0.sfo.rainpole.io, ntp1.sfo.rainpole.io).",
										"referenceToken": "LX4T9B",
									},
								},
							},
						},
					},
				},
			},
			map[string]any{
				"name":              "Configure vSAN disk groups",
				"type":              "VSAN_CONFIG",
				"description":       "Claim disks on the newly added hosts",
				"status":            "SKIPPED",
				"creationTimestamp": "2026-03-11T04:22:07.300Z",
			},
			map[string]any{
				"name":                "Rollback partial cluster expansion",
				"type":                "ROLLBACK",
				"description":         "Remove the partially added host from " + ClusterName,
				"status":              "FAILED",
				"creationTimestamp":   "2026-03-11T04:22:07.400Z",
				"completionTimestamp": "2026-03-11T04:22:08.900Z",
				"errors": []any{
					map[string]any{
						"errorCode":      "CLUSTER_EXPANSION_ROLLBACK_FAILED",
						"errorType":      "OPERATION",
						"message":        "The partial cluster expansion could not be rolled back completely.",
						"referenceToken": "RB55CN",
					},
				},
			},
		},
	}
}

func olderTask() map[string]any {
	return map[string]any{
		"id":                  OlderTaskID,
		"name":                "Add hosts to cluster " + ClusterName,
		"type":                "CLUSTER_EXPANSION",
		"status":              "FAILED",
		"creationTimestamp":   "2026-03-09T22:04:11.220Z",
		"completionTimestamp": "2026-03-09T22:06:48.910Z",
		"isRetryable":         false,
		"resources": []any{
			map[string]any{"resourceId": ClusterResourceID, "type": "CLUSTER", "name": ClusterName},
		},
		"errors": []any{
			map[string]any{
				"errorCode":      "CLUSTER_EXPANSION_FAILED",
				"errorType":      "OPERATION",
				"message":        "Cluster expansion failed. Inspect the sub-tasks for details.",
				"referenceToken": "QQ8811",
			},
		},
	}
}

// hGetTasks honours taskStatus, resourceId, orderDirection and pageSize. The
// backing list is newest-first, so orderDirection=DESC is the identity and
// orderDirection=ASC reverses it.
func hGetTasks(s *Server, w http.ResponseWriter, r *http.Request, _ map[string]string, _ []byte) int {
	q := r.URL.Query()

	all := []map[string]any{failedTask(), olderTask()}
	var kept []map[string]any
	for _, t := range all {
		if v := q.Get("taskStatus"); v != "" && !strings.EqualFold(v, t["status"].(string)) {
			continue
		}
		if v := q.Get("resourceId"); v != "" && !taskTouches(t, v) {
			continue
		}
		kept = append(kept, t)
	}
	if strings.EqualFold(q.Get("orderDirection"), "ASC") {
		for i, j := 0, len(kept)-1; i < j; i, j = i+1, j-1 {
			kept[i], kept[j] = kept[j], kept[i]
		}
	}

	total := len(kept)
	pageSize := total
	if v := q.Get("pageSize"); v != "" {
		n := 0
		for _, c := range v {
			if c < '0' || c > '9' {
				return s.writeErr(w, http.StatusBadRequest, "BAD_REQUEST", "pageSize must be an integer")
			}
			n = n*10 + int(c-'0')
		}
		pageSize = n
	}
	if pageSize < len(kept) {
		kept = kept[:pageSize]
	}

	elements := make([]any, 0, len(kept))
	for _, t := range kept {
		elements = append(elements, t)
	}
	totalPages := 0
	if pageSize > 0 {
		totalPages = (total + pageSize - 1) / pageSize
	}
	return s.writeJSON(w, http.StatusOK, map[string]any{
		"elements": elements,
		"pageMetadata": map[string]any{
			"pageNumber":    0,
			"pageSize":      len(elements),
			"totalElements": total,
			"totalPages":    totalPages,
		},
	})
}

func taskTouches(t map[string]any, resourceID string) bool {
	res, _ := t["resources"].([]any)
	for _, r := range res {
		m, _ := r.(map[string]any)
		if m != nil && m["resourceId"] == resourceID {
			return true
		}
	}
	return false
}

func hGetTask(s *Server, w http.ResponseWriter, _ *http.Request, vars map[string]string, _ []byte) int {
	switch vars["id"] {
	case FailedTaskID:
		return s.writeJSON(w, http.StatusOK, failedTask())
	case OlderTaskID:
		return s.writeJSON(w, http.StatusOK, olderTask())
	}
	return s.writeErr(w, http.StatusNotFound, "TASK_NOT_FOUND", "no task with id "+vars["id"])
}

func hGetNotifications(s *Server, w http.ResponseWriter, _ *http.Request, _ map[string]string, _ []byte) int {
	return s.writeJSON(w, http.StatusOK, []any{
		map[string]any{
			"type":              "OPERATION_FAILURE",
			"severity":          "ERROR",
			"creationTimestamp": "2026-03-11T04:22:10.000Z",
			"message": map[string]any{
				"id":               "vcf.notification.cluster.expansion.failed",
				"localizedMessage": "Cluster sfo-w01-cl01 expansion failed; one host was left outside the cluster.",
			},
			"resources": []any{
				map[string]any{"id": ClusterResourceID, "type": "CLUSTER", "name": ClusterName},
			},
			"domain": map[string]any{"id": "6d2f9a11-70cc-4b8e-9a3d-5c1f0b7e2d64", "name": DomainName},
		},
		map[string]any{
			"type":              "HOST_HEALTH",
			"severity":          "WARNING",
			"creationTimestamp": "2026-03-11T04:21:20.000Z",
			"message": map[string]any{
				"id":               "vcf.notification.host.timesync.drift",
				"localizedMessage": "Time synchronisation drift detected on esx-04.sfo.rainpole.io.",
			},
			"resources": []any{
				map[string]any{"id": HostResourceID, "type": "HOST", "name": "esx-04.sfo.rainpole.io"},
				map[string]any{"id": ClusterResourceID, "type": "CLUSTER", "name": ClusterName},
			},
			"domain": map[string]any{"id": "6d2f9a11-70cc-4b8e-9a3d-5c1f0b7e2d64", "name": DomainName},
		},
		map[string]any{
			"type":              "CERTIFICATE_EXPIRY",
			"severity":          "INFO",
			"creationTimestamp": "2026-03-11T04:26:14.000Z",
			"message": map[string]any{
				"id":               "vcf.notification.certificate.expiring",
				"localizedMessage": "Certificate for sfo-m01-vc01.rainpole.io expires in 21 days.",
			},
			"resources": []any{
				map[string]any{"id": OtherVCID, "type": "VCENTER", "name": "sfo-m01-vc01.rainpole.io"},
			},
		},
		map[string]any{
			"type":              "BACKUP_FAILURE",
			"severity":          "ERROR",
			"creationTimestamp": "2026-03-11T03:00:00.000Z",
			"message": map[string]any{
				"id":               "vcf.notification.backup.failed",
				"localizedMessage": "Scheduled backup to sfo-m01-sftp01.rainpole.io failed.",
			},
			"resources": []any{
				map[string]any{"id": "5b0d3e88-1c47-4a29-9f31-7ad206e4bc55", "type": "SDDC_MANAGER"},
			},
		},
	})
}

func hStartSupportBundle(s *Server, w http.ResponseWriter, _ *http.Request, _ map[string]string, body []byte) int {
	if len(body) == 0 {
		return s.writeErr(w, http.StatusBadRequest, "BAD_REQUEST", "startSupportBundle requires a SupportBundleSpec body")
	}
	return s.writeJSON(w, http.StatusAccepted, map[string]any{
		"id":                BundleID,
		"status":            "IN_PROGRESS",
		"description":       "SoS log collection",
		"bundleAvailable":   "false",
		"bundleName":        "sos-sfo-w01-2026-03-11.tgz",
		"creationTimestamp": "2026-03-11T05:02:44.000Z",
	})
}

// hGetSupportBundleStatus reports IN_PROGRESS on the first poll and
// COMPLETED_WITH_SUCCESS from the second onward, so a client has to poll.
func hGetSupportBundleStatus(s *Server, w http.ResponseWriter, _ *http.Request, vars map[string]string, _ []byte) int {
	id := vars["id"]
	if id != BundleID {
		return s.writeErr(w, http.StatusNotFound, "BUNDLE_NOT_FOUND", "no support bundle with id "+id)
	}
	poll := s.nextPoll(id)
	if poll == 1 {
		return s.writeJSON(w, http.StatusOK, map[string]any{
			"id":                BundleID,
			"status":            "IN_PROGRESS",
			"description":       "SoS log collection",
			"bundleAvailable":   "false",
			"bundleName":        "sos-sfo-w01-2026-03-11.tgz",
			"creationTimestamp": "2026-03-11T05:02:44.000Z",
		})
	}
	if s.terminalStatus == "COMPLETED_WITH_FAILURE" && poll > 2 {
		return s.writeErr(w, http.StatusInternalServerError, "POLL_AFTER_TERMINAL",
			"the client polled again after COMPLETED_WITH_FAILURE")
	}
	bundleAvailable := "true"
	if s.terminalStatus != "COMPLETED_WITH_SUCCESS" {
		bundleAvailable = "false"
	}
	return s.writeJSON(w, http.StatusOK, map[string]any{
		"id":                  BundleID,
		"status":              s.terminalStatus,
		"description":         "SoS log collection",
		"bundleAvailable":     bundleAvailable,
		"bundleName":          "sos-sfo-w01-2026-03-11.tgz",
		"creationTimestamp":   "2026-03-11T05:02:44.000Z",
		"completionTimestamp": "2026-03-11T05:04:02.000Z",
	})
}

func hExportSupportBundle(s *Server, w http.ResponseWriter, _ *http.Request, vars map[string]string, _ []byte) int {
	if vars["id"] != BundleID {
		return s.writeErr(w, http.StatusNotFound, "BUNDLE_NOT_FOUND", "no support bundle with id "+vars["id"])
	}
	b := BundleBytes()
	// Content-Type, not Content-Encoding: the gzip stream is the payload the
	// operation serves, so Go's transport must hand it back untouched.
	w.Header().Set("Content-Type", "application/octet-stream")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(b)
	return http.StatusOK
}
