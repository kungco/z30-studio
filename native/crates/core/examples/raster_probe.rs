//! Developer-only float dump. Not part of the user-facing application.
use std::{
    fs::OpenOptions,
    io::{BufWriter, Write},
    path::PathBuf,
    time::Instant,
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: raster_probe INPUT.jpg|png NEW.f32".into());
    }
    let source = PathBuf::from(&args[0]);
    let output = PathBuf::from(&args[1]);
    if output.extension().is_none_or(|v| v != "f32") {
        return Err("output must be a new developer .f32 file".into());
    }
    let started = Instant::now();
    let decoded = z30_core::raster::decode(&source)?;
    let elapsed = started.elapsed().as_secs_f64();
    let mut file = BufWriter::new(
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output)?,
    );
    file.write_all(bytemuck::cast_slice(&decoded.frame.pixels))?;
    file.flush()?;
    println!(
        "{}",
        serde_json::json!({"width":decoded.frame.width,"height":decoded.frame.height,
        "decode_seconds":elapsed,"source_bits":decoded.source_bits,"policy":decoded.policy,"warnings":decoded.warnings})
    );
    Ok(())
}
