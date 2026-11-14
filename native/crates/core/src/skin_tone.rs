//! Bounded, optional low-frequency skin chroma correction and luminance lift.
//! No learned beauty target or identity change. Source texture/luminance is
//! retained by the colour-only control; all edits require a saved skin gate.
use crate::{
    Error, Result,
    develop::SrgbFrame,
    portrait::{self, Portrait},
    skin::{SIDE, SkinMask},
};
use serde::{Deserialize, Serialize};
const LUMA: [f32; 3] = [0.2126, 0.7152, 0.0722];
fn luma(p: [f32; 3]) -> f32 {
    p[0] * LUMA[0] + p[1] * LUMA[1] + p[2] * LUMA[2]
}
fn ramp(lo: f32, hi: f32, v: f32) -> f32 {
    let t = ((v - lo) / (hi - lo)).clamp(0., 1.);
    t * t * (3. - 2. * t)
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub uniformity: f32,
    pub lighten: f32,
}
impl Settings {
    pub fn active(&self) -> bool {
        self.uniformity > 0. || self.lighten > 0.
    }
    pub fn is_default(&self) -> bool {
        *self == Self::default()
    }
    pub fn validate(&self) -> Result<()> {
        if [self.uniformity, self.lighten]
            .iter()
            .any(|v| !v.is_finite() || !(0.0..=100.0).contains(v))
        {
            return Err(Error::Policy("肤色均匀和提亮强度必须为 0–100".into()));
        }
        Ok(())
    }
}
fn rgb_at(frame: &SrgbFrame, x: f64, y: f64) -> [f32; 3] {
    let x = x.clamp(0., frame.width as f64 - 1.);
    let y = y.clamp(0., frame.height as f64 - 1.);
    let (x0, y0) = (x.floor() as usize, y.floor() as usize);
    let (x1, y1) = (
        (x0 + 1).min(frame.width as usize - 1),
        (y0 + 1).min(frame.height as usize - 1),
    );
    let (dx, dy) = ((x - x0 as f64) as f32, (y - y0 as f64) as f32);
    std::array::from_fn(|c| {
        let a = frame.pixels[y0 * frame.width as usize + x0][c];
        let b = frame.pixels[y0 * frame.width as usize + x1][c];
        let t = a + (b - a) * dx;
        let a = frame.pixels[y1 * frame.width as usize + x0][c];
        let b = frame.pixels[y1 * frame.width as usize + x1][c];
        t + (a + (b - a) * dx - t) * dy
    })
}
fn box_field(input: &[[f32; 5]], rx: usize, ry: usize) -> Vec<[f32; 5]> {
    let mut horizontal = vec![[0.; 5]; SIDE * SIDE];
    let mut result = vec![[0.; 5]; SIDE * SIDE];
    for y in 0..SIDE {
        let mut sum = [0f64; 5];
        for dx in -(rx as isize)..=rx as isize {
            for c in 0..5 {
                sum[c] += input[y * SIDE + dx.clamp(0, SIDE as isize - 1) as usize][c] as f64;
            }
        }
        for x in 0..SIDE {
            for c in 0..5 {
                horizontal[y * SIDE + x][c] = (sum[c] / (2 * rx + 1) as f64) as f32;
                sum[c] -= input
                    [y * SIDE + (x as isize - rx as isize).clamp(0, SIDE as isize - 1) as usize][c]
                    as f64;
                sum[c] += input[y * SIDE + (x + rx + 1).min(SIDE - 1)][c] as f64;
            }
        }
    }
    for x in 0..SIDE {
        let mut sum = [0f64; 5];
        for dy in -(ry as isize)..=ry as isize {
            for c in 0..5 {
                sum[c] += horizontal[dy.clamp(0, SIDE as isize - 1) as usize * SIDE + x][c] as f64;
            }
        }
        for y in 0..SIDE {
            for c in 0..5 {
                result[y * SIDE + x][c] = (sum[c] / (2 * ry + 1) as f64) as f32;
                sum[c] -= horizontal
                    [(y as isize - ry as isize).clamp(0, SIDE as isize - 1) as usize * SIDE + x][c]
                    as f64;
                sum[c] += horizontal[(y + ry + 1).min(SIDE - 1) * SIDE + x][c] as f64;
            }
        }
    }
    result
}
fn delta_field(frame: &SrgbFrame, portrait: &Portrait, skin: &SkinMask) -> Vec<[f32; 3]> {
    let binding = portrait.binding.unwrap();
    let face = portrait.face.unwrap();
    let mut input = vec![[0.; 5]; SIDE * SIDE];
    for y in 0..SIDE {
        for x in 0..SIDE {
            let p = rgb_at(
                frame,
                (x as f64 + 0.5) * frame.width as f64 / SIDE as f64 - 0.5,
                (y as f64 + 0.5) * frame.height as f64 / SIDE as f64 - 0.5,
            );
            let value = luma(p);
            if value <= 0.02 {
                continue;
            }
            let weight = skin.weights()[y * SIDE + x] as f32 / 255.
                * portrait::mask(
                    &face,
                    (x as f32 + 0.5) * binding.width as f32 / SIDE as f32,
                    (y as f32 + 0.5) * binding.height as f32 / SIDE as f32,
                    binding.width,
                    binding.height,
                );
            input[y * SIDE + x] = [
                p[0] / value * weight,
                p[1] / value * weight,
                p[2] / value * weight,
                weight,
                value * weight,
            ];
        }
    }
    let rx = (face.rect[2] * SIDE as f32 * 0.035).round().clamp(1., 12.) as usize;
    let ry = (face.rect[3] * SIDE as f32 * 0.035).round().clamp(1., 12.) as usize;
    let filtered = box_field(&input, rx, ry);
    input
        .iter()
        .zip(filtered)
        .map(|(a, b)| {
            if a[3] < 0.01 || b[3] < 0.01 {
                return [0.; 3];
            }
            let edge = 1. - ramp(0.04, 0.18, (a[4] / a[3] - b[4] / b[3]).abs());
            std::array::from_fn(|c| (b[c] / b[3] - a[c] / a[3]).clamp(-0.08, 0.08) * edge)
        })
        .collect()
}
fn delta_at(field: &[[f32; 3]], x: f64, y: f64) -> [f32; 3] {
    let x = x.clamp(0., (SIDE - 1) as f64);
    let y = y.clamp(0., (SIDE - 1) as f64);
    let (x0, y0) = (x.floor() as usize, y.floor() as usize);
    let (x1, y1) = ((x0 + 1).min(SIDE - 1), (y0 + 1).min(SIDE - 1));
    let (dx, dy) = ((x - x0 as f64) as f32, (y - y0 as f64) as f32);
    std::array::from_fn(|c| {
        let t =
            field[y0 * SIDE + x0][c] + (field[y0 * SIDE + x1][c] - field[y0 * SIDE + x0][c]) * dx;
        t + (field[y1 * SIDE + x0][c] + (field[y1 * SIDE + x1][c] - field[y1 * SIDE + x0][c]) * dx
            - t)
            * dy
    })
}
/// Used after the source-bound rendering preflight. Extra working fields have
/// fixed 256² size, independent of full-size source pixels.
pub fn apply(
    frame: &mut SrgbFrame,
    portrait: &Portrait,
    skin: &SkinMask,
    settings: Settings,
) -> Result<()> {
    settings.validate()?;
    if !settings.active() {
        return Ok(());
    }
    portrait.validate()?;
    skin.check_portrait(portrait)?;
    if frame.width == 0
        || frame.height == 0
        || frame.width as u64 * frame.height as u64 > 80_000_000
        || frame.pixels.len() as u64 != frame.width as u64 * frame.height as u64
        || frame
            .pixels
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
    {
        return Err(Error::Policy("肤色输入缓冲区无效".into()));
    }
    let field = if settings.uniformity > 0. {
        Some(delta_field(frame, portrait, skin))
    } else {
        None
    };
    let face = portrait.face.unwrap();
    let binding = portrait.binding.unwrap();
    let (x0, y0) = (
        (face.rect[0] * frame.width as f32).floor() as usize,
        (face.rect[1] * frame.height as f32).floor() as usize,
    );
    let (x1, y1) = (
        (((face.rect[0] + face.rect[2]) * frame.width as f32).ceil() as usize)
            .min(frame.width as usize),
        (((face.rect[1] + face.rect[3]) * frame.height as f32).ceil() as usize)
            .min(frame.height as usize),
    );
    for y in y0..y1 {
        for x in x0..x1 {
            let (nx, ny) = (
                (x as f64 + 0.5) / frame.width as f64,
                (y as f64 + 0.5) / frame.height as f64,
            );
            let weight = skin.sample(x as f64 + 0.5, y as f64 + 0.5, frame.width, frame.height)
                * portrait::mask(
                    &face,
                    (nx * binding.width as f64) as f32,
                    (ny * binding.height as f64) as f32,
                    binding.width,
                    binding.height,
                );
            if weight == 0. {
                continue;
            }
            let pixel = &mut frame.pixels[y * frame.width as usize + x];
            let value = luma(*pixel);
            let dark = ramp(0.02, 0.12, value);
            if dark == 0. {
                continue;
            }
            if let Some(field) = &field {
                let delta = delta_at(field, nx * SIDE as f64 - 0.5, ny * SIDE as f64 - 0.5);
                let proposed: [f32; 3] = std::array::from_fn(|c| {
                    pixel[c] / value + delta[c] * settings.uniformity / 100. * 0.6 * weight * dark
                });
                let normalized = luma(proposed);
                let offset: [f32; 3] =
                    std::array::from_fn(|c| proposed[c] / normalized * value - pixel[c]);
                let mut step = 1f32;
                for c in 0..3 {
                    if offset[c] > 0. {
                        step = step.min((1. - pixel[c]) / offset[c]);
                    } else if offset[c] < 0. {
                        step = step.min(-pixel[c] / offset[c]);
                    }
                }
                for c in 0..3 {
                    pixel[c] = (pixel[c] + offset[c] * step.clamp(0., 1.)).clamp(0., 1.);
                }
            }
            if settings.lighten > 0. {
                let now = luma(*pixel);
                let target = now
                    + settings.lighten / 100.
                        * 0.045
                        * (1. - now)
                        * weight
                        * dark
                        * (1. - ramp(0.75, 0.98, now));
                let maximum = pixel.iter().copied().fold(0f32, f32::max);
                let ratio = (target / now).min(1. / maximum);
                for channel in pixel {
                    *channel = (*channel * ratio).clamp(0., 1.);
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::portrait::{Binding, Face, Point};
    fn setup() -> (tempfile::TempDir, Portrait, SkinMask, SrgbFrame) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("source");
        std::fs::write(&path, b"source").unwrap();
        let binding = Binding::capture(&path, 256, 256).unwrap();
        let face = Face {
            rect: [0.1, 0.1, 0.8, 0.8],
            confidence: 1.,
            landmarks: [
                Point { x: 0.35, y: 0.35 },
                Point { x: 0.65, y: 0.35 },
                Point { x: 0.5, y: 0.5 },
                Point { x: 0.4, y: 0.7 },
                Point { x: 0.6, y: 0.7 },
            ],
        };
        let p = Portrait {
            face: Some(face),
            binding: Some(binding),
            smooth: 0.,
        };
        let skin = SkinMask::new(binding, vec![255; SIDE * SIDE]).unwrap();
        let pixels = (0..256 * 256)
            .map(|i| {
                let (x, y) = (i % 256, i / 256);
                let v = 0.4 + 0.03 * (x as f32 * 0.9).sin() * (y as f32 * 0.8).cos();
                let bump = if (124..=132).contains(&x) && (124..=132).contains(&y) {
                    0.05
                } else {
                    0.
                };
                [
                    v * 1.28 + bump,
                    v * 0.92 - bump * LUMA[0] / LUMA[1],
                    v * 0.65,
                ]
            })
            .collect();
        (
            dir,
            p,
            skin,
            SrgbFrame {
                width: 256,
                height: 256,
                pixels,
            },
        )
    }
    #[test]
    fn uniformity_reduces_local_chroma_patch_and_preserves_every_luminance() {
        let (_dir, p, skin, mut frame) = setup();
        let before = frame.pixels.clone();
        apply(
            &mut frame,
            &p,
            &skin,
            Settings {
                uniformity: 100.,
                lighten: 0.,
            },
        )
        .unwrap();
        let center = 128 * 256 + 128;
        assert!(frame.pixels[center][0] < before[center][0] - 0.005);
        for (i, (a, b)) in before.iter().zip(&frame.pixels).enumerate() {
            assert!((luma(*a) - luma(*b)).abs() < 1e-6);
            assert!(b.iter().all(|v| v.is_finite() && (0.0..=1.0).contains(v)));
            if portrait::mask(
                &p.face.unwrap(),
                (i % 256) as f32 + 0.5,
                (i / 256) as f32 + 0.5,
                256,
                256,
            ) == 0.
            {
                assert_eq!(a, b);
            }
        }
    }
    #[test]
    fn lightening_is_bounded_monotonic_and_preserves_channel_ratios() {
        let (_dir, p, skin, mut frame) = setup();
        let before = frame.pixels.clone();
        apply(
            &mut frame,
            &p,
            &skin,
            Settings {
                uniformity: 0.,
                lighten: 100.,
            },
        )
        .unwrap();
        assert!(luma(frame.pixels[128 * 256 + 128]) > luma(before[128 * 256 + 128]));
        for (a, b) in before.iter().zip(&frame.pixels) {
            assert!(
                luma(*b) >= luma(*a) - 1e-7
                    && luma(*b) <= luma(*a) + 0.045 * (1. - luma(*a)) + 1e-6
            );
            assert!((a[0] / a[1] - b[0] / b[1]).abs() < 2e-6);
            assert!((a[2] / a[1] - b[2] / b[1]).abs() < 2e-6);
        }
    }
    #[test]
    fn zero_empty_gate_black_white_and_gray_have_safe_results() {
        let (_dir, p, skin, mut frame) = setup();
        let before = frame.pixels.clone();
        apply(&mut frame, &p, &skin, Settings::default()).unwrap();
        assert_eq!(frame.pixels, before);
        let empty = SkinMask::new(p.binding.unwrap(), vec![0; SIDE * SIDE]).unwrap();
        apply(
            &mut frame,
            &p,
            &empty,
            Settings {
                uniformity: 100.,
                lighten: 100.,
            },
        )
        .unwrap();
        assert_eq!(frame.pixels, before);
        for v in [0., 0.4, 1.] {
            frame.pixels.fill([v; 3]);
            apply(
                &mut frame,
                &p,
                &skin,
                Settings {
                    uniformity: 100.,
                    lighten: 100.,
                },
            )
            .unwrap();
            assert!(frame.pixels.iter().all(|p| p[0] == p[1] && p[1] == p[2]));
            if v == 0. || v == 1. {
                assert!(frame.pixels.iter().all(|p| *p == [v; 3]));
            }
        }
    }
    #[test]
    fn weighted_mean_matches_brute_force_at_edges_and_rectangular_radii() {
        let values: Vec<[f32; 5]> = (0..SIDE * SIDE)
            .map(|i| std::array::from_fn(|c| ((i * 17 + c * 29) % 251) as f32 / 250.))
            .collect();
        let actual = box_field(&values, 3, 2);
        for (x, y) in [(0, 0), (255, 255), (127, 129), (1, 254)] {
            let mut expected = [0f64; 5];
            for dy in -2isize..=2 {
                for dx in -3isize..=3 {
                    let xx = (x as isize + dx).clamp(0, 255) as usize;
                    let yy = (y as isize + dy).clamp(0, 255) as usize;
                    for c in 0..5 {
                        expected[c] += values[yy * SIDE + xx][c] as f64;
                    }
                }
            }
            for c in 0..5 {
                assert!((actual[y * SIDE + x][c] as f64 - expected[c] / 35.).abs() < 1e-6);
            }
        }
    }
    #[test]
    fn invalid_controls_pixels_or_mask_binding_are_rejected_before_mutation() {
        let (dir, p, skin, mut frame) = setup();
        let before = frame.pixels.clone();
        assert!(
            apply(
                &mut frame,
                &p,
                &skin,
                Settings {
                    uniformity: f32::NAN,
                    lighten: 0.
                }
            )
            .is_err()
        );
        assert_eq!(frame.pixels, before);
        let other = dir.path().join("other");
        std::fs::write(&other, b"source").unwrap();
        let wrong = SkinMask::new(
            Binding::capture(&other, 256, 256).unwrap(),
            vec![255; SIDE * SIDE],
        )
        .unwrap();
        assert!(
            apply(
                &mut frame,
                &p,
                &wrong,
                Settings {
                    uniformity: 50.,
                    lighten: 50.
                }
            )
            .is_err()
        );
        assert_eq!(frame.pixels, before);
        frame.pixels[0][0] = f32::NAN;
        assert!(
            apply(
                &mut frame,
                &p,
                &skin,
                Settings {
                    uniformity: 50.,
                    lighten: 0.
                }
            )
            .is_err()
        );
        assert!(frame.pixels[0][0].is_nan());
    }
}
