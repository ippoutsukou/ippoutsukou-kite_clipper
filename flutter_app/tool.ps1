param([ValidateSet('doctor','get','analyze','test','windows','build-windows','apk')][string]$Task = 'doctor')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskFlutter = Join-Path $taskRoot 'vendor\flutter\bin\flutter.bat'
$taskOldPub = $env:PUB_CACHE
$taskOldAnalytics = $env:FLUTTER_SUPPRESS_ANALYTICS
$taskOldJava = $env:JAVA_HOME
$taskOldAndroid = $env:ANDROID_HOME
$taskOldAndroidUser = $env:ANDROID_USER_HOME
$taskOldGradle = $env:GRADLE_USER_HOME
Push-Location $PSScriptRoot
try {
    $env:PUB_CACHE = Join-Path $taskRoot 'vendor\pub-cache'
    $env:FLUTTER_SUPPRESS_ANALYTICS = 'true'
    $taskJdk = Join-Path $taskRoot 'vendor\jdk17\jdk-17.0.20.1+1'
    if (Test-Path (Join-Path $taskJdk 'bin\java.exe')) { $env:JAVA_HOME = $taskJdk }
    $env:ANDROID_HOME = Join-Path $taskRoot 'vendor\android-sdk'
    $env:ANDROID_USER_HOME = Join-Path $taskRoot 'vendor\android-user'
    $env:GRADLE_USER_HOME = Join-Path $taskRoot 'vendor\gradle-cache'
    # 起動中のビルドを見分けるため、ビルド時刻（例 0917-0023）をアプリに埋め込む。
    $taskBuildLabel = @('--dart-define', ('BUILD_LABEL=' + (Get-Date -Format 'MMdd-HHmm')))
    switch ($Task) {
        'doctor' { & $taskFlutter doctor -v }
        'get' { & $taskFlutter pub get }
        'analyze' { & $taskFlutter analyze --no-pub }
        'test' { & $taskFlutter test --no-pub --no-test-assets }
        'windows' { & $taskFlutter run -d windows $taskBuildLabel }
        'build-windows' { & $taskFlutter build windows --debug $taskBuildLabel }
        'apk' { & $taskFlutter build apk --debug $taskBuildLabel }
    }
    if ($LASTEXITCODE -ne 0) { throw "Flutter処理に失敗しました: $Task" }
} finally {
    Pop-Location
    $env:PUB_CACHE = $taskOldPub
    $env:FLUTTER_SUPPRESS_ANALYTICS = $taskOldAnalytics
    $env:JAVA_HOME = $taskOldJava
    $env:ANDROID_HOME = $taskOldAndroid
    $env:ANDROID_USER_HOME = $taskOldAndroidUser
    $env:GRADLE_USER_HOME = $taskOldGradle
}
