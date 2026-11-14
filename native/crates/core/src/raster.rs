//! JPEG/PNG decoding and explicit input colour contracts. Original files are read only.
use crate::{Error, MAX_SOURCE_BYTES, Result, colour, frame::Frame};
use std::{
    fs::File,
    io::{Cursor, Read},
    path::Path,
};

pub struct Raster {
    pub frame: Frame,
    pub policy: String,
    pub warnings: Vec<String>,
    pub source_bits: u8,
}
#[derive(Default)]
struct Metadata {
    icc: Option<Vec<u8>>,
    exif: Option<Vec<u8>>,
    srgb: bool,
    gamma: Option<f64>,
    chroma: Option<[f64; 8]>,
    gray: bool,
}
fn invalid(message: impl std::fmt::Display) -> Error {
    Error::Policy(format!("JPEG/PNG 解码失败：{message}"))
}
fn dimensions(width: u32, height: u32) -> Result<usize> {
    let count = width as u64 * height as u64;
    if width == 0 || height == 0 || width > 30000 || height > 30000 || count > 80_000_000 {
        return Err(invalid("图像尺寸超过安全限制"));
    }
    Ok(count as usize)
}
fn pixels(count: usize) -> Result<Vec<[f32; 3]>> {
    let mut out = Vec::new();
    out.try_reserve_exact(count)
        .map_err(|_| invalid("浮点图像内存不足"))?;
    Ok(out)
}

pub fn decode(path: &Path) -> Result<Raster> {
    let mut file = File::open(path)?;
    let info = file.metadata()?;
    if !info.is_file() || info.len() > MAX_SOURCE_BYTES {
        return Err(invalid("源文件超过读取限制"));
    }
    // Bound the actual read too, including a source growing after metadata().
    let mut data = Vec::new();
    file.by_ref()
        .take(MAX_SOURCE_BYTES + 1)
        .read_to_end(&mut data)?;
    if data.len() as u64 > MAX_SOURCE_BYTES {
        return Err(invalid("读取期间源文件超过限制"));
    }
    decode_bytes(&data)
}
fn decode_bytes(data: &[u8]) -> Result<Raster> {
    match image::guess_format(data)? {
        image::ImageFormat::Png => png(data),
        image::ImageFormat::Jpeg => jpeg(data),
        _ => Err(invalid("只接受 JPEG/PNG；NEF 由独立 RAW 引擎处理")),
    }
}

fn exif_policy(data: Option<Vec<u8>>) -> Result<(u8, Option<u32>, Option<Vec<u8>>)> {
    let Some(mut data) = data else {
        return Ok((1, None, None));
    };
    if data.len() > 1024 * 1024 {
        return Err(invalid("EXIF 超过 1 MiB 限制"));
    }
    if data.starts_with(b"Exif\0\0") {
        data.drain(..6);
    }
    let exif = exif::Reader::new()
        .read_raw(data)
        .map_err(|_| invalid("EXIF 损坏，未猜测方向/色彩空间"))?;
    let number = |tag| {
        exif.get_field(tag, exif::In::PRIMARY)
            .and_then(|f| f.value.get_uint(0))
    };
    let orientation = match exif.get_field(exif::Tag::Orientation, exif::In::PRIMARY) {
        Some(field) => field
            .value
            .get_uint(0)
            .ok_or_else(|| invalid("EXIF 方向类型无效"))?,
        None => 1,
    };
    if !(1..=8).contains(&orientation) {
        return Err(invalid("EXIF 方向无效"));
    }
    let interop = exif
        .get_field(exif::Tag::InteroperabilityIndex, exif::In::PRIMARY)
        .and_then(|f| {
            if let exif::Value::Ascii(values) = &f.value {
                values.first().cloned()
            } else {
                None
            }
        });
    Ok((orientation as u8, number(exif::Tag::ColorSpace), interop))
}

