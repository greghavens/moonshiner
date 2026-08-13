package verify

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"

	"example.com/vcf90/gosc/guestcust"
	"example.com/vcf90/gosc/internal/mockvc"
)

// applyAndCaptureSet drives one profile against the loopback vCenter and returns the
// request the mutating operation received.
func applyAndCaptureSet(t *testing.T, profile string) mockvc.Recorded {
	t.Helper()
	srv := mockvc.New(t, contractPath)
	srv.SetCheckInfo(200, mockvc.CheckInfo("SUPPORTED", boolp(true), boolp(true)))

	c := guestcust.NewClient(srv.URL(), sessionID, srv.Client())
	res, err := c.ApplyProfile(context.Background(), loadProfile(t, profile))
	if err != nil {
		t.Fatalf("ApplyProfile(%s): %v", profile, err)
	}
	if !res.Applied {
		t.Fatalf("ApplyProfile(%s): Applied = false", profile)
	}
	sets := srv.RequestsFor(opSet)
	if len(sets) != 1 {
		t.Fatalf("%s was issued %d time(s), want exactly 1", opSet, len(sets))
	}
	if sets[0].Status != 204 {
		t.Fatalf("the loopback vCenter rejected the request body with %d: %s", sets[0].Status, sets[0].Body)
	}
	return sets[0]
}

func TestSetRequestWireShape(t *testing.T) {
	tests := []struct {
		profile string
		vm      string
		want    string
		// absent are JSON paths that must not appear: unset optional properties are
		// omitted, never sent as "", 0, false, [] or null.
		absent []string
		// present are JSON paths that must appear even though their value is a zero
		// value, because the specification marks them required.
		present map[string]any
	}{
		{
			profile: "linux-dhcp.json",
			vm:      "vm-2001",
			want: `{
			  "spec": {
			    "configuration_spec": {
			      "linux_config": {
			        "hostname": {"type": "FIXED", "fixed_name": "web01"},
			        "domain": "corp.example.com"
			      }
			    },
			    "global_dns_settings": {},
			    "interfaces": [
			      {"adapter": {"ipv4": {"type": "DHCP"}}}
			    ]
			  }
			}`,
			absent: []string{
				"name",
				"spec.configuration_spec.windows_config",
				"spec.configuration_spec.cloud_config",
				"spec.configuration_spec.linux_config.time_zone",
				"spec.configuration_spec.linux_config.script_text",
				"spec.configuration_spec.linux_config.compatible_customization_method",
				"spec.configuration_spec.linux_config.hostname.prefix",
				"spec.global_dns_settings.dns_servers",
				"spec.global_dns_settings.dns_suffix_list",
				"spec.interfaces[0].mac_address",
				"spec.interfaces[0].adapter.ipv6",
				"spec.interfaces[0].adapter.windows",
				"spec.interfaces[0].adapter.ipv4.ip_address",
				"spec.interfaces[0].adapter.ipv4.prefix",
				"spec.interfaces[0].adapter.ipv4.gateways",
			},
			present: map[string]any{
				"spec.global_dns_settings": map[string]any{},
			},
		},
		{
			profile: "linux-static.json",
			vm:      "vm-2044",
			want: `{
			  "spec": {
			    "configuration_spec": {
			      "linux_config": {
			        "hostname": {"type": "PREFIX", "prefix": "batch-"},
			        "domain": "batch.corp.example.com",
			        "time_zone": "America/Los_Angeles",
			        "script_text": "#!/bin/sh\nif [ x$1 = x\"postcustomization\" ]; then /usr/local/sbin/register-node; fi\n"
			      }
			    },
			    "global_dns_settings": {
			      "dns_suffix_list": ["corp.example.com", "batch.corp.example.com"],
			      "dns_servers": ["10.20.0.10", "10.20.0.11"]
			    },
			    "interfaces": [
			      {
			        "mac_address": "00:50:56:9a:1b:2c",
			        "adapter": {"ipv4": {
			          "type": "STATIC",
			          "ip_address": "10.20.4.17",
			          "prefix": 24,
			          "gateways": ["10.20.4.1"]
			        }}
			      },
			      {"adapter": {"ipv4": {"type": "DHCP"}}}
			    ]
			  }
			}`,
			absent: []string{
				"name",
				"spec.configuration_spec.linux_config.hostname.fixed_name",
				"spec.configuration_spec.linux_config.compatible_customization_method",
				"spec.interfaces[1].mac_address",
				"spec.interfaces[1].adapter.ipv4.gateways",
			},
		},
		{
			profile: "windows-workgroup.json",
			vm:      "vm-3100",
			want: `{
			  "spec": {
			    "configuration_spec": {
			      "windows_config": {
			        "sysprep": {
			          "user_data": {
			            "computer_name": {"type": "FIXED", "fixed_name": "WKSTA-01"},
			            "full_name": "Platform Engineering",
			            "organization": "Example Corp",
			            "product_key": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
			          },
			          "gui_unattended": {"auto_logon": false, "auto_logon_count": 0, "time_zone": 0},
			          "domain": {"type": "WORKGROUP", "workgroup": "WORKGROUP"}
			        }
			      }
			    },
			    "global_dns_settings": {},
			    "interfaces": []
			  }
			}`,
			absent: []string{
				"name",
				"spec.configuration_spec.linux_config",
				"spec.configuration_spec.cloud_config",
				"spec.configuration_spec.windows_config.reboot",
				"spec.configuration_spec.windows_config.sysprep_xml",
				"spec.configuration_spec.windows_config.sysprep.gui_run_once_commands",
				"spec.configuration_spec.windows_config.sysprep.gui_unattended.password",
				"spec.configuration_spec.windows_config.sysprep.domain.domain",
				"spec.configuration_spec.windows_config.sysprep.domain.domain_username",
				"spec.configuration_spec.windows_config.sysprep.domain.domain_password",
				"spec.configuration_spec.windows_config.sysprep.domain.domain_ou",
			},
			present: map[string]any{
				// Required properties are sent even when their value is the zero value.
				"spec.configuration_spec.windows_config.sysprep.gui_unattended.auto_logon":       false,
				"spec.configuration_spec.windows_config.sysprep.gui_unattended.auto_logon_count": float64(0),
				"spec.configuration_spec.windows_config.sysprep.gui_unattended.time_zone":        float64(0),
				"spec.interfaces": []any{},
			},
		},
		{
			profile: "windows-domain.json",
			vm:      "vm-3177",
			want: `{
			  "spec": {
			    "configuration_spec": {
			      "windows_config": {
			        "sysprep": {
			          "user_data": {
			            "computer_name": {"type": "VIRTUAL_MACHINE"},
			            "full_name": "Platform Engineering",
			            "organization": "Example Corp",
			            "product_key": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
			          },
			          "gui_unattended": {
			            "auto_logon": true,
			            "auto_logon_count": 2,
			            "password": "s3cr3t-admin",
			            "time_zone": 4
			          },
			          "domain": {
			            "type": "DOMAIN",
			            "domain": "ad.corp.example.com",
			            "domain_username": "svc-join@ad.corp.example.com",
			            "domain_password": "s3cr3t-join"
			          }
			        }
			      }
			    },
			    "global_dns_settings": {"dns_servers": ["10.20.0.10"]},
			    "interfaces": [
			      {"adapter": {"ipv4": {
			        "type": "STATIC",
			        "ip_address": "10.20.9.31",
			        "prefix": 25
			      }}}
			    ]
			  }
			}`,
			absent: []string{
				"name",
				"spec.configuration_spec.windows_config.sysprep.user_data.computer_name.fixed_name",
				"spec.configuration_spec.windows_config.sysprep.user_data.computer_name.prefix",
				"spec.configuration_spec.windows_config.sysprep.domain.workgroup",
				"spec.global_dns_settings.dns_suffix_list",
				"spec.interfaces[0].mac_address",
				"spec.interfaces[0].adapter.ipv4.gateways",
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.profile, func(t *testing.T) {
			rec := applyAndCaptureSet(t, tc.profile)

			if got, want := rec.Method, "PUT"; got != want {
				t.Errorf("method = %q, want %q", got, want)
			}
			if got, want := rec.Path, "/api/vcenter/vm/"+tc.vm+"/guest/customization"; got != want {
				t.Errorf("path = %q, want %q", got, want)
			}
			if rec.RawQuery != "" {
				t.Errorf("query = %q, want none", rec.RawQuery)
			}
			if got, want := rec.Header.Get("Content-Type"), "application/json"; got != want {
				t.Errorf("Content-Type = %q, want %q", got, want)
			}
			if got := rec.Header.Get("vmware-api-session-id"); got != sessionID {
				t.Errorf("vmware-api-session-id = %q, want %q", got, sessionID)
			}

			got := decodeJSON(t, rec.Body)
			want := decodeJSON(t, []byte(tc.want))
			if !reflect.DeepEqual(got, want) {
				t.Errorf("request body does not match the contracted wire shape\n got %s\nwant %s",
					reindent(rec.Body), reindent([]byte(tc.want)))
			}

			for _, path := range tc.absent {
				if v, ok := lookup(got, path); ok {
					t.Errorf("%s is present as %#v; an unset optional property is omitted, not sent empty", path, v)
				}
			}
			for path, wantVal := range tc.present {
				v, ok := lookup(got, path)
				if !ok {
					t.Errorf("%s is missing; the specification marks it required", path)
					continue
				}
				if !reflect.DeepEqual(v, wantVal) {
					t.Errorf("%s = %#v, want %#v", path, v, wantVal)
				}
			}
			nulls := nullPaths(got, "")
			sort.Strings(nulls)
			for _, path := range nulls {
				t.Errorf("%s is null; the API omits an unset property rather than nulling it", path)
			}
		})
	}
}

// ---------------------------------------------------------------------------

func decodeJSON(t *testing.T, b []byte) any {
	t.Helper()
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("decode %s: %v", b, err)
	}
	return v
}

