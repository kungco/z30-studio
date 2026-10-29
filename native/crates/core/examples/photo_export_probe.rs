//! Developer end-to-end RAW export acceptance. Writes only into a new directory.
#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::{io::Write, path::PathBuf, time::Instant};
    use z30_core::{
        develop::{Parameters, render},
        export::{self, Format},
        raw::{LibRaw, WhiteBalance},
    };
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 4 {
        return Err("usage: photo_export_probe DLL INPUT.NEF NEW_DIRECTORY PARAMETERS.json".into());
    }
    let (dll, source, directory, parameters) = (
        PathBuf::from(&args[0]),
        PathBuf::from(&args[1]),
        PathBuf::from(&args[2]),
        PathBuf::from(&args[3]),
    );
    if !directory.is_absolute() || parameters.metadata()?.len() > 8192 {
        return Err("new absolute output directory and <=8 KiB parameters required".into());
    }
    let parameters: Parameters = serde_json::from_slice(&std::fs::read(parameters)?)?;
    parameters.validate()?;
    std::fs::create_dir(&directory)?;
    let api = LibRaw::load(&dll)?;
    let started = Instant::now();
    let decoded = api.decode(&source, false, WhiteBalance::Camera)?;
    let decode_seconds = started.elapsed().as_secs_f64();
    let metadata = decoded.metadata.clone();
    let started = Instant::now();
    let frame = render(&decoded.frame, &parameters)?;
    let render_seconds = started.elapsed().as_secs_f64();
    drop(decoded);
    drop(api); // Do not retain the full linear frame while encoding.
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join("rendered.f32"))?;
    file.write_all(bytemuck::cast_slice(&frame.pixels))?;
    file.flush()?;
    drop(file);
    let mut outputs = Vec::new();
    for (name, format) in [
        ("developed.tif", Format::Tiff16),
        ("developed.png", Format::Png),
        ("developed.jpg", Format::Jpeg),
    ] {
        let destination = directory.join(name);
        let started = Instant::now();
        export::save(&frame, &destination, format, 95)?;
        outputs.push(serde_json::json!({"file":name,"encode_seconds":started.elapsed().as_secs_f64(),"bytes":destination.metadata()?.len()}));
    }
    println!(
        "{}",
        serde_json::json!({"width":frame.width,"height":frame.height,"metadata":metadata,"parameters":parameters,
        "decode_seconds":decode_seconds,"render_seconds":render_seconds,"outputs":outputs,"full_migration_complete":false})
    );
    Ok(())
}
#[cfg(not(windows))]
fn main() {
    eprintln!("The pinned LibRaw DLL probe requires Windows");
    std::process::exit(1);
}
