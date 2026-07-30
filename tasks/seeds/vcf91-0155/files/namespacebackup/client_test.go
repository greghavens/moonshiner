package namespacebackup

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"moonshiner.local/vcf91/namespacebackup/internal/contractmock"
)

func TestBackupNamespace_SortsFlippingCollectionsAndPollsTerminal(t *testing.T) {
	comment := "nightly 🔒 <safe>"
	tests := []struct {
		name           string
		comment        *string
		initialReverse bool
		wantBody       string
	}{
		{name: "first response unsorted", wantBody: "{}"},
		{
			name:           "second response unsorted with unicode comment",
			comment:        &comment,
			initialReverse: true,
			wantBody:       `{"comment":"nightly 🔒 <safe>"}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			mock := contractmock.New(t, "../docs/contract.json", contractmock.Options{
				InitialReverse: test.initialReverse,
			})
			callerHTTPClient := mock.HTTPClient()
			callerHTTPClient.Timeout = 2 * time.Second
			callerHTTPClient.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
				return errors.New("caller redirect policy")
			}
			client, err := NewClient(Config{
				VCenterURL:      mock.URL(),
				KubernetesURL:   mock.URL(),
				SessionID:       "session-secret-91",
				KubernetesToken: "kube-secret-91",
				HTTPClient:      callerHTTPClient,
				PollInterval:    0,
				MaxPolls:        8,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if client.httpClient == callerHTTPClient {
				t.Fatal("NewClient retained caller-owned *http.Client")
			}
			if callerHTTPClient.CheckRedirect == nil {
				t.Fatal("NewClient mutated caller redirect policy")
			}

			stopReaders := make(chan struct{})
			var readers sync.WaitGroup
			readers.Add(1)
			go func() {
				defer readers.Done()
				for {
					select {
					case <-stopReaders:
						return
					default:
						_ = mock.Log()
					}
				}
			}()

			namespace := "team /蓝"
			result, err := client.BackupNamespace(context.Background(), BackupRequest{
				Namespace: namespace,
				Comment:   test.comment,
			})
			close(stopReaders)
			readers.Wait()
			if err != nil {
				t.Fatalf("BackupNamespace: %v", err)
			}

			wantClusters := []Cluster{
				{Name: "Alpha", TopologyVersion: "v1.30.6+vmware.1"},
				{Name: "zulu", TopologyVersion: "v1.31.2+vmware.1"},
			}
			if !reflect.DeepEqual(result.Clusters, wantClusters) {
				t.Fatalf("clusters = %#v, want sorted %#v", result.Clusters, wantClusters)
			}
			if result.Namespace != namespace || result.Supervisor != "supervisor/blue zone" {
				t.Fatalf("unexpected identity projection: %#v", result)
			}
			if result.Backup.OperationID != OperationCreateBackup ||
				result.Backup.TaskOperationID != OperationGetTask ||
				result.Backup.TaskID != "task/ 91" ||
				result.Backup.Status != "SUCCEEDED" ||
				result.Backup.PollCount != 4 {
				t.Fatalf("unexpected backup projection: %#v", result.Backup)
			}
			wantTaskResult := map[string]any{"archive": "backup-91"}
			if !reflect.DeepEqual(result.Backup.Result, wantTaskResult) {
				t.Fatalf("task result = %#v, want %#v", result.Backup.Result, wantTaskResult)
			}

			log := mock.Log()
			wantOperations := []string{
				"getSupervisorNamespace",
				"listVksClusters",
				"createSupervisorBackup",
				"getTask",
				"getTask",
				"getTask",
				"getTask",
				"listVksClusters",
			}
			if len(log) != len(wantOperations) {
				t.Fatalf("request count = %d, want %d: %#v", len(log), len(wantOperations), log)
			}
			for i, want := range wantOperations {
				if log[i].Operation != want {
					t.Fatalf("request %d operation = %q, want %q", i, log[i].Operation, want)
				}
			}

			escapedNamespace := url.PathEscape(namespace)
			wantTargets := []string{
				"/api/vcenter/namespaces/instances/v2/" + escapedNamespace,
				"/apis/cluster.x-k8s.io/v1beta2/namespaces/" + escapedNamespace + "/clusters",
				"/api/vcenter/namespace-management/supervisors/" + url.PathEscape("supervisor/blue zone") + "/recovery/backup/jobs",
				"/api/cis/tasks/" + url.PathEscape("task/ 91"),
			}
			if log[0].RequestURI != wantTargets[0] || log[1].RequestURI != wantTargets[1] ||
				log[2].RequestURI != wantTargets[2] {
				t.Fatalf("unexpected initial request targets: %q, %q, %q", log[0].RequestURI, log[1].RequestURI, log[2].RequestURI)
			}
			for i := 3; i <= 6; i++ {
				if log[i].RequestURI != wantTargets[3] {
					t.Fatalf("task request %d target = %q, want %q", i, log[i].RequestURI, wantTargets[3])
				}
			}
			if log[7].RequestURI != wantTargets[1] {
				t.Fatalf("post-task collection target = %q, want %q", log[7].RequestURI, wantTargets[1])
			}
			if reflect.DeepEqual(log[1].ResponseOrder, log[7].ResponseOrder) {
				t.Fatalf("mock did not flip collection order: %v then %v", log[1].ResponseOrder, log[7].ResponseOrder)
			}

			for i, record := range log {
				if strings.Contains(record.RequestURI, "?") {
					t.Fatalf("request %d has forbidden query: %q", i, record.RequestURI)
				}
				if got := record.Header.Values("Accept"); len(got) != 1 || got[0] != "application/json" {
					t.Fatalf("request %d Accept = %v", i, got)
				}
				isKubernetes := i == 1 || i == 7
				if isKubernetes {
					if got := record.Header.Values("Authorization"); len(got) != 1 || got[0] != "Bearer kube-secret-91" {
						t.Fatalf("Kubernetes request %d Authorization = %v", i, got)
					}
					if got := record.Header.Values("vmware-api-session-id"); len(got) != 0 {
						t.Fatalf("Kubernetes request %d leaked vCenter session: %v", i, got)
					}
				} else {
					if got := record.Header.Values("vmware-api-session-id"); len(got) != 1 || got[0] != "session-secret-91" {
						t.Fatalf("vCenter request %d session header = %v", i, got)
					}
					if got := record.Header.Values("Authorization"); len(got) != 0 {
						t.Fatalf("vCenter request %d leaked bearer token: %v", i, got)
					}
				}
				if i == 2 {
					if record.Method != http.MethodPost {
						t.Fatalf("backup method = %s", record.Method)
					}
					if got := record.Header.Values("Content-Type"); len(got) != 1 || got[0] != "application/json" {
						t.Fatalf("backup Content-Type = %v", got)
					}
					if string(record.Body) != test.wantBody {
						t.Fatalf("backup body = %q, want %q", record.Body, test.wantBody)
					}
				} else {
					if record.Method != http.MethodGet {
						t.Fatalf("GET request %d method = %s", i, record.Method)
					}
					if len(record.Body) != 0 || record.ContentLength > 0 || len(record.TransferEncoding) != 0 {
						t.Fatalf("GET request %d was not bodyless: length=%d transfer=%v body=%q", i, record.ContentLength, record.TransferEncoding, record.Body)
					}
					if got := record.Header.Values("Content-Type"); len(got) != 0 {
						t.Fatalf("GET request %d Content-Type = %v", i, got)
					}
				}
			}
		})
	}
}

func TestBackupNamespace_TerminalAndConsistencyErrors(t *testing.T) {
	tests := []struct {
		name         string
		options      contractmock.Options
		maxPolls     int
		wantType     string
		wantTaskGets int
		wantPostList bool
	}{
		{
			name:         "failed task",
			options:      contractmock.Options{TaskStatuses: []string{"PENDING", "FAILED"}},
			maxPolls:     5,
			wantType:     "failed",
			wantTaskGets: 2,
		},
		{
			name:         "poll budget exhausted",
			options:      contractmock.Options{TaskStatuses: []string{"PENDING"}},
			maxPolls:     2,
			wantType:     "timeout",
			wantTaskGets: 2,
		},
		{
			name:         "unknown task status",
			options:      contractmock.Options{TaskStatuses: []string{"MYSTERY"}},
			maxPolls:     3,
			wantType:     "protocol",
			wantTaskGets: 1,
		},
		{
			name:         "namespace not running",
			options:      contractmock.Options{NamespaceStatus: "ERROR"},
			maxPolls:     3,
			wantType:     "namespace",
			wantTaskGets: 0,
		},
		{
			name: "inventory changed after success",
			options: contractmock.Options{
				TaskStatuses: []string{"SUCCEEDED"},
				AfterClusters: []contractmock.ClusterFixture{
					{Name: "Alpha", TopologyVersion: "v1.30.6+vmware.1"},
					{Name: "zulu", TopologyVersion: "v1.32.0+vmware.1"},
				},
			},
			maxPolls:     3,
			wantType:     "protocol",
			wantTaskGets: 1,
			wantPostList: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			mock := contractmock.New(t, "../docs/contract.json", test.options)
			client, err := NewClient(Config{
				VCenterURL:      mock.URL(),
				KubernetesURL:   mock.URL(),
				SessionID:       "session-do-not-leak",
				KubernetesToken: "token-do-not-leak",
				HTTPClient:      mock.HTTPClient(),
				MaxPolls:        test.maxPolls,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			_, err = client.BackupNamespace(context.Background(), BackupRequest{Namespace: "workloads"})
			if err == nil {
				t.Fatal("BackupNamespace unexpectedly succeeded")
			}
			switch test.wantType {
			case "failed":
				var target *TaskFailedError
				if !errors.As(err, &target) || target.TaskID != "task/ 91" || target.Status != "FAILED" {
					t.Fatalf("error = %#v, want TaskFailedError", err)
				}
			case "timeout":
				var target *PollTimeoutError
				if !errors.As(err, &target) || target.MaxPolls != test.maxPolls {
					t.Fatalf("error = %#v, want PollTimeoutError", err)
				}
			case "protocol":
				var target *ProtocolError
				if !errors.As(err, &target) {
					t.Fatalf("error = %#v, want ProtocolError", err)
				}
			case "namespace":
				var target *NamespaceNotReadyError
				if !errors.As(err, &target) || target.Status != "ERROR" {
					t.Fatalf("error = %#v, want NamespaceNotReadyError", err)
				}
			}
			text := err.Error()
			for _, secret := range []string{"session-do-not-leak", "token-do-not-leak", "must not escape"} {
				if strings.Contains(text, secret) {
					t.Fatalf("error leaked %q: %q", secret, text)
				}
			}

			log := mock.Log()
			taskGets := 0
			postLists := 0
			for _, record := range log {
				if record.Operation == "getTask" {
					taskGets++
				}
				if record.Operation == "listVksClusters" {
					postLists++
				}
			}
			if taskGets != test.wantTaskGets {
				t.Fatalf("task GET count = %d, want %d", taskGets, test.wantTaskGets)
			}
			wantLists := 1
			if test.wantPostList {
				wantLists = 2
			}
			if test.wantType == "namespace" {
				wantLists = 0
			}
			if postLists != wantLists {
				t.Fatalf("cluster list count = %d, want %d", postLists, wantLists)
			}
		})
	}
}

func TestValidationIsPreflightAndContextCancellationIsDiscoverable(t *testing.T) {
	tests := []struct {
		name string
		cfg  Config
	}{
		{
			name: "vcenter URL has path",
			cfg:  Config{VCenterURL: "https://vc.example/api", KubernetesURL: "https://kube.example", SessionID: "s", KubernetesToken: "k", MaxPolls: 1},
		},
		{
			name: "kubernetes URL has credentials",
			cfg:  Config{VCenterURL: "https://vc.example", KubernetesURL: "https://u:p@kube.example", SessionID: "s", KubernetesToken: "k", MaxPolls: 1},
		},
		{
			name: "header injection",
			cfg:  Config{VCenterURL: "https://vc.example", KubernetesURL: "https://kube.example", SessionID: "s\r\nx: y", KubernetesToken: "k", MaxPolls: 1},
		},
		{
			name: "zero poll budget",
			cfg:  Config{VCenterURL: "https://vc.example", KubernetesURL: "https://kube.example", SessionID: "s", KubernetesToken: "k"},
		},
		{
			name: "negative poll interval",
			cfg:  Config{VCenterURL: "https://vc.example", KubernetesURL: "https://kube.example", SessionID: "s", KubernetesToken: "k", PollInterval: -time.Second, MaxPolls: 1},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := NewClient(test.cfg); err == nil {
				t.Fatal("NewClient unexpectedly accepted invalid configuration")
			}
		})
	}

	mock := contractmock.New(t, "../docs/contract.json", contractmock.Options{})
	client, err := NewClient(Config{
		VCenterURL:      mock.URL(),
		KubernetesURL:   mock.URL(),
		SessionID:       "session",
		KubernetesToken: "token",
		HTTPClient:      mock.HTTPClient(),
		MaxPolls:        1,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	requestTests := []struct {
		name string
		ctx  context.Context
		req  BackupRequest
	}{
		{name: "nil context", ctx: nil, req: BackupRequest{Namespace: "workloads"}},
		{name: "blank namespace", ctx: context.Background(), req: BackupRequest{Namespace: " \t "}},
	}
	for _, test := range requestTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.BackupNamespace(test.ctx, test.req); err == nil {
				t.Fatal("BackupNamespace unexpectedly accepted invalid input")
			}
		})
	}
	if got := len(mock.Log()); got != 0 {
		t.Fatalf("preflight validation performed %d requests", got)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = client.BackupNamespace(ctx, BackupRequest{Namespace: "workloads"})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled request error = %v, want errors.Is(context.Canceled)", err)
	}
}

func TestPublicResultJSONShape(t *testing.T) {
	value := Result{
		Namespace:  "ns",
		Supervisor: "sup",
		Clusters:   []Cluster{{Name: "c", TopologyVersion: "v"}},
		Backup: BackupResult{
			OperationID: OperationCreateBackup, TaskOperationID: OperationGetTask,
			TaskID: "t", Status: "SUCCEEDED", PollCount: 1,
		},
	}
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != 4 {
		t.Fatalf("top-level JSON keys = %v", got)
	}
}
