//! Float ICC input; no 8-bit intermediate and no silent profile fallback.
use crate::{Error, Result};
use lcms2::{
    CIExyY, CIExyYTRIPLE, ColorSpaceSignature, Flags, Intent, PixelFormat, Profile, Tag,
    TagSignature, ToneCurve, Transform,
};

pub const SRGB_XY: [f64; 8] = [0.3127, 0.3290, 0.64, 0.33, 0.30, 0.60, 0.15, 0.06];
pub const ADOBE_XY: [f64; 8] = [0.3127, 0.3290, 0.64, 0.33, 0.21, 0.71, 0.15, 0.06];
pub const MAX_ICC_BYTES: usize = 2 * 1024 * 1024;

pub fn decode_srgb(v: f32) -> f32 {
    if v <= 0.04045 {
        v / 12.92
    } else {
        ((v + 0.055) / 1.055).powf(2.4)
    }
}
/// Raster samples are discrete 8/16-bit values. Exact tables avoid repeating
/// millions of powf calls; float RAW/tone data must never go through this API.
pub fn decode_raster_srgb(pixels: &mut [[f32; 3]], bits: u8) {
    static EIGHT: std::sync::OnceLock<Vec<f32>> = std::sync::OnceLock::new();
    static SIXTEEN: std::sync::OnceLock<Vec<f32>> = std::sync::OnceLock::new();
    let (maximum, cell) = if bits == 16 {
        (65535usize, &SIXTEEN)
    } else {
        (255usize, &EIGHT)
    };
    let values = cell.get_or_init(|| {
        (0..=maximum)
            .map(|v| decode_srgb(v as f32 / maximum as f32))
            .collect()
    });
    for p in pixels {
        for v in p {
            *v = values[(*v * maximum as f32).round().clamp(0., maximum as f32) as usize];
        }
    }
}
pub fn encode_srgb(v: f32) -> u8 {
    let v = v.clamp(0., 1.);
    let encoded = if v <= 0.0031308 {
        v * 12.92
    } else {
        1.055 * v.powf(1. / 2.4) - 0.055
    };
    (encoded * 255.).round() as u8
}
fn failure(_: lcms2::Error) -> Error {
    Error::Policy("ICC 无效、不支持或无法建立可靠的浮点色彩转换".into())
}

pub fn rgb_profile(xy: [f64; 8], gamma: f64) -> Result<Profile> {
    if !gamma.is_finite()
        || !(0.1..=100000.).contains(&gamma)
        || xy.chunks_exact(2).any(|v| {
            !v[0].is_finite()
                || !v[1].is_finite()
                || v[0] < 0.
                || v[1] <= 0.
                || v[0] + v[1] > 1.00001
        })
    {
        return Err(Error::Policy("色度坐标或传递曲线无效".into()));
    }
    // Singular/collinear primary sets must not be interpreted as a fallback space.
    let determinant = (xy[4] - xy[2]) * (xy[7] - xy[3]) - (xy[6] - xy[2]) * (xy[5] - xy[3]);
    if determinant.abs() < 1e-8 {
        return Err(Error::Policy("色度坐标不可逆".into()));
    }
    let point = |i| CIExyY {
        x: xy[i],
        y: xy[i + 1],
        Y: 1.,
    };
    let primaries = CIExyYTRIPLE {
        Red: point(2),
        Green: point(4),
        Blue: point(6),
    };
    let curve = ToneCurve::new(gamma);
    Profile::new_rgb(&point(0), &primaries, &[&curve, &curve, &curve]).map_err(failure)
}
pub fn embedded(data: &[u8], gray: bool) -> Result<Profile> {
    if data.len() < 128
        || data.len() > MAX_ICC_BYTES
        || u32::from_be_bytes(data[..4].try_into().unwrap()) as usize != data.len()
        || &data[36..40] != b"acsp"
    {
        return Err(Error::Policy("嵌入 ICC 为空、损坏或超过 2 MiB 限制".into()));
    }
    let profile = Profile::new_icc(data).map_err(failure)?;
    // Device-link/abstract/named-colour profiles are not source image profiles.
    if !matches!(&data[12..16], b"scnr" | b"mntr" | b"prtr" | b"spac") {
        return Err(Error::Policy("此 ICC 类型不能作为照片输入配置".into()));
    }
    if profile.color_space() != ColorSpaceSignature::RgbData
        && !(gray && profile.color_space() == ColorSpaceSignature::GrayData)
    {
        return Err(Error::Policy("ICC 色彩空间与照片通道不匹配".into()));
    }
    Ok(profile)
}

