package installer

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
)

const (
	OperationValidateSddcSpec = "validateSddcSpec"
	OperationDeploySddc       = "deploySddc"
)

var ErrNotImplemented = errors.New("PrecheckAndDeploy is not implemented")

// Client calls the two-operation VCF Installer contract in docs/contract.json.
// A Client may be used by multiple goroutines after construction.
type Client struct {
	baseURL     *url.URL
	bearerToken string
	httpClient  *http.Client
}

func NewClient(baseURL, bearerToken string, httpClient *http.Client) (*Client, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return nil, fmt.Errorf("parse VCF Installer base URL: %w", err)
	}
	if parsed.Scheme == "" || parsed.Host == "" {
		return nil, errors.New("VCF Installer base URL must be absolute")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: parsed, bearerToken: bearerToken, httpClient: httpClient}, nil
}

type SddcSpec struct {
	SddcID                      string            `json:"sddcId"`
	WorkflowType                string            `json:"workflowType,omitempty"`
	Version                     string            `json:"version,omitempty"`
	VcenterSpec                 SddcVcenterSpec   `json:"vcenterSpec"`
	NetworkSpecs                []SddcNetworkSpec `json:"networkSpecs"`
	DNSSpec                     DnsSpec           `json:"dnsSpec"`
	NTPServers                  []string          `json:"ntpServers,omitempty"`
	ManagementPoolName          string            `json:"managementPoolName,omitempty"`
	CEIPEnabled                 *bool             `json:"ceipEnabled,omitempty"`
	SkipEsxThumbprintValidation *bool             `json:"skipEsxThumbprintValidation,omitempty"`
	SkipGatewayPingValidation   *bool             `json:"skipGatewayPingValidation,omitempty"`
	VCFInstanceName             string            `json:"vcfInstanceName,omitempty"`
}

type DnsSpec struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers,omitempty"`
}

type SddcNetworkSpec struct {
	NetworkType             string   `json:"networkType"`
	Subnet                  string   `json:"subnet,omitempty"`
	Gateway                 string   `json:"gateway,omitempty"`
	SubnetMask              string   `json:"subnetMask,omitempty"`
	IncludeIPAddress        []string `json:"includeIpAddress,omitempty"`
	VLANID                  int32    `json:"vlanId"`
	MTU                     int32    `json:"mtu,omitempty"`
	TeamingPolicy           string   `json:"teamingPolicy,omitempty"`
	ActiveUplinks           []string `json:"activeUplinks,omitempty"`
	StandbyUplinks          []string `json:"standbyUplinks,omitempty"`
	PortGroupKey            string   `json:"portGroupKey,omitempty"`
	IPAddressVersion        string   `json:"ipAddressVersion,omitempty"`
	IPAddressAssignmentMode string   `json:"ipAddressAssignmentMode,omitempty"`
}

type SddcVcenterSpec struct {
	VcenterHostname       string `json:"vcenterHostname"`
	RootVcenterPassword   string `json:"rootVcenterPassword"`
	VMSize                string `json:"vmSize,omitempty"`
	StorageSize           string `json:"storageSize,omitempty"`
	SSODomain             string `json:"ssoDomain,omitempty"`
	AdminUserSSOUsername  string `json:"adminUserSsoUsername,omitempty"`
	AdminUserSSOPassword  string `json:"adminUserSsoPassword,omitempty"`
	UseExistingDeployment *bool  `json:"useExistingDeployment,omitempty"`
	SSLThumbprint         string `json:"sslThumbprint,omitempty"`
}

type Validation struct {
	ID              string `json:"id"`
	Description     string `json:"description"`
	ExecutionStatus string `json:"executionStatus"`
	ResultStatus    string `json:"resultStatus"`
}

type SddcTask struct {
	ID                string `json:"id"`
	Name              string `json:"name"`
	DeploymentType    string `json:"deploymentType"`
	VCFInstanceName   string `json:"vcfInstanceName"`
	Status            string `json:"status"`
	CreationTimestamp string `json:"creationTimestamp"`
}

type APIError struct {
	OperationID string
	StatusCode  int
	Body        string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s returned HTTP %d", e.OperationID, e.StatusCode)
}

type PrecheckError struct {
	ExecutionStatus string
	ResultStatus    string
}

func (e *PrecheckError) Error() string {
	return fmt.Sprintf("VCF Installer precheck did not succeed (executionStatus=%s, resultStatus=%s)", e.ExecutionStatus, e.ResultStatus)
}

// PrecheckAndDeploy validates spec and starts the installation only after a
// terminal, successful validation result.
func (c *Client) PrecheckAndDeploy(ctx context.Context, spec SddcSpec) (*SddcTask, error) {
	return nil, ErrNotImplemented
}
