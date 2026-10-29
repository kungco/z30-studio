//! Windows LibRaw 0.22.2 bridge. Every C allocation is freed by the same DLL.
use crate::{
    Error, MAX_SOURCE_BYTES, Result,
    frame::Frame,
    raw_geometry::{RawSizes, reframe16},
};
use libloading::Library;
use libloading::os::windows::{LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32};
use serde::Serialize;
use std::{
    ffi::{c_int, c_uint, c_void},
    os::windows::ffi::OsStrExt,
    path::Path,
    ptr::NonNull,
};

#[repr(C)]
struct Info {
    raw_width: u32,
    raw_height: u32,
    width: u32,
    height: u32,
    left: u32,
    top: u32,
    crop_left: u32,
    crop_top: u32,
    crop_width: u32,
    crop_height: u32,
    flip: i32,
    raw_bits: u32,
    aspect: f64,
    model: [u8; 64],
}
#[repr(C)]
struct View {
    width: u32,
    height: u32,
    colors: u32,
    bits: u32,
    bitmap: u32,
    bytes: u64,
    pixels: *const u8,
}
unsafe extern "C" {
    fn z30_raw_info_size() -> usize;
    fn z30_raw_view_size() -> usize;
    fn z30_raw_options(context: *mut c_void, half: c_int, wb: c_int);
    fn z30_get_raw_info(context: *const c_void, info: *mut Info);
    fn z30_get_raw_view(memory: *const c_void, view: *mut View);
}
type ContextFn = unsafe extern "C" fn(*mut c_void) -> c_int;
type FreeFn = unsafe extern "C" fn(*mut c_void);

pub struct LibRaw {
    init: unsafe extern "C" fn(c_uint) -> *mut c_void,
    open: unsafe extern "C" fn(*mut c_void, *const u16) -> c_int,
    unpack: ContextFn,
    process: ContextFn,
    image: unsafe extern "C" fn(*mut c_void, *mut c_int) -> *mut c_void,
    close: FreeFn,
    clear: FreeFn,
    _library: Library,
}
struct Context<'a> {
    pointer: NonNull<c_void>,
    api: &'a LibRaw,
}
impl Drop for Context<'_> {
    fn drop(&mut self) {
        unsafe { (self.api.close)(self.pointer.as_ptr()) }
    }
}
struct Memory<'a> {
    pointer: NonNull<c_void>,
    api: &'a LibRaw,
}
impl Drop for Memory<'_> {
    fn drop(&mut self) {
        unsafe { (self.api.clear)(self.pointer.as_ptr()) }
    }
}

pub use crate::recipe::WhiteBalance;
impl WhiteBalance {
    fn id(self) -> c_int {
        match self {
            Self::Camera => 0,
            Self::Auto => 1,
            Self::Daylight => 2,
        }
    }
}
#[derive(Clone, Debug, Serialize)]
pub struct RawMetadata {
    pub model: String,
    pub source_bits: u32,
    pub output_bits: u32,
    pub linear_space: &'static str,
    pub libraw_version: &'static str,
    pub sensor_width: u32,
    pub sensor_height: u32,
    pub active_width: u32,
    pub active_height: u32,
    pub original_flip: i32,
    pub half_size: bool,
}
pub struct DecodedRaw {
    pub frame: Frame,
    pub metadata: RawMetadata,
    pub timings: RawTimings,
}
#[derive(Clone, Debug, Serialize)]
pub struct RawTimings {
    pub open_seconds: f64,
    pub unpack_seconds: f64,
    pub process_seconds: f64,
    pub bitmap_seconds: f64,
    pub release_seconds: f64,
    pub geometry_seconds: f64,
}
fn policy(message: impl Into<String>) -> Error {
    Error::Policy(message.into())
}
fn code(stage: &str, value: c_int) -> Result<()> {
    if value == 0 {
        Ok(())
    } else {
        Err(policy(format!("LibRaw {stage} 失败（{value}）")))
    }
}

