BeforeAll {
    . (Join-Path $PSScriptRoot '../test/TestBootstrap.ps1')
    $script:ModulePath = Join-Path $PSScriptRoot '../ProfileCache/ProfileCache.psd1'
}

Describe 'ProfileCache test isolation' {
    BeforeEach {
        Import-ModuleUnderTest -Path $script:ModulePath -Name 'ProfileCache'
    }

    AfterAll {
        Remove-Module -Name 'ProfileCache' -Force -ErrorAction SilentlyContinue
    }

    It 'caches a provider result for repeated reads in one case' {
        Mock -CommandName Invoke-ProfileLookup -ModuleName ProfileCache -MockWith {
            [pscustomobject]@{
                UserId = 'shared-user'
                Source = 'case-one-provider'
            }
        }

        $first = Get-CachedProfile -UserId 'shared-user'
        $second = Get-CachedProfile -UserId 'shared-user'

        $first.Source | Should -Be 'case-one-provider'
        $second.Source | Should -Be 'case-one-provider'
        Should -Invoke -CommandName Invoke-ProfileLookup -ModuleName ProfileCache -Times 1 -Exactly
    }

    It 'uses the provider mock installed for the current case' {
        Mock -CommandName Invoke-ProfileLookup -ModuleName ProfileCache -MockWith {
            [pscustomobject]@{
                UserId = 'shared-user'
                Source = 'case-two-provider'
            }
        }

        $profile = Get-CachedProfile -UserId 'shared-user'

        $profile.Source | Should -Be 'case-two-provider'
        Should -Invoke -CommandName Invoke-ProfileLookup -ModuleName ProfileCache -Times 1 -Exactly
    }
}
