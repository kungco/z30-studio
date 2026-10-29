//! Developer-only geometry/reference output; no original file is written.
use std::{io::Write, path::PathBuf, sync::atomic::AtomicBool};
fn write_f32(path: PathBuf, values: impl Iterator<Item = f32>) -> std::io::Result<()> {
    let mut f = std::io::BufWriter::new(
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)?,
    );
    for v in values {
        f.write_all(&v.to_le_bytes())?;
    }
    f.flush()
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use z30_core::{develop, raster};
    use z30_vision::{contour, detector::Detector};
    let args: Vec<PathBuf> = std::env::args_os().skip(1).map(PathBuf::from).collect();
    if args.len() != 4 || args.iter().any(|p| !p.is_absolute()) {
        return Err("usage: contour_probe ABS_VISION ABS_CONTOUR ABS_IMAGE NEW_ABS_OUTPUT".into());
    }
    let [runtime, model, input, output] = &args[..] else {
        unreachable!()
    };
    let decoded = raster::decode(input)?;
    let frame = develop::render(&decoded.frame, &Default::default())?;
    drop(decoded);
    std::fs::create_dir(output)?;
    write_f32(
        output.join("neutral.f32"),
        frame.pixels.iter().flatten().copied(),
    )?;
    let mut detector = Detector::open(runtime)?;
    let faces = detector.detect(&frame, &AtomicBool::new(false))?;
    let result = if let Some(face) = faces.faces.first() {
        let roi = contour::Roi::from_face(face, frame.width, frame.height)?;
        write_f32(
            output.join("input.f32"),
            contour::prepare(&frame, roi)?.into_iter(),
        )?;
        let mut engine = contour::Landmarker::open(runtime, model)?;
        let mesh = engine.infer(&frame, face, || false)?;
        let pins = contour::propose(&mesh, (frame.width, frame.height))?;
        serde_json::json!({"mesh":mesh,"pins":pins,"face":face,"face_count":faces.faces.len()})
    } else {
        serde_json::json!({"mesh":null,"pins":[],"face_count":0})
    };
    let result = serde_json::json!({"result":result,"width":frame.width,"height":frame.height,"local_only":true,"identity_recognition":false});
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output.join("result.json"))?
        .write_all(serde_json::to_string_pretty(&result)?.as_bytes())?;
    println!("{result}");
    Ok(())
}
