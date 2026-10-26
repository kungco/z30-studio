//! Bounded nine-control AI advice. Model text never becomes an edit until validated.
use crate::{
    Error, Result,
    develop::Parameters,
    recipe::{Recipe, WhiteBalance},
};
use serde::{Deserialize, Serialize};

pub const AUTO_GOAL: &str = "自动判断画面类型、曝光、动态范围和偏色，完成自然、有节制的全局调色。保留场景氛围、肤色和现有风格；黑白画面保持黑白，避免过曝、过饱和和过度锐化。以当前调整为起点，已经合适则保持参数，不为修改而修改；不要声称恢复丢失细节。";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Advice {
    pub title: String,
    pub summary: String,
    pub observations: Vec<String>,
    pub adjustments: Adjustments,
    pub limitations: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Adjustments {
    pub exposure: f32,
    pub contrast: f32,
    pub highlights: f32,
    pub shadows: f32,
    pub temperature: f32,
    pub tint: f32,
    pub saturation: f32,
    pub vibrance: f32,
    pub sharpen: f32,
    pub wb: WhiteBalance,
}

impl Adjustments {
    pub fn parameters(self) -> Parameters {
        Parameters {
            exposure: self.exposure,
            contrast: self.contrast,
            highlights: self.highlights,
            shadows: self.shadows,
            temperature: self.temperature,
            tint: self.tint,
            saturation: self.saturation,
            vibrance: self.vibrance,
            sharpen: self.sharpen,
        }
    }
}

impl Advice {
    pub fn parse(bytes: &[u8], base: Recipe) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > 50_000 {
            return Err(Error::Policy("模型建议为空或超过 50 KiB".into()));
        }
        let advice: Self = serde_json::from_slice(bytes)?;
        if [&advice.title, &advice.summary, &advice.limitations]
            .iter()
            .any(|s| s.len() > 2000)
            || advice.observations.len() > 8
            || advice.observations.iter().any(|s| s.len() > 1000)
        {
            return Err(Error::Policy("模型建议说明过长".into()));
        }
        if advice.adjustments.wb != base.white_balance {
            return Err(Error::Policy("模型试图更改白平衡模式；没有应用建议".into()));
        }
        advice.adjustments.parameters().validate()?;
        Ok(advice)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    pub strength: f32,
    pub exposure_limit: f32,
    pub color_limit: f32,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            strength: 100.,
            exposure_limit: 1.,
            color_limit: 25.,
        }
    }
}
impl Options {
    pub fn validate(self) -> Result<Self> {
        if !self.strength.is_finite()
            || !(0.0..=100.).contains(&self.strength)
            || !self.exposure_limit.is_finite()
            || !(0.0..=2.).contains(&self.exposure_limit)
            || !self.color_limit.is_finite()
            || !(0.0..=60.).contains(&self.color_limit)
        {
            return Err(Error::Policy("AI 强度或参数限制无效".into()));
        }
        Ok(self)
    }
    pub fn constrain(self, base: Parameters, proposed: Parameters) -> Result<Parameters> {
        self.validate()?;
        base.validate()?;
        proposed.validate()?;
        let gain = self.strength / 100.;
        let mut out = base;
        macro_rules! bounded {
            ($key:ident, $limit:expr) => {
                out.$key = base.$key + (proposed.$key - base.$key).clamp(-$limit, $limit) * gain;
            };
        }
        bounded!(exposure, self.exposure_limit);
        bounded!(contrast, 40.);
        bounded!(highlights, 40.);
        bounded!(shadows, 40.);
        bounded!(temperature, self.color_limit);
        bounded!(tint, self.color_limit);
        bounded!(saturation, self.color_limit);
        bounded!(vibrance, self.color_limit);
        bounded!(sharpen, 20.);
        out.validate()?;
        Ok(out)
    }
}

pub fn blend(base: Parameters, candidate: Parameters, fraction: f32) -> Result<Parameters> {
    if !fraction.is_finite() || !(0.0..=1.).contains(&fraction) {
        return Err(Error::Policy("AI 回退强度无效".into()));
    }
    // Quality fallback starts from the already bounded proposal. Do not clamp
    // it again to a different exposure/color limit.
    let mut out = base;
    macro_rules! lerp {
        ($key:ident) => {
            out.$key = base.$key + (candidate.$key - base.$key) * fraction;
        };
    }
    lerp!(exposure);
    lerp!(contrast);
    lerp!(highlights);
    lerp!(shadows);
    lerp!(temperature);
    lerp!(tint);
    lerp!(saturation);
    lerp!(vibrance);
    lerp!(sharpen);
    out.validate()?;
    Ok(out)
}

