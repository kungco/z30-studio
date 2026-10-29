fn main() {
    println!("cargo:rerun-if-changed=src/native_raw.c");
    println!("cargo:rerun-if-changed=../../vendor/libraw/libraw");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let mut build = cc::Build::new();
    build
        .file("src/native_raw.c")
        .include("../../vendor/libraw");
    if build.get_compiler().is_like_msvc() {
        build.flag_if_supported("/std:c11");
    } else {
        build.flag_if_supported("-std=c11");
    }
    build.compile("z30_raw_layout");
}