fn finish(
    mut frame: Frame,
    alpha: Option<Vec<f32>>,
    mut metadata: Metadata,
    bits: u8,
) -> Result<Raster> {
    let (orientation, exif_space, interop) = exif_policy(metadata.exif.take())?;
    let mut warnings = Vec::new();
    let policy;
    if let Some(data) = metadata.icc {
        colour::to_linear_raster(
            &mut frame.pixels,
            &colour::embedded(&data, metadata.gray)?,
            bits,
        )?;
        policy = "ICC → 浮点线性 sRGB";
    } else if metadata.srgb {
        colour::decode_raster_srgb(&mut frame.pixels, bits);
        policy = "PNG sRGB";
    } else if metadata.gamma.is_some() || metadata.chroma.is_some() {
        let gamma = metadata
            .gamma
            .ok_or_else(|| invalid("PNG cHRM 缺少 gAMA，无法可靠转换"))?;
        if !gamma.is_finite() || gamma <= 0. || gamma > 10. {
            return Err(invalid("PNG gAMA 无效或超出可支持范围"));
        }
        colour::to_linear_raster(
            &mut frame.pixels,
            &colour::rgb_profile(metadata.chroma.unwrap_or(colour::SRGB_XY), 1. / gamma)?,
            bits,
        )?;
        policy = "PNG gAMA/cHRM → 浮点线性 sRGB";
    } else if exif_space == Some(2) || interop.as_deref() == Some(b"R03") {
        colour::to_linear_raster(
            &mut frame.pixels,
            &colour::rgb_profile(colour::ADOBE_XY, 563. / 256.)?,
            bits,
        )?;
        policy = "EXIF Adobe RGB → 浮点线性 sRGB";
    } else {
        colour::decode_raster_srgb(&mut frame.pixels, bits);
        policy = "sRGB（无 ICC）";
        if exif_space != Some(1) && interop.as_deref() != Some(b"R98") {
            warnings.push("文件无 ICC；按 sRGB 解释，无法确认原始色彩空间".into());
        }
    }
    if let Some(alpha) = alpha {
        for (p, a) in frame.pixels.iter_mut().zip(alpha) {
            for v in p {
                *v = *v * a + 1. - a;
            }
        }
        warnings.push("透明区域已在线性光空间合成到白色背景".into());
    }
    frame = frame.oriented(orientation)?;
    Ok(Raster {
        frame,
        policy: policy.into(),
        warnings,
        source_bits: bits,
    })
}

