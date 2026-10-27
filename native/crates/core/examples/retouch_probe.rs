//! Developer-only frozen-corpus float dump. Not a desktop repair tool.
use serde::Deserialize;
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    time::Instant,
};
use z30_core::{
    frame::Frame,
    retouch::{self, Repair},
};
#[derive(Deserialize)]
struct Choose {
    x: f64,
    y: f64,
    radius: f64,
}
#[derive(Deserialize)]
struct Case {
    name: String,
    width: u32,
    height: u32,
    pixels: Vec<[f32; 3]>,
    edits: Vec<Repair>,
    choose: Option<Choose>,
}
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: retouch_probe CORPUS.json NEW_OUTPUT_DIR".into());
    }
    let input = PathBuf::from(&args[0]);
    let output = PathBuf::from(&args[1]);
    if fs::metadata(&input)?.len() > 8 * 1024 * 1024 {
        return Err("corpus too large".into());
    }
    let corpus: Corpus = serde_json::from_slice(&fs::read(input)?)?;
    if corpus.cases.is_empty() || corpus.cases.len() > 32 {
        return Err("invalid corpus count".into());
    }
    fs::create_dir(&output)?;
    let mut records = Vec::new();
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
        let source = Frame::new(case.width, case.height, case.pixels)?;
        let start = Instant::now();
        let result = retouch::render(&source, &case.edits)?;
        let seconds = start.elapsed().as_secs_f64();
        let donor = case
            .choose
            .map(|v| retouch::choose_donor(&source, v.x, v.y, v.radius))
            .transpose()?;
        let result = result.as_ref().unwrap_or(&source);
        let file = output.join(format!("{}.f32", case.name));
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(file)?
            .write_all(bytemuck::cast_slice(&result.pixels))?;
        records.push(serde_json::json!({"case":case.name,"width":result.width,"height":result.height,"seconds":seconds,"donor":donor}));
    }
    println!(
        "{}",
        serde_json::json!({"cases":records,"synthetic":true,"desktop_integration":false})
    );
    Ok(())
}
