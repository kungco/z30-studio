//! Opt-in file markers for process/latency acceptance, with no server or IPC listener.
use std::{
    fs,
    io::{self, Write},
    path::PathBuf,
    time::Instant,
};

#[derive(Default)]
pub struct Harness {
    ready: Option<PathBuf>,
    stop: Option<PathBuf>,
    wrote_ready: bool,
    wrote_preview: bool,
    control: Option<PathBuf>,
    next_command: u32,
}
#[derive(serde::Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Snapshot,
    RepairTool{mode:i32},
    RepairCanvas{x:f32,y:f32},
    RepairSelect{index:i32},
    RepairAdjust{parameter:String,value:f32},
    RepairPick,
    RepairSuggest,
    RepairRemove,
    RepairClear,
    RepairCancel,
    RepairMarks{visible:bool},
    About{visible:bool},
    View{zoom:f32,pan_x:f32,pan_y:f32},
    Adjust {
        parameter: String,
        value: f32,
    },
    Select {
        index: i32,
    },
    Undo,
    Redo,
    Reset,
    CancelPreview,
    RetryPreview,
    CancelExport,
    BatchStart {
        directory: PathBuf,
        format: i32,
        mode: i32,
    },
    BatchPauseResume,
    BatchCancel,
    BatchRetry,
    BatchClear,
    FaceAssets { directory: PathBuf },
    SkinAssets { directory: PathBuf },
    SegmentSkin,
    ShowSkinMask { visible: bool },
    DetectFace,
    CancelFace,
    NextFace,
    ClearPortrait,
    AiStart {
        mode: Option<i32>,
        strength: Option<f32>,
        goal: Option<String>,
        provider: Option<i32>,
        base_url: Option<String>,
        model: Option<String>,
        key: Option<String>,
    },
    AiCancel,
    AiApply,
    AiDismiss,
    Compare {
        enabled: bool,
    },
    Export {
        destination: PathBuf,
        format: i32,
    },
}
impl Harness {
    pub fn arguments() -> io::Result<(Self, Vec<PathBuf>)> {
        let mut harness = Self::default();
        let mut photos = vec![];
        let mut args = std::env::args_os().skip(1);
        while let Some(arg) = args.next() {
            match arg.to_str() {
                Some("--qa-ready") => {
                    harness.ready = Some(
                        args.next()
                            .ok_or_else(|| io::Error::other("Missing ready marker path"))?
                            .into(),
                    )
                }
                Some("--qa-stop") => {
                    harness.stop = Some(
                        args.next()
                            .ok_or_else(|| io::Error::other("Missing stop marker path"))?
                            .into(),
                    )
                }
                Some("--qa-control") => {
                    harness.control = Some(
                        args.next()
                            .ok_or_else(|| io::Error::other("Missing QA control directory"))?
                            .into(),
                    );
                }
                Some(flag) if flag.starts_with("--") => {
                    return Err(io::Error::other("Unknown native option"));
                }
                _ => photos.push(arg.into()),
            }
        }
        if harness.ready.is_some() != harness.stop.is_some() {
            return Err(io::Error::other("QA requires both marker paths"));
        }
        if let Some(control) = &harness.control {
            if harness.ready.is_none()
                || !control.is_absolute()
                || !control.is_dir()
                || fs::read_dir(control)?.next().is_some()
            {
                return Err(io::Error::other(
                    "QA control requires ready/stop flags and a new empty absolute directory",
                ));
            }
            harness.next_command = 1;
        }
        if let (Some(ready), Some(stop)) = (&harness.ready, &harness.stop) {
            if !ready.is_absolute()
                || !stop.is_absolute()
                || ready == stop
                || ready.extension().is_none_or(|v| v != "json")
                || ready.exists()
                || ready.with_extension("preview.json").exists()
                || stop.exists()
            {
                return Err(io::Error::other(
                    "QA requires new absolute JSON/stop marker paths; existing files are never replaced",
                ));
            }
        }
        Ok((harness, photos))
    }
    pub fn command(&mut self) -> io::Result<Option<(u32, Command)>> {
        let Some(root) = &self.control else {
            return Ok(None);
        };
        let id = self.next_command;
        let path = root.join(format!("{id:06}.json"));
        if !path.exists() {
            return Ok(None);
        }
        let file = fs::File::open(path)?;
        use std::io::Read;
        let mut bytes = Vec::new();
        file.take(8193).read_to_end(&mut bytes)?;
        if bytes.len() > 8192 {
            return Err(io::Error::other("QA command too large"));
        }
        let command = serde_json::from_slice(&bytes)?;
        self.next_command += 1;
        Ok(Some((id, command)))
    }
    pub fn respond(&self, id: u32, value: serde_json::Value) -> io::Result<()> {
        let Some(root) = &self.control else {
            return Ok(());
        };
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(root.join(format!("{id:06}.result.json")))?;
        file.write_all(&serde_json::to_vec(&value)?)?;
        file.sync_all()
    }
    pub fn poll(&mut self, launched: Instant) -> io::Result<bool> {
        if !self.wrote_ready {
            if let Some(path) = &self.ready {
                let data = format!(
                    r#"{{"pid":{},"event_loop_ready_ms":{:.3},"backend":"winit-software","version":"{}","source_commit":"{}","source_dirty":{}}}"#,
                    std::process::id(),
                    launched.elapsed().as_secs_f64() * 1000.0,
                    env!("CARGO_PKG_VERSION"),
                    env!("Z30_SOURCE_COMMIT"),
                    env!("Z30_SOURCE_DIRTY")
                );
                let mut file = fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(path)?;
                file.write_all(data.as_bytes())?;
                file.sync_all()?;
            }
            self.wrote_ready = true;
        }
        Ok(self.stop.as_ref().is_some_and(|p| p.exists()))
    }
    pub fn preview_ready(&mut self, launched: Instant, width: u32, height: u32) -> io::Result<()> {
        if self.wrote_preview {
            return Ok(());
        }
        if let Some(path) = &self.ready {
            let data = format!(
                r#"{{"first_preview_ms":{:.3},"width":{},"height":{}}}"#,
                launched.elapsed().as_secs_f64() * 1000.0,
                width,
                height
            );
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(path.with_extension("preview.json"))?;
            file.write_all(data.as_bytes())?;
            file.sync_all()?;
        }
        self.wrote_preview = true;
        Ok(())
    }
}
