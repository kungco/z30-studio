//! Float linear-RGB clone / mean-matched heal, ported from spatial.py.
//! Core only: no automatic blemish detection or desktop/persistence wiring yet.
use crate::{Error, Result, frame::Frame};
use serde::{Deserialize, Serialize};

pub const MAX_REPAIRS: usize = 256;
pub const MAX_PATCH_PIXELS: u64 = 128_000_000;
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Heal,
    Clone,
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Repair {
    pub mode: Mode,
    #[serde(default = "half")]
    pub x: f64,
    #[serde(default = "half")]
    pub y: f64,
    #[serde(default = "default_donor_x")]
    pub sx: f64,
    #[serde(default = "half")]
    pub sy: f64,
    #[serde(default = "default_radius")]
    pub radius: f64,
    #[serde(default = "one")]
    pub opacity: f64,
    #[serde(default = "half")]
    pub feather: f64,
}
fn half() -> f64 {
    0.5
}
fn one() -> f64 {
    1.
}
fn default_donor_x() -> f64 {
    0.6
}
fn default_radius() -> f64 {
    0.015
}
impl Default for Repair {
    fn default() -> Self {
        Self {
            mode: Mode::Heal,
            x: 0.5,
            y: 0.5,
            sx: 0.6,
            sy: 0.5,
            radius: 0.015,
            opacity: 1.,
            feather: 0.5,
        }
    }
}
impl Repair {
    pub fn validate(&self) -> Result<()> {
        for (v, lo, hi) in [
            (self.x, 0., 1.),
            (self.y, 0., 1.),
            (self.sx, 0., 1.),
            (self.sy, 0., 1.),
            (self.radius, 0.002, 0.2),
            (self.opacity, 0., 1.),
            (self.feather, 0.01, 1.),
        ] {
            if !v.is_finite() || !(lo..=hi).contains(&v) {
                return Err(Error::Policy("修复坐标或强度无效".into()));
            }
        }
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct Donor {
    pub x: f64,
    pub y: f64,
}
fn check_frame(frame: &Frame) -> Result<()> {
    if frame.width == 0
        || frame.height == 0
        || frame.width as u64 * frame.height as u64 > 80_000_000
        || frame.pixels.len() as u64 != frame.width as u64 * frame.height as u64
        || frame.pixels.iter().flatten().any(|v| !v.is_finite())
    {
        return Err(Error::Policy("修复输入缓冲区无效".into()));
    }
    Ok(())
}
// Match the legacy float32 bilinear sample, including zero outside the source.
fn sample(frame: &Frame, x: f32, y: f32) -> Option<[f32; 3]> {
    if x < 0. || y < 0. || x > (frame.width - 1) as f32 || y > (frame.height - 1) as f32 {
        return None;
    }
    let (x0, y0) = (x as usize, y as usize);
    let (x1, y1) = (
        (x0 + 1).min(frame.width as usize - 1),
        (y0 + 1).min(frame.height as usize - 1),
    );
    let (fx, fy) = (x - x0 as f32, y - y0 as f32);
    Some(std::array::from_fn(|c| {
        let a = frame.pixels[y0 * frame.width as usize + x0][c] * (1. - fx)
            + frame.pixels[y0 * frame.width as usize + x1][c] * fx;
        let b = frame.pixels[y1 * frame.width as usize + x0][c] * (1. - fx)
            + frame.pixels[y1 * frame.width as usize + x1][c] * fx;
        a * (1. - fy) + b * fy
    }))
}
/// Select once on the preview and store normalized coordinates for full-size
/// replay. Six ring-matched candidates, deterministic score/x/y tie-break.
/// No semantic judgement that the selected source is unblemished is implied.
pub fn choose_donor(frame: &Frame, x: f64, y: f64, radius: f64) -> Result<Donor> {
    Repair {
        x,
        y,
        radius,
        ..Default::default()
    }
    .validate()?;
    check_frame(frame)?;
    let r = (radius * frame.width.min(frame.height) as f64).max(1.);
    let (cx, cy) = (x * (frame.width - 1) as f64, y * (frame.height - 1) as f64);
    let patch = |px: f64, py: f64| -> Vec<[f32; 3]> {
        (0..256)
            .map(|i| {
                sample(
                    frame,
                    (px - r + 2. * r * (i % 16) as f64 / 15.) as f32,
                    (py - r + 2. * r * (i / 16) as f64 / 15.) as f32,
                )
                .unwrap_or([0.; 3])
            })
            .collect()
    };
    let reference = patch(cx, cy);
    let ring: Vec<usize> = (0..256)
        .filter(|i| {
            let xx = (*i % 16) as f64 * 2. / 15. - 1.;
            let yy = (*i / 16) as f64 * 2. / 15. - 1.;
            xx.hypot(yy) > 0.8
        })
        .collect();
    let mut candidates = Vec::new();
    for (dx, dy) in [
        (3., 0.),
        (-3., 0.),
        (0., 3.),
        (0., -3.),
        (2.2, 2.2),
        (-2.2, -2.2),
    ] {
        let (sx, sy) = (cx + dx * r, cy + dy * r);
        if sx < r || sy < r || sx >= frame.width as f64 - r || sy >= frame.height as f64 - r {
            continue;
        }
        let donor = patch(sx, sy);
        let mut sum = 0f64;
        for i in &ring {
            for c in 0..3 {
                let difference = donor[*i][c] - reference[*i][c];
                sum += (difference * difference) as f64;
            }
        }
        let score = sum / (ring.len() * 3) as f64;
        if !score.is_finite() {
            return Err(Error::Policy("取样颜色超出可处理范围".into()));
        }
        candidates.push((
            score,
            sx / (frame.width - 1).max(1) as f64,
            sy / (frame.height - 1).max(1) as f64,
        ));
    }
    candidates.sort_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then(a.1.total_cmp(&b.1))
            .then(a.2.total_cmp(&b.2))
    });
    candidates
        .first()
        .map(|c| Donor { x: c.1, y: c.2 })
        .ok_or_else(|| Error::Policy("没有足够的邻近取样区域，请缩小修复点或手动取样".into()))
}
struct Patch {
    left: usize,
    right: usize,
    top: usize,
    bottom: usize,
    cx: f32,
    cy: f32,
    radius: f32,
    sx: f32,
    sy: f32,
}
impl Patch {
    fn new(width: u32, height: u32, edit: &Repair) -> Self {
        let (cx, cy) = (edit.x * (width - 1) as f64, edit.y * (height - 1) as f64);
        let radius = (edit.radius * width.min(height) as f64).max(1.);
        Self {
            left: (cx - radius).max(0.) as usize,
            right: ((cx + radius) as usize + 2).min(width as usize),
            top: (cy - radius).max(0.) as usize,
            bottom: ((cy + radius) as usize + 2).min(height as usize),
            cx: cx as f32,
            cy: cy as f32,
            radius: radius as f32,
            sx: (edit.sx * (width - 1) as f64) as f32,
            sy: (edit.sy * (height - 1) as f64) as f32,
        }
    }
    fn at(&self, source: &Frame, x: usize, y: usize) -> (f32, Option<[f32; 3]>) {
        let (dx, dy) = (x as f32 - self.cx, y as f32 - self.cy);
        (
            ((dx * dx + dy * dy).sqrt() / self.radius),
            sample(source, self.sx + dx, self.sy + dy),
        )
    }
}
pub(crate) fn check_work(width: u32, height: u32, edits: &[Repair]) -> Result<()> {
    let mut work = 0u64;
    for edit in edits.iter().filter(|e| e.opacity > 0.) {
        let patch = Patch::new(width, height, edit);
        work += (patch.right - patch.left) as u64 * (patch.bottom - patch.top) as u64;
    }
    if work > MAX_PATCH_PIXELS {
        Err(Error::Policy("本次修复区域过大，请分组应用".into()))
    } else {
        Ok(())
    }
}
/// One owned output only, donors always read from the immutable original, and
/// healing ring means read from the sequential output (legacy semantics).
/// None means exact passthrough with no image allocation.
pub fn render(source: &Frame, edits: &[Repair]) -> Result<Option<Frame>> {
    render_with_cancel(source, edits, || false)
}
pub fn render_with_cancel(
    source: &Frame,
    edits: &[Repair],
    mut cancelled: impl FnMut() -> bool,
) -> Result<Option<Frame>> {
    if edits.len() > MAX_REPAIRS {
        return Err(Error::Policy("最多支持 256 个修复点".into()));
    }
    for edit in edits {
        edit.validate()?;
    }
    if edits.iter().all(|e| e.opacity == 0.) {
        return Ok(None);
    }
    check_frame(source)?;
    check_work(source.width, source.height, edits)?;
    let mut checkpoint = || -> Result<()> {
        if cancelled() {
            Err(Error::Policy("修复已取消".into()))
        } else {
            Ok(())
        }
    };
    checkpoint()?;
    let mut output = source.pixels.clone();
    for edit in edits.iter().filter(|e| e.opacity > 0.) {
        checkpoint()?;
        let patch = Patch::new(source.width, source.height, edit);
        let mut difference = [0f64; 3];
        let mut count = 0u64;
        if edit.mode == Mode::Heal {
            for y in patch.top..patch.bottom {
                if y % 32 == 0 {
                    checkpoint()?;
                }
                for x in patch.left..patch.right {
                    let (distance, donor) = patch.at(source, x, y);
                    if distance > 0.7 && distance < 1.1 {
                        if let Some(donor) = donor {
                            count += 1;
                            for c in 0..3 {
                                difference[c] += output[y * source.width as usize + x][c] as f64
                                    - donor[c] as f64;
                            }
                        }
                    }
                }
            }
            if count > 0 {
                for c in &mut difference {
                    *c /= count as f64;
                }
            }
        }
        for y in patch.top..patch.bottom {
            if y % 32 == 0 {
                checkpoint()?;
            }
            for x in patch.left..patch.right {
                let (distance, donor) = patch.at(source, x, y);
                let t = ((1. - distance) / edit.feather as f32).clamp(0., 1.);
                let weight = t * t * (3. - 2. * t) * edit.opacity as f32;
                if weight == 0. {
                    continue;
                }
                let Some(donor) = donor else {
                    continue;
                };
                let pixel = &mut output[y * source.width as usize + x];
                for c in 0..3 {
                    let replacement = if edit.mode == Mode::Heal && count > 0 {
                        (donor[c] as f64 + difference[c]).max(0.) as f32
                    } else {
                        donor[c]
                    };
                    pixel[c] = pixel[c] * (1. - weight) + replacement * weight;
                    if !pixel[c].is_finite() {
                        return Err(Error::Policy("修复结果超出浮点范围，未应用".into()));
                    }
                }
            }
        }
    }
    Ok(Some(Frame {
        width: source.width,
        height: source.height,
        pixels: output,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn frame() -> Frame {
        Frame::new(
            160,
            100,
            (0..16000)
                .map(|i| {
                    let v = 0.15 + 0.15 * (i % 160) as f32 / 159.;
                    if (47..53).contains(&(i / 160)) && (77..83).contains(&(i % 160)) {
                        [0.; 3]
                    } else {
                        [v, v * 1.2, v * 1.4]
                    }
                })
                .collect(),
        )
        .unwrap()
    }
    #[test]
    fn heals_float_spot_without_mutating_source_or_losing_hdr() {
        let mut source = frame();
        let original = source.pixels.clone();
        let donor = choose_donor(&source, 0.5, 0.5, 0.06).unwrap();
        let edit = Repair {
            sx: donor.x,
            sy: donor.y,
            radius: 0.06,
            ..Default::default()
        };
        let result = render(&source, &[edit]).unwrap().unwrap();
        assert!(result.pixels[50 * 160 + 80][0] > 0.1);
        assert_eq!(source.pixels, original);
        source
            .pixels
            .iter_mut()
            .for_each(|p| p.iter_mut().for_each(|v| *v *= 8.));
        let result = render(&source, &[edit]).unwrap().unwrap();
        assert!(result.pixels.iter().flatten().any(|v| *v > 1.));
    }
    #[test]
    fn zero_strength_is_passthrough_and_untouched_pixels_are_exact() {
        let source = frame();
        assert!(render(&source, &[]).unwrap().is_none());
        assert!(
            render(
                &source,
                &[Repair {
                    opacity: 0.,
                    ..Default::default()
                }]
            )
            .unwrap()
            .is_none()
        );
        let out = render(
            &source,
            &[Repair {
                mode: Mode::Clone,
                radius: 0.06,
                ..Default::default()
            }],
        )
        .unwrap()
        .unwrap();
        assert_eq!(&source.pixels[..40 * 160], &out.pixels[..40 * 160]);
    }
    #[test]
    fn invalid_input_and_cancel_are_rejected_without_changing_original() {
        let source = frame();
        let original = source.pixels.clone();
        assert!(
            render(
                &source,
                &[Repair {
                    x: f64::NAN,
                    ..Default::default()
                }]
            )
            .is_err()
        );
        assert!(render(&source, &vec![Repair::default(); 257]).is_err());
        assert!(render_with_cancel(&source, &[Repair::default()], || true).is_err());
        let mut checks = 0;
        assert!(
            render_with_cancel(
                &source,
                &[Repair {
                    radius: 0.2,
                    ..Default::default()
                }],
                || {
                    checks += 1;
                    checks >= 3
                }
            )
            .is_err()
        );
        assert_eq!(source.pixels, original);
        let invalid = Frame {
            width: 0,
            height: 1,
            pixels: vec![],
        };
        assert!(render(&invalid, &[Repair::default()]).is_err());
        let invalid = Frame {
            width: 2,
            height: 1,
            pixels: vec![[f32::NAN; 3]; 2],
        };
        assert!(render(&invalid, &[Repair::default()]).is_err());
    }
    #[test]
    fn flat_donor_ties_are_deterministic_and_tiny_frames_fail_safely() {
        let frame = Frame::new(160, 100, vec![[0.3; 3]; 16000]).unwrap();
        let donor = choose_donor(&frame, 0.5, 0.5, 0.06).unwrap();
        assert_eq!(donor.x, (79.5 - 18.) / 159.);
        assert_eq!(donor.y, 0.5);
        assert_eq!(donor, choose_donor(&frame, 0.5, 0.5, 0.06).unwrap());
        let tiny = Frame::new(1, 1, vec![[0.1; 3]]).unwrap();
        assert!(choose_donor(&tiny, 0.5, 0.5, 0.06).is_err());
    }
    #[test]
    fn geometric_work_budget_rejects_oversized_groups_before_image_allocation() {
        assert!(
            check_work(
                5472,
                3648,
                &vec![
                    Repair {
                        radius: 0.015,
                        ..Default::default()
                    };
                    256
                ]
            )
            .is_ok()
        );
        assert!(
            check_work(
                5472,
                3648,
                &vec![
                    Repair {
                        radius: 0.2,
                        ..Default::default()
                    };
                    64
                ]
            )
            .is_err()
        );
        assert!(
            check_work(
                5472,
                3648,
                &vec![
                    Repair {
                        radius: 0.2,
                        opacity: 0.,
                        ..Default::default()
                    };
                    256
                ]
            )
            .is_ok()
        );
    }
    #[test]
    fn wire_format_requires_explicit_mode_and_rejects_unknown_fields() {
        assert!(serde_json::from_str::<Repair>("{}").is_err());
        assert!(serde_json::from_str::<Repair>(r#"{"mode":"command"}"#).is_err());
        assert!(serde_json::from_str::<Repair>(r#"{"mode":"heal","unexpected":1}"#).is_err());
        assert_eq!(
            serde_json::from_str::<Repair>(r#"{"mode":"heal"}"#).unwrap(),
            Repair::default()
        );
    }
}
