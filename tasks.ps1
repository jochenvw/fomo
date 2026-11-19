<#
.SYNOPSIS
  PowerShell equivalent of the Makefile. Every task is a plain `uv run` command.
.EXAMPLE
  ./tasks.ps1 check
  ./tasks.ps1 preflight -N 40
  ./tasks.ps1 provision -P cosmic-wombat     # plan only; re-run the printed command with --apply
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'lint', 'typecheck', 'test', 'check', 'bicep', 'preflight', 'provision',
                 'cheatcode', 'status', 'capacity', 'corpus', 'integration')]
    [string]$Task = 'check',
    [string]$P = '',
    [int]$N = 1
)
$ErrorActionPreference = 'Stop'
$participant = if ($P) { @('--participant', $P) } else { @() }

function Invoke-Step([string[]]$cmd) {
    Write-Host "> $($cmd -join ' ')"
    & $cmd[0] $cmd[1..($cmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Task) {
    'setup'       { Invoke-Step @('uv', 'sync', '--all-groups') }
    'lint'        { Invoke-Step @('uv', 'run', 'ruff', 'check', 'src', 'tests', 'corpus/generator') }
    'typecheck'   { Invoke-Step @('uv', 'run', 'mypy') }
    'test'        { Invoke-Step @('uv', 'run', 'pytest', '-q') }
    'check'       { & $PSCommandPath lint; & $PSCommandPath typecheck; & $PSCommandPath test }
    'bicep'       { Invoke-Step @('az', 'bicep', 'build', '--file', 'infra/main.bicep', '--stdout') | Out-Null
                    Invoke-Step @('az', 'bicep', 'build', '--file', 'infra/reuse-existing.bicep', '--stdout') | Out-Null }
    'preflight'   { Invoke-Step @('uv', 'run', 'fomo', 'preflight', '--participants', "$N") }
    'provision'   { Invoke-Step (@('uv', 'run', 'fomo', 'provision') + $participant) }
    'cheatcode'   { Invoke-Step (@('uv', 'run', 'fomo', 'cheatcode') + $participant) }
    'status'      { Invoke-Step (@('uv', 'run', 'fomo', 'status') + $participant) }
    'capacity'    { Invoke-Step @('uv', 'run', 'fomo', 'capacity', '--participants', "$N") }
    'corpus'      { Invoke-Step @('uv', 'run', 'python', '-m', 'corpus.generator') }
    'integration' { Invoke-Step @('uv', 'run', 'pytest', '-m', 'integration', '-q') }
}
