use serde::Deserialize;
use z30_core::{
    frame::Frame,
    warp::{self, Map, Pin},
};
#[derive(Deserialize)]
struct Sample {
    at: [f64; 2],
    source: [f64; 2],
    determinant: f64,
}
#[derive(Deserialize)]
struct Case {
    name: String,
    width: u32,
    height: u32,
    pixels: Vec<[f32; 3]>,
    pins: Vec<Pin>,
    expected: Vec<[f32; 3]>,
    map_samples: Vec<Sample>,
    owned: bool,
}
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
fn source() -> Frame {
    Frame::new(
        129,
        97,
        (0..129 * 97)
            .map(|i| {
                [
                    (i % 129) as f32 / 128.,
                    (i / 129) as f32 / 96.,
                    1.5 - (i % 57) as f32 * 0.04,
                ]
            })
            .collect(),
    )
    .unwrap()
}
fn drag(shift: f64) -> Pin {
    Pin {
        from: [0.5 - shift * 97. * 0.2 / 128., 0.5],
        to: [0.5, 0.5],
        radius: 0.2,
    }
}
fn jacobian(map: &Map, x: f64, y: f64) -> f64 {
    let eps = 1e-6;
    let a = map.source_at(x + eps, y).unwrap();
    let b = map.source_at(x - eps, y).unwrap();
    let c = map.source_at(x, y + eps).unwrap();
    let d = map.source_at(x, y - eps).unwrap();
    ((a[0] - b[0]) * (c[1] - d[1]) - (c[0] - d[0]) * (a[1] - b[1])) / (4. * eps * eps)
}
#[test]
fn frozen_independent_numpy_geometry_and_hdr_pixels_match_without_mutating_the_source() {
    let corpus: Corpus = serde_json::from_str(include_str!("fixtures/warp.json")).unwrap();
    assert_eq!(corpus.cases.len(), 8);
    for case in corpus.cases {
        let frame = Frame::new(case.width, case.height, case.pixels).unwrap();
        let before = frame.pixels.clone();
        let map = Map::new(case.width, case.height, &case.pins).unwrap();
        let output = warp::render(&frame, &case.pins).unwrap();
        assert_eq!(output.is_some(), case.owned, "{}", case.name);
        let actual = output.as_ref().unwrap_or(&frame);
        assert_eq!(actual.pixels.len(), case.expected.len());
        for (a, b) in actual
            .pixels
            .iter()
            .flatten()
            .zip(case.expected.iter().flatten())
        {
            assert!((a - b).abs() <= 2e-6, "{} {a} {b}", case.name);
        }
        for sample in case.map_samples {
            let got = map.source_at(sample.at[0], sample.at[1]).unwrap();
            assert!(
                (got[0] - sample.source[0]).abs() < 1e-9
                    && (got[1] - sample.source[1]).abs() < 1e-9,
                "{}",
                case.name
            );
            assert!((jacobian(&map, sample.at[0], sample.at[1]) - sample.determinant).abs() < 1e-7);
        }
        assert_eq!(frame.pixels, before);
    }
}
#[test]
fn invalid_geometry_budget_and_nonfinite_buffers_are_rejected_before_writing() {
    let frame = source();
    let mut invalid = drag(0.1001);
    assert!(warp::render(&frame, &[invalid]).is_err());
    invalid = drag(0.1);
    invalid.to[0] = f64::NAN;
    assert!(warp::render(&frame, &[invalid]).is_err());
    invalid = drag(0.1);
    invalid.radius = 0.3;
    assert!(warp::render(&frame, &[invalid]).is_err());
    invalid = Pin {
        from: [0.001, 0.5],
        to: [0.002, 0.5],
        radius: 0.1,
    };
    assert!(warp::render(&frame, &[invalid]).is_err());
    assert!(Map::new(129, 97, &[drag(0.1); 5]).is_err());
    assert!(Map::new(0, 3, &[]).is_err());
    assert!(Map::new(10001, 8001, &[]).is_err());
    let pins = [[0.15, 0.15], [0.85, 0.15], [0.15, 0.85], [0.85, 0.85]].map(|to| Pin {
        from: [to[0] - 0.0001, to[1]],
        to,
        radius: 0.05,
    });
    assert!(
        Map::new(10000, 8000, &pins)
            .err()
            .unwrap()
            .to_string()
            .contains("预算")
    );
    let bad = Frame {
        width: 2,
        height: 2,
        pixels: vec![[0.; 3]],
    };
    assert!(warp::render(&bad, &[]).is_err());
    let mut bad = source();
    bad.pixels[0][0] = f32::NAN;
    assert!(warp::render(&bad, &[drag(0.05)]).is_err());
}
#[test]
fn inactive_is_borrowed_and_target_anchor_boundary_and_outside_pixels_are_exact() {
    let frame = source();
    assert!(warp::render(&frame, &[]).unwrap().is_none());
    assert!(warp::render(&frame, &[drag(0.)]).unwrap().is_none());
    let p = drag(0.1);
    let map = Map::new(frame.width, frame.height, &[p]).unwrap();
    let centre = [64., 48.];
    let radius = 0.2 * 97.;
    let at = map.source_at(centre[0], centre[1]).unwrap();
    assert!((at[0] - p.from[0] * 128.).abs() < 1e-10);
    assert_eq!(at[1], 48.);
    assert_eq!(
        map.source_at(centre[0] + radius, centre[1]).unwrap(),
        [centre[0] + radius, centre[1]]
    );
    let eps = 1e-6;
    let inside = map.source_at(centre[0] + radius - eps, centre[1]).unwrap();
    assert!((inside[0] - (centre[0] + radius - eps)).abs() < 1e-8);
    let changed = warp::render(&frame, &[p]).unwrap().unwrap();
    assert!(changed.pixels != frame.pixels);
    for y in 0..97 {
        for x in 0..129 {
            if (x as f64 - 64.).hypot(y as f64 - 48.) >= radius {
                for c in 0..3 {
                    let i = y * 129 + x;
                    assert_eq!(changed.pixels[i][c].to_bits(), frame.pixels[i][c].to_bits());
                }
            }
        }
    }
    assert!(map.source_at(-1., 0.).is_err());
    assert!(map.source_at(f64::INFINITY, 0.).is_err());
}
#[test]
fn four_maximum_overlapping_drags_keep_positive_bounded_jacobians() {
    let map = Map::new(129, 97, &[drag(0.1); 4]).unwrap();
    for y in 30..67 {
        for x in 45..84 {
            let det = jacobian(&map, x as f64, y as f64);
            assert!(det > 0.5 && det < 1.8, "{x},{y}: {det}");
        }
    }
}
#[test]
fn cooperative_cancellation_drops_partial_output_and_preserves_original() {
    let frame = source();
    let before = frame.pixels.clone();
    let pin = drag(0.1);
    assert!(warp::render_with_cancel(&frame, &[pin], || true).is_err());
    let mut polls = 0;
    assert!(
        warp::render_with_cancel(&frame, &[pin], || {
            polls += 1;
            polls == 4
        })
        .is_err()
    );
    assert!(polls >= 4);
    assert_eq!(frame.pixels, before);
}
