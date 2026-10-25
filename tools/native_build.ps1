param(
    [string]$ToolchainRoot = $env:Z30_TOOLCHAIN_ROOT,
    [switch]$Test,
    [switch]$DebugBuild
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
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
Push-Location (Join-Path $repoRoot 'native')
try {
    & $rustcTool -Vv
    if($LASTEXITCODE -ne 0){throw 'Rust toolchain unavailable'}
    if($Test){& $cargoTool test --workspace --locked -j 4;if($LASTEXITCODE -ne 0){throw 'Native tests failed'}}
    $buildArgs=@('build','--workspace','--locked','-j','4')
    if(-not $DebugBuild){$buildArgs+='--release'}
    & $cargoTool @buildArgs
    if($LASTEXITCODE -ne 0){throw 'Native build failed'}
    $profile=if($DebugBuild){'debug'}else{'release'}
    $binary=Join-Path (Get-Location) "target\$profile\z30-native.exe"
    $staging=Join-Path $repoRoot ('native\dist\stage-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    Copy-Item -LiteralPath $binary -Destination (Join-Path $staging 'Z30Studio.exe')
    Copy-Item -LiteralPath (Join-Path $repoRoot 'native\licenses') -Destination (Join-Path $staging 'licenses') -Recurse
    Copy-Item -LiteralPath (Join-Path $repoRoot 'native\README.md') -Destination (Join-Path $staging '使用说明.md')
    $buildInfo=[ordered]@{
        app='Z30 Studio Native';version='0.1.0-stage1';profile=$profile
        source_commit=(& git rev-parse HEAD).Trim()
        source_tree_dirty=[bool]((& git status --porcelain) -join '')
        compiler=(& $rustcTool --version)
        exe_bytes=(Get-Item -LiteralPath $binary).Length
        exe_sha256=(Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
        full_feature_migration_complete=$false
        models_bundled=$false
        runtime='Windows system DLLs and OS UI fonts; no Python/browser/local HTTP server'
    }
    [System.IO.File]::WriteAllText((Join-Path $staging 'build-info.json'),($buildInfo|ConvertTo-Json -Depth 5),[System.Text.UTF8Encoding]::new($false))
    Get-Item -LiteralPath $binary | Select-Object FullName,Length
    Get-FileHash -LiteralPath $binary -Algorithm SHA256
    Write-Output "Staged app: $staging\Z30Studio.exe"
} finally {Pop-Location}
