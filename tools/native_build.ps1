param(
    [string]$ToolchainRoot = $env:Z30_TOOLCHAIN_ROOT,
    [switch]$Test,
    [switch]$DebugBuild,
    [switch]$RawProbe,
    [switch]$RawFixtures,
    [switch]$ExportProbe,
    [switch]$RasterProbe,
    [switch]$PhotoExportProbe,
    [switch]$EditProbe,
    [string]$RawRuntime
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
if($RawRuntime){$runtimePath=(Resolve-Path -LiteralPath $RawRuntime).Path}
$sourceCommit=(& git -C $repoRoot rev-parse HEAD).Trim()
$sourceDirty=[bool]((& git -C $repoRoot status --porcelain) -join '')
$cargoTool='cargo'
$rustcTool='rustc'
if($ToolchainRoot){
    $resolvedToolchain=(Resolve-Path -LiteralPath $ToolchainRoot).Path
    $env:RUSTUP_HOME=Join-Path $resolvedToolchain 'rustup'
    $env:CARGO_HOME=Join-Path $resolvedToolchain 'cargo'
    $env:PATH=(Join-Path $env:CARGO_HOME 'bin')+';'+$env:PATH
    # A matching installed compiler can be used directly while rustup's optional
    # component download is unavailable; never silently use a different version.
    $pinnedVersion=(Select-String -LiteralPath (Join-Path $repoRoot 'native\rust-toolchain.toml') -Pattern 'channel\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    $candidates=@("$pinnedVersion-x86_64-pc-windows-gnu","$pinnedVersion-x86_64-pc-windows-msvc",'stable-x86_64-pc-windows-gnu','stable-x86_64-pc-windows-msvc')
    foreach($candidate in $candidates){
        $candidateBin=Join-Path $env:RUSTUP_HOME "toolchains\$candidate\bin"
        $candidateRustc=Join-Path $candidateBin 'rustc.exe'
        if(Test-Path -LiteralPath $candidateRustc){
            $actualVersion=& $candidateRustc --version
            if($LASTEXITCODE -eq 0 -and $actualVersion -like "rustc $pinnedVersion *"){
                $rustcTool=$candidateRustc;$cargoTool=Join-Path $candidateBin 'cargo.exe'
                $env:RUSTC=$rustcTool;$env:PATH=$candidateBin+';'+$env:PATH
                $selfContained=Join-Path (Split-Path -Parent $candidateBin) 'lib\rustlib\x86_64-pc-windows-gnu\bin\self-contained'
                if(Test-Path -LiteralPath $selfContained){
                    $env:PATH=$selfContained+';'+$env:PATH
                    # GNU's automatic mode disables bundled CRTs when it finds
                    # a GCC on PATH. Keep Rust's matching CRT/import libraries.
                    $extraRustFlags=@('-C','link-self-contained=yes')
                }
                break
            }
        }
    }
    $mingwBin=Join-Path $resolvedToolchain 'w64devkit\bin'
    if(Test-Path -LiteralPath $mingwBin){
        $env:PATH=$mingwBin+';'+$env:PATH
        $env:CC_x86_64_pc_windows_gnu=Join-Path $mingwBin 'gcc.exe'
        $env:AR_x86_64_pc_windows_gnu=Join-Path $mingwBin 'ar.exe'
        # Use Rust's matching linker/libgcc; the portable toolchain supplies the
        # assembler absent from rust-mingw, not a replacement CRT configuration.
        if($selfContained -and (Test-Path -LiteralPath $selfContained)){$env:PATH=$selfContained+';'+$env:PATH}
        if($extraRustFlags){
            $portableDlltool=Join-Path $mingwBin 'dlltool.exe'
            if(Test-Path -LiteralPath $portableDlltool){$extraRustFlags+=@('-C',"dlltool=$portableDlltool")}
        }
    }
    if($extraRustFlags){
        $priorFlags=if($env:CARGO_ENCODED_RUSTFLAGS){$env:CARGO_ENCODED_RUSTFLAGS -split [char]31}else{($env:RUSTFLAGS -split '\s+') | Where-Object {$_}}
        $env:CARGO_ENCODED_RUSTFLAGS=(@($priorFlags)+$extraRustFlags) -join [char]31
    }
}
if(-not (Get-Command $cargoTool -ErrorAction SilentlyContinue)){throw 'Install Rust or pass -ToolchainRoot. No global PATH changes are made.'}
$previousBuildCommit=$env:Z30_BUILD_SOURCE_COMMIT
$previousBuildDirty=$env:Z30_BUILD_SOURCE_DIRTY
Push-Location (Join-Path $repoRoot 'native')
try {
    $env:Z30_BUILD_SOURCE_COMMIT=$sourceCommit
    $env:Z30_BUILD_SOURCE_DIRTY=$sourceDirty.ToString().ToLowerInvariant()
    & $rustcTool -Vv
    if($LASTEXITCODE -ne 0){throw 'Rust toolchain unavailable'}
    if($Test){& $cargoTool test --workspace --locked -j 4;if($LASTEXITCODE -ne 0){throw 'Native tests failed'}}
    if($RawFixtures){& $cargoTool test -p z30-core --release --locked raw::tests::real_z30_12_and_14_bit_full_and_half -- --ignored --test-threads=1;if($LASTEXITCODE -ne 0){throw 'Explicit RAW fixture tests failed'}}
    $buildArgs=@('build','--workspace','--locked','-j','4')
    if(-not $DebugBuild){$buildArgs+='--release'}
    & $cargoTool @buildArgs
    if($LASTEXITCODE -ne 0){throw 'Native build failed'}
    if($RawProbe){& $cargoTool build -p z30-core --example raw_probe --release --locked -j 4;if($LASTEXITCODE -ne 0){throw 'RAW probe build failed'}}
    if($ExportProbe){& $cargoTool build -p z30-core --example export_probe --release --locked -j 4;if($LASTEXITCODE -ne 0){throw 'Export probe build failed'}}
    if($RasterProbe){& $cargoTool build -p z30-core --example raster_probe --release --locked -j 4;if($LASTEXITCODE -ne 0){throw 'Raster probe build failed'}}
    if($PhotoExportProbe){& $cargoTool build -p z30-core --example photo_export_probe --release --locked -j 4;if($LASTEXITCODE -ne 0){throw 'Photo export probe build failed'}}
    if($EditProbe){& $cargoTool build -p z30-core --example edit_preview_probe --release --locked -j 4;if($LASTEXITCODE -ne 0){throw 'Edit preview probe build failed'}}
    $profile=if($DebugBuild){'debug'}else{'release'}
    if(((& git rev-parse HEAD).Trim() -ne $sourceCommit) -or ([bool]((& git status --porcelain) -join '') -ne $sourceDirty)){
        throw 'Source revision/cleanliness changed during build; rebuild before staging'
    }
    $binary=Join-Path (Get-Location) "target\$profile\z30-native.exe"
    $staging=Join-Path $repoRoot ('native\dist\stage-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    Copy-Item -LiteralPath $binary -Destination (Join-Path $staging 'Z30Studio.exe')
    Copy-Item -LiteralPath (Join-Path $repoRoot 'native\licenses') -Destination (Join-Path $staging 'licenses') -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot 'native\README.md') -Destination (Join-Path $staging '使用说明.md')
    if($RawRuntime){
        $packPython=Join-Path $repoRoot '.venv\Scripts\python.exe'
        if(-not (Test-Path -LiteralPath $packPython)){$packPython='python'}
        & $packPython (Join-Path $repoRoot 'tools\package_native.py') install-runtime --stage $staging --runtime $runtimePath
        if($LASTEXITCODE -ne 0){throw 'RAW runtime distribution verification failed'}
    }
    $buildInfo=[ordered]@{
        app='Z30 Studio Native';version='0.1.0-stage2g';profile=$profile
        source_commit=$sourceCommit
        source_tree_dirty=$sourceDirty
        compiler=(& $rustcTool --version)
        exe_bytes=(Get-Item -LiteralPath $binary).Length
        exe_sha256=(Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
        full_feature_migration_complete=$false
        models_bundled=$false
        raw_bundled=[bool]$RawRuntime
        runtime='Windows system DLLs and OS UI fonts; no Python/browser/local HTTP server'
    }
    [System.IO.File]::WriteAllText((Join-Path $staging 'build-info.json'),($buildInfo|ConvertTo-Json -Depth 5),[System.Text.UTF8Encoding]::new($false))
    Get-Item -LiteralPath $binary | Select-Object FullName,Length
    Get-FileHash -LiteralPath $binary -Algorithm SHA256
    Write-Output "Staged app: $staging\Z30Studio.exe"
} finally {
    [System.Environment]::SetEnvironmentVariable('Z30_BUILD_SOURCE_COMMIT',$previousBuildCommit,'Process')
    [System.Environment]::SetEnvironmentVariable('Z30_BUILD_SOURCE_DIRTY',$previousBuildDirty,'Process')
    Pop-Location
}
