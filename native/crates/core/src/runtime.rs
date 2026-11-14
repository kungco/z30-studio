//! Resolve optional RAW support without searching PATH or the working directory.
use crate::{Error, Result};
use std::{
    ffi::OsStr,
    path::{Path, PathBuf},
};

pub fn raw_library(executable: &Path, explicit: Option<&OsStr>) -> Result<Option<PathBuf>> {
    if let Some(value) = explicit {
        let path = PathBuf::from(value);
        if !path.is_absolute() {
            return Err(Error::Policy("Z30_LIBRAW_DLL 必须是绝对路径".into()));
        }
        // A broken explicit override is an error at decode time, never a silent
        // fallback to a different decoder that could change image results.
        return Ok(Some(path));
    }
    if !executable.is_absolute() {
        return Err(Error::Policy("程序路径必须是绝对路径".into()));
    }
    let directory = executable
        .parent()
        .ok_or_else(|| Error::Policy("无法确定程序目录".into()))?;
    let bundled = directory.join("raw").join("libraw.dll");
    Ok(bundled.is_file().then_some(bundled))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundle_is_relative_to_executable_and_missing_raw_is_optional() {
        let root = tempfile::tempdir().unwrap();
        let exe = root.path().join("独立 软件").join("Z30Studio.exe");
        std::fs::create_dir_all(exe.parent().unwrap()).unwrap();
        std::fs::write(root.path().join("libraw.dll"), b"decoy").unwrap();
        assert!(raw_library(&exe, None).unwrap().is_none());
        let directory = exe.parent().unwrap().join("raw");
        std::fs::create_dir(&directory).unwrap();
        let bundled = directory.join("libraw.dll");
        std::fs::write(&bundled, b"test path selection only").unwrap();
        assert_eq!(raw_library(&exe, None).unwrap(), Some(bundled));
    }

    #[test]
    fn explicit_override_wins_without_relative_or_silent_fallback() {
        let root = tempfile::tempdir().unwrap();
        let exe = root.path().join("Z30Studio.exe");
        let override_path = root.path().join("custom.dll");
        assert_eq!(
            raw_library(&exe, Some(override_path.as_os_str())).unwrap(),
            Some(override_path)
        );
        assert!(raw_library(&exe, Some(OsStr::new("raw/libraw.dll"))).is_err());
        assert!(raw_library(Path::new("Z30Studio.exe"), None).is_err());
    }
}
