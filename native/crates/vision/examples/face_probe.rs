//! Developer-only CPU detector acceptance; reads images and writes JSON only.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::{path::PathBuf, sync::atomic::AtomicBool, time::Instant};
    use z30_core::{
        develop::{self, Parameters},
        raster,
    };
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if !(2..=3).contains(&args.len()) {
        return Err(
            "usage: face_probe ABSOLUTE_VISION_DIRECTORY ABSOLUTE_IMAGE [NEW_FLOAT_DUMP_DIRECTORY]"
                .into(),
        );
    }
    let directory = PathBuf::from(&args[0]);
    let input = PathBuf::from(&args[1]);
    if !input.is_absolute() {
        return Err("absolute input image required".into());
    }
    let decoded = raster::decode(&input)?;
    let frame = develop::render(&decoded.frame, &Parameters::default())?;
    drop(decoded);
    if let Some(path) = args.get(2) {
        use std::io::Write;
        let directory = PathBuf::from(path);
        if !directory.is_absolute() {
            return Err("absolute new float dump directory required".into());
        }
        std::fs::create_dir(&directory)?;
        let mut file = std::io::BufWriter::new(
            std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(directory.join("neutral.f32"))?,
        );
        for pixel in &frame.pixels {
            for value in pixel {
                file.write_all(&value.to_le_bytes())?;
            }
        }
        file.flush()?;
    }
    let started = Instant::now();
    let mut detector = z30_vision::detector::Detector::open(&directory)?;
    let model_load_seconds = started.elapsed().as_secs_f64();
    let result = detector.detect(&frame, &AtomicBool::new(false))?;
    let repeated = detector.detect(&frame, &AtomicBool::new(false))?;
    println!(
        "{}",
        serde_json::json!({"model_load_seconds":model_load_seconds,
        "first":result,"repeat":repeated,"identity_recognition":false,"cloud_requests":0})
    );
    Ok(())
}
