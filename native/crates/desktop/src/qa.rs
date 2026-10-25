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
                Some(flag) if flag.starts_with("--") => {
                    return Err(io::Error::other("Unknown native option"));
                }
                _ => photos.push(arg.into()),
            }
        }
        if harness.ready.is_some() != harness.stop.is_some() {
            return Err(io::Error::other("QA requires both marker paths"));
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
