use std::{path::PathBuf, time::Instant};
use z30_core::{
    develop::SrgbFrame,
    export::{Format, save},
};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let target = PathBuf::from(
        std::env::args_os()
            .nth(1)
            .ok_or("Provide a NEW absolute .tif destination")?,
    );
    let frame = SrgbFrame {
        width: 1024,
        height: 1,
        pixels: (0..1024)
            .map(|i| {
                let x = (i as f32 + 0.123) / 1024.0;
                [x, x * 0.5, 1.0 - x]
            })
            .collect(),
    };
    let clock = Instant::now();
    save(&frame, &target, Format::Tiff16, 92)?;
    println!(
        "{}",
        serde_json::json!({"width":1024,"height":1,"export_seconds":clock.elapsed().as_secs_f64(),"bytes":target.metadata()?.len()})
    );
    Ok(())
}