/// The caller owns the frame; process in place without a second RGB frame.
pub fn to_linear(pixels: &mut [[f32; 3]], profile: &Profile) -> Result<()> {
    convert(pixels, profile, None)
}

/// Exact discrete source samples only. General float data uses to_linear().
pub fn to_linear_raster(pixels: &mut [[f32; 3]], profile: &Profile, bits: u8) -> Result<()> {
    if !matches!(bits, 1 | 2 | 4 | 8 | 16) {
        return Err(Error::Policy("光栅输入位深无效".into()));
    }
    let maximum = if bits == 16 { 65535usize } else { 255usize };
    if pixels.iter().flatten().any(|&v| {
        !v.is_finite()
            || !(0. ..=1.).contains(&v)
            || (v * maximum as f32 - (v * maximum as f32).round()).abs() > 0.01
    }) {
        return Err(Error::Policy(
            "离散 ICC 输入含非采样值；浮点输入必须走通用 CMS".into(),
        ));
    }
    convert(pixels, profile, Some(maximum))
}

fn convert(pixels: &mut [[f32; 3]], profile: &Profile, discrete: Option<usize>) -> Result<()> {
    // Use the same serialized primaries/white point as our export ICC. Creating
    // another virtual sRGB profile adds matrix rounding differences even for an
    // image tagged with our own sRGB profile. Only replace its transfer curves.
    let mut destination =
        Profile::new_icc(include_bytes!("../assets/srgb.icc")).map_err(failure)?;
    let linear = ToneCurve::new(1.);
    for tag in [
        TagSignature::RedTRCTag,
        TagSignature::GreenTRCTag,
        TagSignature::BlueTRCTag,
    ] {
        if !destination.write_tag(tag, Tag::ToneCurve(&linear)) {
            return Err(invalid_destination());
        }
    }
    if profile.color_space() == ColorSpaceSignature::GrayData {
        let transform = Transform::<f32, [f32; 3]>::new_flags(
            profile,
            PixelFormat::GRAY_FLT,
            &destination,
            PixelFormat::RGB_FLT,
            Intent::RelativeColorimetric,
            Flags::NO_OPTIMIZE,
        )
        .map_err(failure)?;
        if let Some(maximum) = discrete.filter(|&m| pixels.len() > m) {
            let input: Vec<f32> = (0..=maximum).map(|i| i as f32 / maximum as f32).collect();
            let mut table = vec![[0.; 3]; maximum + 1];
            transform.transform_pixels(&input, &mut table);
            for p in pixels.iter_mut() {
                *p = table[(p[0] * maximum as f32).round() as usize];
            }
        } else {
            let mut input = Vec::with_capacity(4096);
            for chunk in pixels.chunks_mut(4096) {
                input.clear();
                input.extend(chunk.iter().map(|v| v[0]));
                transform.transform_pixels(&input, chunk);
            }
        }
    } else {
        let transform = Transform::<[f32; 3], [f32; 3]>::new_flags(
            profile,
            PixelFormat::RGB_FLT,
            &destination,
            PixelFormat::RGB_FLT,
            Intent::RelativeColorimetric,
            Flags::NO_OPTIMIZE,
        )
        .map_err(failure)?;
        let separable = profile.is_matrix_shaper()
            && ![
                TagSignature::AToB0Tag,
                TagSignature::AToB1Tag,
                TagSignature::AToB2Tag,
                TagSignature::DToB0Tag,
                TagSignature::DToB1Tag,
                TagSignature::DToB2Tag,
                TagSignature::DToB3Tag,
            ]
            .iter()
            .any(|&tag| profile.has_tag(tag));
        if !discrete
            .filter(|&maximum| separable && pixels.len() > maximum)
            .is_some_and(|maximum| axis_tables(pixels, &transform, maximum))
        {
            transform.transform_in_place(pixels);
        }
    }
    // Preserve negative/wide RGB values for later gamut mapping, reject bad CMS data.
    if pixels
        .iter()
        .flatten()
        .any(|v| !v.is_finite() || v.abs() > 1e6)
    {
        return Err(Error::Policy("ICC 转换产生无效或过大数值".into()));
    }
    Ok(())
}

