//! Nine-control float pipeline migrated from the retained processing.py oracle.
//! Inputs are linear sRGB; output remains float until display/file quantization.
use crate::{Error, Result, frame::Frame};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};

const LUMA: [f32; 3] = [0.2126, 0.7152, 0.0722];
const RGB_LMS: [[f32; 3]; 3] = [
    [0.41222146, 0.53633255, 0.051445995],
    [0.2119035, 0.6806995, 0.10739696],
    [0.08830246, 0.28171885, 0.6299787],
];
const LMS_LAB: [[f32; 3]; 3] = [
    [0.21045426, 0.7936178, -0.004072047],
    [1.9779985, -2.4285922, 0.4505937],
    [0.025904037, 0.78277177, -0.80867577],
];
const LAB_LMS64: [[f64; 3]; 3] = [
    [0.9999999984799417, 0.3963377921537674, 0.21580375802634233],
    [
        1.0000000088980117,
        -0.10556134234721802,
        -0.06385417483942997,
    ],
    [1.000000054630844, -0.08948418213793843, -1.2914855379547747],
];
const LMS_RGB64: [[f64; 3]; 3] = [
    [4.076741942889828, -3.307711991174428, 0.23096997692536173],
    [-1.2684381261827504, 2.609757604306311, -0.34131942433773027],
    [
        -0.004196055076226249,
        -0.7034186762444677,
        1.7076146699636876,
    ],
];
fn dot(a: [f32; 3], b: [f32; 3]) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn matrix64(m: [[f64; 3]; 3], p: [f64; 3]) -> [f64; 3] {
    m.map(|r| r[0] * p[0] + r[1] * p[1] + r[2] * p[2])
}
fn lab_to_rgb64(p: [f64; 3]) -> [f64; 3] {
    matrix64(LMS_RGB64, matrix64(LAB_LMS64, p).map(|v| v * v * v))
}
fn in_gamut(p: [f32; 3]) -> bool {
    p.iter().all(|&v| (0.0..=1.0).contains(&v))
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Parameters {
    pub exposure: f32,
    pub contrast: f32,
    pub highlights: f32,
    pub shadows: f32,
    pub temperature: f32,
    pub tint: f32,
    pub saturation: f32,
    pub vibrance: f32,
    pub sharpen: f32,
}
impl Parameters {
    pub fn validate(&self) -> Result<()> {
        let all = [
            self.exposure,
            self.contrast,
            self.highlights,
            self.shadows,
            self.temperature,
            self.tint,
            self.saturation,
            self.vibrance,
            self.sharpen,
        ];
        if all.iter().any(|v| !v.is_finite())
            || !(-4.0..=4.0).contains(&self.exposure)
            || all[1..8].iter().any(|v| !(-100.0..=100.0).contains(v))
            || !(0.0..=100.0).contains(&self.sharpen)
        {
            return Err(Error::Policy("调色参数非有限值或超出原有九参数范围".into()));
        }
        Ok(())
    }
}
/// Gamma-encoded sRGB float, deliberately separate from linear Frame.
pub struct SrgbFrame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<[f32; 3]>,
}

