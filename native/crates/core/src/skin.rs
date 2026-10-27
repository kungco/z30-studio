//! Immutable, source-bound confidence gate; image pixels stay floating point.
use crate::{
    Error, Result,
    portrait::{Binding, Portrait},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};
use std::sync::Arc;

pub const SIDE: usize = 256;

#[derive(Clone, Debug)]
pub struct SkinMask {
    binding: Binding,
    weights: Arc<[u8]>,
    digest: [u8; 32],
}
impl PartialEq for SkinMask {
    fn eq(&self, other: &Self) -> bool {
        self.binding == other.binding
            && (Arc::ptr_eq(&self.weights, &other.weights) || self.digest == other.digest)
    }
}
fn checksum(binding: &Binding, weights: &[u8]) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(b"Z30SkinMask-v1\0");
    hash.update(serde_json::to_vec(binding).expect("finite integer binding"));
    hash.update(weights);
    hash.finalize().into()
}
impl Serialize for SkinMask {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        #[derive(Serialize)]
        struct Wire<'a> {
            version: u32,
            binding: &'a Binding,
            encoding: &'static str,
            weights: String,
            sha256: String,
        }
        let mut runs = Vec::new();
        let mut position = 0;
        while position < self.weights.len() {
            let value = self.weights[position];
            let mut count = 1usize;
            while position + count < self.weights.len()
                && count < 65535
                && self.weights[position + count] == value
            {
                count += 1;
            }
            runs.extend_from_slice(&(count as u16).to_le_bytes());
            runs.push(value);
            position += count;
            if runs.len() >= self.weights.len() {
                break;
            }
        }
        let (encoding, payload) =
            if position == self.weights.len() && runs.len() < self.weights.len() {
                ("rle8", runs.as_slice())
            } else {
                ("raw8", self.weights.as_ref())
            };
        Wire {
            version: 1,
            binding: &self.binding,
            encoding,
            weights: STANDARD.encode(payload),
            sha256: self.digest.iter().map(|b| format!("{b:02x}")).collect(),
        }
        .serialize(serializer)
    }
}
impl<'de> Deserialize<'de> for SkinMask {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        fn decode<'de, D: Deserializer<'de>>(
            deserializer: D,
        ) -> std::result::Result<Vec<u8>, D::Error> {
            struct Encoded;
            impl serde::de::Visitor<'_> for Encoded {
                type Value = Vec<u8>;
                fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                    f.write_str("a bounded 256x256 skin mask")
                }
                fn visit_str<E: serde::de::Error>(
                    self,
                    value: &str,
                ) -> std::result::Result<Vec<u8>, E> {
                    if value.is_empty() || value.len() > 87384 {
                        return Err(E::custom("皮肤蒙版编码长度无效"));
                    }
                    let decoded = STANDARD.decode(value).map_err(E::custom)?;
                    if decoded.len() > SIDE * SIDE {
                        return Err(E::custom("皮肤蒙版编码数据过长"));
                    }
                    Ok(decoded)
                }
            }
            deserializer.deserialize_str(Encoded)
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Wire {
            version: u32,
            binding: Binding,
            #[serde(default)]
            encoding: Option<String>,
            #[serde(deserialize_with = "decode")]
            weights: Vec<u8>,
            sha256: String,
        }
        let data = Wire::deserialize(deserializer)?;
        if data.version != 1 {
            return Err(serde::de::Error::custom("未知皮肤蒙版版本"));
        }
        let weights = match data.encoding.as_deref().unwrap_or("raw8") {
            "raw8" => data.weights,
            "rle8" => {
                if data.weights.len() % 3 != 0 {
                    return Err(serde::de::Error::custom("皮肤蒙版压缩数据截断"));
                }
                let mut decoded = Vec::with_capacity(SIDE * SIDE);
                for run in data.weights.chunks_exact(3) {
                    let count = u16::from_le_bytes([run[0], run[1]]) as usize;
                    if count == 0 || count > SIDE * SIDE - decoded.len() {
                        return Err(serde::de::Error::custom("皮肤蒙版展开长度无效"));
                    }
                    decoded.resize(decoded.len() + count, run[2]);
                }
                decoded
            }
            _ => return Err(serde::de::Error::custom("未知皮肤蒙版编码")),
        };
        let mask = SkinMask::new(data.binding, weights).map_err(serde::de::Error::custom)?;
        let expected: String = mask.digest.iter().map(|b| format!("{b:02x}")).collect();
        if data.sha256 != expected {
            return Err(serde::de::Error::custom("皮肤蒙版校验失败；未退回几何柔化"));
        }
        Ok(mask)
    }
}
impl SkinMask {
    pub fn new(binding: Binding, weights: Vec<u8>) -> Result<Self> {
        binding.validate()?;
        if weights.len() != SIDE * SIDE {
            return Err(Error::Policy("皮肤蒙版尺寸无效".into()));
        }
        Ok(Self {
            binding,
            digest: checksum(&binding, &weights),
            weights: weights.into(),
        })
    }
    pub fn weights(&self) -> &[u8] {
        &self.weights
    }
    pub fn fingerprint(&self) -> [u8; 32] {
        self.digest
    }
    pub fn check_portrait(&self, portrait: &Portrait) -> Result<()> {
        if portrait.binding != Some(self.binding) || portrait.face.is_none() {
            return Err(Error::Policy("皮肤蒙版与人像原片绑定不一致，未应用".into()));
        }
        Ok(())
    }
    /// Pixel-centre coordinates in any preview/export size of the same oriented
    /// image. No extra rotation, letterboxing, or input-image quantization.
    pub fn sample(&self, x: f64, y: f64, width: u32, height: u32) -> f32 {
        if width == 0
            || height == 0
            || !x.is_finite()
            || !y.is_finite()
            || x < 0.
            || y < 0.
            || x >= width as f64
            || y >= height as f64
        {
            return 0.;
        }
        let fx = (x * SIDE as f64 / width as f64 - 0.5).clamp(0., (SIDE - 1) as f64);
        let fy = (y * SIDE as f64 / height as f64 - 0.5).clamp(0., (SIDE - 1) as f64);
        let (x0, y0) = (fx.floor() as usize, fy.floor() as usize);
        let (x1, y1) = ((x0 + 1).min(SIDE - 1), (y0 + 1).min(SIDE - 1));
        let (dx, dy) = ((fx - x0 as f64) as f32, (fy - y0 as f64) as f32);
        let a = self.weights[y0 * SIDE + x0] as f32;
        let b = self.weights[y0 * SIDE + x1] as f32;
        let c = self.weights[y1 * SIDE + x0] as f32;
        let d = self.weights[y1 * SIDE + x1] as f32;
        let top = a + (b - a) * dx;
        (top + (c + (d - c) * dx - top) * dy) / 255.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalized_sampling_preserves_non_square_preview_full_coordinates() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("sample");
        std::fs::write(&path, b"sample").unwrap();
        let binding = Binding::capture(&path, 6016, 4000).unwrap();
        let weights = (0..SIDE * SIDE)
            .map(|i| ((i % SIDE + 3 * (i / SIDE)) % 256) as u8)
            .collect();
        let mask = SkinMask::new(binding, weights).unwrap();
        for (x, y) in [(0.1, 0.2), (0.5, 0.5), (0.9, 0.8)] {
            let full = mask.sample(x * 6016., y * 4000., 6016, 4000);
            assert_eq!(full, mask.sample(x * 3008., y * 2000., 3008, 2000));
            assert_eq!(full, mask.sample(x * 1504., y * 1000., 1504, 1000));
        }
        assert_eq!(mask.sample(f64::NAN, 2., 10, 10), 0.);
        assert_eq!(mask.sample(1., 1., 0, 10), 0.);
        assert_eq!(mask.sample(-1., 1., 10, 10), 0.);
        assert_eq!(mask.sample(10., 1., 10, 10), 0.);
        assert!(SkinMask::new(binding, vec![0; 3]).is_err());
    }
}
