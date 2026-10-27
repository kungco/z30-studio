//! UI-independent contracts. Python remains the image-quality oracle during migration.
pub mod ai;
pub mod batch;
mod bounded;
pub mod colour;
pub mod develop;
pub mod editing;
pub mod editor;
pub mod export;
pub mod export_job;
pub mod frame;
pub mod preview;
pub mod portrait;
pub mod rendering;
pub mod source;
pub mod skin;
pub mod skin_tone;
pub mod raster;
#[cfg(windows)]
pub mod raw;
pub mod raw_geometry;
pub mod recipe;
pub mod retouch;
pub mod runtime;
pub mod session;
pub mod worker;

pub const MAX_PHOTOS: usize = 256;
pub const MAX_SOURCE_BYTES: u64 = 150 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{0}")]
    Policy(String),
    #[error("文件读写失败：{0}")]
    Io(#[from] std::io::Error),
    #[error("图像解码失败：{0}")]
    Image(#[from] image::ImageError),
    #[error("项目数据无效：{0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, Error>;
