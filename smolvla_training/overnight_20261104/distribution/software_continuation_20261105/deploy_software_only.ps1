param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+$')]
    [string]$Target
)
$ErrorActionPreference = 'Stop'
$taskManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SOFTWARE_BUNDLE_MANIFEST.json') -Raw | ConvertFrom-Json
foreach ($taskName in @($taskManifest.archive_name, $taskManifest.installer_name)) {
    if ($taskName -notmatch '^[A-Za-z0-9_.-]+$') { throw '파일명 검증 실패' }
}
$taskArchive = Join-Path $PSScriptRoot $taskManifest.archive_name
$taskInstaller = Join-Path $PSScriptRoot $taskManifest.installer_name
if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskManifest.archive_sha256) { throw '묶음 해시 불일치' }
if ((Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskManifest.installer_sha256) { throw '설치기 해시 불일치' }
$taskSshOptions = @('-F','none','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','-o','PubkeyAuthentication=no','-o','PreferredAuthentications=password','-o','NumberOfPasswordPrompts=1')
$taskTransferDir = 'nov_software_transfer_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
& ssh @taskSshOptions $Target "test ! -e ~/$taskTransferDir && mkdir -m 700 -- ~/$taskTransferDir"
if ($LASTEXITCODE -ne 0) { throw 'SSH 접속/새 전송 폴더 생성 실패. 재시도나 네트워크 설정 변경 없음' }
& scp @taskSshOptions $taskArchive $taskInstaller "${Target}:$taskTransferDir/"
if ($LASTEXITCODE -ne 0) { throw '파일 전송 실패. 로봇/서비스 실행 없음' }
$taskRemoteCommand = "python3 -B ~/$taskTransferDir/$($taskManifest.installer_name) $($taskManifest.archive_name) $($taskManifest.archive_sha256) --execute && bash ~/nov_live_prep_20261104/resume_software_only.sh"
& ssh @taskSshOptions -t $Target $taskRemoteCommand
if ($LASTEXITCODE -ne 0) { throw '소프트웨어 배포/오프라인 점검 실패. 원격 보고서 확인 필요' }
Write-Output 'SOFTWARE_ONLY_FINISHED: 로봇/serial/카메라/live 실행 없음. 실기 준비 완료 의미 아님'
