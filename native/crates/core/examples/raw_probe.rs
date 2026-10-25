#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::{io::Write, path::PathBuf, time::Instant};
    use z30_core::develop::{Parameters, render};
    use z30_core::raw::{LibRaw, WhiteBalance};
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() < 3 || args.len() > 6 {
        return Err(
            "usage: raw_probe DLL INPUT.NEF NEW_OUTPUT.f32 [half] [--develop PARAMETERS.json]"
                .into(),
        );
    }
    let dll = PathBuf::from(&args[0]);
    let source = PathBuf::from(&args[1]);
    let output = PathBuf::from(&args[2]);
    if output.extension().is_none_or(|ext| ext != "f32") {
        return Err("probe output must be a NEW .f32 file".into());
    }
    let mut half = false;
    let mut parameters = None;
    let mut cursor = 3;
    while cursor < args.len() {
        match args[cursor].to_str() {
            Some("half") if !half => {
                half = true;
                cursor += 1;
            }
            Some("--develop") if parameters.is_none() && cursor + 1 < args.len() => {
                let path = PathBuf::from(&args[cursor + 1]);
                if path.metadata()?.len() > 8192 {
                    return Err("parameters exceed 8 KiB".into());
                }
                let p: Parameters = serde_json::from_slice(&std::fs::read(path)?)?;
                p.validate()?;
                parameters = Some(p);
                cursor += 2;
            }
            _ => return Err("unknown or repeated probe mode".into()),
        }
    }
    let api = LibRaw::load(&dll)?;
    let started = Instant::now();
    let decoded = api.decode(&source, half, WhiteBalance::Camera)?;
    let elapsed = started.elapsed().as_secs_f64();
    let started = Instant::now();
    let rendered = parameters
        .as_ref()
        .map(|p| render(&decoded.frame, p))
        .transpose()?;
    let render_seconds = rendered.as_ref().map(|_| started.elapsed().as_secs_f64());
    let pixels = rendered
        .as_ref()
        .map_or(&decoded.frame.pixels, |frame| &frame.pixels);
    let file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?;
    let mut writer = std::io::BufWriter::with_capacity(262144, file);
    for value in pixels.iter().flatten() {
        writer.write_all(&value.to_le_bytes())?;
    }
    writer.flush()?;
    println!(
        "{}",
        serde_json::json!({"metadata":decoded.metadata,"width":decoded.frame.width,"height":decoded.frame.height,"decode_seconds":elapsed,"timings":decoded.timings,"render_seconds":render_seconds,"output_space":if parameters.is_some(){"encoded-srgb-float32"}else{"linear-srgb-float32"}})
    );
    Ok(())
}
#[cfg(not(windows))]
fn main() {
    eprintln!("The pinned DLL probe requires Windows");
    std::process::exit(1);
}
