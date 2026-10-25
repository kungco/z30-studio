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
    let commit = git(&["rev-parse", "HEAD"]);
    println!(
        "cargo:rustc-env=Z30_SOURCE_COMMIT={}",
        if commit.is_empty() {
            "unknown"
        } else {
            &commit
        }
    );
    println!(
        "cargo:rustc-env=Z30_SOURCE_DIRTY={}",
        !git(&["status", "--porcelain"]).is_empty()
    );
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
