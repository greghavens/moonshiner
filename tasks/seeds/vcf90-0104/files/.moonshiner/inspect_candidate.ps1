[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ModulePath
)

$ErrorActionPreference = 'Stop'

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $ModulePath,
    [ref] $tokens,
    [ref] $parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw "Candidate module has PowerShell parse errors: $($parseErrors[0].Message)"
}

$requiredCommands = @(
    'Initialize-VcfInstallerDepotAccount'
    'Initialize-VcfInstallerDepotSettings'
    'Invoke-VcfInstallerUpdateDepotSettings'
)
$commandAsts = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true
    )
)
$stringAsts = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.StringConstantExpressionAst]
        },
        $true
    )
)
$referencedNames = @(
    $commandAsts | ForEach-Object { $_.GetCommandName() }
    $stringAsts | ForEach-Object { $_.Value }
)
foreach ($requiredCommand in $requiredCommands) {
    $matches = @(
        $referencedNames | Where-Object {
            (($_ -split '\\')[-1]) -ceq $requiredCommand
        }
    )
    if ($matches.Count -eq 0) {
        throw "Implementation does not invoke $requiredCommand."
    }
}

$functionAsts = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
        },
        $true
    )
)
foreach ($function in $functionAsts) {
    if ($function.Name -cin $requiredCommands) {
        throw "Candidate shadows genuine SDK command $($function.Name)."
    }
}

$forbiddenCommands = @(
    'Invoke-RestMethod'
    'Invoke-WebRequest'
    'curl'
    'curl.exe'
    'wget'
)
foreach ($command in $commandAsts) {
    $commandName = ($command.GetCommandName() -split '\\')[-1]
    if ($commandName -cin $forbiddenCommands) {
        throw "Implementation bypasses the VMware SDK with $commandName."
    }
}

$forbiddenTypes = @(
    'System.Net.Http.HttpClient'
    'System.Net.WebClient'
    'System.Net.Sockets.TcpClient'
    'System.Net.Sockets.UdpClient'
    'System.Net.Sockets.Socket'
)
$typeAsts = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.TypeExpressionAst]
        },
        $true
    )
)
foreach ($type in $typeAsts) {
    if ($type.TypeName.FullName -cin $forbiddenTypes) {
        throw "Implementation bypasses the VMware SDK with $($type.TypeName.FullName)."
    }
}