fn png(data: &[u8]) -> Result<Raster> {
    // The PNG crate may ignore malformed ancillary chunks. If an iCCP was
    // present, silently treating it as an untagged image is forbidden.
    let mut offset = 8usize;
    let mut icc_present = false;
    let mut icc_count = 0;
    let mut gamma_present = false;
    let mut chroma_present = false;
    let mut srgb_present = false;
    let mut exif_present = false;
    while offset + 12 <= data.len() {
        let len = u32::from_be_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
        let end = offset
            .checked_add(len)
            .and_then(|v| v.checked_add(12))
            .filter(|&v| v <= data.len())
            .ok_or_else(|| invalid("PNG 区块长度无效"))?;
        let kind = &data[offset + 4..offset + 8];
        if kind == b"iCCP" {
            icc_present = true;
            icc_count += 1;
        }
        if kind == b"sRGB" {
            srgb_present = true;
        }
        if kind == b"eXIf" {
            exif_present = true;
        }
        if kind == b"gAMA" {
            gamma_present = true;
            if len != 4 {
                return Err(invalid("PNG gAMA 长度无效"));
            }
        }
        if kind == b"cHRM" {
            chroma_present = true;
            if len != 32 {
                return Err(invalid("PNG cHRM 长度无效"));
            }
        }
        // HDR cICP requires another transfer/primaries pipeline; do not mislabel it sRGB.
        if kind == b"cICP" {
            return Err(invalid("此 PNG cICP/HDR 色彩标记尚未支持"));
        }
        offset = end;
        if kind == b"IEND" {
            break;
        }
    }
    if icc_count > 1 {
        return Err(invalid("PNG 包含重复 ICC 配置"));
    }
    let mut decoder = png::Decoder::new_with_limits(
        Cursor::new(data),
        png::Limits {
            bytes: 512 * 1024 * 1024,
        },
    );
    decoder.set_transformations(png::Transformations::EXPAND); // palette/low-bit/tRNS, never STRIP_16
    let mut reader = decoder.read_info().map_err(invalid)?;
    let info = reader.info();
    let count = dimensions(info.width, info.height)?;
    if info.animation_control.is_some() {
        return Err(invalid("动画 PNG 不作为单张照片导入"));
    }
    let source_bits = info.bit_depth as u8;
    let length = reader
        .output_buffer_size()
        .filter(|&v| v <= 512 * 1024 * 1024)
        .ok_or_else(|| invalid("PNG 像素缓冲超过限制"))?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(length)
        .map_err(|_| invalid("PNG 内存不足"))?;
    bytes.resize(length, 0);
    let output = reader.next_frame(&mut bytes).map_err(invalid)?;
    reader.finish().map_err(invalid)?;
    let info = reader.info();
    if icc_present && info.icc_profile.is_none() {
        return Err(invalid("PNG ICC 损坏或无法读取"));
    }
    if gamma_present && info.gama_chunk.is_none() {
        return Err(invalid("PNG gAMA 损坏"));
    }
    if chroma_present && info.chrm_chunk.is_none() {
        return Err(invalid("PNG cHRM 损坏"));
    }
    if srgb_present && info.srgb.is_none() {
        return Err(invalid("PNG sRGB 色彩声明损坏"));
    }
    if exif_present && info.exif_metadata.is_none() {
        return Err(invalid("PNG EXIF 损坏"));
    }
    let chroma = info.chrm_chunk.map(|c| {
        [
            c.white.0, c.white.1, c.red.0, c.red.1, c.green.0, c.green.1, c.blue.0, c.blue.1,
        ]
        .map(|v| v.into_scaled() as f64 / 100000.)
    });
    let metadata = Metadata {
        icc: info.icc_profile.as_ref().map(|v| v.to_vec()),
        exif: info.exif_metadata.as_ref().map(|v| v.to_vec()),
        srgb: info.srgb.is_some(),
        gamma: info.gama_chunk.map(|v| v.into_scaled() as f64 / 100000.),
        chroma,
        gray: matches!(
            info.color_type,
            png::ColorType::Grayscale | png::ColorType::GrayscaleAlpha
        ),
    };
    let channels = output.color_type.samples();
    let bits = output.bit_depth as u8;
    if !matches!(bits, 8 | 16) {
        return Err(invalid("PNG 解码位深无效"));
    }
    let mut out = pixels(count)?;
    let has_alpha = matches!(
        output.color_type,
        png::ColorType::Rgba | png::ColorType::GrayscaleAlpha
    );
    let mut alpha = if has_alpha {
        Some(Vec::with_capacity(count))
    } else {
        None
    };
    let stride = channels * (bits as usize / 8);
    for pixel in bytes[..output.buffer_size()].chunks_exact(stride) {
        let sample = |i| {
            if bits == 16 {
                u16::from_be_bytes([pixel[i * 2], pixel[i * 2 + 1]]) as f32 / 65535.
            } else {
                pixel[i] as f32 / 255.
            }
        };
        out.push(if channels <= 2 {
            [sample(0); 3]
        } else {
            [sample(0), sample(1), sample(2)]
        });
        if let Some(values) = &mut alpha {
            values.push(sample(channels - 1));
        }
    }
    drop(bytes);
    drop(reader);
    finish(
        Frame::new(output.width, output.height, out)?,
        alpha,
        metadata,
        source_bits,
    )
}

