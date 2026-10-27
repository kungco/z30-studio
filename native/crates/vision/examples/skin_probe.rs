//! Explicit developer acceptance: source pixels/masks/retouched floats, never
//! a desktop UI claim. All output files are created exclusively in a new dir.
use std::{io::Write, path::PathBuf, sync::atomic::AtomicBool, time::Instant};

fn write_float(path: PathBuf, values: impl Iterator<Item = f32>) -> std::io::Result<()> {
    let mut file = std::io::BufWriter::new(
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)?,
    );
    for value in values {
        file.write_all(&value.to_le_bytes())?;
    }
    file.flush()
}

fn write_bytes(path: PathBuf, bytes: &[u8]) -> std::io::Result<()> {
    std::fs::OpenOptions::new().write(true).create_new(true).open(path)?.write_all(bytes)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    use z30_core::{
        develop,
        portrait::{Binding, Portrait},
        raster,
        recipe::Recipe,
        rendering,
        skin::SkinMask,
    };
    let args: Vec<PathBuf> = std::env::args_os().skip(1).map(PathBuf::from).collect();
    if args.len() != 4 || args.iter().any(|p| !p.is_absolute()) {
        return Err(
            "usage: skin_probe ABS_VISION_DIR ABS_SKIN_MODEL_DIR ABS_IMAGE NEW_ABS_OUTPUT_DIR"
                .into(),
        );
    }
    let [runtime, model, input, output] = &args[..] else {
        unreachable!()
    };
    let decoded = raster::decode(input)?;
    let neutral = develop::render(&decoded.frame, &Default::default())?;
    std::fs::create_dir(output)?;
    write_float(
        output.join("neutral.f32"),
        neutral.pixels.iter().flatten().copied(),
    )?;
    let start = Instant::now();
    let mut detector = z30_vision::detector::Detector::open(runtime)?;
    let mut segmenter = z30_vision::skin::Segmenter::open(runtime, model)?;
    let load_seconds = start.elapsed().as_secs_f64();
    let cancel = AtomicBool::new(false);
    let faces = detector.detect(&neutral, &cancel)?;
    let first = segmenter.segment(&neutral, &cancel)?;
    let repeat = segmenter.segment(&neutral, &cancel)?;
    if first.weights != repeat.weights {
        return Err("CPU repeat produced different mask".into());
    }
    write_float(
        output.join("face-probabilities.f32"),
        first.face_probabilities.iter().copied(),
    )?;
    write_bytes(output.join("categories.u8"), &first.categories)?;
    write_bytes(output.join("weights.u8"), &first.weights)?;
    let mut filter_seconds = None;
    if let Some(face) = faces.faces.first() {
        let binding = Binding::capture(input, neutral.width, neutral.height)?;
        let skin = SkinMask::new(binding, first.weights.clone())?;
        let recipe = Recipe {
            portrait: Portrait {
                face: Some(*face),
                binding: Some(binding),
                smooth: 70.,
            },
            ..Default::default()
        };
        let geometry = rendering::render(
            &decoded.frame,
            &recipe,
            input,
            (neutral.width, neutral.height),
        )?;
        let start = Instant::now();
        let semantic = rendering::render_with_skin(
            &decoded.frame,
            &recipe,
            input,
            (neutral.width, neutral.height),
            Some(&skin),
        )?;
        filter_seconds = Some(start.elapsed().as_secs_f64());
        write_float(
            output.join("geometry.f32"),
            geometry.pixels.iter().flatten().copied(),
        )?;
        write_float(
            output.join("semantic.f32"),
            semantic.pixels.iter().flatten().copied(),
        )?;
    }
    let cancelled = segmenter.segment(&neutral, &AtomicBool::new(true)).is_err();
    if !cancelled {
        return Err("Cancelled request was accepted".into());
    }
    println!(
        "{}",
        serde_json::json!({"model_load_seconds":load_seconds,"first":first,"repeat":repeat,
        "faces":faces.faces,"filter_seconds":filter_seconds,"cancelled_request_rejected":cancelled,
        "user_photos_tested":false,"desktop_integration":false,"cloud_requests":0})
    );
    Ok(())
}
