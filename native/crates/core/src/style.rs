//! Small, persistent advice queue. Stores no credentials or preview pixels.
use crate::{Error, Result, ai::Advice, develop::Parameters, editing::EditRequest, recipe::Recipe};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    time::UNIX_EPOCH,
};
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileStamp {
    bytes: u64,
    seconds: u64,
    nanos: u32,
}
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, Book) {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("target.png");
        fs::write(&path, b"original").unwrap();
        let mut book = Book::empty();
        book.reference = Some(Reference {
            path: root.path().join("ref.png"),
            parameters: Default::default(),
            goal: "自然".into(),
        });
        book.shots.push(
            Shot::new(EditRequest {
                path,
                recipe: Default::default(),
            })
            .unwrap(),
        );
        (root, book)
    }
    #[test]
    fn restart_cancels_queued_and_running_without_resuming_calls() {
        let (root, mut b) = fixture();
        let mut store = Store::new(root.path().join("data")).unwrap();
        store.load().unwrap();
        b.shots[0].status = Status::Running;
        b.shots[0].attempts = 1;
        store.save(&b).unwrap();
        drop(store);
        let mut store = Store::new(root.path().join("data")).unwrap();
        let recovered = store.load().unwrap();
        assert_eq!(recovered.shots[0].status, Status::Cancelled);
        assert_eq!(recovered.shots[0].attempts, 1);
        assert!(recovered.shots[0].advice.is_none());
        assert_eq!(fs::read(&b.shots[0].path).unwrap(), b"original");
    }
    #[test]
    fn corrupted_and_external_records_never_get_overwritten() {
        let (root, b) = fixture();
        let dir = root.path().join("data");
        let mut s = Store::new(dir.clone()).unwrap();
        s.load().unwrap();
        s.save(&b).unwrap();
        fs::write(dir.join("style-jobs.json"), b"{broken").unwrap();
        assert!(s.save(&b).is_err());
        assert_eq!(fs::read(dir.join("style-jobs.json")).unwrap(), b"{broken");
        drop(s);
        let mut s = Store::new(dir.clone()).unwrap();
        assert!(s.load().is_err());
        assert!(s.save(&b).is_err());
        assert_eq!(fs::read(dir.join("style-jobs.json")).unwrap(), b"{broken");
    }
    #[test]
    fn new_batch_preserves_exact_previous_records_even_with_identical_reference() {
        let (root, mut b) = fixture();
        let dir = root.path().join("data");
        let mut s = Store::new(dir.clone()).unwrap();
        assert!(Store::new(dir.clone()).is_err());
        s.load().unwrap();
        s.save(&b).unwrap();
        let bytes = fs::read(dir.join("style-jobs.json")).unwrap();
        b.generation += 1;
        s.save(&b).unwrap();
        let backups = fs::read_dir(&dir)
            .unwrap()
            .map(|p| p.unwrap().path())
            .filter(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("style-backup-")
            })
            .collect::<Vec<_>>();
        assert_eq!(backups.len(), 1);
        assert_eq!(fs::read(&backups[0]).unwrap(), bytes);
    }
    #[test]
    fn malformed_advice_and_oversized_selection_fail_closed() {
        let (_root, mut b) = fixture();
        b.shots[0].status = Status::Ready;
        assert!(b.validate().is_err());
        b.shots[0].status = Status::Queued;
        let p = b.shots[0].clone();
        b.shots = vec![p; 21];
        assert!(b.validate().is_err());
        let bytes = serde_json::to_vec(&b).unwrap();
        assert!(serde_json::from_slice::<Book>(&bytes).is_err());
    }
}
impl FileStamp {
    pub fn read(path: &Path) -> Result<Self> {
        let m = path.metadata()?;
        if !path.is_absolute() || !m.is_file() || m.len() == 0 || m.len() > crate::MAX_SOURCE_BYTES
        {
            return Err(Error::Policy("原片路径或容量无效".into()));
        }
        let t = m
            .modified()?
            .duration_since(UNIX_EPOCH)
            .map_err(|e| Error::Policy(e.to_string()))?;
        Ok(Self {
            bytes: m.len(),
            seconds: t.as_secs(),
            nanos: t.subsec_nanos(),
        })
    }
    pub fn matches(&self, path: &Path) -> bool {
        Self::read(path).is_ok_and(|stamp| stamp == *self)
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reference {
    pub path: PathBuf,
    pub parameters: Parameters,
    pub goal: String,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Status {
    Queued,
    Running,
    Ready,
    Applied,
    Failed,
    Cancelled,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Shot {
    pub path: PathBuf,
    pub recipe: Recipe,
    pub stamp: FileStamp,
    pub status: Status,
    pub attempts: u8,
    pub advice: Option<Advice>,
    pub error: String,
}
impl Shot {
    pub fn new(request: EditRequest) -> Result<Self> {
        Ok(Self {
            stamp: FileStamp::read(&request.path)?,
            path: request.path,
            recipe: request.recipe,
            status: Status::Queued,
            attempts: 0,
            advice: None,
            error: String::new(),
        })
    }
    pub fn request(&self) -> EditRequest {
        EditRequest {
            path: self.path.clone(),
            recipe: self.recipe.clone(),
        }
    }
}
#[derive(Clone, Default, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Book {
    pub version: u32,
    pub generation: u64,
    pub reference: Option<Reference>,
    #[serde(deserialize_with = "crate::bounded::list::<_, Shot, 20>")]
    pub shots: Vec<Shot>,
}
impl Book {
    pub fn empty() -> Self {
        Self {
            version: 1,
            ..Default::default()
        }
    }
    pub fn validate(&self) -> Result<()> {
        if self.version != 1
            || self.shots.len() > 20
            || (!self.shots.is_empty() && self.reference.is_none())
        {
            return Err(Error::Policy("风格记录版本或容量无效".into()));
        }
        if let Some(r) = &self.reference {
            r.parameters.validate()?;
            if !r.path.is_absolute() || r.goal.chars().count() > 1200 {
                return Err(Error::Policy("参考风格无效".into()));
            }
        }
        let mut seen = std::collections::HashSet::new();
        for s in &self.shots {
            s.recipe.validate()?;
            if !s.path.is_absolute()
                || !seen.insert(&s.path)
                || s.attempts > 5
                || s.error.len() > 4000
                || self.reference.as_ref().is_some_and(|r| r.path == s.path)
            {
                return Err(Error::Policy("风格照片记录无效".into()));
            }
            if let Some(advice) = &s.advice {
                Advice::parse(&serde_json::to_vec(advice)?, s.recipe.clone())?;
            }
            if matches!(s.status, Status::Ready | Status::Applied) && s.advice.is_none() {
                return Err(Error::Policy("风格建议缺失".into()));
            }
        }
        Ok(())
    }
    pub fn recover(&mut self) -> bool {
        let mut changed = false;
        for s in &mut self.shots {
            if matches!(s.status, Status::Queued | Status::Running) {
                s.error = "上次任务未完成；未自动调用模型，请手动确认继续或重试".into();
                s.status = Status::Cancelled;
                changed = true;
            }
        }
        changed
    }
}
const MAX_BYTES: u64 = 32 * 1024 * 1024;
fn snapshot(path: &Path) -> Result<Option<Vec<u8>>> {
    let mut f = match fs::File::open(path) {
        Ok(v) => v,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    if f.metadata()?.len() > MAX_BYTES {
        return Err(Error::Policy("风格记录超过32MiB".into()));
    }
    let mut out = Vec::new();
    Read::by_ref(&mut f)
        .take(MAX_BYTES + 1)
        .read_to_end(&mut out)?;
    if out.len() as u64 > MAX_BYTES {
        return Err(Error::Policy("风格记录超过读取预算".into()));
    }
    Ok(Some(out))
}
pub struct Store {
    root: PathBuf,
    _lock: fs::File,
    persisted: Option<Vec<u8>>,
    valid: bool,
}
impl Store {
    pub fn new(root: PathBuf) -> Result<Self> {
        fs::create_dir_all(&root)?;
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(root.join("style.lock"))?;
        lock.try_lock()
            .map_err(|_| Error::Policy("风格任务已由另一实例使用".into()))?;
        Ok(Self {
            root,
            _lock: lock,
            persisted: None,
            valid: false,
        })
    }
    pub fn load(&mut self) -> Result<Book> {
        self.valid = false;
        let bytes = snapshot(&self.root.join("style-jobs.json"))?;
        let mut book = match &bytes {
            Some(b) => serde_json::from_slice::<Book>(b)?,
            None => Book::empty(),
        };
        book.validate()?;
        self.persisted = bytes;
        self.valid = true;
        if book.recover() {
            self.save(&book)?;
        }
        Ok(book)
    }
    pub fn save(&mut self, book: &Book) -> Result<()> {
        book.validate()?;
        if !self.valid || snapshot(&self.root.join("style-jobs.json"))? != self.persisted {
            return Err(Error::Policy(
                "风格记录损坏或被外部修改，未覆盖原记录".into(),
            ));
        }
        let bytes = serde_json::to_vec_pretty(book)?;
        if bytes.len() as u64 > MAX_BYTES {
            return Err(Error::Policy("风格记录超过写入预算".into()));
        }
        if let Some(old) = &self.persisted {
            let previous: Book = serde_json::from_slice(old)?;
            if (previous.reference != book.reference || previous.generation != book.generation)
                && !previous.shots.is_empty()
            {
                let mut backup = tempfile::NamedTempFile::new_in(&self.root)?;
                backup.write_all(old)?;
                backup.as_file().sync_all()?;
                let name = format!(
                    "style-backup-{}.json",
                    std::time::SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_nanos()
                );
                backup
                    .persist_noclobber(self.root.join(name))
                    .map_err(|e| Error::Io(e.error))?;
            }
        }
        let mut file = tempfile::NamedTempFile::new_in(&self.root)?;
        file.write_all(&bytes)?;
        file.as_file().sync_all()?;
        file.persist(self.root.join("style-jobs.json"))
            .map_err(|e| Error::Io(e.error))?;
        self.persisted = Some(bytes);
        Ok(())
    }
}
