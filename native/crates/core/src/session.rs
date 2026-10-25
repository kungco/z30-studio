use crate::{Error, MAX_PHOTOS, MAX_SOURCE_BYTES, Result};
use serde::{Deserialize, Serialize};
use std::{fs, io::Write, path::{Path, PathBuf}, time::{SystemTime, UNIX_EPOCH}};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Photo {
    pub path: PathBuf,
    #[serde(default)]
    pub rating: u8,
    #[serde(default)]
    pub flag: i8,
}

impl Photo {
    pub fn name(&self) -> String {
        self.path.file_name().unwrap_or_default().to_string_lossy().into_owned()
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Session {
    pub version: u32,
    pub photos: Vec<Photo>,
    pub selected: Option<usize>,
}

impl Default for Session {
    fn default() -> Self { Self { version: 1, photos: vec![], selected: None } }
}

impl Session {
    pub fn validate(&self) -> Result<()> {
        if self.version != 1 || self.photos.len() > MAX_PHOTOS || self.selected.is_some_and(|i| i >= self.photos.len()) {
            return Err(Error::Policy("原生草稿版本、容量或选中照片无效；未覆盖旧草稿".into()));
        }
        if self.photos.iter().any(|p| p.rating > 5 || !(-1..=1).contains(&p.flag) || !p.path.is_absolute()) {
            return Err(Error::Policy("原生草稿包含无效评分、旗标或文件路径".into()));
        }
        Ok(())
    }

    pub fn add(&mut self, path: &Path) -> Result<bool> {
        let extension = path.extension().unwrap_or_default().to_string_lossy().to_lowercase();
        if !["nef", "jpg", "jpeg", "png"].contains(&extension.as_str()) {
            return Err(Error::Policy("仅接受 NEF、JPEG、PNG 原片".into()));
        }
        let path = path.canonicalize()?;
        if self.photos.iter().any(|p| p.path == path) { return Ok(false); }
        let info = path.metadata()?;
        if !info.is_file() || info.len() == 0 || info.len() > MAX_SOURCE_BYTES {
            return Err(Error::Policy("文件为空、不是普通文件或超过 150 MiB".into()));
        }
        if self.photos.len() >= MAX_PHOTOS { return Err(Error::Policy("当前项目上限为 256 张".into())); }
        self.photos.push(Photo { path, rating: 0, flag: 0 });
        self.selected.get_or_insert(0);
        Ok(true)
    }
}

pub struct SessionStore { root: PathBuf }

impl SessionStore {
    pub fn new(root: PathBuf) -> Self { Self { root } }
    pub fn load(&self) -> Result<Session> {
        let path = self.root.join("session.json");
        if !path.exists() { return Ok(Session::default()); }
        if path.metadata()?.len() > 2 * 1024 * 1024 { return Err(Error::Policy("草稿超过安全读取大小".into())); }
        let session: Session = serde_json::from_slice(&fs::read(path)?)?;
        session.validate()?;
        Ok(session)
    }
    pub fn save(&self, session: &Session) -> Result<()> {
        session.validate()?;
        fs::create_dir_all(&self.root)?;
        let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
        let temp = self.root.join(format!("session-{}-{stamp}.part", std::process::id()));
        let result = (|| {
            let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temp)?;
            file.write_all(&serde_json::to_vec_pretty(session)?)?;
            file.sync_all()?;
            drop(file);
            fs::rename(&temp, self.root.join("session.json"))?;
            Ok(())
        })();
        if result.is_err() { let _ = fs::remove_file(&temp); }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn autosave_is_versioned_and_never_touches_originals() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("原始 照片.png");fs::write(&source, b"original bytes").unwrap();
        let store = SessionStore::new(dir.path().join("draft"));
        let mut session = Session::default();assert!(session.add(&source).unwrap());assert!(!session.add(&source).unwrap());
        store.save(&session).unwrap();session.photos[0].rating = 5;store.save(&session).unwrap();
        assert_eq!(store.load().unwrap(), session);assert_eq!(fs::read(&source).unwrap(), b"original bytes");
        session.version = 999;assert!(store.save(&session).is_err());assert_eq!(store.load().unwrap().version, 1);
    }
    #[test]
    fn rejects_bad_indices_flags_and_truncated_json_without_replacing_it() {
        let dir = tempfile::tempdir().unwrap();let store = SessionStore::new(dir.path().into());
        let mut invalid = Session::default();invalid.selected = Some(0);assert!(store.save(&invalid).is_err());
        fs::write(dir.path().join("session.json"), b"{broken").unwrap();assert!(store.load().is_err());
        assert_eq!(fs::read(dir.path().join("session.json")).unwrap(), b"{broken");
    }
}