pub fn tone_luminance(mut y: f32, p: &Parameters) -> f32 {
    y = y.max(0.0);
    y *= ((p.shadows / 100.0 * 1.2) * (-y / 0.18).exp()).exp2();
    if p.contrast != 0.0 {
        y = 0.18 * (y / 0.18).powf(2.0f32.powf(p.contrast / 200.0));
    }
    if p.highlights != 0.0 {
        y *= (p.highlights / 100.0 * (y / (y + 0.3))).exp2();
    }
    let knee = 0.65;
    let above = (y - knee).max(0.0);
    if y <= knee {
        y
    } else {
        knee + (1.0 - knee) * above / (above + 1.0 - knee)
    }
}
fn table(p: &Parameters, ceiling: f32) -> Vec<f32> {
    (0..=32768)
        .map(|i| {
            let root = (i as f64 * (ceiling as f64).sqrt() / 32768.0) as f32;
            tone_luminance(root * root, p)
        })
        .collect()
}
fn mapped(y: f32, table: &[f32], ceiling: f32) -> f32 {
    let position = (y.max(0.0) / ceiling).sqrt() * 32768.0;
    let index = (position as usize).min(32767);
    let fraction = position - index as f32;
    table[index] + (table[index + 1] - table[index]) * fraction
}
fn gamut(mut lab: [f64; 3]) -> [f32; 3] {
    lab[0] = lab[0].clamp(0.0, 1.0);
    // Paired f64 inverses and a 24-bit chroma search avoid the old 1/1024
    // staircase: two ULPs of L previously changed encoded blue by ~0.008.
    let mut rgb = lab_to_rgb64(lab);
    let neutral = lab_to_rgb64([lab[0], 0.0, 0.0]);
    let lower = neutral.iter().copied().fold(f64::INFINITY, f64::min);
    let upper = neutral.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let guard = 1e-5f64.min(lower * 0.25).min((1.0 - upper) * 0.25).max(0.0);
    if rgb.iter().any(|&v| !(guard..=1.0 - guard).contains(&v)) {
        let original = [lab[1], lab[2]];
        let (mut low, mut high) = (0.0, 1.0);
        for _ in 0..24 {
            let mid = (low + high) * 0.5;
            let candidate = [lab[0], original[0] * mid, original[1] * mid];
            if lab_to_rgb64(candidate)
                .iter()
                .all(|&v| (guard..=1.0 - guard).contains(&v))
            {
                low = mid;
            } else {
                high = mid;
            }
        }
        rgb = lab_to_rgb64([lab[0], original[0] * low, original[1] * low]);
    }
    rgb.map(|v| v.clamp(0.0, 1.0) as f32)
}
fn encode_analytic(v: f32) -> f32 {
    let v = v.max(0.0);
    if v <= 0.0031308 {
        (v * 12.92).clamp(0.0, 1.0)
    } else {
        (1.055 * v.powf(1.0 / 2.4) - 0.055).clamp(0.0, 1.0)
    }
}

fn encode(v: f32) -> f32 {
    let v = v.clamp(0., 1.);
    if v == 1. {
        return 1.;
    }
    if v <= 0.0031308 {
        return v * 12.92;
    }
    // Interpolate float values, rather than rounding input to a 16-bit code.
    // The dense independent analytic regression bounds the transfer error.
    static CURVE: std::sync::OnceLock<Vec<f32>> = std::sync::OnceLock::new();
    let table = CURVE.get_or_init(|| {
        (0..=65536)
            .map(|i| encode_analytic(i as f32 / 65536.))
            .collect()
    });
    let position = v * 65536.;
    let index = (position as usize).min(65535);
    let fraction = position - index as f32;
    (table[index] + (table[index + 1] - table[index]) * fraction).clamp(0., 1.)
}

