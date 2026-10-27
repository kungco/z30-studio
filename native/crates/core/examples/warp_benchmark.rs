//! Synthetic float CPU/peak-memory probe, not photographic quality acceptance.
use std::time::Instant;
use z30_core::{
    frame::Frame,
    warp::{self, Pin},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 4 {
        return Err("usage: warp_benchmark WIDTH HEIGHT COUNT".into());
    }
    let (w, h, n): (u32, u32, usize) = (args[1].parse()?, args[2].parse()?, args[3].parse()?);
    let count = w as u64 * h as u64;
    if w < 32 || h < 32 || count > 80_000_000 || n > 4 {
        return Err("invalid synthetic shape/count".into());
    }
    let source = Frame::new(
        w,
        h,
        (0..count)
            .map(|i| {
                let (x, y) = ((i % w as u64) as f32, (i / w as u64) as f32);
                let v = 0.3 + 0.15 * x / w as f32 + 0.03 * (x * 0.15).sin() * (y * 0.13).cos();
                [v * 1.28 - 0.4, v * 0.92, v * 0.65 + 1.]
            })
            .collect(),
    )?;
    let centres = [[0.4, 0.46], [0.6, 0.46], [0.4, 0.61], [0.6, 0.61]];
    let radius = 0.08;
    let r = radius * w.min(h) as f64;
    let pins: Vec<_> = centres[..n]
        .iter()
        .enumerate()
        .map(|(i, &to)| Pin {
            from: [
                to[0] - (if i % 2 == 0 { 0.08 } else { -0.08 }) * r / (w - 1) as f64,
                to[1] - 0.02 * r / (h - 1) as f64,
            ],
            to,
            radius,
        })
        .collect();
    let start = Instant::now();
    let output = warp::render(&source, &pins)?;
    let seconds = start.elapsed().as_secs_f64();
    let pixels = &output.as_ref().unwrap_or(&source).pixels;
    let finite = pixels.iter().flatten().all(|v| v.is_finite());
    let changed = pixels != &source.pixels;
    println!(
        "{}",
        serde_json::json!({"width":w,"height":h,"count":n,"seconds":seconds,"finite":finite,"changed":changed,"owned_output_created":output.is_some(),"source_frame_bytes":count*12,"synthetic":true})
    );
    if !finite || changed != (n > 0) {
        return Err("invalid warp benchmark output".into());
    }
    Ok(())
}