impl LibRaw {
    /// Only an explicit absolute DLL is accepted. Dependencies are searched in
    /// its directory and System32, never the working directory or process PATH.
    pub fn load(dll: &Path) -> Result<Self> {
        if unsafe { z30_raw_info_size() } != std::mem::size_of::<Info>()
            || unsafe { z30_raw_view_size() } != std::mem::size_of::<View>()
        {
            return Err(policy("RAW 布局桥接尺寸不一致"));
        }
        if !dll.is_absolute() {
            return Err(policy("LibRaw DLL 必须使用明确的绝对路径"));
        }
        let path = dll.canonicalize()?;
        unsafe {
            let library: Library = libloading::os::windows::Library::load_with_flags(
                &path,
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
            )
            .map_err(|e| {
                policy(format!(
                    "无法加载 RAW 解码组件：{e}；请重新完整解压软件包，或检查自定义运行库的依赖"
                ))
            })?
            .into();
            let version = library
                .get::<unsafe extern "C" fn() -> c_int>(b"libraw_versionNumber\0")
                .map_err(|e| policy(e.to_string()))?;
            if version() != 0x1602 {
                return Err(policy("LibRaw ABI 不匹配：此构建只接受 0.22.2"));
            }
            macro_rules! symbol {
                ($name:literal) => {
                    *library
                        .get(concat!($name, "\0").as_bytes())
                        .map_err(|e| policy(e.to_string()))?
                };
            }
            Ok(Self {
                init: symbol!("libraw_init"),
                open: symbol!("libraw_open_wfile"),
                unpack: symbol!("libraw_unpack"),
                process: symbol!("libraw_dcraw_process"),
                image: symbol!("libraw_dcraw_make_mem_image"),
                close: symbol!("libraw_close"),
                clear: symbol!("libraw_dcraw_clear_mem"),
                _library: library,
            })
        }
    }
    /// Read-only source; no LibRaw writer function is ever loaded.
    pub fn decode(&self, path: &Path, half: bool, wb: WhiteBalance) -> Result<DecodedRaw> {
        let path = path.canonicalize()?;
        let metadata = path.metadata()?;
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_SOURCE_BYTES {
            return Err(policy("RAW 文件为空、非普通文件或超过 150 MiB"));
        }
        let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
        if wide.contains(&0) {
            return Err(policy("RAW 路径含空字符"));
        }
        wide.push(0);
        let clock = std::time::Instant::now();
        // Init/close and make_mem_image/clear_mem are matched even on early errors.
        let context = Context {
            pointer: NonNull::new(unsafe { (self.init)(0) })
                .ok_or_else(|| policy("LibRaw 初始化失败"))?,
            api: self,
        };
        let pointer = context.pointer.as_ptr();
        unsafe {
            z30_raw_options(pointer, i32::from(half), wb.id());
            code("open", (self.open)(pointer, wide.as_ptr()))?;
        }
        // All-zero is valid for these integer/floating/byte-only repr(C) structs.
        let mut info: Info = unsafe { std::mem::zeroed() };
        unsafe { z30_get_raw_info(pointer, &mut info) };
        let pixels = info.width as u64 * info.height as u64;
        if pixels == 0
            || pixels > 80_000_000
            || info.raw_width as u64 * info.raw_height as u64 > 100_000_000
        {
            return Err(policy("RAW 传感器尺寸无效或超过内存预算"));
        }
        if info.aspect != 1. {
            return Err(policy("非方形像素 RAW 尚未验证"));
        }
        let open_seconds = clock.elapsed().as_secs_f64();
        let clock = std::time::Instant::now();
        unsafe {
            code("unpack", (self.unpack)(pointer))?;
            z30_get_raw_info(pointer, &mut info);
        }
        let unpack_seconds = clock.elapsed().as_secs_f64();
        let sizes = RawSizes {
            raw_width: info.raw_width,
            raw_height: info.raw_height,
            width: info.width,
            height: info.height,
            left: info.left,
            top: info.top,
            crop_left: info.crop_left,
            crop_top: info.crop_top,
            crop_width: info.crop_width,
            crop_height: info.crop_height,
            flip: info.flip,
        };
        let clock = std::time::Instant::now();
        unsafe {
            code("process", (self.process)(pointer))?;
        }
        let process_seconds = clock.elapsed().as_secs_f64();
        let clock = std::time::Instant::now();
        let mut error = 0;
        let image_pointer = unsafe { (self.image)(pointer, &mut error) };
        let memory = NonNull::new(image_pointer).map(|pointer| Memory { pointer, api: self });
        code("make_mem_image", error)?;
        let memory = memory.ok_or_else(|| policy("LibRaw 返回空图像"))?;
        let mut view: View = unsafe { std::mem::zeroed() };
        unsafe { z30_get_raw_view(memory.pointer.as_ptr(), &mut view) };
        let expected = (view.width as u64)
            .checked_mul(view.height as u64)
            .and_then(|n| n.checked_mul(6));
        if view.bitmap != 1
            || view.colors != 3
            || view.bits != 16
            || view.pixels.is_null()
            || expected != Some(view.bytes)
            || view.bytes == 0
            || view.bytes > 480_000_000
        {
            return Err(policy("LibRaw 返回了非预期的 RGB16 缓冲"));
        }
        let bitmap_seconds = clock.elapsed().as_secs_f64();
        let clock = std::time::Instant::now();
        // Official 0.22.2 mem_image.cpp malloc/copies an independent bitmap.
        // Releasing the decoder now removes its RAW/working buffers before we
        // allocate float pixels; Memory still owns its separate bitmap/DLL.
        drop(context);
        let release_seconds = clock.elapsed().as_secs_f64();
        let clock = std::time::Instant::now();
        // LibRaw owns this checked, bounded bitmap. It outlives this slice and
        // is freed by Memory only after reframe16 has produced owned float data.
        let bytes = unsafe { std::slice::from_raw_parts(view.pixels, view.bytes as usize) };
        let frame = reframe16(bytes, view.width, view.height, sizes, half)?;
        let geometry_seconds = clock.elapsed().as_secs_f64();
        let end = info
            .model
            .iter()
            .position(|&c| c == 0)
            .unwrap_or(info.model.len());
        Ok(DecodedRaw {
            frame,
            timings: RawTimings {
                open_seconds,
                unpack_seconds,
                process_seconds,
                bitmap_seconds,
                release_seconds,
                geometry_seconds,
            },
            metadata: RawMetadata {
                model: String::from_utf8_lossy(&info.model[..end]).into_owned(),
                source_bits: info.raw_bits,
                output_bits: 16,
                linear_space: "linear-srgb",
                libraw_version: "0.22.2",
                sensor_width: info.raw_width,
                sensor_height: info.raw_height,
                active_width: info.width,
                active_height: info.height,
                original_flip: info.flip,
                half_size: half,
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn never_load_a_relative_or_missing_library() {
        assert!(LibRaw::load(Path::new("libraw.dll")).is_err());
        assert!(LibRaw::load(Path::new("C:/does-not-exist-z30/libraw.dll")).is_err());
    }
    #[test]
    #[ignore = "Requires explicit Z30_LIBRAW_DLL and public Z30_RAW_12 / Z30_RAW_14 fixtures"]
    fn real_z30_12_and_14_bit_full_and_half() {
        let api = LibRaw::load(Path::new(
            &std::env::var_os("Z30_LIBRAW_DLL").expect("missing Z30_LIBRAW_DLL"),
        ))
        .unwrap();
        for (name, bits) in [("Z30_RAW_12", 12), ("Z30_RAW_14", 14)] {
            let source = std::env::var_os(name).expect("missing explicit RAW fixture");
            for half in [false, true] {
                let result = api
                    .decode(Path::new(&source), half, WhiteBalance::Camera)
                    .unwrap();
                assert_eq!(result.metadata.source_bits, bits);
                assert_eq!(
                    (result.frame.width, result.frame.height),
                    if half { (1856, 2784) } else { (3712, 5568) }
                );
                assert!(
                    result
                        .frame
                        .pixels
                        .iter()
                        .flatten()
                        .all(|&v| (0.0..=1.0).contains(&v))
                );
                assert!(result.frame.pixels.iter().any(|p| {
                    p.iter()
                        .any(|v| (v * 255. - (v * 255.).round()).abs() > 0.001)
                }));
            }
        }
    }
}
