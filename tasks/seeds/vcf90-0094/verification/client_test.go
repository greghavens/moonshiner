package vcflogs_test

import (
	"context"
	"testing"

	vcflogs "example.com/vcflogs"
	"example.com/vcflogs/mock"
)

func TestUpdateForwarder(t *testing.T) {
	zero := 0
	empty := ""
	falseValue := false

	testCases := []struct {
		name   string
		update vcflogs.ForwarderUpdate
	}{
		{
			name: "required fields",
			update: vcflogs.ForwarderUpdate{
				Host: "logs-backup.example.test", Port: 9543, Protocol: "CFAPI", SSLEnabled: false,
			},
		},
		{
			name: "explicit optional zero values",
			update: vcflogs.ForwarderUpdate{
				Host: "logs-backup.example.test", Port: 9543, Protocol: "CFAPI", SSLEnabled: false,
				Name: &empty, WorkerCount: &zero, Filter: &empty, TestConnection: &falseValue,
			},
		},
	}

	for _, testCase := range testCases {
		t.Run(testCase.name, func(t *testing.T) {
			server, err := mock.New()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(server.Close)

			client := vcflogs.NewClient(server.URL(), "session-token", server.HTTPClient())
			_, err = client.UpdateForwarder(context.Background(), "forwarder-1", testCase.update)
			if err == nil {
				t.Fatal("first update unexpectedly succeeded")
			}
			result, err := client.UpdateForwarder(context.Background(), "forwarder-1", testCase.update)
			if err != nil {
				t.Fatal(err)
			}
			if result.ID != "forwarder-1" || len(server.Requests()) != 2 || server.EffectCount() != 1 {
				t.Fatalf("result=%#v requests=%d effects=%d", result, len(server.Requests()), server.EffectCount())
			}
		})
	}
}
