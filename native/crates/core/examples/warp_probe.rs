//! Developer-only independent-corpus float dump; no face detector or GUI.
use serde::Deserialize;
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    time::Instant,
};
use z30_core::{
    develop::SrgbFrame,
    frame::Frame,
    warp::{self, Map, Pin},
};
#[derive(Deserialize)]
struct Sample {
    at: [f64; 2],
}
#[derive(Deserialize)]
struct Case {
    #[serde(default)]
    space: Option<String>,
    name: String,
    width: u32,
    height: u32,
    pixels: Vec<[f32; 3]>,
    pins: Vec<Pin>,
    map_samples: Vec<Sample>,
    #[serde(default)]
    forward_samples: Vec<Sample>,
}
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: warp_probe CORPUS.json NEW_OUTPUT_DIR".into());
    }
    let input = PathBuf::from(&args[0]);
    let output = PathBuf::from(&args[1]);
    if fs::metadata(&input)?.len() > 16 * 1024 * 1024 {
        return Err("corpus exceeds budget".into());
    }
    let corpus: Corpus = serde_json::from_slice(&fs::read(input)?)?;
    if corpus.cases.is_empty() || corpus.cases.len() > 32 {
        return Err("invalid corpus count".into());
    }
    fs::create_dir(&output)?;
    let mut records = vec![];
    for case in corpus.cases {
        if case.name.is_empty()
            || case.name.len() > 48
            || !case
                .name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err("invalid case name".into());
        }
        let (pixels, owned, seconds) = match case.space.as_deref().unwrap_or("linear") {
            "linear" => {
                let frame = Frame::new(case.width, case.height, case.pixels)?;
                let start = Instant::now();
                let rendered = warp::render(&frame, &case.pins)?;
                let seconds = start.elapsed().as_secs_f64();
                let owned = rendered.is_some();
                (
                    rendered.map(|r| r.pixels).unwrap_or(frame.pixels),
                    owned,
                    seconds,
                )
            }
            "srgb" => {
                let frame = SrgbFrame {
                    width: case.width,
                    height: case.height,
                    pixels: case.pixels,
                };
                let start = Instant::now();
                let rendered = warp::render_srgb(&frame, &case.pins)?;
                let seconds = start.elapsed().as_secs_f64();
                let owned = rendered.is_some();
                (
                    rendered.map(|r| r.pixels).unwrap_or(frame.pixels),
                    owned,
                    seconds,
                )
            }
            _ => return Err("unknown working space".into()),
        };
        let map = Map::new(case.width, case.height, &case.pins)?;
        let samples: Vec<_> = case
            .map_samples
            .iter()
            .map(|s| map.source_at(s.at[0], s.at[1]))
            .collect::<z30_core::Result<_>>()?;
        let forward: Vec<_> = case
            .forward_samples
            .iter()
            .map(|s| map.destination_at(s.at[0], s.at[1]))
            .collect::<z30_core::Result<_>>()?;
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output.join(format!("{}.f32", case.name)))?
            .write_all(bytemuck::cast_slice(&pixels))?;
        records.push(serde_json::json!({"case":case.name,"seconds":seconds,"samples":samples,"forward":forward,"owned":owned}));
    }
    println!(
        "{}",
        serde_json::json!({"cases":records,"synthetic":true,"desktop_integration":false})
    );
    Ok(())
}