func reindent(b []byte) string {
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		return string(b)
	}
	out, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return string(b)
	}
	return string(out)
}

// lookup walks a decoded JSON document along a path like a.b[0].c.
func lookup(doc any, path string) (any, bool) {
	cur := doc
	for _, step := range strings.Split(path, ".") {
		name, rest, _ := strings.Cut(step, "[")
		if name != "" {
			m, ok := cur.(map[string]any)
			if !ok {
				return nil, false
			}
			cur, ok = m[name]
			if !ok {
				return nil, false
			}
		}
		for rest != "" {
			idxStr, tail, _ := strings.Cut(rest, "]")
			rest = strings.TrimPrefix(tail, "[")
			i, err := strconv.Atoi(idxStr)
			if err != nil {
				return nil, false
			}
			a, ok := cur.([]any)
			if !ok || i >= len(a) {
				return nil, false
			}
			cur = a[i]
		}
	}
	return cur, true
}

func nullPaths(doc any, prefix string) []string {
	var out []string
	switch v := doc.(type) {
	case nil:
		if prefix != "" {
			out = append(out, prefix)
		}
	case map[string]any:
		for k, sub := range v {
			p := k
			if prefix != "" {
				p = prefix + "." + k
			}
			out = append(out, nullPaths(sub, p)...)
		}
	case []any:
		for i, sub := range v {
			out = append(out, nullPaths(sub, fmt.Sprintf("%s[%d]", prefix, i))...)
		}
	}
	return out
}
