//! Synthetic full-size CPU/memory probe. No original photograph or model is read.
use std::{path::Path, time::Instant};
use z30_core::{
    develop::SrgbFrame,
    portrait::{Binding, Face, Point, Portrait},
    skin::{SIDE, SkinMask},
    skin_tone::{self, Settings},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 6 {
        return Err(
            "usage: skin_tone_probe WIDTH HEIGHT UNIFORMITY LIGHTEN NEW_ANCHOR_FILE".into(),
        );
    }
    let (width, height): (u32, u32) = (args[1].parse()?, args[2].parse()?);
    let count = width as u64 * height as u64;
    if count == 0 || count > 80_000_000 {
        return Err("invalid synthetic size".into());
    }
    let settings = Settings {
        uniformity: args[3].parse()?,
        lighten: args[4].parse()?,
    };
    settings.validate()?;
    let anchor = Path::new(&args[5]);
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(anchor)?;
    std::io::Write::write_all(&mut file, b"synthetic benchmark binding; not an image")?;
    let binding = Binding::capture(anchor, width, height)?;
    let portrait = Portrait {
        binding: Some(binding),
        smooth: 0.,
        face: Some(Face {
            rect: [0.1, 0.1, 0.8, 0.8],
            confidence: 1.,
            landmarks: [
                Point { x: 0.35, y: 0.35 },
                Point { x: 0.65, y: 0.35 },
                Point { x: 0.5, y: 0.5 },
                Point { x: 0.4, y: 0.7 },
                Point { x: 0.6, y: 0.7 },
            ],
        }),
    };
    let skin = SkinMask::new(binding, vec![255; SIDE * SIDE])?;
    let mut frame = SrgbFrame {
        width,
        height,
        pixels: (0..count)
            .map(|i| {
                let (x, y) = ((i % width as u64) as f32, (i / width as u64) as f32);
                let v = 0.35 + 0.15 * x / width as f32 + 0.03 * (x * 0.15).sin() * (y * 0.13).cos();
                [v * 1.28, v * 0.92, v * 0.65]
            })
            .collect(),
    };
    let started = Instant::now();
    skin_tone::apply(&mut frame, &portrait, &skin, settings)?;
    let elapsed = started.elapsed().as_secs_f64();
    let finite_in_gamut = frame
        .pixels
        .iter()
        .flatten()
        .all(|v| v.is_finite() && (0.0..=1.0).contains(v));
    println!(
        "{}",
        serde_json::json!({"synthetic":true,"width":width,"height":height,
        "face_rect":portrait.face.unwrap().rect,"uniformity":settings.uniformity,
        "lighten":settings.lighten,"tone_seconds":elapsed,"finite_in_gamut":finite_in_gamut,
        "sample":frame.pixels[frame.pixels.len()/2],"source_frame_bytes":count*12})
    );
    if !finite_in_gamut {
        return Err("non-finite or out-of-gamut output".into());
    }
    Ok(())
}
