//! One immutable export snapshot, independent from current UI selection.
use crate::{
    Error, Result,
    editing::EditRequest,
    editor::Stamp,
    export::{self, Format},
    frame::Frame,
    raster,
    recipe::WhiteBalance,
};
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU8, Ordering},
    },
    time::{Duration, Instant},
};

const MAX_CACHED_FRAME_BYTES: usize = 320 * 1024 * 1024;
const CACHE_IDLE: Duration = Duration::from_secs(30);

struct CachedFrame {
    stamp: Stamp,
    runtime: Option<PathBuf>,
    frame: Arc<Frame>,
    last_used: Instant,
}

/// At most one unquantized full-size image. A changed source or white balance
/// evicts before decoding the next image, so a batch never holds two RAW frames.
#[derive(Default)]
pub struct FullFrameCache {
    cached: Option<CachedFrame>,
}
impl FullFrameCache {
    pub fn clear(&mut self) {
        self.cached = None;
    }
    pub fn bytes(&self) -> usize {
        self.cached.as_ref().map_or(0, |cached| {
            cached
                .frame
                .pixels
                .len()
                .saturating_mul(std::mem::size_of::<[f32; 3]>())
        })
    }
    pub fn evict_if_idle(&mut self) {
        if self
            .cached
            .as_ref()
            .is_some_and(|v| v.last_used.elapsed() >= CACHE_IDLE)
        {
            self.clear();
        }
    }
    /// Called under the same compute lock before a preview decodes another
    /// photo, keeping an old full-size cache out of that decode's memory peak.
    pub fn discard_other_source(
        &mut self,
        path: &Path,
        white_balance: WhiteBalance,
        runtime: Option<&Path>,
    ) {
        self.evict_if_idle();
        if let Some(cached) = &self.cached {
            if Stamp::read(path, white_balance).ok().as_ref() != Some(&cached.stamp)
                || cached.runtime.as_deref() != runtime
            {
                self.clear();
            }
        }
    }
    fn get(&mut self, stamp: &Stamp, runtime: Option<&Path>) -> Option<Arc<Frame>> {
        self.evict_if_idle();
        if self
            .cached
            .as_ref()
            .is_some_and(|v| &v.stamp == stamp && v.runtime.as_deref() == runtime)
        {
            let cached = self.cached.as_mut().unwrap();
            cached.last_used = Instant::now();
            Some(Arc::clone(&cached.frame))
        } else {
            self.clear();
            None
        }
    }
    fn insert(&mut self, stamp: Stamp, runtime: Option<&Path>, frame: Arc<Frame>) {
        let bytes = frame
            .pixels
            .len()
            .saturating_mul(std::mem::size_of::<[f32; 3]>());
        if bytes <= MAX_CACHED_FRAME_BYTES {
            self.cached = Some(CachedFrame {
                stamp,
                runtime: runtime.map(Path::to_path_buf),
                frame,
                last_used: Instant::now(),
            });
        }
    }
}

