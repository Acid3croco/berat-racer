# Check a map package received on the PC against its SHA256SUMS (written by send_to_pc.sh on the Mac).
#   powershell -ExecutionPolicy Bypass -File verify_on_pc.ps1 C:\berat\package\berat70scale-2m
param([Parameter(Mandatory = $true)][string]$Package)

$bad = 0; $count = 0
foreach ($line in Get-Content (Join-Path $Package "SHA256SUMS")) {
    $hash, $file = $line -split '\s+', 2
    $path = Join-Path $Package ($file.TrimStart('*').Substring(2))      # "./sectors/0_0/height.png"
    $count++
    if (-not (Test-Path $path)) { Write-Output "missing: $path"; $bad++; continue }
    if ((Get-FileHash -Algorithm SHA256 $path).Hash.ToLower() -ne $hash) { Write-Output "differs: $path"; $bad++ }
}
Write-Output "$count files checked, $bad wrong"
exit [int]($bad -gt 0)
