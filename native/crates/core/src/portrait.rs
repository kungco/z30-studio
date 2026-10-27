//! Conservative spatial face smoothing on unquantized display sRGB.
use crate::{Error, Result, develop::SrgbFrame, source::SourceState};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub x: f32,
    pub y: f32,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Face {
    pub rect: [f32; 4],
    pub landmarks: [Point; 5],
    pub confidence: f32,
}
impl Face {
    pub fn validate(&self) -> Result<()> {
        let [x, y, w, h] = self.rect;
        if !self.rect.iter().all(|v| v.is_finite())
            || x < 0.
            || y < 0.
            || w <= 0.
            || h <= 0.
            || x + w > 1.000001
            || y + h > 1.000001
            || !self.confidence.is_finite()
            || !(0.0..=1.0).contains(&self.confidence)
            || self.landmarks.iter().any(|p| {
                !p.x.is_finite()
                    || !p.y.is_finite()
                    || !(0.0..=1.0).contains(&p.x)
                    || !(0.0..=1.0).contains(&p.y)
            })
        {
            return Err(Error::Policy("人脸框或五点坐标无效".into()));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub source: SourceState,
    pub path_sha256: [u8; 32],
    pub width: u32,
    pub height: u32,
}
impl Binding {
    pub fn capture(path: &Path, width: u32, height: u32) -> Result<Self> {
        let binding = Self {
            source: SourceState::read(path)?,
            path_sha256: Self::path_key(path)?,
            width,
            height,
        };
        binding.validate()?;
        Ok(binding)
    }
    pub(crate) fn validate(&self) -> Result<()> {
        self.source.validate()?;
        let count = self.width as u64 * self.height as u64;
        if count == 0 || count > 80_000_000 {
            return Err(Error::Policy("人像绑定尺寸无效".into()));
        }
        Ok(())
    }
    fn path_key(path: &Path) -> Result<[u8; 32]> {
        let path = path.canonicalize()?;
        let text = path
            .to_str()
            .ok_or_else(|| Error::Policy("人像原片路径无法保存为 UTF-8".into()))?;
        Ok(Sha256::digest(text.as_bytes()).into())
    }
    pub fn matches_source(&self, path: &Path) -> Result<bool> {
        Ok(self.source == SourceState::read(path)? && self.path_sha256 == Self::path_key(path)?)
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Portrait {
    pub face: Option<Face>,
    pub binding: Option<Binding>,
    pub smooth: f32,
}
impl Portrait {
    pub fn validate(&self) -> Result<()> {
        if !self.smooth.is_finite() || !(0.0..=100.0).contains(&self.smooth) {
            return Err(Error::Policy("面部柔化强度必须为 0–100".into()));
        }
        match (self.face, self.binding) {
            (Some(face), Some(binding)) => {
                face.validate()?;
                binding.validate()?;
            }
            (None, None) if self.smooth == 0. => {}
            _ => return Err(Error::Policy("面部柔化需要完整的原片/尺寸/人脸绑定".into())),
        }
        Ok(())
    }
    pub fn check_source(&self, path: &Path, width: u32, height: u32) -> Result<()> {
        self.validate()?;
        if self.smooth == 0. {
            return Ok(());
        }
        let binding = self.binding.unwrap();
        let face = self.face.unwrap();
        if !binding.matches_source(path)? || (binding.width, binding.height) != (width, height) {
            return Err(Error::Policy(
                "原片或尺寸已变化，请重新检测面部；未使用旧人像位置".into(),
            ));
        }
        if face.rect[2] * (width as f32) < 32. || face.rect[3] * (height as f32) < 32. {
            return Err(Error::Policy("面部像素不足，未应用柔化".into()));
        }
        Ok(())
    }
}

fn smoothstep(low: f32, high: f32, value: f32) -> f32 {
    let x = ((value - low) / (high - low)).clamp(0., 1.);
    x * x * (3. - 2. * x)
}
fn hole(x: f32, y: f32, cx: f32, cy: f32, rx: f32, ry: f32) -> f32 {
    let distance = ((x - cx) / rx.max(1.)).powi(2) + ((y - cy) / ry.max(1.)).powi(2);
    smoothstep(1., 2., distance)
}
/// Geometry mask, not semantic skin/hair segmentation. Central features and
/// the feathered face boundary are protected even when the filter finds detail.
pub fn mask(face: &Face, x: f32, y: f32, width: u32, height: u32) -> f32 {
    let [left, top, w, h] = face.rect;
    let (w, h) = (w * width as f32, h * height as f32);
    let cx = (left + face.rect[2] * 0.5) * width as f32;
    let cy = top * height as f32 + h * 0.55;
    let ellipse = ((x - cx) / (w * 0.46).max(1.)).powi(2) + ((y - cy) / (h * 0.45).max(1.)).powi(2);
    let mut weight = smoothstep(0., 0.35, 1. - ellipse);
    if weight == 0. {
        return 0.;
    }
    let points = face
        .landmarks
        .map(|p| [p.x * width as f32, p.y * height as f32]);
    let eye_distance = ((points[0][0] - points[1][0]).powi(2)
        + (points[0][1] - points[1][1]).powi(2))
    .sqrt()
    .max(1.);
    for eye in &points[..2] {
        weight *= hole(
            x,
            y,
            eye[0],
            eye[1] - eye_distance * 0.05,
            eye_distance * 0.24,
            eye_distance * 0.18,
        );
    }
    let mouth_width =
        ((points[3][0] - points[4][0]).powi(2) + (points[3][1] - points[4][1]).powi(2)).sqrt();
    weight *= hole(
        x,
        y,
        (points[3][0] + points[4][0]) * 0.5,
        (points[3][1] + points[4][1]) * 0.5,
        mouth_width * 0.75,
        eye_distance * 0.18,
    );
    weight
}

/// Separable sliding box mean. Uses f64 sums to keep preview/full-size dark
/// gradients stable, with edge replication and bounded O(number of pixels).
fn box_mean(input: &[f32], width: usize, height: usize, radius: usize) -> Vec<f32> {
    let mut horizontal = vec![0.; input.len()];
    let mut output = vec![0.; input.len()];
    let divisor = (radius * 2 + 1) as f64;
    for y in 0..height {
        let row = &input[y * width..(y + 1) * width];
        let mut sum = 0.;
        for offset in -(radius as isize)..=radius as isize {
            sum += row[offset.clamp(0, width as isize - 1) as usize] as f64;
        }
        for x in 0..width {
            horizontal[y * width + x] = (sum / divisor) as f32;
            sum -= row[(x as isize - radius as isize).clamp(0, width as isize - 1) as usize] as f64;
            sum += row[(x + radius + 1).min(width - 1)] as f64;
        }
    }
    for x in 0..width {
        let mut sum = 0.;
        for offset in -(radius as isize)..=radius as isize {
            sum += horizontal[offset.clamp(0, height as isize - 1) as usize * width + x] as f64;
        }
        for y in 0..height {
            output[y * width + x] = (sum / divisor) as f32;
            sum -= horizontal
                [(y as isize - radius as isize).clamp(0, height as isize - 1) as usize * width + x]
                as f64;
            sum += horizontal[(y + radius + 1).min(height - 1) * width + x] as f64;
        }
    }
    output
}

pub fn apply(frame: &mut SrgbFrame, portrait: &Portrait) -> Result<()> {
    apply_with_skin(frame, portrait, None)
}

/// An explicit empty mask is a no-op, never a fallback to the face ellipse.
/// Callers must check the source through rendering::render_with_skin first.
pub fn apply_with_skin(
    frame: &mut SrgbFrame,
    portrait: &Portrait,
    skin: Option<&crate::skin::SkinMask>,
) -> Result<()> {
    portrait.validate()?;
    if portrait.smooth == 0. {
        return Ok(());
    }
    if let Some(skin) = skin {
        skin.check_portrait(portrait)?;
    }
    let count = frame.width as u64 * frame.height as u64;
    if count == 0 || count > 80_000_000 || count as usize != frame.pixels.len() {
        return Err(Error::Policy("人像输出缓冲区无效".into()));
    }
    let face = portrait.face.unwrap();
    let face_width = face.rect[2] * frame.width as f32;
    let small_radius = (face_width * 0.003).round().clamp(1., 12.) as usize;
    let large_radius = (face_width * 0.018).round().clamp(2., 32.) as usize;
    let padding = small_radius + large_radius + 2;
    let x0 = ((face.rect[0] * frame.width as f32).floor() as usize).saturating_sub(padding);
    let y0 = ((face.rect[1] * frame.height as f32).floor() as usize).saturating_sub(padding);
    let x1 = (((face.rect[0] + face.rect[2]) * frame.width as f32).ceil() as usize + padding)
        .min(frame.width as usize);
    let y1 = (((face.rect[1] + face.rect[3]) * frame.height as f32).ceil() as usize + padding)
        .min(frame.height as usize);
    let (width, height) = (x1 - x0, y1 - y0);
    let mut luminance = Vec::with_capacity(width * height);
    for y in y0..y1 {
        for x in x0..x1 {
            let p = frame.pixels[y * frame.width as usize + x];
            if p.iter()
                .any(|v| !v.is_finite() || !(0.0..=1.000001).contains(v))
            {
                return Err(Error::Policy("人像输入像素无效".into()));
            }
            luminance.push(p[0] * 0.2126 + p[1] * 0.7152 + p[2] * 0.0722);
        }
    }
    let small = box_mean(&luminance, width, height, small_radius);
    let large = box_mean(&small, width, height, large_radius);
    let amount = portrait.smooth / 100. * 0.65;
    for y in 0..height {
        for x in 0..width {
            let i = y * width + x;
            let local = luminance[i];
            if local <= 0.01 {
                continue;
            }
            let mut weight = mask(
                &face,
                (x0 + x) as f32 + 0.5,
                (y0 + y) as f32 + 0.5,
                frame.width,
                frame.height,
            );
            if let Some(skin) = skin {
                weight *= skin.sample(
                    (x0 + x) as f64 + 0.5,
                    (y0 + y) as f64 + 0.5,
                    frame.width,
                    frame.height,
                );
            }
            if weight == 0. {
                continue;
            }
            let detail = ((local - small[i]).abs() / 0.12).clamp(0., 1.);
            let delta = (large[i] - small[i]).clamp(-0.035, 0.035)
                * amount
                * weight
                * (1. - detail * detail);
            let pixel = &mut frame.pixels[(y0 + y) * frame.width as usize + x0 + x];
            let maximum = pixel.iter().copied().fold(0f32, f32::max);
            let ratio =
                (1. + delta / local).clamp(0., if maximum > 0. { 1. / maximum } else { 1. });
            for channel in pixel {
                *channel = (*channel * ratio).clamp(0., 1.);
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn face() -> Face {
        Face {
            rect: [0.2, 0.1, 0.6, 0.8],
            confidence: 1.,
            landmarks: [
                Point { x: 0.36, y: 0.36 },
                Point { x: 0.64, y: 0.36 },
                Point { x: 0.5, y: 0.5 },
                Point { x: 0.42, y: 0.68 },
                Point { x: 0.58, y: 0.68 },
            ],
        }
    }
    #[test]
    fn source_binding_and_strength_fail_closed_but_zero_is_exact() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source.png");
        std::fs::write(&source, b"original").unwrap();
        let mut portrait = Portrait {
            face: Some(face()),
            binding: Some(Binding::capture(&source, 100, 100).unwrap()),
            smooth: 50.,
        };
        assert!(portrait.check_source(&source, 100, 100).is_ok());
        std::fs::write(&source, b"changed source").unwrap();
        assert!(portrait.check_source(&source, 100, 100).is_err());
        portrait.smooth = 0.;
        let mut frame = SrgbFrame {
            width: 1,
            height: 1,
            pixels: vec![[0.3, 0.4, 0.5]],
        };
        let before = frame.pixels.clone();
        apply(&mut frame, &portrait).unwrap();
        assert_eq!(frame.pixels, before);
        portrait.smooth = f32::NAN;
        assert!(portrait.validate().is_err());
        assert!(
            Portrait {
                smooth: 20.,
                ..Default::default()
            }
            .validate()
            .is_err()
        );
    }
    #[test]
    fn eye_mouth_and_outside_masks_are_zero_and_filter_preserves_rgb_ratios() {
        let face = face();
        assert_eq!(mask(&face, 5., 5., 100, 100), 0.);
        assert_eq!(mask(&face, 36., 36., 100, 100), 0.);
        assert_eq!(mask(&face, 50., 68., 100, 100), 0.);
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source");
        std::fs::write(&source, b"x").unwrap();
        let portrait = Portrait {
            face: Some(face),
            binding: Some(Binding::capture(&source, 100, 100).unwrap()),
            smooth: 100.,
        };
        let mut pixels = Vec::new();
        for y in 0..100 {
            for x in 0..100 {
                let v = 0.4
                    + 0.1 * (x as f32 * 0.3).sin()
                    + if (x + y) % 2 == 0 { 0.01 } else { -0.01 };
                pixels.push([v, v * 0.8, v * 0.6]);
            }
        }
        let before = pixels.clone();
        let mut frame = SrgbFrame {
            width: 100,
            height: 100,
            pixels,
        };
        apply(&mut frame, &portrait).unwrap();
        let mut changed = 0;
        for y in 0..100 {
            for x in 0..100 {
                let i = y * 100 + x;
                let a = before[i];
                let b = frame.pixels[i];
                if mask(&face, x as f32 + 0.5, y as f32 + 0.5, 100, 100) == 0. {
                    assert_eq!(a, b);
                }
                changed += usize::from(a != b);
                assert!(b.iter().all(|v| v.is_finite() && (0.0..=1.0).contains(v)));
                assert!((b[1] / b[0] - 0.8).abs() < 1e-6 && (b[2] / b[0] - 0.6).abs() < 1e-6);
            }
        }
        assert!(changed > 100);
    }
    #[test]
    fn normalized_face_mask_aligns_at_quarter_half_and_full_resolution() {
        let face = face();
        for row in 0..64 {
            for column in 0..64 {
                let x = (column as f32 + 0.5) / 64.;
                let y = (row as f32 + 0.5) / 64.;
                let reference = mask(&face, x * 400., y * 225., 400, 225);
                for (width, height) in [(800, 450), (1600, 900)] {
                    assert_eq!(
                        mask(&face, x * width as f32, y * height as f32, width, height),
                        reference,
                        "normalized location ({x}, {y}) at {width}x{height}"
                    );
                }
            }
        }
    }
    #[test]
    fn semantic_gate_limits_changes_and_rejects_another_source_without_mutation() {
        use crate::skin::{SIDE, SkinMask};
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("source");
        std::fs::write(&path, b"source").unwrap();
        let binding = Binding::capture(&path, 256, 256).unwrap();
        let portrait = Portrait {
            face: Some(face()),
            binding: Some(binding),
            smooth: 100.,
        };
        let before: Vec<[f32; 3]> = (0..256 * 256)
            .map(|i| {
                let v = 0.4 + 0.1 * ((i % 256) as f32 * 0.2).sin();
                [v, v * 0.8, v * 0.6]
            })
            .collect();
        let fresh = || SrgbFrame {
            width: 256,
            height: 256,
            pixels: before.clone(),
        };
        let mut geometry = fresh();
        apply(&mut geometry, &portrait).unwrap();
        let full = SkinMask::new(binding, vec![255; SIDE * SIDE]).unwrap();
        let empty = SkinMask::new(binding, vec![0; SIDE * SIDE]).unwrap();
        let mut same = fresh();
        apply_with_skin(&mut same, &portrait, Some(&full)).unwrap();
        assert_eq!(same.pixels, geometry.pixels);
        let mut untouched = fresh();
        apply_with_skin(&mut untouched, &portrait, Some(&empty)).unwrap();
        assert_eq!(untouched.pixels, before);
        let left = SkinMask::new(
            binding,
            (0..SIDE * SIDE)
                .map(|i| if i % SIDE < 120 { 255 } else { 0 })
                .collect(),
        )
        .unwrap();
        let mut limited = fresh();
        apply_with_skin(&mut limited, &portrait, Some(&left)).unwrap();
        let mut changed = 0;
        for (i, p) in limited.pixels.iter().enumerate() {
            if i % 256 >= 120 {
                assert_eq!(*p, before[i]);
            }
            assert!(p.iter().all(|v| v.is_finite() && (0.0..=1.0).contains(v)));
            assert!((p[1] / p[0] - 0.8).abs() < 1e-6 && (p[2] / p[0] - 0.6).abs() < 1e-6);
            changed += usize::from(*p != before[i]);
        }
        assert!(changed > 100);
        let other = root.path().join("other");
        std::fs::write(&other, b"source").unwrap();
        let wrong = SkinMask::new(
            Binding::capture(&other, 256, 256).unwrap(),
            vec![255; SIDE * SIDE],
        )
        .unwrap();
        let mut rejected = fresh();
        assert!(apply_with_skin(&mut rejected, &portrait, Some(&wrong)).is_err());
        assert_eq!(rejected.pixels, before);
        let frame = crate::frame::Frame::new(256, 256, before).unwrap();
        let recipe = crate::recipe::Recipe {
            portrait,
            ..Default::default()
        };
        std::fs::write(&path, b"changed source").unwrap();
        assert!(
            crate::rendering::render_with_skin(&frame, &recipe, &path, (256, 256), Some(&full))
                .is_err()
        );
    }
    #[test]
    fn sliding_mean_matches_independent_brute_force_on_thin_and_rectangular_inputs() {
        for (width, height, radius) in [(1, 7, 3), (6, 1, 2), (5, 8, 2)] {
            let values: Vec<_> = (0..width * height)
                .map(|i| (i as f32 * 0.37).sin())
                .collect();
            let filtered = box_mean(&values, width, height, radius);
            for y in 0..height {
                for x in 0..width {
                    let mut sum = 0f64;
                    for dy in -(radius as isize)..=radius as isize {
                        for dx in -(radius as isize)..=radius as isize {
                            let xx = (x as isize + dx).clamp(0, width as isize - 1) as usize;
                            let yy = (y as isize + dy).clamp(0, height as isize - 1) as usize;
                            sum += values[yy * width + xx] as f64;
                        }
                    }
                    assert!(
                        (filtered[y * width + x] as f64 - sum / ((radius * 2 + 1).pow(2) as f64))
                            .abs()
                            < 1e-6
                    );
                }
            }
        }
    }
}