#[derive(Default)]
pub struct ExportControl {
    pub cancelled: AtomicBool,
    stage: AtomicU8,
}
impl ExportControl {
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }
    pub fn stage(&self) -> &'static str {
        match self.stage.load(Ordering::Acquire) {
            1 => "解码原片",
            2 => "全尺寸显影",
            3 => "编码并写入",
            _ => "等待处理",
        }
    }
}
pub struct ExportSource {
    pub edit: EditRequest,
    stamp: Stamp,
}
pub struct ExportReceipt {
    pub destination: PathBuf,
    pub width: u32,
    pub height: u32,
    pub seconds: f64,
    pub decode_seconds: f64,
    pub render_seconds: f64,
    pub encode_seconds: f64,
    pub reused_decode: bool,
}
impl ExportSource {
    pub fn new(edit: EditRequest) -> Result<Self> {
        edit.recipe.validate()?;
        let stamp = Stamp::read(&edit.path, edit.recipe.white_balance)?;
        Ok(Self { edit, stamp })
    }
    pub fn unchanged(&self) -> Result<()> {
        if Stamp::read(&self.edit.path, self.edit.recipe.white_balance)? != self.stamp {
            Err(Error::Policy(
                "原片在选择导出后发生变化，请重新预览并导出".into(),
            ))
        } else {
            Ok(())
        }
    }
    pub fn run(
        &self,
        destination: &Path,
        format: Format,
        raw_dll: Option<&Path>,
        control: &ExportControl,
    ) -> Result<ExportReceipt> {
        self.run_internal(destination, format, raw_dll, control, None, false)
    }
    pub fn run_with_cache(
        &self,
        destination: &Path,
        format: Format,
        raw_dll: Option<&Path>,
        control: &ExportControl,
        cache: &mut FullFrameCache,
    ) -> Result<ExportReceipt> {
        self.run_internal(destination, format, raw_dll, control, Some(cache), true)
    }
    /// Batches can use an already decoded photo, but do not retain a newly
    /// decoded one when the usual next task is a different source.
    pub fn run_reusing_cache(
        &self,
        destination: &Path,
        format: Format,
        raw_dll: Option<&Path>,
        control: &ExportControl,
        cache: &mut FullFrameCache,
    ) -> Result<ExportReceipt> {
        self.run_internal(destination, format, raw_dll, control, Some(cache), false)
    }
    fn run_internal(
        &self,
        destination: &Path,
        format: Format,
        raw_dll: Option<&Path>,
        control: &ExportControl,
        mut cache: Option<&mut FullFrameCache>,
        retain_decoded: bool,
    ) -> Result<ExportReceipt> {
        export::check_cancel(&control.cancelled)?;
        self.unchanged()?;
        if destination.try_exists()? {
            return Err(Error::Policy(
                "目标已存在；请选择新文件名，原片和已有文件不会被覆盖".into(),
            ));
        }
        let started = Instant::now();
        control.stage.store(1, Ordering::Release);
        let (frame, reused_decode) = if let Some(frame) = cache
            .as_deref_mut()
            .and_then(|v| v.get(&self.stamp, raw_dll))
        {
            (frame, true)
        } else {
            let frame = if self
                .edit
                .path
                .extension()
                .is_some_and(|s| s.eq_ignore_ascii_case("nef"))
            {
                #[cfg(windows)]
                {
                    let dll = raw_dll.ok_or_else(|| {
                        Error::Policy("缺少 RAW 解码组件，请完整解压软件包并保留 raw 文件夹".into())
                    })?;
                    crate::raw::LibRaw::load(dll)?
                        .decode(&self.edit.path, false, self.edit.recipe.white_balance)?
                        .frame
                }
                #[cfg(not(windows))]
                {
                    let _ = raw_dll;
                    return Err(Error::Policy("RAW 桥接仅支持 Windows".into()));
                }
            } else {
                raster::decode(&self.edit.path)?.frame
            };
            (Arc::new(frame), false)
        };
        let decode_seconds = started.elapsed().as_secs_f64();
        export::check_cancel(&control.cancelled)?;
        control.stage.store(2, Ordering::Release);
        let render_started = Instant::now();
        let rendered = crate::rendering::render_with_cancel(
            frame.as_ref(),
            &self.edit.recipe,
            &self.edit.path,
            (frame.width, frame.height),
            || control.cancelled.load(Ordering::Acquire),
        )?;
        let render_seconds = render_started.elapsed().as_secs_f64();
        // Preserve the old low-memory path when caching is disabled or the
        // source exceeds the cap: release linear pixels before TIFF encoding.
        let keep_for_cache = retain_decoded
            && !reused_decode
            && cache.is_some()
            && frame
                .pixels
                .len()
                .saturating_mul(std::mem::size_of::<[f32; 3]>())
                <= MAX_CACHED_FRAME_BYTES;
        let frame_for_cache = if keep_for_cache {
            Some(frame)
        } else {
            drop(frame);
            None
        };
        export::check_cancel(&control.cancelled)?;
        control.stage.store(3, Ordering::Release);
        let encode_started = Instant::now();
        export::save_guarded(
            &rendered,
            destination,
            format,
            95,
            &control.cancelled,
            || self.unchanged(),
        )?;
        let encode_seconds = encode_started.elapsed().as_secs_f64();
        if let (Some(cache), Some(frame)) = (cache, frame_for_cache) {
            cache.insert(self.stamp.clone(), raw_dll, frame);
        }
        Ok(ExportReceipt {
            destination: destination.to_owned(),
            width: rendered.width,
            height: rendered.height,
            seconds: started.elapsed().as_secs_f64(),
            decode_seconds,
            render_seconds,
            encode_seconds,
            reused_decode,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{develop, recipe::Recipe};
    #[test]
    fn export_uses_captured_recipe_and_full_size_not_the_active_preview() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("original.png");
        image::RgbImage::from_pixel(1801, 3, image::Rgb([80, 60, 40]))
            .save(&source)
            .unwrap();
        let original = std::fs::read(&source).unwrap();
        let mut edit = EditRequest {
            path: source.clone(),
            recipe: Recipe::default(),
        };
        edit.recipe.parameters.exposure = 1.;
        let snapshot = ExportSource::new(edit.clone()).unwrap();
        edit.recipe.parameters.exposure = -2.;
        edit.path = root.path().join("another-photo.png");
        let destination = root.path().join("edited.tif");
        let receipt = snapshot
            .run(
                &destination,
                Format::Tiff16,
                None,
                &ExportControl::default(),
            )
            .unwrap();
        assert_eq!((receipt.width, receipt.height), (1801, 3));
        let expected = develop::render(
            &raster::decode(&source).unwrap().frame,
            &snapshot.edit.recipe.parameters,
        )
        .unwrap();
        let actual = image::open(&destination).unwrap().to_rgb16();
        for (a, b) in actual.pixels().zip(&expected.pixels) {
            for c in 0..3 {
                assert_eq!(a[c], (b[c] * 65535.).round_ties_even() as u16);
            }
        }
        assert_eq!(std::fs::read(source).unwrap(), original);
    }
    #[test]
    fn changed_source_and_pre_cancelled_job_do_not_create_outputs() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("input.png");
        image::RgbImage::from_pixel(4, 3, image::Rgb([60, 80, 20]))
            .save(&source)
            .unwrap();
        let snapshot = ExportSource::new(EditRequest {
            path: source.clone(),
            recipe: Recipe::default(),
        })
        .unwrap();
        let control = ExportControl::default();
        control.cancel();
        let destination = root.path().join("out.png");
        assert!(
            snapshot
                .run(&destination, Format::Png, None, &control)
                .is_err()
        );
        use std::io::Write;
        std::fs::OpenOptions::new()
            .append(true)
            .open(source)
            .unwrap()
            .write_all(b"changed")
            .unwrap();
        assert!(
            snapshot
                .run(&destination, Format::Png, None, &ExportControl::default())
                .is_err()
        );
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
    }
    #[test]
    fn bounded_full_frame_cache_reuses_only_the_unchanged_source() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source.png");
        image::RgbImage::from_pixel(20, 12, image::Rgb([65, 80, 95]))
            .save(&source)
            .unwrap();
        let original = std::fs::read(&source).unwrap();
        let snapshot = ExportSource::new(EditRequest {
            path: source.clone(),
            recipe: Recipe::default(),
        })
        .unwrap();
        let mut cache = FullFrameCache::default();
        let first = snapshot
            .run_with_cache(
                &root.path().join("first.tif"),
                Format::Tiff16,
                None,
                &ExportControl::default(),
                &mut cache,
            )
            .unwrap();
        let second = snapshot
            .run_with_cache(
                &root.path().join("second.tif"),
                Format::Tiff16,
                None,
                &ExportControl::default(),
                &mut cache,
            )
            .unwrap();
        assert!(!first.reused_decode && second.reused_decode);
        assert_eq!(std::fs::read(&source).unwrap(), original);
        assert_eq!(
            std::fs::read(root.path().join("first.tif")).unwrap(),
            std::fs::read(root.path().join("second.tif")).unwrap()
        );
        image::RgbImage::from_pixel(21, 12, image::Rgb([20, 150, 30]))
            .save(&source)
            .unwrap();
        let changed = ExportSource::new(EditRequest {
            path: source.clone(),
            recipe: Recipe::default(),
        })
        .unwrap();
        let third = changed
            .run_with_cache(
                &root.path().join("third.tif"),
                Format::Tiff16,
                None,
                &ExportControl::default(),
                &mut cache,
            )
            .unwrap();
        assert!(!third.reused_decode && third.width == 21);
        cache.clear();
        let fourth = changed
            .run_with_cache(
                &root.path().join("fourth.tif"),
                Format::Tiff16,
                None,
                &ExportControl::default(),
                &mut cache,
            )
            .unwrap();
        assert!(!fourth.reused_decode);
        assert!(cache.bytes() > 0);
        cache.cached.as_mut().unwrap().last_used = Instant::now() - CACHE_IDLE;
        cache.evict_if_idle();
        assert_eq!(cache.bytes(), 0);
        let fifth = changed
            .run_reusing_cache(
                &root.path().join("fifth.tif"),
                Format::Tiff16,
                None,
                &ExportControl::default(),
                &mut cache,
            )
            .unwrap();
        assert!(!fifth.reused_decode);
        assert_eq!(
            cache.bytes(),
            0,
            "a batch miss must not retain another full frame"
        );
    }
}
