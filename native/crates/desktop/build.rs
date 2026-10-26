fn main() {
    fn git(args: &[&str]) -> String {
        std::process::Command::new("git")
            .args(args)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned())
            .unwrap_or_default()
    }
    for name in ["Z30_BUILD_SOURCE_COMMIT", "Z30_BUILD_SOURCE_DIRTY"] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    for path in [
        "src",
        "../core/src",
        "../core/build.rs",
        "Cargo.toml",
        "../../Cargo.lock",
    ] {
        println!("cargo:rerun-if-changed={path}");
    }
    let commit =
        std::env::var("Z30_BUILD_SOURCE_COMMIT").unwrap_or_else(|_| git(&["rev-parse", "HEAD"]));
    println!(
        "cargo:rustc-env=Z30_SOURCE_COMMIT={}",
        if commit.is_empty() {
            "unknown"
        } else {
            &commit
        }
    );
    let dirty = std::env::var("Z30_BUILD_SOURCE_DIRTY").unwrap_or_else(|_| {
        let status = git(&["status", "--porcelain"]);
        (!status.is_empty()).to_string()
    });
    println!("cargo:rustc-env=Z30_SOURCE_DIRTY={dirty}");
    for file in [
        "HEAD".to_owned(),
        "index".to_owned(),
        format!("refs/heads/{}", git(&["branch", "--show-current"])),
    ] {
        let path = git(&["rev-parse", "--path-format=absolute", "--git-path", &file]);
        if !path.is_empty() {
            println!("cargo:rerun-if-changed={path}");
        }
    }
    slint_build::compile_with_config(
        "ui/main.slint",
        slint_build::CompilerConfiguration::new().with_style("fluent-dark".into()),
    )
    .expect("Slint UI must compile");
}
