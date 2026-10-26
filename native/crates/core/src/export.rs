//! Colour-tagged, no-overwrite output. Encoding finishes in a same-directory
//! temporary file, then persist_noclobber publishes only if the name is unused.
use crate::{Error, Result, develop::SrgbFrame};
use image::{
    ExtendedColorType, ImageEncoder,
    codecs::{png::PngEncoder, tiff::TiffEncoder},
};
use std::{
    io::{Seek, SeekFrom, Write},
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
};
pub const SRGB_ICC: &[u8] = include_bytes!("../assets/srgb.icc");
#[derive(Clone, Copy, Debug)]
pub enum Format {
    Jpeg,
    Png,
    Tiff16,
}

pub fn save(frame: &SrgbFrame, destination: &Path, format: Format, jpeg_quality: u8) -> Result<()> {
    save_guarded(
        frame,
        destination,
        format,
        jpeg_quality,
        &AtomicBool::new(false),
        || Ok(()),
    )
}

pub(crate) fn check_cancel(cancel: &AtomicBool) -> Result<()> {
    if cancel.load(Ordering::Acquire) {
        Err(Error::Policy("导出已取消，未发布新文件".into()))
    } else {
        Ok(())
    }
}

struct CheckedWriter<'a> {
    file: &'a mut std::fs::File,
    cancel: &'a AtomicBool,
}
impl CheckedWriter<'_> {
    fn check(&self) -> std::io::Result<()> {
        // Interrupted would cause write_all to retry indefinitely.
        if self.cancel.load(Ordering::Acquire) {
            Err(std::io::Error::other("导出已取消"))
        } else {
            Ok(())
        }
    }
}
impl Write for CheckedWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.check()?;
        self.file.write(bytes)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.check()?;
        self.file.flush()
    }
}
impl Seek for CheckedWriter<'_> {
    fn seek(&mut self, from: SeekFrom) -> std::io::Result<u64> {
        self.check()?;
        self.file.seek(from)
    }
}

pub(crate) fn save_guarded(
    frame: &SrgbFrame,
    destination: &Path,
    format: Format,
    jpeg_quality: u8,
    cancel: &AtomicBool,
    before_publish: impl FnOnce() -> Result<()>,
) -> Result<()> {
    check_cancel(cancel)?;
    if !destination.is_absolute() || !(1..=100).contains(&jpeg_quality) {
        return Err(Error::Policy(
            "导出路径必须为绝对路径，JPEG 质量范围为 1–100".into(),
        ));
    }
    let extension = destination
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let valid = match format {
        Format::Jpeg => matches!(extension.as_str(), "jpg" | "jpeg"),
        Format::Png => extension == "png",
        Format::Tiff16 => matches!(extension.as_str(), "tif" | "tiff"),
    };
    if !valid {
        return Err(Error::Policy(
            "导出扩展名与格式不匹配，NEF 不能作为输出".into(),
        ));
    }
    if matches!(format, Format::Jpeg) && (frame.width > 65500 || frame.height > 65500) {
        return Err(Error::Policy("JPEG 单边尺寸不能超过 65500 像素".into()));
    }
    let count = frame.width as u64 * frame.height as u64;
    if count == 0
        || count > 80_000_000
        || count != frame.pixels.len() as u64
        || frame
            .pixels
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || !(-1e-6..=1.0 + 1e-6).contains(v))
    {
        return Err(Error::Policy("编码输入不是有效的浮点 sRGB 图像".into()));
    }
    if destination.try_exists()? {
        return Err(Error::Policy(
            "目标文件已存在；导出不会覆盖原片或已有文件".into(),
        ));
    }
    let parent = destination
        .parent()
        .ok_or_else(|| Error::Policy("导出目录无效".into()))?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".z30-export-")
        .suffix(".part")
        .tempfile_in(parent)?;
    match format {
        Format::Tiff16 => {
            let data: Vec<u16> = frame
                .pixels
                .iter()
                .flatten()
                .map(|v| (v.clamp(0.0, 1.0) * 65535.0).round_ties_even() as u16)
                .collect();
            let mut encoder = TiffEncoder::new(CheckedWriter {
                file: temporary.as_file_mut(),
                cancel,
            });
            encoder
                .set_icc_profile(SRGB_ICC.to_vec())
                .map_err(image::ImageError::Unsupported)?;
            encoder.write_image(
                bytemuck::cast_slice(&data),
                frame.width,
                frame.height,
                ExtendedColorType::Rgb16,
            )?;
        }
        Format::Jpeg | Format::Png => {
            let data: Vec<u8> = frame
                .pixels
                .iter()
                .flatten()
                .map(|v| (v.clamp(0.0, 1.0) * 255.0).round_ties_even() as u8)
                .collect();
            if matches!(format, Format::Jpeg) {
                encode_jpeg(
                    CheckedWriter {
                        file: temporary.as_file_mut(),
                        cancel,
                    },
                    &data,
                    frame.width,
                    frame.height,
                    jpeg_quality,
                )?;
            } else {
                let mut encoder = PngEncoder::new(CheckedWriter {
                    file: temporary.as_file_mut(),
                    cancel,
                });
                encoder
                    .set_icc_profile(SRGB_ICC.to_vec())
                    .map_err(image::ImageError::Unsupported)?;
                encoder.write_image(&data, frame.width, frame.height, ExtendedColorType::Rgb8)?;
            }
        }
    }
    temporary.as_file_mut().flush()?;
    temporary.as_file().sync_all()?;
    before_publish()?;
    check_cancel(cancel)?;
    // This is the final atomic no-replace operation, including races after
    // try_exists. On failure the still-owned temporary file is cleaned up.
    temporary
        .persist_noclobber(destination)
        .map_err(|error| Error::Io(error.error))?;
    Ok(())
}

