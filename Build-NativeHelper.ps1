<# Build the Native Messaging host executable. #>

$ErrorActionPreference = 'Stop'
$scriptFile = Join-Path $PSScriptRoot 'native-helper\PdfPageExportHost.py'
$distDir = Join-Path $PSScriptRoot 'native-helper\build-output'
$workDir = Join-Path $PSScriptRoot 'native-helper\pyinstaller-work'
$specDir = Join-Path $PSScriptRoot 'native-helper\pyinstaller-spec'

# Keep stdin/stdout for Native Messaging, hide any owned console, and avoid
# extracting Python/Tk on every launch (especially slow after boot).
py -m PyInstaller --noconfirm --clean --onedir --hide-console hide-early --name PdfPageExportHost --distpath $distDir --workpath $workDir --specpath $specDir $scriptFile
if ($LASTEXITCODE -ne 0) { throw '本地小助手构建失败，未替换现有程序。' }
$bundle = Join-Path $distDir 'PdfPageExportHost'
Copy-Item -LiteralPath (Join-Path $bundle '_internal') -Destination (Join-Path $PSScriptRoot 'native-helper') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $bundle 'PdfPageExportHost.exe') -Destination (Join-Path $PSScriptRoot 'native-helper\PdfPageExportHost.exe') -Force
# Only remove the verified build-output directory within this project.
$resolvedDist = [IO.Path]::GetFullPath($distDir)
$expectedDist = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'native-helper\build-output'))
if ($resolvedDist -ne $expectedDist) { throw '构建输出路径不正确，停止清理。' }
Remove-Item -LiteralPath $resolvedDist -Recurse -Force

Write-Host '本地小助手已构建完成。'
