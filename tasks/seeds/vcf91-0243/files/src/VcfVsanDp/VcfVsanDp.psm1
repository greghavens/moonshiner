#Requires -Version 7.2

Set-StrictMode -Version Latest

# The vSAN Data Protection (snapservice) API has no binding in the VMware.Sdk.Vcf
# family, so this module provides one. The wire contract it must honour is
# docs/contract.json, derived from the published OpenAPI specification.
#
# Nothing is implemented yet.

throw 'VcfVsanDp is not implemented yet.'