fn encode_jpeg(
    writer: impl Write,
    data: &[u8],
    width: u32,
    height: u32,
    quality: u8,
) -> Result<()> {
    // The upstream safe wrapper reports C errors by unwinding. An encoder or
    // output-device failure must remain a regular failed no-overwrite export.
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| -> Result<()> {
        let mut encoder = mozjpeg::Compress::new(mozjpeg::ColorSpace::JCS_RGB);
        encoder.set_fastest_defaults();
        encoder.set_size(width as usize, height as usize);
        encoder.set_quality(quality as f32);
        encoder.set_chroma_sampling_pixel_sizes((1, 1), (1, 1));
        let mut started = encoder.start_compress(writer)?;
        // One-based ICC sequence, as required by ICC's JPEG embedding format.
        // mozjpeg 0.10.13 write_icc_profile() uses a zero-based chunk number.
        let mut marker = b"ICC_PROFILE\0\x01\x01".to_vec();
        marker.extend_from_slice(SRGB_ICC);
        started.write_marker(mozjpeg::Marker::APP(2), &marker);
        started.write_scanlines(data)?;
        let mut writer = started.finish()?;
        writer.flush()?;
        Ok(())
    }))
    .unwrap_or_else(|_| Err(Error::Policy("JPEG 编码失败；已有文件未被覆盖".into())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{ImageDecoder, ImageReader};
    #[test]
    fn cancelled_or_changed_source_never_publishes_and_cleans_temporary() {
        for (extension, format) in [
            ("jpg", Format::Jpeg),
            ("png", Format::Png),
            ("tif", Format::Tiff16),
        ] {
            let root = tempfile::tempdir().unwrap();
            let destination = root.path().join(format!("cancelled.{extension}"));
            let cancel = AtomicBool::new(false);
            assert!(
                save_guarded(&frame(), &destination, format, 95, &cancel, || {
                    cancel.store(true, Ordering::Release);
                    Ok(())
                })
                .is_err()
            );
            assert!(!destination.exists());
            assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
            cancel.store(false, Ordering::Release);
            assert!(
                save_guarded(&frame(), &destination, format, 95, &cancel, || Err(
                    Error::Policy("source changed".into())
                ))
                .is_err()
            );
            assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
            // A destination created after preflight must also survive intact.
            assert!(
                save_guarded(&frame(), &destination, format, 95, &cancel, || {
                    std::fs::write(&destination, b"concurrent file")?;
                    Ok(())
                })
                .is_err()
            );
            assert_eq!(std::fs::read(&destination).unwrap(), b"concurrent file");
        }
    }
    // Check the TIFF IFD directly as well as the image adapter below; this
    // avoids conflating metadata-read errors with a profile absent on disk.
    fn icc(path: &Path, format: Format) -> Vec<u8> {
        if matches!(format, Format::Tiff16) {
            tiff::decoder::Decoder::new(std::fs::File::open(path).unwrap())
                .unwrap()
                .get_tag_u8_vec(tiff::tags::Tag::IccProfile)
                .unwrap()
        } else {
            ImageReader::open(path)
                .unwrap()
                .into_decoder()
                .unwrap()
                .icc_profile()
                .unwrap()
                .unwrap()
        }
    }
    fn frame() -> SrgbFrame {
        SrgbFrame {
            width: 1024,
            height: 1,
            pixels: (0..1024)
                .map(|i| {
                    let x = (i as f32 + 0.123) / 1024.0;
                    [x, x * 0.5, 1.0 - x]
                })
                .collect(),
        }
    }
    #[test]
    fn jpeg_limits_and_output_failures_return_errors() {
        struct Broken;
        impl Write for Broken {
            fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("injected output failure"))
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Err(std::io::Error::other("injected flush failure"))
            }
        }
        assert!(encode_jpeg(Broken, &[128; 3 * 16 * 16], 16, 16, 95).is_err());
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("too-wide.jpg");
        let frame = SrgbFrame {
            width: 65501,
            height: 1,
            pixels: vec![[0.; 3]; 65501],
        };
        assert!(
            matches!(save(&frame,&destination,Format::Jpeg,95),Err(Error::Policy(message)) if message.contains("65500"))
        );
        assert!(!destination.exists());
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
    }
    #[test]
    fn real_tiff16_keeps_sub_eight_bit_values_and_srgb_tag() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("真正 16 位.tif");
        let source = frame();
        save(&source, &path, Format::Tiff16, 92).unwrap();
        let decoder = ImageReader::open(&path).unwrap().into_decoder().unwrap();
        assert_eq!(decoder.color_type(), image::ColorType::Rgb16);
        assert_eq!(icc(&path, Format::Tiff16), SRGB_ICC);
        let mut typed = image::codecs::tiff::TiffDecoder::new(std::io::BufReader::new(
            std::fs::File::open(&path).unwrap(),
        ))
        .unwrap();
        assert_eq!(typed.icc_profile().unwrap().unwrap(), SRGB_ICC);
        let result = image::open(path).unwrap().to_rgb16();
        let unique: std::collections::BTreeSet<_> = result.pixels().map(|p| p[0]).collect();
        assert_eq!(unique.len(), 1024);
        assert!(unique.iter().any(|v| v % 257 != 0));
        for (actual, expected) in result.pixels().zip(&source.pixels) {
            for c in 0..3 {
                assert_eq!(actual[c], (expected[c] * 65535.0).round_ties_even() as u16);
            }
        }
    }
    #[test]
    fn all_formats_tag_colour_and_refuse_existing_files() {
        let root = tempfile::tempdir().unwrap();
        for (extension, format) in [
            ("jpg", Format::Jpeg),
            ("png", Format::Png),
            ("tif", Format::Tiff16),
        ] {
            let path = root.path().join(format!("原片保护.{extension}"));
            save(&frame(), &path, format, 90).unwrap();
            let before = std::fs::read(&path).unwrap();
            assert!(save(&frame(), &path, format, 90).is_err());
            assert_eq!(std::fs::read(&path).unwrap(), before);
            assert_eq!(icc(&path, format), SRGB_ICC);
            if !matches!(format, Format::Tiff16) {
                let preview = crate::preview::decode(&path).unwrap();
                assert!(preview.policy.contains("ICC →"));
                assert_eq!((preview.source_width, preview.source_height), (1024, 1));
                let expected = crate::raster::decode(&path).unwrap();
                assert_eq!(expected.source_bits, 8);
                assert_eq!(std::fs::read(&path).unwrap(), before);
            }
        }
        assert!(save(&frame(), &root.path().join("原片.NEF"), Format::Tiff16, 90).is_err());
        assert!(
            std::fs::read_dir(root.path()).unwrap().all(|p| !p
                .unwrap()
                .path()
                .to_string_lossy()
                .ends_with(".part"))
        );
    }
}
