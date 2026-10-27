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
                        Error::Policy("缺少 RAW 解码组件，请完整解压软件包并保留 raw 文件夹；原片未修改".into())
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
        let rendered = crate::rendering::render(&cache.linear, &recipe, path, (cache.source_width,cache.source_height))?;
        if Stamp::read(path, wb)? != cache.stamp {
            return Err(Error::Policy("预览期间原片发生变化，请重新读取".into()));
        }
        Ok(EditPreview {
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
