//! Synthetic float CPU/memory benchmark; not photographic quality validation.
use std::time::Instant;
use z30_core::{
    frame::Frame,
    retouch::{self, Mode, Repair},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 5 {
        return Err("usage: retouch_benchmark WIDTH HEIGHT COUNT clone|heal".into());
    }
    let (w, h, n): (u32, u32, usize) = (args[1].parse()?, args[2].parse()?, args[3].parse()?);
    let count = w as u64 * h as u64;
    if count == 0 || count > 80_000_000 || n > 256 {
        return Err("invalid synthetic dimensions/count".into());
    }
    let mode = match args[4].as_str() {
        "clone" => Mode::Clone,
        "heal" => Mode::Heal,
        _ => return Err("invalid mode".into()),
    };
    let source = Frame::new(
        w,
        h,
        (0..count)
            .map(|i| {
                let (x, y) = ((i % w as u64) as f32, (i / w as u64) as f32);
                let v = 0.3 + 0.15 * x / w as f32 + 0.03 * (x * 0.15).sin() * (y * 0.13).cos();
                [v * 1.28, v * 0.92, v * 0.65]
            })
            .collect(),
    )?;
    let edits: Vec<Repair> = (0..n)
        .map(|i| Repair {
            mode,
            x: 0.2 + (i % 8) as f64 * 0.07,
            y: 0.2 + ((i / 8) % 8) as f64 * 0.07,
            sx: 0.65,
            sy: 0.65,
            radius: 0.015,
            ..Default::default()
        })
        .collect();
    let started = Instant::now();
    let output = retouch::render(&source, &edits)?;
    let seconds = started.elapsed().as_secs_f64();
    let valid = output
        .as_ref()
        .unwrap_or(&source)
        .pixels
        .iter()
        .flatten()
        .all(|v| v.is_finite());
    println!(
        "{}",
        serde_json::json!({"synthetic":true,"width":w,"height":h,"count":n,"mode":args[4],
        "seconds":seconds,"owned_output_created":output.is_some(),"source_frame_bytes":count*12,"finite":valid})
    );
    if !valid {
        return Err("invalid output".into());
    }
    Ok(())
}
