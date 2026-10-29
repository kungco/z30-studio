use z30_core::{blemish, frame::Frame, retouch::Repair};
fn image(w: u32, h: u32, spots: &[(f64, f64, f64)], scale: f32) -> Frame {
    Frame::new(
        w,
        h,
        (0..w * h)
            .map(|i| {
                let (x, y) = ((i % w) as f64, (i / w) as f64);
                let mut v = 0.3 + 0.0002 * x + 0.0001 * y;
                for &(cx, cy, r) in spots {
                    v *= 1. - 0.55 * (-((x - cx).powi(2) + (y - cy).powi(2)) / (2. * r * r)).exp();
                }
                [v as f32 * scale; 3]
            })
            .collect(),
    )
    .unwrap()
}
#[test]
fn compact_dark_spots_are_localized_without_modifying_float_pixels() {
    let f = image(160, 128, &[(55., 64., 1.4), (100., 70., 1.6)], 1.);
    let before = f.pixels.clone();
    let p = blemish::detect(&f, &vec![255; 160 * 128], &[], || false).unwrap();
    assert_eq!(p.len(), 2);
    for (x, y) in [(55., 64.), (100., 70.)] {
        assert!(
            p.iter()
                .any(|p| (p.x * 159. - x).hypot(p.y * 127. - y) < 1.)
        );
    }
    assert_eq!(f.pixels, before);
    assert!(p.iter().all(|p| (0.002..=0.02).contains(&p.radius)));
}
#[test]
fn flat_gradients_ungated_regions_and_existing_repairs_do_not_become_suggestions() {
    let plain = image(160, 128, &[], 1.);
    assert!(
        blemish::detect(&plain, &vec![255; 160 * 128], &[], || false)
            .unwrap()
            .is_empty()
    );
    let f = image(160, 128, &[(55., 64., 1.4)], 1.);
    assert!(
        blemish::detect(&f, &vec![0; 160 * 128], &[], || false)
            .unwrap()
            .is_empty()
    );
    let old = Repair {
        x: 55. / 159.,
        y: 64. / 127.,
        sx: 0.25,
        sy: 0.25,
        radius: 0.02,
        ..Default::default()
    };
    assert!(
        blemish::detect(&f, &vec![255; 160 * 128], &[old], || false)
            .unwrap()
            .is_empty()
    );
}
#[test]
fn multiplicative_exposure_preserves_dark_skin_and_hdr_localization() {
    let base = image(160, 128, &[(55., 64., 1.4)], 1.);
    let expected = blemish::detect(&base, &vec![255; 160 * 128], &[], || false).unwrap();
    for scale in [0.02, 0.2, 4., 16.] {
        let f = image(160, 128, &[(55., 64., 1.4)], scale);
        let actual = blemish::detect(&f, &vec![255; 160 * 128], &[], || false).unwrap();
        assert_eq!(actual.len(), expected.len());
        for (a, b) in actual.iter().zip(&expected) {
            assert!((a.x - b.x).abs() < 0.002);
            assert!((a.y - b.y).abs() < 0.002);
        }
    }
}
#[test]
fn elongated_edges_and_large_regions_are_rejected() {
    let mut f = image(160, 128, &[], 1.);
    for y in 35..95 {
        for x in 65..68 {
            f.pixels[y * 160 + x] = [0.05; 3];
        }
    }
    assert!(
        blemish::detect(&f, &vec![255; 160 * 128], &[], || false)
            .unwrap()
            .is_empty()
    );
    let big = image(160, 128, &[(80., 64., 15.)], 1.);
    assert!(
        blemish::detect(&big, &vec![255; 160 * 128], &[], || false)
            .unwrap()
            .is_empty()
    );
}
#[test]
fn budgets_invalid_data_and_mid_analysis_cancellation_are_explicit() {
    let f = image(160, 128, &[(55., 64., 1.4)], 1.);
    assert!(blemish::detect(&f, &[], &[], || false).is_err());
    assert!(blemish::detect(&f, &vec![255; 160 * 128], &[], || true).is_err());
    let mut calls = 0;
    assert!(
        blemish::detect(&f, &vec![255; 160 * 128], &[], || {
            calls += 1;
            calls > 4
        })
        .is_err()
    );
    let invalid = Frame {
        width: 160,
        height: 128,
        pixels: vec![[f32::NAN; 3]; 160 * 128],
    };
    assert!(blemish::detect(&invalid, &vec![255; 160 * 128], &[], || false).is_err());
}