pub fn render(source: &Frame, p: &Parameters) -> Result<SrgbFrame> {
    p.validate()?;
    let count = source.width as u64 * source.height as u64;
    if count == 0
        || count > 80_000_000
        || count != source.pixels.len() as u64
        || source
            .pixels
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || v.abs() > 1e6)
    {
        return Err(Error::Policy("浮点输入尺寸/数值无效或超出显影预算".into()));
    }
    let mut gains = [
        (2.0f64.powf(p.temperature as f64 / 180.0)) as f32,
        (2.0f64.powf(-p.tint as f64 / 220.0)) as f32,
        (2.0f64.powf(-p.temperature as f64 / 180.0)) as f32,
    ];
    let normal = dot(gains, LUMA);
    let exposure = 2.0f64.powf(p.exposure as f64) as f32;
    for value in &mut gains {
        *value = (*value / normal) * exposure;
    }
    let mut output: Vec<[f32; 3]> = Vec::new();
    output
        .try_reserve_exact(source.pixels.len())
        .map_err(|_| Error::Policy("浮点输出内存不足".into()))?;
    let mut tables = BTreeMap::new();
    for input in source.pixels.chunks(source.width as usize * 128) {
        let start = output.len();
        let mut maximum = 0.0f32;
        for value in input {
            let x = std::array::from_fn(|c| value[c] * gains[c]);
            maximum = maximum.max(dot(x, LUMA));
            output.push(x);
        }
        let exponent = (maximum.max(1.0) as f64).log2().ceil() as i32;
        let ceiling = 2.0f32.powi(exponent);
        let lut = tables.entry(exponent).or_insert_with(|| table(p, ceiling));
        let pixels = &mut output[start..];
        let mut needs_gamut = false;
        for x in pixels.iter_mut() {
            let y = dot(*x, LUMA);
            let ratio = if y > 1e-12 {
                mapped(y, lut, ceiling) / y
            } else {
                0.0
            };
            for c in x.iter_mut() {
                *c *= ratio;
            }
            needs_gamut |= !in_gamut(*x);
        }
        if p.saturation != 0.0 || p.vibrance != 0.0 || needs_gamut {
            for x in pixels.iter_mut() {
                let lms =
                    matrix64(RGB_LMS.map(|r| r.map(f64::from)), x.map(f64::from)).map(f64::cbrt);
                let mut lab = matrix64(LMS_LAB.map(|r| r.map(f64::from)), lms);
                if p.saturation != 0.0 || p.vibrance != 0.0 {
                    let chroma = (lab[1] * lab[1] + lab[2] * lab[2]).sqrt();
                    let hue = lab[2].atan2(lab[1]);
                    let warm =
                        (-((hue - 0.8) / 0.55).powi(2)).exp() * (-(chroma / 0.2).powi(2)).exp();
                    let saturation = p.saturation as f64 / 100.0;
                    let factor = (if saturation > 0.0 {
                        1.0 + saturation * (1.0 - 0.55 * warm)
                    } else {
                        1.0 + saturation
                    }) * (1.0
                        + p.vibrance as f64 / 100.0 * (-chroma / 0.12).exp() * (1.0 - 0.6 * warm));
                    lab[1] *= factor;
                    lab[2] *= factor;
                }
                *x = gamut(lab).map(encode);
            }
        } else {
            for x in pixels {
                *x = x.map(encode);
            }
        }
    }
    if p.sharpen != 0.0 {
        sharpen(
            &mut output,
            source.width as usize,
            source.height as usize,
            p.sharpen,
        );
    }
    Ok(SrgbFrame {
        width: source.width,
        height: source.height,
        pixels: output,
    })
}

