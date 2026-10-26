//! Durable, bounded batch queue. Each job owns a frozen recipe and source
//! fingerprint; no edit of the active photo can change a queued export.
use crate::{
    Error, MAX_SOURCE_BYTES, Result,
    editing::EditRequest,
    export::Format,
    export_job::{ExportControl, ExportReceipt, ExportSource, FullFrameCache},
    recipe::Recipe,
    session::Photo,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashSet,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{SystemTime, UNIX_EPOCH},
};

pub const MAX_JOBS: usize = 512;
const MAX_ATTEMPTS: u8 = 5;
const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Done,
    Failed,
    Interrupted,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceState {
    bytes: u64,
    modified_ns: u64,
}
impl SourceState {
    fn read(path: &Path) -> Result<Self> {
        let info = path.metadata()?;
        if !info.is_file() || info.len() == 0 || info.len() > MAX_SOURCE_BYTES {
            return Err(Error::Policy(
                "批量原片为空、不是普通文件或超过 150 MiB".into(),
            ));
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
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Job {
    pub id: u64,
    pub source: PathBuf,
    pub recipe: Recipe,
    pub source_state: SourceState,
    pub destination: PathBuf,
    pub format: Format,
    pub state: JobState,
    pub attempts: u8,
    pub last_error: String,
}
impl Job {
    pub fn run(&self, raw_dll: Option<&Path>, control: &ExportControl) -> Result<ExportReceipt> {
        let source = self.prepare()?;
        source.run(&self.destination, self.format, raw_dll, control)
    }
    pub fn run_reusing_cache(
        &self,
        raw_dll: Option<&Path>,
        control: &ExportControl,
        cache: &mut FullFrameCache,
    ) -> Result<ExportReceipt> {
        let source = self.prepare()?;
        source.run_reusing_cache(&self.destination, self.format, raw_dll, control, cache)
    }
    fn prepare(&self) -> Result<ExportSource> {
        if self.state != JobState::Running {
            return Err(Error::Policy("批量任务尚未持久化为运行中".into()));
        }
        if SourceState::read(&self.source)? != self.source_state {
            return Err(Error::Policy(
                "原片在加入批量队列后发生变化；未导出，请重新建立任务".into(),
            ));
        }
        if self.destination.try_exists()? {
            return Err(Error::Policy("目标文件已存在；批量导出不会覆盖".into()));
        }
        let source = ExportSource::new(EditRequest {
            path: self.source.clone(),
            recipe: self.recipe,
        })?;
        if SourceState::read(&self.source)? != self.source_state {
            return Err(Error::Policy("原片在启动批量导出时发生变化".into()));
        }
        Ok(source)
    }
}

#[derive(Clone, Copy, Debug)]
pub enum RecipeMode {
    PerPhoto,
    Shared(Recipe),
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Queue {
    pub version: u32,
    pub next_id: u64,
    pub paused: bool,
    pub jobs: Vec<Job>,
}
impl Default for Queue {
    fn default() -> Self {
        Self {
            version: 1,
            next_id: 1,
            paused: true,
            jobs: Vec::new(),
        }
    }
}
impl Queue {
    pub fn validate(&self) -> Result<()> {
        if self.version != 1 || self.next_id == 0 || self.jobs.len() > MAX_JOBS {
            return Err(Error::Policy("批量队列版本或容量无效".into()));
        }
        let mut ids = HashSet::new();
        let mut destinations = HashSet::new();
        for job in &self.jobs {
            if job.id == 0
                || job.id >= self.next_id
                || !ids.insert(job.id)
                || !job.source.is_absolute()
                || !job.destination.is_absolute()
                || job.source == job.destination
                || job.source_state.bytes == 0
                || job.source_state.bytes > MAX_SOURCE_BYTES
                || job.attempts > MAX_ATTEMPTS
                || (job.state == JobState::Running && job.attempts == 0)
                || job.last_error.len() > 512
            {
                return Err(Error::Policy("批量队列含无效任务；未覆盖原记录".into()));
            }
            job.recipe.validate()?;
            if !valid_extension(&job.destination, job.format)
                || !destinations.insert(destination_key(&job.destination))
            {
                return Err(Error::Policy("批量队列输出路径重复或格式不匹配".into()));
            }
        }
        Ok(())
    }
    /// Preflight every job before mutating the queue. The selected directory
    /// must already exist; outputs get stable unique names and are never reused.
    pub fn enqueue(
        &mut self,
        photos: &[Photo],
        output_dir: &Path,
        format: Format,
        mode: RecipeMode,
    ) -> Result<usize> {
        self.validate()?;
        if photos.is_empty() || self.jobs.len() + photos.len() > MAX_JOBS {
            return Err(Error::Policy("批量照片为空或队列超过 512 项".into()));
        }
        let output_dir = output_dir.canonicalize()?;
        if !output_dir.is_dir() {
            return Err(Error::Policy("批量输出目录不存在".into()));
        }
        let mut destinations: HashSet<String> = self
            .jobs
            .iter()
            .map(|job| destination_key(&job.destination))
            .collect();
        let mut additions = Vec::with_capacity(photos.len());
        let mut next_id = self.next_id;
        for (index, photo) in photos.iter().enumerate() {
            let source = photo.path.canonicalize()?;
            let source_state = SourceState::read(&source)?;
            let recipe = match mode {
                RecipeMode::PerPhoto => photo.recipe,
                RecipeMode::Shared(recipe) => recipe,
            };
            recipe.validate()?;
            let name = source
                .file_stem()
                .ok_or_else(|| Error::Policy("原片缺少文件名".into()))?
                .to_string_lossy();
            let destination =
                output_dir.join(format!("{name}-z30-{:03}.{}", index + 1, extension(format)));
            if destination == source
                || destination.try_exists()?
                || !destinations.insert(destination_key(&destination))
            {
                return Err(Error::Policy(
                    "批量目标与原片或已有任务冲突；整个批次均未加入".into(),
                ));
            }
            additions.push(Job {
                id: next_id,
                source,
                recipe,
                source_state,
                destination,
                format,
                state: JobState::Queued,
                attempts: 0,
                last_error: String::new(),
            });
            next_id = next_id
                .checked_add(1)
                .ok_or_else(|| Error::Policy("批量任务编号耗尽".into()))?;
        }
        self.jobs.extend(additions);
        self.next_id = next_id;
        self.paused = true;
        Ok(photos.len())
    }
    /// Never silently resume after a process crash/restart. A running job may
    /// have published output just before the crash, so it needs user review.
    pub fn recover(&mut self) -> bool {
        let mut changed = false;
        for job in &mut self.jobs {
            if job.state == JobState::Running {
                job.state = JobState::Interrupted;
                job.last_error = if job.destination.exists() {
                    "上次中断时目标文件已存在；请检查文件，重试不会覆盖它".into()
                } else {
                    "上次导出中断；可手动重试".into()
                };
                changed = true;
            }
        }
        if !self.paused && self.jobs.iter().any(|j| j.state == JobState::Queued) {
            self.paused = true;
            changed = true;
        }
        if changed {
            self.paused = true;
        }
        changed
    }
    pub fn resume(&mut self) {
        self.paused = false;
    }
    pub fn pause(&mut self) {
        self.paused = true;
    }
    pub fn start_next(&mut self) -> Result<Option<Job>> {
        self.validate()?;
        if self.paused {
            return Ok(None);
        }
        let Some(job) = self.jobs.iter_mut().find(|j| j.state == JobState::Queued) else {
            return Ok(None);
        };
        if job.attempts >= MAX_ATTEMPTS {
            job.state = JobState::Failed;
            job.last_error = "已达到五次尝试上限；请新建批次".into();
            return Ok(None);
        }
        job.attempts += 1;
        job.state = JobState::Running;
        job.last_error.clear();
        Ok(Some(job.clone()))
    }
    pub fn finish(
        &mut self,
        id: u64,
        result: &Result<ExportReceipt>,
        was_cancelled: bool,
    ) -> Result<()> {
        let job = self
            .jobs
            .iter_mut()
            .find(|job| job.id == id && job.state == JobState::Running)
            .ok_or_else(|| Error::Policy("批量结果不对应运行中的任务".into()))?;
        match result {
            Ok(_) => {
                job.state = JobState::Done;
                job.last_error.clear();
            }
            Err(error) => {
                job.state = if was_cancelled {
                    JobState::Cancelled
                } else {
                    JobState::Failed
                };
                job.last_error = error.to_string().chars().take(160).collect();
            }
        }
        Ok(())
    }
    pub fn cancel_pending(&mut self) -> usize {
        self.paused = true;
        let mut changed = 0;
        for job in &mut self.jobs {
            if job.state == JobState::Queued {
                job.state = JobState::Cancelled;
                job.last_error = "用户取消待处理任务".into();
                changed += 1;
            }
        }
        changed
    }
    pub fn retry_failed(&mut self) -> usize {
        self.paused = true;
        let mut changed = 0;
        for job in &mut self.jobs {
            if matches!(
                job.state,
                JobState::Failed | JobState::Interrupted | JobState::Cancelled
            ) && job.attempts < MAX_ATTEMPTS
                && !job.destination.exists()
            {
                job.state = JobState::Queued;
                job.last_error.clear();
                changed += 1;
            }
        }
        changed
    }
    pub fn clear_finished(&mut self) -> usize {
        let before = self.jobs.len();
        self.jobs
            .retain(|job| matches!(job.state, JobState::Queued | JobState::Running));
        before - self.jobs.len()
    }
    pub fn counts(&self) -> Counts {
        let mut counts = Counts::default();
        counts.total = self.jobs.len();
        for job in &self.jobs {
            match job.state {
                JobState::Queued => counts.queued += 1,
                JobState::Running => counts.running += 1,
                JobState::Done => counts.done += 1,
                JobState::Failed => counts.failed += 1,
                JobState::Interrupted => counts.interrupted += 1,
                JobState::Cancelled => counts.cancelled += 1,
            }
        }
        counts
    }
}

#[derive(Default, Debug, PartialEq, Eq)]
pub struct Counts {
    pub total: usize,
    pub queued: usize,
    pub running: usize,
    pub done: usize,
    pub failed: usize,
    pub interrupted: usize,
    pub cancelled: usize,
}

fn extension(format: Format) -> &'static str {
    match format {
        Format::Jpeg => "jpg",
        Format::Png => "png",
        Format::Tiff16 => "tif",
    }
}
fn valid_extension(path: &Path, format: Format) -> bool {
    path.extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case(extension(format)))
}
fn destination_key(path: &Path) -> String {
    let text = path.to_string_lossy();
    if cfg!(windows) {
        text.to_lowercase()
    } else {
        text.into_owned()
    }
}

pub struct Store {
    root: PathBuf,
    _lock: fs::File,
    persisted: Mutex<Option<Vec<u8>>>,
    validated: AtomicBool,
}
impl Store {
    pub fn new(root: PathBuf) -> Result<Self> {
        fs::create_dir_all(&root)?;
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(root.join("batch.lock"))?;
        lock.try_lock()
            .map_err(|_| Error::Policy("此批量队列已由另一个实例使用".into()))?;
        Ok(Self {
            validated: AtomicBool::new(!root.join("batch-jobs.json").exists()),
            root,
            _lock: lock,
            persisted: Mutex::new(None),
        })
    }
    pub fn load(&self) -> Result<Queue> {
        self.validated.store(false, Ordering::Release);
        let path = self.root.join("batch-jobs.json");
        let bytes = read_snapshot(&path)?;
        let mut queue = match &bytes {
            Some(bytes) => serde_json::from_slice(bytes)?,
            None => Queue::default(),
        };
        queue.validate()?;
        *self.persisted.lock().unwrap_or_else(|e| e.into_inner()) = bytes;
        self.validated.store(true, Ordering::Release);
        if queue.recover() {
            self.save(&queue)?;
        }
        Ok(queue)
    }
    pub fn save(&self, queue: &Queue) -> Result<()> {
        queue.validate()?;
        if !self.validated.load(Ordering::Acquire) {
            return Err(Error::Policy("批量记录未通过读取验证；已阻止覆盖".into()));
        }
        let path = self.root.join("batch-jobs.json");
        let mut persisted = self.persisted.lock().unwrap_or_else(|e| e.into_inner());
        if read_snapshot(&path)? != *persisted {
            return Err(Error::Policy("批量记录被外部修改；未覆盖磁盘版本".into()));
        }
        let bytes = serde_json::to_vec_pretty(queue)?;
        if bytes.len() as u64 > MAX_FILE_BYTES {
            return Err(Error::Policy("批量队列超过 2 MiB，未写入".into()));
        }
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let temp = self
            .root
            .join(format!("batch-{}-{stamp}.part", std::process::id()));
        let result = (|| {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            drop(file);
            fs::rename(&temp, &path)?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temp);
        } else {
            *persisted = Some(bytes);
        }
        result
    }
}
fn read_snapshot(path: &Path) -> Result<Option<Vec<u8>>> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let mut bytes = Vec::new();
    file.take(MAX_FILE_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_FILE_BYTES {
        return Err(Error::Policy("批量记录超过 2 MiB；未覆盖旧文件".into()));
    }
    Ok(Some(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(root: &Path, name: &str, colour: [u8; 3]) -> Photo {
        let path = root.join(name);
        image::RgbImage::from_pixel(30, 20, image::Rgb(colour))
            .save(&path)
            .unwrap();
        Photo {
            path,
            rating: 0,
            flag: 0,
            recipe: Recipe::default(),
        }
    }
    #[test]
    fn two_photos_keep_their_own_recipes_and_never_change_originals() {
        let dir = tempfile::tempdir().unwrap();
        let originals = dir.path().join("photos");
        let exports = dir.path().join("exports");
        fs::create_dir_all(&originals).unwrap();
        fs::create_dir_all(&exports).unwrap();
        let mut photos = vec![
            fixture(&originals, "a.png", [120, 80, 40]),
            fixture(&originals, "b.png", [120, 80, 40]),
        ];
        photos[0].recipe.parameters.exposure = 1.;
        photos[1].recipe.parameters.exposure = -1.;
        let source_bytes: Vec<_> = photos.iter().map(|p| fs::read(&p.path).unwrap()).collect();
        let store = Store::new(dir.path().join("data")).unwrap();
        let mut queue = store.load().unwrap();
        assert_eq!(
            queue
                .enqueue(&photos, &exports, Format::Tiff16, RecipeMode::PerPhoto)
                .unwrap(),
            2
        );
        store.save(&queue).unwrap();
        queue.resume();
        store.save(&queue).unwrap();
        for _ in 0..2 {
            let job = queue.start_next().unwrap().unwrap();
            store.save(&queue).unwrap();
            let result = job.run(None, &ExportControl::default());
            assert!(result.is_ok());
            queue.finish(job.id, &result, false).unwrap();
            store.save(&queue).unwrap();
        }
        assert_eq!(queue.counts().done, 2);
        let first = image::open(&queue.jobs[0].destination).unwrap().to_rgb16();
        let second = image::open(&queue.jobs[1].destination).unwrap().to_rgb16();
        assert_ne!(first.get_pixel(0, 0), second.get_pixel(0, 0));
        for (photo, bytes) in photos.iter().zip(source_bytes) {
            assert_eq!(fs::read(&photo.path).unwrap(), bytes);
        }
        assert!(
            queue
                .enqueue(&photos, &exports, Format::Tiff16, RecipeMode::PerPhoto)
                .is_err()
        );
        assert_eq!(queue.jobs.len(), 2);
    }
    #[test]
    fn recovery_never_auto_resumes_and_retry_will_not_replace_existing_output() {
        let dir = tempfile::tempdir().unwrap();
        let photo = fixture(dir.path(), "source.png", [10, 50, 100]);
        let export = dir.path().join("exports");
        fs::create_dir_all(&export).unwrap();
        let root = dir.path().join("data");
        let store = Store::new(root.clone()).unwrap();
        let mut queue = store.load().unwrap();
        queue
            .enqueue(&[photo], &export, Format::Png, RecipeMode::PerPhoto)
            .unwrap();
        queue.resume();
        let job = queue.start_next().unwrap().unwrap();
        store.save(&queue).unwrap();
        fs::write(&job.destination, b"already published").unwrap();
        drop(store);
        let reopened = Store::new(root).unwrap();
        let mut recovered = reopened.load().unwrap();
        assert!(recovered.paused);
        assert_eq!(recovered.jobs[0].state, JobState::Interrupted);
        assert_eq!(recovered.retry_failed(), 0);
        assert_eq!(fs::read(&job.destination).unwrap(), b"already published");
        assert!(recovered.jobs[0].last_error.contains("已存在"));
    }
    #[test]
    fn changed_source_and_corrupt_queue_fail_closed() {
        let dir = tempfile::tempdir().unwrap();
        let photo = fixture(dir.path(), "source.png", [10, 50, 100]);
        let output = dir.path().join("out");
        fs::create_dir_all(&output).unwrap();
        let root = dir.path().join("data");
        let store = Store::new(root.clone()).unwrap();
        let mut queue = store.load().unwrap();
        queue
            .enqueue(&[photo.clone()], &output, Format::Png, RecipeMode::PerPhoto)
            .unwrap();
        queue.resume();
        let job = queue.start_next().unwrap().unwrap();
        store.save(&queue).unwrap();
        fs::OpenOptions::new()
            .append(true)
            .open(&photo.path)
            .unwrap()
            .write_all(b"changed")
            .unwrap();
        assert!(job.run(None, &ExportControl::default()).is_err());
        assert!(!job.destination.exists());
        fs::write(root.join("batch-jobs.json"), b"{invalid").unwrap();
        assert!(store.save(&queue).is_err());
        drop(store);
        let reopened = Store::new(root.clone()).unwrap();
        assert!(reopened.load().is_err());
        assert!(reopened.save(&Queue::default()).is_err());
        assert_eq!(fs::read(root.join("batch-jobs.json")).unwrap(), b"{invalid");
    }
}
