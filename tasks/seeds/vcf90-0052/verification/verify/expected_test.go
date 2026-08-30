package verify

import "example.com/vcf90/gosc/internal/mockvc"

// Everything in this file is transcribed from
// specifications/vsphere/openapi/automation/vcenter.yaml at tag 9.0.0.0 of
// github.com/vmware/vcf-api-specs (Apache-2.0), commit
// 85151f6b1bb58f13b6ac0304bfec53904bea085f.

const (
	specRepository = "https://github.com/vmware/vcf-api-specs"
	specLicense    = "Apache-2.0"
	specTag        = "9.0.0.0"
	specCommitSHA  = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	specPath       = "specifications/vsphere/openapi/automation/vcenter.yaml"
	specVersion    = "9.0.0.0"

	opCheck = "Vcenter.Vm.Guest.Customization_check"
	opSet   = "Vcenter.Vm.Guest.Customization_set"
)

// theNineOneCommitSHA is the same file one minor release later. A contract derived
// from it is not the contract this project pins.
const theNineOneCommitSHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"

func sp(s string) *string { return &s }

func str(required bool, enum ...string) mockvc.Property {
	return mockvc.Property{Type: "string", Required: required, Enum: enum}
}

func password(required bool) mockvc.Property {
	return mockvc.Property{Type: "string", Required: required, Format: "password"}
}

func int64p(required bool) mockvc.Property {
	return mockvc.Property{Type: "integer", Required: required, Format: "int64"}
}

func boolean(required bool) mockvc.Property {
	return mockvc.Property{Type: "boolean", Required: required}
}

func obj(required bool, ref string) mockvc.Property {
	return mockvc.Property{Type: "object", Required: required, Ref: ref}
}

func arrayOf(required bool, elemType string) mockvc.Property {
	return mockvc.Property{Type: "array", Required: required, Items: &mockvc.Items{Type: elemType}}
}

func arrayRef(required bool, ref string) mockvc.Property {
	return mockvc.Property{Type: "array", Required: required, Items: &mockvc.Items{Ref: ref}}
}

