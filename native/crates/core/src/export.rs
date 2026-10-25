//! Colour-tagged, no-overwrite output. Encoding finishes in a same-directory
//! temporary file, then persist_noclobber publishes only if the name is unused.
use crate::{Error, Result, develop::SrgbFrame};
use image::{
    ExtendedColorType, ImageEncoder,
    codecs::{jpeg::JpegEncoder, png::PngEncoder, tiff::TiffEncoder},
};
use std::{io::Write, path::Path};
pub const SRGB_ICC: &[u8] = include_bytes!("../assets/srgb.icc");
#[derive(Clone, Copy)]
pub enum Format {
    Jpeg,
    Png,
    Tiff16,
}

pub fn save(frame: &SrgbFrame, destination: &Path, format: Format, jpeg_quality: u8) -> Result<()> {
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
            let mut encoder = TiffEncoder::new(temporary.as_file_mut());
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
                let mut encoder =
                    JpegEncoder::new_with_quality(temporary.as_file_mut(), jpeg_quality);
                encoder
                    .set_icc_profile(SRGB_ICC.to_vec())
                    .map_err(image::ImageError::Unsupported)?;
                encoder.write_image(&data, frame.width, frame.height, ExtendedColorType::Rgb8)?;
            } else {
                let mut encoder = PngEncoder::new(temporary.as_file_mut());
                encoder
                    .set_icc_profile(SRGB_ICC.to_vec())
                    .map_err(image::ImageError::Unsupported)?;
                encoder.write_image(&data, frame.width, frame.height, ExtendedColorType::Rgb8)?;
            }
        }
    }
    temporary.as_file_mut().flush()?;
    temporary.as_file().sync_all()?;
    // This is the final atomic no-replace operation, including races after
    // try_exists. On failure the still-owned temporary file is cleaned up.
    temporary
        .persist_noclobber(destination)
        .map_err(|error| Error::Io(error.error))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{ImageDecoder, ImageReader};
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
