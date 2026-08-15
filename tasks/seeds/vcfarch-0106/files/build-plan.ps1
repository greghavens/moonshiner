[CmdletBinding()]
param(
    [string] $InventoryPath = (Join-Path $PSScriptRoot 'inventory/estate.json'),
    [string] $CompatibilitySnapshotPath = (Join-Path $PSScriptRoot 'contracts/compatibility-snapshot.json'),
    [string] $OutputPath = (Join-Path $PSScriptRoot 'architecture/migration-plan.json')
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'VcfFleetArchitecture/VcfFleetArchitecture.psd1') -Force

New-VcfFleetMigrationPlan `
    -InventoryPath $InventoryPath `
    -CompatibilitySnapshotPath $CompatibilitySnapshotPath `
    -OutputPath $OutputPath
