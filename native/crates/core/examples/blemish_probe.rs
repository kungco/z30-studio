use serde::Deserialize;
use z30_core::{blemish, frame::Frame};
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
#[derive(Deserialize)]
struct Case {
    name: String,
    width: u32,
    height: u32,
    gain: f64,
    kind: String,
    spots: Vec<[f64; 3]>,
    blocked: bool,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .ok_or("usage: blemish_probe CORPUS.json")?;
    let corpus: Corpus = serde_json::from_slice(&std::fs::read(path)?)?;
    let mut output = Vec::new();
    for c in corpus.cases {
        let pixels = (0..c.width * c.height)
            .map(|i| {
                let (x, y) = ((i % c.width) as f64, (i / c.width) as f64);
                let mut v = 0.3 + 0.0002 * x + 0.0001 * y;
                for &[cx, cy, r] in &c.spots {
                    let distance = (x - cx).powi(2) + (y - cy).powi(2);
                    if c.kind == "gaussian" {
                        v *= 1. - 0.55 * (-distance / (2. * r * r)).exp();
                    } else if distance <= r * r {
                        v *= 0.35;
                    }
                }
                if c.kind == "line" && (60.0..63.0).contains(&x) && (25.0..100.0).contains(&y) {
                    v *= 0.2;
                }
                [(v * c.gain) as f32; 3]
            })
            .collect();
        let frame = Frame::new(c.width, c.height, pixels)?;
        let gate = vec![if c.blocked { 0 } else { 255 }; (c.width * c.height) as usize];
        let points = blemish::detect(&frame, &gate, &[], || false)?;
        output.push(serde_json::json!({"name":c.name,"points":points}));
    }
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