func expectedContract() mockvc.Contract {
	return mockvc.Contract{
		API:            "vSphere Automation API",
		SpecVersion:    specVersion,
		ServerBasePath: "/api",
		Auth: mockvc.Auth{
			Scheme: "api_key_auth",
			In:     "header",
			Name:   "vmware-api-session-id",
		},
		Operations: []mockvc.Operation{
			{
				OperationID:  opCheck,
				Method:       "POST",
				Path:         "/vcenter/vm/{vm}/guest/customization",
				Query:        map[string]string{"action": "check"},
				SpecPathKey:  "/vcenter/vm/{vm}/guest/customization?action=check",
				PathParams:   []string{"vm"},
				RequestBody:  nil,
				SuccessCode:  200,
				ResponseBody: sp("Vcenter.Vm.Guest.Customization.CheckInfo"),
			},
			{
				OperationID:  opSet,
				Method:       "PUT",
				Path:         "/vcenter/vm/{vm}/guest/customization",
				Query:        map[string]string{},
				SpecPathKey:  "/vcenter/vm/{vm}/guest/customization",
				PathParams:   []string{"vm"},
				RequestBody:  sp("Vcenter.Vm.Guest.Customization.SetSpec"),
				SuccessCode:  204,
				ResponseBody: nil,
			},
		},
		Schemas: map[string]mockvc.Schema{
			"Vcenter.Vm.Guest.Customization.SetSpec": {Properties: map[string]mockvc.Property{
				"name": str(false),
				"spec": obj(false, "Vcenter.Guest.CustomizationSpec"),
			}},
			"Vcenter.Vm.Guest.Customization.CheckInfo": {Properties: map[string]mockvc.Property{
				"check_status":          str(true, "SUPPORTED", "NOT_SUPPORTED"),
				"supported_guest_os":    boolean(false),
				"supported_power_state": boolean(false),
			}},
			"Vcenter.Guest.CustomizationSpec": {Properties: map[string]mockvc.Property{
				"configuration_spec":  obj(true, "Vcenter.Guest.ConfigurationSpec"),
				"global_dns_settings": obj(true, "Vcenter.Guest.GlobalDNSSettings"),
				"interfaces":          arrayRef(true, "Vcenter.Guest.AdapterMapping"),
			}},
			"Vcenter.Guest.ConfigurationSpec": {Properties: map[string]mockvc.Property{
				"windows_config": obj(false, "Vcenter.Guest.WindowsConfiguration"),
				"linux_config":   obj(false, "Vcenter.Guest.LinuxConfiguration"),
				"cloud_config":   obj(false, "Vcenter.Guest.CloudConfiguration"),
			}},
			"Vcenter.Guest.WindowsConfiguration": {Properties: map[string]mockvc.Property{
				"reboot":      str(false, "REBOOT", "NO_REBOOT", "SHUTDOWN"),
				"sysprep":     obj(false, "Vcenter.Guest.WindowsSysprep"),
				"sysprep_xml": str(false),
			}},
			"Vcenter.Guest.WindowsSysprep": {Properties: map[string]mockvc.Property{
				"gui_run_once_commands": arrayOf(false, "string"),
				"user_data":             obj(true, "Vcenter.Guest.UserData"),
				"domain":                obj(false, "Vcenter.Guest.Domain"),
				"gui_unattended":        obj(true, "Vcenter.Guest.GuiUnattended"),
			}},
			"Vcenter.Guest.UserData": {Properties: map[string]mockvc.Property{
				"computer_name": obj(true, "Vcenter.Guest.HostnameGenerator"),
				"full_name":     str(true),
				"organization":  str(true),
				"product_key":   str(true),
			}},
			"Vcenter.Guest.HostnameGenerator": {Properties: map[string]mockvc.Property{
				"type":       str(true, "FIXED", "PREFIX", "VIRTUAL_MACHINE", "USER_INPUT_REQUIRED"),
				"fixed_name": str(false),
				"prefix":     str(false),
			}},
			"Vcenter.Guest.Domain": {Properties: map[string]mockvc.Property{
				"type":            str(true, "WORKGROUP", "DOMAIN"),
				"workgroup":       str(false),
				"domain":          str(false),
				"domain_username": str(false),
				"domain_password": password(false),
				"domain_ou":       str(false),
			}},
			"Vcenter.Guest.GuiUnattended": {Properties: map[string]mockvc.Property{
				"auto_logon":       boolean(true),
				"auto_logon_count": int64p(true),
				"password":         password(false),
				"time_zone":        int64p(true),
			}},
			"Vcenter.Guest.LinuxConfiguration": {Properties: map[string]mockvc.Property{
				"hostname":                        obj(true, "Vcenter.Guest.HostnameGenerator"),
				"domain":                          str(true),
				"time_zone":                       str(false),
				"script_text":                     str(false),
				"compatible_customization_method": str(false),
			}},
			"Vcenter.Guest.CloudConfiguration": {Properties: map[string]mockvc.Property{
				"type":      str(true, "CLOUDINIT"),
				"cloudinit": obj(false, "Vcenter.Guest.CloudinitConfiguration"),
			}},
			"Vcenter.Guest.CloudinitConfiguration": {Properties: map[string]mockvc.Property{
				"metadata": str(true),
				"userdata": str(false),
			}},
			"Vcenter.Guest.GlobalDNSSettings": {Properties: map[string]mockvc.Property{
				"dns_suffix_list": arrayOf(false, "string"),
				"dns_servers":     arrayOf(false, "string"),
			}},
			"Vcenter.Guest.AdapterMapping": {Properties: map[string]mockvc.Property{
				"mac_address": str(false),
				"adapter":     obj(true, "Vcenter.Guest.IPSettings"),
			}},
			"Vcenter.Guest.IPSettings": {Properties: map[string]mockvc.Property{
				"ipv4":    obj(false, "Vcenter.Guest.Ipv4"),
				"ipv6":    obj(false, "Vcenter.Guest.Ipv6"),
				"windows": obj(false, "Vcenter.Guest.WindowsNetworkAdapterSettings"),
			}},
			"Vcenter.Guest.Ipv4": {Properties: map[string]mockvc.Property{
				"type":       str(true, "DHCP", "STATIC", "USER_INPUT_REQUIRED"),
				"ip_address": str(false),
				"prefix":     int64p(false),
				"gateways":   arrayOf(false, "string"),
			}},
			"Vcenter.Guest.Ipv6": {Properties: map[string]mockvc.Property{
				"type":     str(true, "DHCP", "STATIC", "USER_INPUT_REQUIRED"),
				"ipv6":     arrayRef(false, "Vcenter.Guest.Ipv6Address"),
				"gateways": arrayOf(false, "string"),
			}},
			"Vcenter.Guest.Ipv6Address": {Properties: map[string]mockvc.Property{
				"ip_address": str(true),
				"prefix":     int64p(true),
			}},
			"Vcenter.Guest.WindowsNetworkAdapterSettings": {Properties: map[string]mockvc.Property{
				"dns_servers":   arrayOf(false, "string"),
				"dns_domain":    str(false),
				"wins_servers":  arrayOf(false, "string"),
				"net_bios_mode": str(false, "USE_DHCP", "ENABLE", "DISABLE"),
			}},
		},
	}
}
