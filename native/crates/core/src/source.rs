//! Small persisted identity guard; originals are always read only.
use crate::{Error, MAX_SOURCE_BYTES, Result};
use serde::{Deserialize, Serialize};
use std::{path::Path, time::UNIX_EPOCH};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceState {
    pub(crate) bytes: u64,
    modified_ns: u64,
}
impl SourceState {
    pub fn read(path: &Path) -> Result<Self> {
        let info = path.metadata()?;
        if !info.is_file() || info.len() == 0 || info.len() > MAX_SOURCE_BYTES {
            return Err(Error::Policy("原片为空、不是普通文件或超过 150 MiB".into()));
        }
        let modified_ns = info
            .modified()?
            .duration_since(UNIX_EPOCH)
            .map_err(|_| Error::Policy("原片修改时间无效".into()))?
            .as_nanos()
            .try_into()
            .map_err(|_| Error::Policy("原片修改时间超出范围".into()))?;
        Ok(Self {
            bytes: info.len(),
            modified_ns,
        })
    }
    pub fn validate(&self) -> Result<()> {
        if self.bytes == 0 || self.bytes > MAX_SOURCE_BYTES {
            Err(Error::Policy("原片绑定大小无效".into()))
        } else {
            Ok(())
        }
    }
}
