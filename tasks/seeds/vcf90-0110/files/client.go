package installer

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"time"
)

// SddcSpec is the contract projection used by this package. Pointer fields
// distinguish an explicitly supplied false value from an omitted option.
type SddcSpec struct {
	SddcID                      string            `json:"sddcId"`
	WorkflowType                string            `json:"workflowType,omitempty"`
	Version                     string            `json:"version,omitempty"`
	VcenterSpec                 SddcVcenterSpec   `json:"vcenterSpec"`
	NetworkSpecs                []SddcNetworkSpec `json:"networkSpecs"`
	DNSSpec                     DnsSpec           `json:"dnsSpec"`
	NTPServers                  []string          `json:"ntpServers,omitempty"`
	CeipEnabled                 *bool             `json:"ceipEnabled,omitempty"`
	SkipEsxThumbprintValidation *bool             `json:"skipEsxThumbprintValidation,omitempty"`
	SkipGatewayPingValidation   *bool             `json:"skipGatewayPingValidation,omitempty"`
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
	Version               string `json:"version,omitempty"`
	SSLThumbprint         string `json:"sslThumbprint,omitempty"`
}

type SddcNetworkSpec struct {
	NetworkType      string   `json:"networkType"`
	VlanID           int32    `json:"vlanId"`
	Subnet           string   `json:"subnet,omitempty"`
	Gateway          string   `json:"gateway,omitempty"`
	SubnetMask       string   `json:"subnetMask,omitempty"`
	IncludeIPAddress []string `json:"includeIpAddress,omitempty"`
	MTU              *int32   `json:"mtu,omitempty"`
	TeamingPolicy    string   `json:"teamingPolicy,omitempty"`
	ActiveUplinks    []string `json:"activeUplinks,omitempty"`
	StandbyUplinks   []string `json:"standbyUplinks,omitempty"`
	PortGroupKey     string   `json:"portGroupKey,omitempty"`
}

type DnsSpec struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers,omitempty"`
}

type Validation struct {
	ID              string `json:"id"`
	Description     string `json:"description"`
	ExecutionStatus string `json:"executionStatus"`
	ResultStatus    string `json:"resultStatus"`
}

type SddcTask struct {
	ID                string `json:"id,omitempty"`
	Name              string `json:"name,omitempty"`
	Status            string `json:"status"`
	CreationTimestamp string `json:"creationTimestamp"`
}

type DeployOptions struct {
	SkipValidations *bool
}

var ErrNotImplemented = errors.New("vcf installer client is not implemented")

type Client struct {
	baseURL      string
	httpClient   *http.Client
	refreshToken string
	mu           sync.RWMutex
	accessToken  string
}

func NewClient(baseURL, accessToken, refreshToken string, httpClient *http.Client) (*Client, error) {
	return nil, ErrNotImplemented
}

func (c *Client) ValidateAndDeploy(ctx context.Context, spec SddcSpec, options DeployOptions, pollInterval time.Duration) (SddcTask, error) {
	return SddcTask{}, ErrNotImplemented
}
