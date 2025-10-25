param(
    [string]$Path = ".",
    [switch]$Recursive,
    [switch]$Remove
)

# Set parameters for Get-ChildItem using splatting for clarity
$gciParams = @{
    Path   = $Path
    Filter = '*.py'
    File   = $true
}
if ($Recursive.IsPresent) {
    $gciParams['Recurse'] = $true
}

# Find all .py files using the robust parameter set
$files = Get-ChildItem @gciParams

# Define the pattern for characters that are NOT allowed
$pattern = '[^\w\s()\[\]{}''".,;:|\\/!?@#$%^&*<>~`=+-]'

foreach ($file in $files) {
    if ($Remove.IsPresent) {
        # --- REMOVAL MODE ---
        Write-Host "`nProcessing: $($file.FullName)" -ForegroundColor Cyan
        
        # Read the entire file content at once
        $originalContent = Get-Content -Path $file.FullName -Raw
        
        # Use the -replace operator to remove all matching characters
        $modifiedContent = $originalContent -replace $pattern, ''
        
        # Only write back to the file if changes were actually made
        if ($originalContent.Length -ne $modifiedContent.Length) {
            Write-Host "  -> Found and removed non-standard characters. Saving file..." -ForegroundColor Green
            try {
                # Save the modified content back to the file, defaulting to UTF-8
                Set-Content -Path $file.FullName -Value $modifiedContent -Encoding Utf8 -Force -ErrorAction Stop
            }
            catch {
                Write-Warning "Failed to save file $($file.FullName): $_"
            }
        }
        else {
            Write-Host "  -> File is clean. No changes needed." -ForegroundColor Gray
        }
    }
    else {
        # --- REPORTING MODE (Original Behavior) ---
        Write-Host "`nFile: $($file.FullName)" -ForegroundColor Cyan
        
        Get-Content $file.FullName | ForEach-Object -Process {
            $matches = [regex]::Matches($_, $pattern)
            
            if ($matches.Count -gt 0) {
                Write-Host "  Line $($_.ReadCount): " -NoNewline -ForegroundColor Yellow
                
                foreach ($match in $matches) {
                    $charCode = [int][char]$match.Value
                    Write-Host "$($match.Value) (U+$($charCode.ToString('X4'))) " -NoNewline -ForegroundColor Red
                }
                Write-Host "" # Newline after all matches on a line
                Write-Host "    $_" -ForegroundColor Gray
            }
        }
    }
}