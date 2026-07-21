@{
    RootModule = 'ProfileCache.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'd1ab4dd8-e5d7-4e54-8c3d-5034df2c50db'
    Author = 'Moonshiner'
    Description = 'A small profile lookup cache used by the isolation fixture.'
    PowerShellVersion = '7.2'
    FunctionsToExport = @(
        'Get-CachedProfile'
        'Clear-ProfileCache'
    )
    CmdletsToExport = @()
    VariablesToExport = @()
    AliasesToExport = @()
}
