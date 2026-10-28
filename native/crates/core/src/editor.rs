//! Bounded per-worker edit preview cache. Does not write photos or spawn a server.
use crate::{
    Error, MAX_SOURCE_BYTES, Result,
    develop::{self, Parameters, SrgbFrame},
    frame::Frame,
    raster,
    recipe::{Recipe, WhiteBalance},
};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::{Instant, SystemTime},
};

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct Stamp {
    path: PathBuf,
    bytes: u64,
    modified: SystemTime,
    white_balance: WhiteBalance,
}
impl Stamp {
    pub(crate) fn read(path: &Path, white_balance: WhiteBalance) -> Result<Self> {
        let path = path.canonicalize()?;
        let info = path.metadata()?;
        if !info.is_file() || info.len() == 0 || info.len() > MAX_SOURCE_BYTES {
            return Err(Error::Policy("原片为空或超过读取限制".into()));
        }
        Ok(Self {
            path,
            bytes: info.len(),
            modified: info.modified()?,
            white_balance,
        })
    }
}
struct Cached {
    stamp: Stamp,
    linear: Frame,
    neutral: Arc<SrgbFrame>,
    source_width: u32,
    source_height: u32,
    policy: String,
    warnings: Vec<String>,
    source_bits: u32,
}
pub struct EditPreview {
    pub binding: crate::portrait::Binding,
    pub rendered: SrgbFrame,
    pub neutral: Arc<SrgbFrame>,
    pub source_width: u32,
    pub source_height: u32,
    pub policy: String,
    pub warnings: Vec<String>,
    pub source_bits: u32,
    pub reused_decode: bool,
    pub decode_resize_seconds: f64,
    pub render_seconds: f64,
}
pub struct PreviewEngine {
    raw_dll: Option<PathBuf>,
    cached: Option<Cached>,
}
impl PreviewEngine {
    pub fn new(raw_dll: Option<PathBuf>) -> Result<Self> {
        if raw_dll.as_ref().is_some_and(|p| !p.is_absolute()) {
            return Err(Error::Policy("RAW 动态库必须为显式绝对路径".into()));
        }
        Ok(Self {
            raw_dll,
            cached: None,
        })
    }
    pub fn clear(&mut self) {
        self.cached = None;
    }
    pub fn preview(&mut self, path: &Path, recipe: Recipe) -> Result<EditPreview> {
        self.preview_with_cancel(path, recipe, || false)
    }
    pub fn preview_with_cancel(
        &mut self,
        path: &Path,
        recipe: Recipe,
        mut cancelled: impl FnMut() -> bool,
    ) -> Result<EditPreview> {
        if cancelled() {
            return Err(Error::Policy("预览已取消".into()));
        }
        recipe.validate()?;
        let raw = path
            .extension()
            .is_some_and(|v| v.eq_ignore_ascii_case("nef"));
        let wb = if raw {
            recipe.white_balance
        } else {
            WhiteBalance::Camera
        };
        let stamp = Stamp::read(path, wb)?;
        let reused = self
            .cached
            .as_ref()
            .is_some_and(|cache| cache.stamp == stamp);
        let started = Instant::now();
        if !reused {
            let (frame, policy, warnings, bits) = if raw {
                #[cfg(windows)]
                {
                    let dll = self.raw_dll.as_ref().ok_or_else(|| {
                        Error::Policy(
                            "缺少 RAW 解码组件，请完整解压软件包并保留 raw 文件夹；原片未修改"
                                .into(),
                        )
                    })?;
                    let decoded = crate::raw::LibRaw::load(dll)?.decode(&stamp.path, false, wb)?;
                    (
                        decoded.frame,
                        "RAW → 浮点线性 sRGB".to_owned(),
                        Vec::new(),
                        decoded.metadata.source_bits,
                    )
                }
                #[cfg(not(windows))]
                {
                    return Err(Error::Policy("此 RAW 桥接仅支持 Windows".into()));
                }
            } else {
                let decoded = raster::decode(&stamp.path)?;
                (
                    decoded.frame,
                    decoded.policy,
                    decoded.warnings,
                    decoded.source_bits as u32,
                )
            };
            let (source_width, source_height) = (frame.width, frame.height);
            let linear = frame.thumbnail(1600, 1600)?;
            drop(frame);
            let neutral = Arc::new(develop::render(&linear, &Parameters::default())?);
            if Stamp::read(path, wb)? != stamp {
                return Err(Error::Policy("读取期间原片发生变化，请重新读取".into()));
            }
            self.cached = Some(Cached {
                stamp,
                linear,
                neutral,
                source_width,
                source_height,
                policy,
                warnings,
                source_bits: bits,
            });
        }
        let decode_resize_seconds = started.elapsed().as_secs_f64();
        let cache = self.cached.as_ref().unwrap();
        let started = Instant::now();
        let rendered = crate::rendering::render_with_cancel(
            &cache.linear,
            &recipe,
            path,
            (cache.source_width, cache.source_height),
            cancelled,
        )?;
        let binding =
            crate::portrait::Binding::capture(path, cache.source_width, cache.source_height)?;
        if Stamp::read(path, wb)? != cache.stamp {
            return Err(Error::Policy("预览期间原片发生变化，请重新读取".into()));
        }
        Ok(EditPreview {
            binding,
            rendered,
            neutral: Arc::clone(&cache.neutral),
            source_width: cache.source_width,
            source_height: cache.source_height,
            policy: cache.policy.clone(),
            warnings: cache.warnings.clone(),
            source_bits: cache.source_bits,
            reused_decode: reused,
            decode_resize_seconds,
            render_seconds: started.elapsed().as_secs_f64(),
        })
    }
    /// Use the already displayed original linear thumbnail. Never decode or
    /// copy a full-resolution image in response to a pointer event.
    pub fn plan_repair(
        &self,
        path: &Path,
        recipe: &Recipe,
        binding: crate::portrait::Binding,
        size: (u32, u32),
        action: crate::repair_plan::Action,
    ) -> Result<crate::repair_plan::Planned> {
        let cache = self
            .cached
            .as_ref()
            .ok_or_else(|| Error::Policy("请先完成照片预览".into()))?;
        let raw = path
            .extension()
            .is_some_and(|v| v.eq_ignore_ascii_case("nef"));
        let wb = if raw {
            recipe.white_balance
        } else {
            WhiteBalance::Camera
        };
        if (cache.linear.width, cache.linear.height) != size
            || cache.stamp != Stamp::read(path, wb)?
            || binding
                != crate::portrait::Binding::capture(path, cache.source_width, cache.source_height)?
        {
            return Err(Error::Policy("显示来源已变化，请重新预览后再取样".into()));
        }
        let result = crate::repair_plan::plan(recipe, binding, action, |point, radius| {
            let p = crate::retouch::choose_donor(&cache.linear, point.x, point.y, radius)?;
            Ok(crate::repair_view::Point { x: p.x, y: p.y })
        })?;
        if cache.stamp != Stamp::read(path, wb)? {
            return Err(Error::Policy("取样期间原片已变化，未应用修复".into()));
        }
        Ok(result)
    }

