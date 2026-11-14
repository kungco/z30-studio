use serde::Deserialize;
use z30_core::{
    develop::SrgbFrame,
    frame::Frame,
    warp::{self, Map, Pin},
};
#[derive(Deserialize)]
struct Sample {
    at: [f64; 2],
    source: [f64; 2],
}
#[derive(Deserialize)]
struct Forward {
    at: [f64; 2],
    destination: [f64; 2],
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
    forward_samples: Vec<Forward>,
    owned: bool,
}
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
fn drag() -> Pin {
    Pin {
        from: [15.5 / 32., 0.5],
        to: [0.5, 0.5],
        radius: 0.2,
    }
}
#[test]
fn encoded_colour_and_forward_coordinates_match_independent_numpy_and_bisection() {
    let corpus: Corpus = serde_json::from_str(include_str!("fixtures/warp_srgb.json")).unwrap();
    assert_eq!(corpus.cases.len(), 4);
    for case in corpus.cases {
        let frame = SrgbFrame {
            width: case.width,
            height: case.height,
            pixels: case.pixels,
        };
        let before = frame.pixels.clone();
        let map = Map::new(frame.width, frame.height, &case.pins).unwrap();
        let result = warp::render_srgb(&frame, &case.pins).unwrap();
        assert_eq!(result.is_some(), case.owned);
        let output = result.as_ref().unwrap_or(&frame);
        for (a, b) in output
            .pixels
            .iter()
            .flatten()
            .zip(case.expected.iter().flatten())
        {
            assert!((a - b).abs() < 2e-6, "{} {a} {b}", case.name);
        }
        for s in case.map_samples {
            let got = map.source_at(s.at[0], s.at[1]).unwrap();
            assert!((got[0] - s.source[0]).abs() < 1e-9 && (got[1] - s.source[1]).abs() < 1e-9);
        }
        for s in case.forward_samples {
            let got = map.destination_at(s.at[0], s.at[1]).unwrap();
            assert!(
                (got[0] - s.destination[0]).abs() < 1e-9
                    && (got[1] - s.destination[1]).abs() < 1e-9,
                "{}",
                case.name
            );
        }
        assert_eq!(frame.pixels, before);
    }
}
#[test]
fn encoded_black_white_half_pixel_is_averaged_in_linear_light() {
    let frame = SrgbFrame {
        width: 33,
        height: 33,
        pixels: (0..33 * 33)
            .map(|i| [if i % 33 < 16 { 0. } else { 1. }; 3])
            .collect(),
    };
    let warped = warp::render_srgb(&frame, &[drag()]).unwrap().unwrap();
    assert!((warped.pixels[16 * 33 + 16][0] - 0.73535698).abs() < 1e-7);
    let linear = Frame::new(33, 33, frame.pixels).unwrap();
    let plain = warp::render(&linear, &[drag()]).unwrap().unwrap();
    assert!((plain.pixels[16 * 33 + 16][0] - 0.5).abs() < 1e-7);
}
#[test]
fn forward_and_inverse_composition_round_trip_maximum_ordered_drags() {
    let p = Pin {
        from: [0.5 - 0.1 * 0.2 * 97. / 128., 0.5],
        to: [0.5, 0.5],
        radius: 0.2,
    };
    let map = Map::new(129, 97, &[p; 4]).unwrap();
    for y in (8..90).step_by(5) {
        for x in (8..120).step_by(5) {
            let at = map.destination_at(x as f64, y as f64).unwrap();
            let back = map.source_at(at[0], at[1]).unwrap();
            assert!((back[0] - x as f64).abs() < 1e-8 && (back[1] - y as f64).abs() < 1e-8);
        }
    }
    assert!(map.destination_at(-1., 0.).is_err());
    assert!(map.destination_at(0., f64::NAN).is_err());
}
#[test]
fn encoded_cancel_and_bad_buffers_preserve_the_source() {
    let frame = SrgbFrame {
        width: 33,
        height: 33,
        pixels: vec![[0.2, 0.4, 0.7]; 33 * 33],
    };
    let before = frame.pixels.clone();
    assert!(warp::render_srgb_with_cancel(&frame, &[drag()], || true).is_err());
    let mut calls = 0;
    assert!(
        warp::render_srgb_with_cancel(&frame, &[drag()], || {
            calls += 1;
            calls == 3
        })
        .is_err()
    );
    assert_eq!(frame.pixels, before);
    let bad = SrgbFrame {
        width: 33,
        height: 33,
        pixels: vec![[f32::NAN; 3]; 33 * 33],
    };
    assert!(warp::render_srgb(&bad, &[drag()]).is_err());
}
