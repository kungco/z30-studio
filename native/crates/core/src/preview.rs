use crate::{Error, MAX_SOURCE_BYTES, Result};
use image::{ImageDecoder, ImageReader};
use std::path::Path;

pub struct Preview {
    pub width: u32,
    pub height: u32,
    pub source_width: u32,
    pub source_height: u32,
    pub rgb: Vec<u8>,
    pub thumbnail: Vec<u8>,
    pub thumb_width: u32,
    pub thumb_height: u32,
    pub policy: String,
}

pub fn decode(path: &Path) -> Result<Preview> {
    let info = path.metadata()?;
    if !info.is_file() || info.len() > MAX_SOURCE_BYTES {
        return Err(Error::Policy("源文件超出读取限制".into()));
    }
    if path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("nef"))
    {
        return Err(Error::Policy(
            "NEF 已加入图库；原生 RAW 引擎在阶段 2 接入。原片未修改。".into(),
        ));
    }
    let mut reader = ImageReader::open(path)?.with_guessed_format()?;
    let mut limits = image::Limits::default();
    limits.max_alloc = Some(512 * 1024 * 1024);
    limits.max_image_width = Some(30000);
    limits.max_image_height = Some(30000);
    reader.limits(limits);
    let mut decoder = reader.into_decoder()?;
    let orientation = decoder.orientation()?;
    if decoder.icc_profile()?.is_some() {
        return Err(Error::Policy(
            "检测到 ICC；阶段 1 尚未完成原生色彩转换，暂停此照片预览。原 Python 版仍可处理。"
                .into(),
        ));
    }
    let (w, h) = decoder.dimensions();
    if u64::from(w) * u64::from(h) > 80_000_000 {
        return Err(Error::Policy("图像像素数超过安全解码限制".into()));
    }
    let mut image = image::DynamicImage::from_decoder(decoder)?;
    image.apply_orientation(orientation);
    let (source_width, source_height) = (image.width(), image.height());
    let displayed = image.thumbnail(1600, 1600).to_rgb8();
    let thumbnail = image.thumbnail(128, 96).to_rgb8();
    Ok(Preview {
        width: displayed.width(),
        height: displayed.height(),
        source_width,
        source_height,
        rgb: displayed.into_raw(),
        thumb_width: thumbnail.width(),
        thumb_height: thumbnail.height(),
        thumbnail: thumbnail.into_raw(),
        policy: "无 ICC：查看器暂按 sRGB 显示；原生调色/导出尚未启用".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preview_preserves_dimensions_and_source_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("测试.png");
        image::RgbImage::from_pixel(43, 21, image::Rgb([20, 100, 230]))
            .save(&path)
            .unwrap();
        let before = std::fs::read(&path).unwrap();
        let p = decode(&path).unwrap();
        assert_eq!((p.source_width, p.source_height), (43, 21));
        assert_eq!(p.rgb.len(), (p.width * p.height * 3) as usize);
        assert_eq!(std::fs::read(path).unwrap(), before);
    }
}