    /// Suggest a bounded set from the verified original linear thumbnail.
    /// No new decode, no image copy, no automatic editing of the recipe.
    pub fn propose_blemishes(
        &self,
        path: &Path,
        recipe: &Recipe,
        binding: crate::portrait::Binding,
        size: (u32, u32),
        mut cancelled: impl FnMut() -> bool,
    ) -> Result<Vec<crate::retouch::Repair>> {
        recipe.validate()?;
        let cache = self
            .cached
            .as_ref()
            .ok_or_else(|| Error::Policy("请先完成照片预览".into()))?;
        let raw = path
            .extension()
            .is_some_and(|v| v.eq_ignore_ascii_case("nef"));
        let wb = if raw {
            recipe.white_balance
        } else {
            WhiteBalance::Camera
        };
        if (cache.linear.width, cache.linear.height) != size
            || cache.stamp != Stamp::read(path, wb)?
            || binding
                != crate::portrait::Binding::capture(path, cache.source_width, cache.source_height)?
        {
            return Err(Error::Policy("显示来源已变化，请重新预览后再分析".into()));
        }
        if recipe.warp.as_ref().is_some_and(|w| w.active()) {
            return Err(Error::Policy("请先将形变强度归零，再检查原位置皮肤".into()));
        }
        let skin = recipe
            .skin
            .as_ref()
            .ok_or_else(|| Error::Policy("请先定位面部并识别皮肤".into()))?;
        skin.check_portrait(&recipe.portrait)?;
        recipe.portrait.check_source_for_effect(
            path,
            cache.source_width,
            cache.source_height,
            true,
        )?;
        let face = recipe.portrait.face.as_ref().unwrap();
        let frame = &cache.linear;
        if face.confidence < 0.8
            || face.rect[2] * (frame.width as f32) < 64.
            || face.rect[3] * (frame.height as f32) < 64.
        {
            return Err(Error::Policy("面部太小或定位不确定，未提出瑕疵建议".into()));
        }
        let eyes = face.landmarks;
        let distance = ((eyes[0].x - eyes[1].x) as f64 * frame.width as f64)
            .hypot((eyes[0].y - eyes[1].y) as f64 * frame.height as f64)
            .max(1.);
        let nose = [
            eyes[2].x as f64 * frame.width as f64,
            eyes[2].y as f64 * frame.height as f64 + distance * 0.02,
        ];
        let eligible = |x: f64, y: f64| {
            x >= 0.
                && y >= 0.
                && x < frame.width as f64
                && y < frame.height as f64
                && skin.sample(x, y, frame.width, frame.height)
                    * crate::portrait::mask(face, x as f32, y as f32, frame.width, frame.height)
                    >= 0.85
                && ((x - nose[0]) / (distance * 0.18)).powi(2)
                    + ((y - nose[1]) / (distance * 0.22)).powi(2)
                    > 1.
        };
        let mut gate = Vec::new();
        gate.try_reserve_exact(frame.pixels.len())
            .map_err(|_| Error::Policy("皮肤候选范围内存不足".into()))?;
        for y in 0..frame.height {
            if y % 32 == 0 && cancelled() {
                return Err(Error::Policy("瑕疵分析已取消".into()));
            }
            for x in 0..frame.width {
                gate.push(if eligible(x as f64, y as f64) { 255 } else { 0 });
            }
        }
        let old = recipe.repairs.as_ref().map_or(&[][..], |r| r.points());
        let candidates = crate::blemish::detect(frame, &gate, old, &mut cancelled)?;
        drop(gate);
        let mut output = Vec::with_capacity(candidates.len());
        let pixel = |x: f64, y: f64| [x * (frame.width - 1) as f64, y * (frame.height - 1) as f64];
        let patch_allowed = |cx: f64, cy: f64, r: f64| {
            if !eligible(cx, cy) {
                return false;
            }
            for y in (cy - r).floor() as i32..=(cy + r).ceil() as i32 {
                for x in (cx - r).floor() as i32..=(cx + r).ceil() as i32 {
                    if (x as f64 - cx).powi(2) + (y as f64 - cy).powi(2) <= r * r
                        && !eligible(x as f64, y as f64)
                    {
                        return false;
                    }
                }
            }
            true
        };
        for p in &candidates {
            if cancelled() {
                return Err(Error::Policy("瑕疵分析已取消".into()));
            }
            let radius = p.radius * frame.width.min(frame.height) as f64;
            let [tx, ty] = pixel(p.x, p.y);
            if !patch_allowed(tx, ty, radius) {
                continue;
            }
            let donor = crate::retouch::choose_donor(frame, p.x, p.y, p.radius)?;
            let [x, y] = pixel(donor.x, donor.y);
            if !patch_allowed(x, y, radius) {
                continue;
            }
            if candidates.iter().any(|q| {
                let [qx, qy] = pixel(q.x, q.y);
                (x - qx).hypot(y - qy)
                    < (p.radius + q.radius) * frame.width.min(frame.height) as f64 * 1.2
            }) {
                continue;
            }
            if old.iter().any(|q| {
                let [qx, qy] = pixel(q.x, q.y);
                (x - qx).hypot(y - qy)
                    < (p.radius + q.radius) * frame.width.min(frame.height) as f64 * 1.2
            }) {
                continue;
            }
            output.push(crate::retouch::Repair {
                mode: crate::retouch::Mode::Heal,
                x: p.x,
                y: p.y,
                sx: donor.x,
                sy: donor.y,
                radius: p.radius,
                opacity: 1.,
                feather: 0.5,
            });
        }
        if cancelled() || cache.stamp != Stamp::read(path, wb)? {
            return Err(Error::Policy("分析取消或原片已变化，未返回候选".into()));
        }
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repeated_edits_reuse_linear_data_without_mutating_source_or_neutral() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("照片.png");
        image::RgbImage::from_pixel(20, 10, image::Rgb([128, 64, 32]))
            .save(&path)
            .unwrap();
        let before = std::fs::read(&path).unwrap();
        let mut engine = PreviewEngine::new(None).unwrap();
        let first = engine.preview(&path, Recipe::default()).unwrap();
        assert!(!first.reused_decode);
        let neutral = first.neutral.pixels.clone();
        let mut recipe = Recipe::default();
        recipe.parameters.exposure = 1.;
        let edited = engine.preview(&path, recipe.clone()).unwrap();
        assert!(edited.reused_decode);
        assert!(Arc::ptr_eq(&first.neutral, &edited.neutral));
        assert!(edited.rendered.pixels[0][0] > first.rendered.pixels[0][0] + 0.1);
        assert_eq!(edited.neutral.pixels, neutral);
        assert_eq!(std::fs::read(&path).unwrap(), before);
        engine.clear();
        assert!(!engine.preview(&path, recipe.clone()).unwrap().reused_decode);
    }
    #[test]
    fn photo_and_source_changes_invalidate_the_single_frame_cache() {
        let root = tempfile::tempdir().unwrap();
        let mut engine = PreviewEngine::new(None).unwrap();
        let first = root.path().join("one.png");
        let second = root.path().join("two.png");
        for path in [&first, &second] {
            image::RgbImage::from_pixel(13, 7, image::Rgb([60, 120, 200]))
                .save(path)
                .unwrap();
        }
        engine.preview(&first, Recipe::default()).unwrap();
        assert!(
            !engine
                .preview(&second, Recipe::default())
                .unwrap()
                .reused_decode
        );
        assert!(
            !engine
                .preview(&first, Recipe::default())
                .unwrap()
                .reused_decode
        );
        // The file length change is deterministic even on coarse timestamp filesystems.
        use std::io::Write;
        std::fs::OpenOptions::new()
            .append(true)
            .open(&first)
            .unwrap()
            .write_all(b" ")
            .unwrap();
        assert!(
            !engine
                .preview(&first, Recipe::default())
                .unwrap()
                .reused_decode
        );
        let mut invalid = Recipe::default();
        invalid.parameters.exposure = 50.;
        assert!(engine.preview(&first, invalid).is_err());
        assert!(
            engine
                .preview(&first, Recipe::default())
                .unwrap()
                .reused_decode
        );
    }
}