fn jpeg(data: &[u8]) -> Result<Raster> {
    let has_icc = jpeg_has_icc(data)?;
    // The safe wrapper's C-unwind error handler can panic on invalid JPEGs.
    // Convert that into an ordinary failed import; never poison the UI worker.
    std::panic::catch_unwind(|| jpeg_inner(data, has_icc))
        .unwrap_or_else(|_| Err(invalid("JPEG 数据损坏或不受支持")))
}
fn jpeg_inner(data: &[u8], has_icc: bool) -> Result<Raster> {
    let mut decoder =
        mozjpeg::Decompress::with_markers(&[mozjpeg::Marker::APP(1), mozjpeg::Marker::APP(2)])
            .from_mem(data)
            .map_err(invalid)?;
    let (width, height) = (decoder.width() as u32, decoder.height() as u32);
    let count = dimensions(width, height)?;
    let gray = match decoder.color_space() {
        mozjpeg::ColorSpace::JCS_GRAYSCALE => true,
        mozjpeg::ColorSpace::JCS_RGB | mozjpeg::ColorSpace::JCS_YCbCr => false,
        mozjpeg::ColorSpace::JCS_CMYK | mozjpeg::ColorSpace::JCS_YCCK => {
            return Err(invalid(
                "CMYK/YCCK JPEG 需要独立印刷 ICC 路径；当前原生版本拒绝猜测，原 Python 版可处理带 ICC 的文件",
            ));
        }
        _ => return Err(invalid("此 JPEG 通道组合尚未支持")),
    };
    let mut metadata = Metadata {
        gray,
        ..Default::default()
    };
    let mut fragments = std::collections::BTreeMap::new();
    for marker in decoder.markers() {
        if marker.marker == mozjpeg::Marker::APP(1) && marker.data.starts_with(b"Exif\0\0") {
            if metadata.exif.is_some() {
                return Err(invalid("JPEG 包含重复 EXIF"));
            }
            metadata.exif = Some(marker.data[6..].to_vec());
        }
        if marker.marker == mozjpeg::Marker::APP(2) && marker.data.starts_with(b"ICC_PROFILE\0") {
            if marker.data.len() < 14 {
                return Err(invalid("JPEG ICC 分段头截断"));
            }
            fragments.insert(marker.data[12], &marker.data[14..]);
        }
    }
    if has_icc {
        let length: usize = fragments.values().map(|v| v.len()).sum();
        if length > colour::MAX_ICC_BYTES {
            return Err(invalid("ICC 超过 2 MiB 限制"));
        }
        metadata.icc = Some(fragments.values().flat_map(|v| v.iter().copied()).collect());
    }
    decoder.dct_method(mozjpeg::DctMethod::IntegerSlow);
    decoder.do_fancy_upsampling(true);
    let mut started = if gray {
        decoder.grayscale()
    } else {
        decoder.rgb()
    }
    .map_err(invalid)?;
    let bytes = started.read_scanlines::<u8>().map_err(invalid)?;
    started.finish().map_err(invalid)?;
    let channels = if gray { 1 } else { 3 };
    if bytes.len() != count * channels {
        return Err(invalid("JPEG 像素长度不匹配"));
    }
    let mut out = pixels(count)?;
    for pixel in bytes.chunks_exact(channels) {
        out.push(if gray {
            [pixel[0] as f32 / 255.; 3]
        } else {
            [
                pixel[0] as f32 / 255.,
                pixel[1] as f32 / 255.,
                pixel[2] as f32 / 255.,
            ]
        });
    }
    drop(bytes);
    finish(Frame::new(width, height, out)?, None, metadata, 8)
}
fn jpeg_has_icc(data: &[u8]) -> Result<bool> {
    let mut i = 2;
    let mut present = false;
    let mut count = None;
    let mut parts = std::collections::BTreeSet::new();
    let mut profile_bytes = 0usize;
    let mut scans = 0;
    let mut finished = false;
    while i + 1 < data.len() {
        if data[i] != 0xff {
            return Err(invalid("JPEG 标记损坏"));
        }
        while i < data.len() && data[i] == 0xff {
            i += 1;
        }
        let marker = *data.get(i).ok_or_else(|| invalid("JPEG 标记截断"))?;
        i += 1;
        if marker == 0xd9 {
            finished = true;
            break;
        }
        if marker == 0x01 || (0xd0..=0xd8).contains(&marker) {
            continue;
        }
        let length = data
            .get(i..i + 2)
            .map(|v| u16::from_be_bytes([v[0], v[1]]) as usize)
            .ok_or_else(|| invalid("JPEG 长度截断"))?;
        if length < 2 || i + length > data.len() {
            return Err(invalid("JPEG 分段越界"));
        }
        if marker == 0xe2 && data[i + 2..i + length].starts_with(b"ICC_PROFILE\0") {
            if scans != 0 {
                return Err(invalid("JPEG ICC 必须位于扫描数据之前"));
            }
            present = true;
            let payload = &data[i + 2..i + length];
            if payload.len() < 14 {
                return Err(invalid("JPEG ICC 分段头截断"));
            }
            let (sequence, total) = (payload[12], payload[13]);
            if total == 0
                || sequence == 0
                || sequence > total
                || count.is_some_and(|v| v != total)
                || !parts.insert(sequence)
            {
                return Err(invalid("JPEG ICC 分段序号重复或无效"));
            }
            count = Some(total);
            profile_bytes += payload.len() - 14;
            if profile_bytes > colour::MAX_ICC_BYTES {
                return Err(invalid("ICC 超过 2 MiB 限制"));
            }
        }
        i += length;
        if marker == 0xda {
            scans += 1;
            if scans > 100 {
                return Err(invalid("JPEG 超过 100 次渐进扫描上限"));
            }
            // Skip entropy data, byte-stuffing and restart markers. Stop at the
            // next real marker, so progressive scans can be bounded before C decoding.
            while i + 1 < data.len() {
                if data[i] != 0xff {
                    i += 1;
                    continue;
                }
                let mut j = i + 1;
                while j < data.len() && data[j] == 0xff {
                    j += 1;
                }
                let Some(&code) = data.get(j) else {
                    break;
                };
                if code == 0 || (0xd0..=0xd7).contains(&code) {
                    i = j + 1;
                    continue;
                }
                i = j - 1;
                break;
            }
        }
    }
    if !finished {
        return Err(invalid("JPEG 截断或缺少结束标记"));
    }
    if count.is_some_and(|total| parts.len() != total as usize) {
        return Err(invalid("JPEG ICC 分段不完整"));
    }
    Ok(present)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(serde::Deserialize)]
    struct Case {
        name: String,
        hex: String,
        width: Option<u32>,
        height: Option<u32>,
        expected: Option<Vec<[f32; 3]>>,
        tolerance: Option<f32>,
        error_contains: Option<String>,
        warning_expected: Option<bool>,
        distinct_red: Option<usize>,
    }
    #[test]
    fn synthetic_colour_orientation_depth_and_invalid_profile_corpus() {
        #[derive(serde::Deserialize)]
        struct Corpus {
            cases: Vec<Case>,
        }
        let corpus: Corpus =
            serde_json::from_str(include_str!("../tests/fixtures/raster.json")).unwrap();
        for case in corpus.cases {
            let bytes: Vec<u8> = case
                .hex
                .as_bytes()
                .chunks_exact(2)
                .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
                .collect();
            let decoded = decode_bytes(&bytes);
            if let Some(message) = case.error_contains {
                let Err(error) = decoded else {
                    panic!("{} should fail", case.name);
                };
                assert!(
                    error.to_string().contains(&message),
                    "{}: {error}",
                    case.name
                );
                continue;
            }
            let raster = decoded.unwrap_or_else(|error| panic!("{}: {error}", case.name));
            assert_eq!(
                (raster.frame.width, raster.frame.height),
                (case.width.unwrap(), case.height.unwrap()),
                "{}",
                case.name
            );
            let reference = case.expected.unwrap();
            assert_eq!(raster.frame.pixels.len(), reference.len(), "{}", case.name);
            let error = raster
                .frame
                .pixels
                .iter()
                .flatten()
                .zip(reference.iter().flatten())
                .map(|(a, b)| (a - b).abs())
                .fold(0f32, f32::max);
            assert!(
                error <= case.tolerance.unwrap(),
                "{}: error {error}",
                case.name
            );
            if let Some(warning) = case.warning_expected {
                assert_eq!(!raster.warnings.is_empty(), warning, "{}", case.name);
            }
            if let Some(count) = case.distinct_red {
                let distinct: std::collections::BTreeSet<_> =
                    raster.frame.pixels.iter().map(|v| v[0].to_bits()).collect();
                assert_eq!(raster.source_bits, 16);
                assert_eq!(distinct.len(), count, "{}", case.name);
            }
        }
    }
}