fn sharpen(rgb: &mut [[f32; 3]], width: usize, height: usize, amount: f32) {
    let weights = [1.0, 4.0, 6.0, 4.0, 1.0];
    let mut rows: VecDeque<(usize, Vec<f32>)> = VecDeque::new();
    for y in 0..height {
        let indices: [usize; 5] = std::array::from_fn(|i| {
            (y as isize + i as isize - 2).clamp(0, height as isize - 1) as usize
        });
        rows.retain(|(row, _)| *row >= indices[0]);
        for &sy in &indices {
            if !rows.iter().any(|(row, _)| *row == sy) {
                // Future source rows remain untouched; previous rows are retained
                // in the five-row cache. No full-image luminance copies are needed.
                let horizontal = (0..width)
                    .map(|x| {
                        let mut sum = 0.0;
                        for (i, weight) in weights.iter().enumerate() {
                            let sx =
                                (x as isize + i as isize - 2).clamp(0, width as isize - 1) as usize;
                            sum += dot(rgb[sy * width + sx], LUMA) * weight;
                        }
                        sum / 16.0
                    })
                    .collect();
                rows.push_back((sy, horizontal));
            }
        }
        let cached = indices.map(|sy| &rows.iter().find(|(row, _)| *row == sy).unwrap().1);
        for x in 0..width {
            let pixel = &mut rgb[y * width + x];
            let luminance = dot(*pixel, LUMA);
            let mut blur = 0.0;
            for i in 0..5 {
                blur += cached[i][x] * weights[i];
            }
            blur /= 16.0;
            let mut detail = luminance - blur;
            detail *= (detail.abs() / (3.0 / 255.0)).min(1.0);
            let target = (luminance + detail * (amount / 75.0)).max(0.0);
            let gain = if luminance > 1e-10 {
                target / luminance
            } else {
                1.0
            };
            let maximum = pixel.iter().copied().fold(f32::NEG_INFINITY, f32::max);
            let gain = gain.min(1.0 / maximum.max(1e-10));
            for c in pixel {
                *c *= gain;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn interpolated_srgb_is_monotonic_and_matches_dense_independent_analytic_curve() {
        let mut previous = 0.;
        let mut maximum = 0f64;
        for i in 0..=2_000_000 {
            let x = i as f32 / 2_000_000.;
            let actual = encode(x);
            let x = x as f64;
            let expected = if x <= 0.0031308 {
                12.92 * x
            } else {
                1.055 * x.powf(1. / 2.4) - 0.055
            };
            maximum = maximum.max((actual as f64 - expected).abs());
            assert!(actual >= previous);
            previous = actual;
        }
        assert!(maximum < 2e-6, "sRGB interpolation error {maximum}");
        assert_eq!(encode(0.), 0.);
        assert_eq!(encode(1.), 1.);
        assert_eq!(encode(-1.), 0.);
        assert_eq!(encode(5.), 1.);
    }
    #[test]
    fn gamut_boundary_matches_independent_polynomial_roots() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/gamut-boundaries.json")).unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let lab: [f32; 3] = serde_json::from_value(case["lab"].clone()).unwrap();
            let expected: [f32; 3] = serde_json::from_value(case["expected"].clone()).unwrap();
            let actual = gamut(lab.map(f64::from));
            for c in 0..3 {
                assert!(
                    (actual[c] - expected[c]).abs() < 2e-6,
                    "cubic boundary mismatch: {lab:?}, {actual:?}, {expected:?}"
                );
            }
        }
    }
    #[test]
    fn two_ulps_do_not_create_visible_gamut_staircases() {
        let lab = [
            0.8651548624038696f32,
            -0.012072279118001461,
            0.24119898676872253,
        ];
        let mut shifted = lab;
        shifted[0] = f32::from_bits(lab[0].to_bits() + 2);
        let a = gamut(lab.map(f64::from)).map(encode);
        let b = gamut(shifted.map(f64::from)).map(encode);
        assert!(a.iter().zip(b).all(|(x, y)| (x - y).abs() <= 2e-5));
    }
    #[test]
    fn rejects_nonfinite_out_of_range_and_unknown_parameters() {
        assert!(
            Parameters {
                exposure: f32::NAN,
                ..Parameters::default()
            }
            .validate()
            .is_err()
        );
        assert!(
            Parameters {
                sharpen: -1.0,
                ..Parameters::default()
            }
            .validate()
            .is_err()
        );
        assert!(serde_json::from_str::<Parameters>(r#"{"exposure":0,"shell":"execute"}"#).is_err());
    }
    #[test]
    fn black_anchor_monotonic_shoulder_and_highlight_separation() {
        let p = Parameters {
            shadows: 75.0,
            highlights: -70.0,
            contrast: 30.0,
            ..Parameters::default()
        };
        assert_eq!(tone_luminance(0.0, &p), 0.0);
        let mut previous = 0.0;
        for i in 0..10000 {
            let y = tone_luminance(i as f32 / 100.0, &p);
            assert!(y >= previous && y < 1.0);
            previous = y;
        }
        assert!(tone_luminance(32.0, &p) > tone_luminance(16.0, &p));
    }
    #[test]
    fn eight_frozen_nine_control_outputs_match_python() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/develop.json")).unwrap();
        let pixels = serde_json::from_value(fixture["pixels"].clone()).unwrap();
        let source = Frame::new(10, 8, pixels).unwrap();
        let original = source.pixels.clone();
        for case in fixture["cases"].as_array().unwrap() {
            let parameters: Parameters =
                serde_json::from_value(case["parameters"].clone()).unwrap();
            let result = render(&source, &parameters).unwrap();
            let expected: Vec<[f32; 3]> = serde_json::from_value(case["expected"].clone()).unwrap();
            let maximum = result
                .pixels
                .iter()
                .flatten()
                .zip(expected.iter().flatten())
                .map(|(a, b)| (a - b).abs())
                .fold(0.0, f32::max);
            assert!(
                maximum <= 2e-5,
                "nine-control oracle mismatch {maximum}: {parameters:?}"
            );
            assert!(
                result
                    .pixels
                    .iter()
                    .flatten()
                    .all(|&v| v.is_finite() && (0.0..=1.0).contains(&v))
            );
        }
        assert_eq!(source.pixels, original);
    }
}