pub fn schema() -> serde_json::Value {
    let number = serde_json::json!({"type":"number"});
    let mut p = serde_json::Map::new();
    for field in [
        "exposure",
        "contrast",
        "highlights",
        "shadows",
        "temperature",
        "tint",
        "saturation",
        "vibrance",
        "sharpen",
    ] {
        p.insert(field.to_owned(), number.clone());
    }
    p.insert(
        "wb".into(),
        serde_json::json!({"type":"string","enum":["camera","auto","daylight"]}),
    );
    serde_json::json!({
        "type":"object","additionalProperties":false,
        "properties":{
            "title":{"type":"string"},"summary":{"type":"string"},
            "observations":{"type":"array","items":{"type":"string"}},
            "adjustments":{"type":"object","additionalProperties":false,"properties":p,
                "required":["exposure","contrast","highlights","shadows","temperature","tint","saturation","vibrance","sharpen","wb"]},
            "limitations":{"type":"string"}
        },
        "required":["title","summary","observations","adjustments","limitations"]
    })
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Metrics {
    pub channel_clip: f32,
    pub white_pixels: f32,
    pub highlights: f32,
    pub tone_span: f32,
    pub chroma95: f32,
}

pub fn metrics(rgb: &[u8], width: u32, height: u32) -> Result<Metrics> {
    if width == 0 || height == 0 || rgb.len() != width as usize * height as usize * 3 {
        return Err(Error::Policy("AI 画质检测预览尺寸无效".into()));
    }
    let count = (width as usize) * (height as usize);
    let mut clipped = 0usize;
    let mut white = 0usize;
    let mut highlights = 0usize;
    let step = (width.max(height) as usize).div_ceil(512);
    let mut luminances = Vec::with_capacity((count / step.max(1)).min(300_000));
    let mut chromas = Vec::with_capacity(luminances.capacity());
    for (i, p) in rgb.chunks_exact(3).enumerate() {
        let high = *p.iter().max().unwrap();
        let low = *p.iter().min().unwrap();
        if high == 255 {
            clipped += 1;
        }
        if low == 255 {
            white += 1;
        }
        let luma = (0.2126 * p[0] as f32 + 0.7152 * p[1] as f32 + 0.0722 * p[2] as f32) / 255.;
        if luma >= 0.97 {
            highlights += 1;
        }
        if i % step.max(1) == 0 {
            luminances.push(luma);
            chromas.push((high - low) as f32 / 255.);
        }
    }
    luminances.sort_by(f32::total_cmp);
    chromas.sort_by(f32::total_cmp);
    let quantile = |v: &[f32], q: f32| v[((v.len() - 1) as f32 * q).round() as usize];
    Ok(Metrics {
        channel_clip: clipped as f32 / count as f32,
        white_pixels: white as f32 / count as f32,
        highlights: highlights as f32 / count as f32,
        tone_span: quantile(&luminances, 0.95) - quantile(&luminances, 0.05),
        chroma95: quantile(&chromas, 0.95),
    })
}

pub fn degradation(before: Metrics, after: Metrics) -> Vec<&'static str> {
    let mut reasons = Vec::new();
    if after.channel_clip - before.channel_clip > (0.02f32).max(before.channel_clip * 0.75) {
        reasons.push("输出通道到顶比例明显增加");
    }
    if after.white_pixels - before.white_pixels > 0.03
        || after.highlights - before.highlights > 0.30
    {
        reasons.push("大面积亮部趋于过白");
    }
    if before.tone_span > 0.08 && after.tone_span < before.tone_span * 0.65 {
        reasons.push("亮度层次范围显著缩小");
    }
    if after.chroma95 > before.chroma95 * 1.5 + 0.035 {
        reasons.push("色度提升过强");
    }
    reasons
}

#[cfg(test)]
mod tests {
    use super::*;
    fn valid() -> Vec<u8> {
        r#"{"title":"自然","summary":"保守","observations":["偏暗"],"adjustments":{"exposure":0.4,"contrast":0,"highlights":-10,"shadows":12,"temperature":-4,"tint":0,"saturation":-3,"vibrance":0,"sharpen":0,"wb":"camera"},"limitations":"仅全局调整"}"#.as_bytes().to_vec()
    }
    #[test]
    fn strict_advice_rejects_missing_unknown_nonfinite_and_wb_change() {
        let base = Recipe::default();
        let correct = Advice::parse(&valid(), base).unwrap();
        assert_eq!(correct.adjustments.parameters().exposure, 0.4);
        for bytes in [
            String::from_utf8(valid())
                .unwrap()
                .replace("\"exposure\":0.4,", ""),
            String::from_utf8(valid())
                .unwrap()
                .replace("\"summary\"", "\"extra\":1,\"summary\""),
            String::from_utf8(valid())
                .unwrap()
                .replace("\"exposure\":0.4", "\"exposure\":true"),
            String::from_utf8(valid())
                .unwrap()
                .replace("\"exposure\":0.4", "\"exposure\":4.1"),
            String::from_utf8(valid())
                .unwrap()
                .replace("\"wb\":\"camera\"", "\"wb\":\"auto\""),
        ] {
            assert!(Advice::parse(bytes.as_bytes(), base).is_err());
        }
        assert!(Advice::parse(&vec![b'x'; 50_001], base).is_err());
        assert!(
            Options {
                strength: f32::NAN,
                ..Options::default()
            }
            .validate()
            .is_err()
        );
    }
    #[test]
    fn bounded_options_and_quality_flags_real_clipping() {
        let base = Parameters::default();
        let suggested = Parameters {
            exposure: 4.,
            saturation: 100.,
            sharpen: 100.,
            ..base
        };
        let p = Options {
            strength: 50.,
            ..Options::default()
        }
        .constrain(base, suggested)
        .unwrap();
        assert_eq!((p.exposure, p.saturation, p.sharpen), (0.5, 12.5, 10.));
        assert_eq!(blend(base, p, 0.).unwrap(), base);
        let mut normal = vec![80u8; 100 * 100 * 3];
        let original = metrics(&normal, 100, 100).unwrap();
        for pixel in normal.chunks_exact_mut(3).take(2000) {
            pixel.copy_from_slice(&[255, 255, 255]);
        }
        let blown = metrics(&normal, 100, 100).unwrap();
        assert!(
            degradation(original, blown)
                .iter()
                .any(|v| v.contains("到顶"))
        );
        assert!(metrics(&normal, 99, 100).is_err());
    }
}
