Param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [String[]]$Args
)

# Run semabridge sync with TMDL engine enabled for this invocation.
$env:USE_TMDL_ENGINE = 'true'
Write-Host "Running semabridge sync with USE_TMDL_ENGINE=$($env:USE_TMDL_ENGINE)"

# Forward any arguments to the semabridge CLI
python -m semabridge sync @Args
