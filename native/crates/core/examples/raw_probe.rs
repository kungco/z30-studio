#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::{io::Write, path::PathBuf, time::Instant};
    use z30_core::raw::{LibRaw, WhiteBalance};
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() < 3 || args.len() > 4 {
        return Err("usage: raw_probe DLL INPUT.NEF NEW_OUTPUT.f32 [half]".into());
    }
    let dll = PathBuf::from(&args[0]);
    let source = PathBuf::from(&args[1]);
    let output = PathBuf::from(&args[2]);
    if output.extension().is_none_or(|ext| ext != "f32") {
        return Err("probe output must be a NEW .f32 file".into());
    }
    let half = args.get(3).is_some_and(|s| s == "half");
    if args.len() == 4 && !half {
        return Err("unknown mode".into());
    }
    let api = LibRaw::load(&dll)?;
    let started = Instant::now();
    let decoded = api.decode(&source, half, WhiteBalance::Camera)?;
    let elapsed = started.elapsed().as_secs_f64();
    let file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?;
    let mut writer = std::io::BufWriter::with_capacity(262144, file);
    for value in decoded.frame.pixels.iter().flatten() {
        writer.write_all(&value.to_le_bytes())?;
    }
    writer.flush()?;
    println!(
        "{}",
        serde_json::json!({"metadata":decoded.metadata,"width":decoded.frame.width,"height":decoded.frame.height,"decode_seconds":elapsed})
    );
    Ok(())
}
#[cfg(not(windows))]
fn main() {
    eprintln!("The pinned DLL probe requires Windows");
    std::process::exit(1);
}