/// A matrix/TRC input followed by a linear destination is separable. Sample
/// full CMS axis responses at every actual source code: no interpolation, no
/// assumed gamma/white point, and no 8-bit intermediate for 16-bit sources.
fn axis_tables(
    pixels: &mut [[f32; 3]],
    transform: &Transform<[f32; 3], [f32; 3]>,
    maximum: usize,
) -> bool {
    let mut black = [[0.; 3]];
    transform.transform_in_place(&mut black);
    let mut tables = Vec::with_capacity(3);
    for channel in 0..3 {
        let mut values: Vec<[f32; 3]> = (0..=maximum)
            .map(|i| {
                let mut p = [0.; 3];
                p[channel] = i as f32 / maximum as f32;
                p
            })
            .collect();
        transform.transform_in_place(&mut values);
        // Extreme/ill-conditioned profiles retain the generic evaluation path.
        if values
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || v.abs() > 8.)
        {
            return false;
        }
        tables.push(values);
    }
    for p in pixels {
        let code = p.map(|v| (v * maximum as f32).round() as usize);
        *p = std::array::from_fn(|c| {
            (tables[0][code[0]][c] as f64
                + tables[1][code[1]][c] as f64
                + tables[2][code[2]][c] as f64
                - 2. * black[0][c] as f64) as f32
        });
    }
    true
}
fn invalid_destination() -> Error {
    Error::Policy("无法创建与输出 ICC 一致的线性工作空间".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn samples(bits: u8) -> Vec<[f32; 3]> {
        let maximum = if bits == 16 { 65535usize } else { 255usize };
        (0..=maximum)
            .map(|i| {
                [
                    i,
                    (i * 73 + 31) % (maximum + 1),
                    (i * 199 + 7) % (maximum + 1),
                ]
                .map(|v| v as f32 / maximum as f32)
            })
            .collect()
    }
    fn assert_cached_matches_generic(profile: &Profile, bits: u8) {
        let mut expected = samples(bits);
        let mut actual = expected.clone();
        to_linear(&mut expected, profile).unwrap();
        to_linear_raster(&mut actual, profile, bits).unwrap();
        let maximum = expected
            .iter()
            .flatten()
            .zip(actual.iter().flatten())
            .map(|(a, b)| (a - b).abs())
            .fold(0f32, f32::max);
        assert!(maximum < 2e-6, "{bits}-bit CMS cache error: {maximum}");
    }
    #[test]
    fn discrete_matrix_curves_match_generic_cms_at_every_source_code() {
        let srgb = embedded(include_bytes!("../assets/srgb.icc"), false).unwrap();
        let wide = rgb_profile(ADOBE_XY, 563. / 256.).unwrap();
        let mut offset = rgb_profile(SRGB_XY, 1.).unwrap();
        let curve = ToneCurve::new_tabulated(&[1024, 28000, 65535]);
        for tag in [
            TagSignature::RedTRCTag,
            TagSignature::GreenTRCTag,
            TagSignature::BlueTRCTag,
        ] {
            assert!(offset.write_tag(tag, Tag::ToneCurve(&curve)));
        }
        for bits in [8, 16] {
            for profile in [&srgb, &wide, &offset] {
                assert_cached_matches_generic(profile, bits);
            }
        }
        assert!(to_linear_raster(&mut [[0.12345; 3]], &srgb, 8).is_err());
        assert!(to_linear_raster(&mut [[0.; 3]], &srgb, 32).is_err());
    }
    #[test]
    fn gray_and_lut_override_retain_the_generic_float_contract() {
        let gray = Profile::new_gray(
            &CIExyY {
                x: 0.3127,
                y: 0.3290,
                Y: 1.,
            },
            &ToneCurve::new(2.2),
        )
        .unwrap();
        for bits in [8, 16] {
            let mut expected = samples(bits);
            for p in &mut expected {
                *p = [p[0]; 3];
            }
            let mut actual = expected.clone();
            to_linear(&mut expected, &gray).unwrap();
            to_linear_raster(&mut actual, &gray, bits).unwrap();
            assert_eq!(expected, actual);
        }
        // A profile can contain matrix/TRC tags AND a nonlinear forward LUT.
        // Build a cross-channel pipeline with a nonlinear output curve, then
        // install it as a synthetic AToB override. Treating it as separable is wrong.
        let mut profile = rgb_profile(ADOBE_XY, 1.).unwrap();
        let output = Profile::new_srgb();
        let transform = Transform::<[f32; 3], [f32; 3]>::new_flags(
            &profile,
            PixelFormat::RGB_FLT,
            &output,
            PixelFormat::RGB_FLT,
            Intent::RelativeColorimetric,
            Flags::NO_OPTIMIZE,
        )
        .unwrap();
        let link = Profile::new_device_link(&transform, 4.3, Flags::NO_OPTIMIZE).unwrap();
        let Tag::Pipeline(pipeline) = link.read_tag(TagSignature::AToB0Tag) else {
            panic!("Missing synthetic pipeline");
        };
        assert!(profile.write_tag(TagSignature::AToB0Tag, Tag::Pipeline(pipeline)));
        assert!(profile.is_matrix_shaper());
        for bits in [8, 16] {
            assert_cached_matches_generic(&profile, bits);
        }
    }
    #[test]
    fn icc_srgb_is_not_double_encoded_and_invalid_profiles_fail() {
        let original = [128. / 255., 64. / 255., 32. / 255.];
        let mut pixels = [original];
        to_linear(
            &mut pixels,
            &embedded(include_bytes!("../assets/srgb.icc"), false).unwrap(),
        )
        .unwrap();
        // This ICC stores a 16.16 parametric TRC. Its coefficients differ very
        // slightly from the ideal untagged sRGB formula; honor the actual file.
        let icc = include_bytes!("../assets/srgb.icc");
        let coefficient = |i: usize| {
            i32::from_be_bytes(icc[532 + i * 4..536 + i * 4].try_into().unwrap()) as f64 / 65536.
        };
        for c in 0..3 {
            let x = original[c] as f64;
            let expected = if x >= coefficient(4) {
                (coefficient(1) * x + coefficient(2)).powf(coefficient(0))
            } else {
                coefficient(3) * x
            };
            assert!((pixels[0][c] as f64 - expected).abs() < 2e-6);
            assert_eq!(
                encode_srgb(pixels[0][c]),
                (original[c] * 255.).round() as u8
            );
        }
        assert!(embedded(b"broken", false).is_err());
        let gray = Profile::new_gray(
            &CIExyY {
                x: 0.3127,
                y: 0.3290,
                Y: 1.,
            },
            &ToneCurve::new(2.2),
        )
        .unwrap();
        assert!(embedded(&gray.icc().unwrap(), false).is_err());
        assert!(embedded(&gray.icc().unwrap(), true).is_ok());
    }
    #[test]
    fn adobe_float_conversion_matches_analytic_matrix_and_retains_wide_values() {
        let source = [0.5f32, 0.3, 0.2];
        let mut pixels = [source, [0., 1., 0.]];
        to_linear(&mut pixels, &rgb_profile(ADOBE_XY, 563. / 256.).unwrap()).unwrap();
        // Independent RGB->XYZ/Bradford->sRGB reference, not an LCMS roundtrip.
        let matrix = [
            [1.3983557, -0.39835574, 0.],
            [0., 1., 0.],
            [0., -0.04292899, 1.04292899],
        ];
        let linear = source.map(|v| (v as f64).powf(563. / 256.));
        for c in 0..3 {
            let expected: f64 = (0..3).map(|i| matrix[c][i] * linear[i]).sum();
            assert!(
                (pixels[0][c] as f64 - expected).abs() < 3e-5,
                "{:?}",
                pixels[0]
            );
        }
        assert!(pixels[1][0] < -0.3 && pixels[1][2] < 0.);
    }
}
