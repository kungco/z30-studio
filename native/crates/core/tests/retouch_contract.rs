use serde::Deserialize;
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
    expected: Vec<[f32; 3]>,
    choose: Option<Choose>,
    expected_donor: Option<[f64; 2]>,
}
#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}
#[test]
fn float_repairs_match_frozen_python_including_edges_overlap_and_hdr() {
    let corpus: Corpus = serde_json::from_str(include_str!("fixtures/retouch.json")).unwrap();
    assert_eq!(corpus.cases.len(), 10);
    for case in corpus.cases {
        let source = Frame::new(case.width, case.height, case.pixels).unwrap();
        let original = source.pixels.clone();
        let output = retouch::render(&source, &case.edits).unwrap();
        let actual = output.as_ref().unwrap_or(&source);
        assert_eq!(actual.pixels.len(), case.expected.len());
        for (i, (a, b)) in actual
            .pixels
            .iter()
            .flatten()
            .zip(case.expected.iter().flatten())
            .enumerate()
        {
            assert!(
                (a - b).abs() <= 2e-6,
                "{} channel {}: {} vs {}",
                case.name,
                i,
                a,
                b
            );
        }
        assert_eq!(source.pixels, original);
        if let Some(choose) = case.choose {
            let donor = retouch::choose_donor(&source, choose.x, choose.y, choose.radius).unwrap();
            let expected = case.expected_donor.unwrap();
            assert!(
                (donor.x - expected[0]).abs() < 1e-8 && (donor.y - expected[1]).abs() < 1e-8,
                "{} donor {:?} vs {:?}",
                case.name,
                donor,
                expected
            );
        }
    }
}
