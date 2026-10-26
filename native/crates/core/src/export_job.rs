//! One immutable export snapshot, independent from current UI selection.
use crate::{
    Error, Result, develop,
    editing::EditRequest,
    editor::Stamp,
    export::{self, Format},
    raster,
};
use std::{
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, AtomicU8, Ordering},
    time::Instant,
};

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
        export::check_cancel(&control.cancelled)?;
        self.unchanged()?;
        if destination.try_exists()? {
            return Err(Error::Policy(
                "目标已存在；请选择新文件名，原片和已有文件不会被覆盖".into(),
            ));
        }
        let started = Instant::now();
        control.stage.store(1, Ordering::Release);
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
        export::check_cancel(&control.cancelled)?;
        control.stage.store(2, Ordering::Release);
        let rendered = develop::render(&frame, &self.edit.recipe.parameters)?;
        drop(frame);
        export::check_cancel(&control.cancelled)?;
        control.stage.store(3, Ordering::Release);
        export::save_guarded(
            &rendered,
            destination,
            format,
            95,
            &control.cancelled,
            || self.unchanged(),
        )?;
        Ok(ExportReceipt {
            destination: destination.to_owned(),
            width: rendered.width,
            height: rendered.height,
            seconds: started.elapsed().as_secs_f64(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::recipe::Recipe;
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
}
