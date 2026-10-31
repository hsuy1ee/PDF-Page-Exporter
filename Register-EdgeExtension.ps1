<#
.SYNOPSIS
    Registers the local helper for an unpacked Edge extension.

.DESCRIPTION
    First load edge-extension in edge://extensions, copy its ID, then run:
    .\Register-EdgeExtension.ps1 -ExtensionId YOUR_EDGE_EXTENSION_ID
#>

param(
    [string]$ExtensionId
)

$ErrorActionPreference = 'Stop'
if ($ExtensionId) {
    $ExtensionId = $ExtensionId.Trim()
    if ($ExtensionId -cnotmatch '^[a-p]{32}$') {
        throw '扩展 ID 应为 32 位小写字母（a-p）。请从 edge://extensions 复制完整 ID。'
    }
} else {
    Write-Host '请先在 edge://extensions 加载 edge-extension 文件夹，并复制扩展卡片上的 ID。'
    do {
        $ExtensionId = (Read-Host '粘贴 32 位扩展 ID，然后按回车').Trim()
        if ($ExtensionId -cmatch '^[a-p]{32}$') { break }
        Write-Host 'ID 格式不正确，请粘贴完整的 32 位小写字母（a-p）。' -ForegroundColor Yellow
    } while ($true)
}
$baseDir = $PSScriptRoot
$hostExecutable = Join-Path $baseDir 'native-helper\PdfPageExportHost.exe'
$hostManifest = Join-Path $baseDir 'native-helper\com.xsy.pdf_page_exporter.json'
$registryPath = 'HKCU:\SOFTWARE\Microsoft\Edge\NativeMessagingHosts\com.xsy.pdf_page_exporter'

if (-not (Test-Path -LiteralPath $hostExecutable)) {
    throw "找不到本地小助手：$hostExecutable。请先运行 Build-NativeHelper.ps1。"
}

$manifest = @{
    name = 'com.xsy.pdf_page_exporter'
    description = 'PDF 页面导出图片本地小助手'
    path = $hostExecutable
    type = 'stdio'
    allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json -Depth 3

[IO.File]::WriteAllText($hostManifest, $manifest, (New-Object System.Text.UTF8Encoding($false)))
New-Item -Path $registryPath -Force | Out-Null
Set-ItemProperty -Path $registryPath -Name '(default)' -Value $hostManifest

Write-Host "已注册 Edge 本地小助手。现在可以点击扩展的图标进行导出。"
