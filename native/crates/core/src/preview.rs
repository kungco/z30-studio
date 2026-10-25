use crate::{Error, Result, colour, raster};
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
    if path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("nef"))
    {
        return Err(Error::Policy(
            "NEF 已加入图库；原生 RAW 编辑界面仍在接入，原片未修改。".into(),
        ));
    }
    let decoded = raster::decode(path)?;
    let source = decoded.frame;
    let (source_width, source_height) = (source.width, source.height);
    let displayed = source.thumbnail(1600, 1600)?;
    let thumbnail = displayed.thumbnail(128, 96)?;
    let mut policy = decoded.policy;
    for warning in decoded.warnings {
        policy.push_str("；");
        policy.push_str(&warning);
    }
    policy.push_str("；显示输出为 sRGB，尚未适配显示器 ICC");
    Ok(Preview {
        width: displayed.width,
        height: displayed.height,
        source_width,
        source_height,
        rgb: displayed
            .pixels
            .into_iter()
            .flat_map(|p| p.map(colour::encode_srgb))
            .collect(),
        thumb_width: thumbnail.width,
        thumb_height: thumbnail.height,
        thumbnail: thumbnail
            .pixels
            .into_iter()
            .flat_map(|p| p.map(colour::encode_srgb))
            .collect(),
        policy,
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
