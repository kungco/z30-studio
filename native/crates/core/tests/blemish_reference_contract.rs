use serde::Deserialize;
use z30_core::{blemish, frame::Frame};
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
#[derive(Deserialize)]
struct Expected {
    x: f64,
    y: f64,
    radius: f64,
    contrast: f64,
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
    expected: Vec<Expected>,
}
#[test]
fn independent_numpy_windows_and_eigen_moments_match_all_eight_frozen_cases() {
    let corpus: Corpus = serde_json::from_str(include_str!("fixtures/blemish.json")).unwrap();
    assert_eq!(corpus.cases.len(), 8);
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
        let frame = Frame::new(c.width, c.height, pixels).unwrap();
        let gate = vec![if c.blocked { 0 } else { 255 }; (c.width * c.height) as usize];
        let actual = blemish::detect(&frame, &gate, &[], || false).unwrap();
        assert_eq!(actual.len(), c.expected.len(), "{}", c.name);
        for (a, e) in actual.iter().zip(c.expected) {
            assert!(
                (a.x - e.x).abs() < 1e-6
                    && (a.y - e.y).abs() < 1e-6
                    && (a.radius - e.radius).abs() < 1e-6
                    && (a.contrast - e.contrast).abs() < 1e-4,
                "{}",
                c.name
            );
        }
    }
}
