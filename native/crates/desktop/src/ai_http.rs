//! Optional OpenAI-compatible image endpoint. The default remains Codex CLI.
//! `curl` is a cancellable OS process; the API key is passed through stdin to
//! `--header @-` and is never put in a command line, project or temporary file.
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::Deserialize;
use std::{
    fs,
    io::{Read, Write},
    path::PathBuf,
    process::{Command, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
use url::{Host, Url};
use z30_core::{
    Error, Result,
    ai::{self, Advice},
    recipe::Recipe,
};

const MAX_JPEG_BYTES: usize = 2 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(180);

pub struct Config {
    pub endpoint: Url,
    pub model: String,
    key: Option<String>,
    loopback: bool,
}

impl Config {
    pub fn from_selection(provider: i32, base: &str, model: &str, key: &str) -> Result<Self> {
        let (base, model, fallback) = match provider {
            1 => (
                "https://api.deepseek.com",
                "deepseek-flash",
                "DEEPSEEK_API_KEY",
            ),
            2 => (base, model, "Z30_AI_API_KEY"),
            _ => return Err(Error::Policy("未知 AI 服务商；没有发送照片".into())),
        };
        let mut url = Url::parse(base.trim())
            .map_err(|_| Error::Policy("AI Base URL 无效；没有发送照片".into()))?;
        if url.username() != ""
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(Error::Policy(
                "AI Base URL 不可包含账号、密码、查询或片段".into(),
            ));
        }
        let loopback = match url.host() {
            Some(Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
            Some(Host::Ipv4(ip)) => ip.is_loopback(),
            Some(Host::Ipv6(ip)) => ip.is_loopback(),
            None => false,
        };
        if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
            return Err(Error::Policy(
                "远端 AI 接口必须使用 HTTPS；HTTP 仅允许本机回环地址".into(),
            ));
        }
        if !model.is_ascii()
            || model.is_empty()
            || model.len() > 120
            || !model
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_/.:".contains(&b))
        {
            return Err(Error::Policy("AI 模型名无效；没有发送照片".into()));
        }
        let path = url.path().trim_end_matches('/');
        if !path.ends_with("/chat/completions") {
            url.set_path(&format!("{path}/chat/completions"));
        }
        let supplied = if !key.is_empty() {
            Some(key.to_owned())
        } else if loopback {
            // A cloud credential in the environment must not be sent to an
            // unrelated local model simply because the user changed provider.
            None
        } else {
            std::env::var(fallback).ok()
        };
        let key = supplied.filter(|s| !s.is_empty());
        if !loopback && key.is_none() {
            return Err(Error::Policy(format!(
                "远端 AI 需要本机输入 API Key，或设置 {fallback}；没有发送照片"
            )));
        }
        if key
            .as_ref()
            .is_some_and(|s| s.len() > 512 || s.chars().any(char::is_control))
        {
            return Err(Error::Policy("API Key 格式无效；没有发送照片".into()));
        }
        Ok(Self {
            endpoint: url,
            model: model.into(),
            key,
            loopback,
        })
    }
}

fn system_curl() -> Result<PathBuf> {
    #[cfg(windows)]
    let candidate = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join("System32")
        .join("curl.exe");
    #[cfg(not(windows))]
    let candidate = PathBuf::from("/usr/bin/curl");
    if candidate.is_file() {
        Ok(candidate)
    } else {
        Err(Error::Policy(
            "系统 curl 不可用；无法连接兼容接口，Codex CLI 模式仍可用".into(),
        ))
    }
}

fn request_body(
    recipe: Recipe,
    goal: &str,
    raw: bool,
    neutral: &[u8],
    current: &[u8],
    model: &str,
) -> Result<Vec<u8>> {
    for jpeg in [neutral, current] {
        if jpeg.len() < 4
            || jpeg.len() > MAX_JPEG_BYTES
            || !jpeg.starts_with(&[0xff, 0xd8])
            || !jpeg.ends_with(&[0xff, 0xd9])
        {
            return Err(Error::Policy("AI 预览 JPEG 无效；没有发送照片".into()));
        }
    }
    let data = |jpeg: &[u8]| format!("data:image/jpeg;base64,{}", STANDARD.encode(jpeg));
    let prompt = format!(
        "{}\n请以 json 对象返回；不得加 Markdown 代码块。准确遵守此 JSON Schema：{}",
        crate::ai_cli::prompt(recipe, goal, raw)?,
        serde_json::to_string(&ai::schema())?
    );
    Ok(serde_json::to_vec(&serde_json::json!({
        "model": model,
        "temperature": 0.3,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type":"text", "text":prompt},
            {"type":"image_url", "image_url":{"url":data(neutral)}},
            {"type":"image_url", "image_url":{"url":data(current)}}
        ]}]
    }))?)
}

#[derive(Deserialize)]
struct Completion {
    choices: Vec<Choice>,
}
#[derive(Deserialize)]
struct Choice {
    message: Message,
}
#[derive(Deserialize)]
struct Message {
    content: String,
}

pub fn run(
    config: &Config,
    recipe: Recipe,
    goal: &str,
    raw: bool,
    neutral: &[u8],
    current: &[u8],
    cancel: &AtomicBool,
) -> Result<Advice> {
    if cancel.load(Ordering::Acquire) {
        return Err(Error::Policy("AI 调色已取消".into()));
    }
    let body = request_body(recipe.clone(), goal, raw, neutral, current, &config.model)?;
    let exe = system_curl()?;
    let temp = tempfile::Builder::new().prefix("z30-ai-http-").tempdir()?;
    fs::write(temp.path().join("request.json"), body)?;
    let mut cmd = Command::new(exe);
    cmd.current_dir(temp.path()).args([
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--max-time",
        "180",
        "--connect-timeout",
        "15",
        "--max-filesize",
        "100000",
        "--request",
        "POST",
        "--header",
        "Content-Type: application/json",
        "--header",
        "Accept: application/json",
        "--data-binary",
        "@request.json",
        "--output",
        "response.json",
    ]);
    if config.key.is_some() {
        cmd.args(["--header", "@-"]);
    }
    if config.loopback {
        cmd.args(["--noproxy", "*"]);
    }
    cmd.arg(config.endpoint.as_str())
        .stdin(if config.key.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    if cancel.load(Ordering::Acquire) {
        return Err(Error::Policy("AI 调色已取消".into()));
    }
    let mut child = cmd
        .spawn()
        .map_err(|_| Error::Policy("无法启动系统 curl；没有发送照片".into()))?;
    if let Some(key) = &config.key {
        let wrote = child
            .stdin
            .take()
            .ok_or_else(|| Error::Policy("AI 接口凭证管道不可用".into()))
            .and_then(|mut stdin| {
                stdin
                    .write_all(format!("Authorization: Bearer {key}\n").as_bytes())
                    .map_err(|_| Error::Policy("AI 接口凭证传递失败".into()))
            });
        if let Err(error) = wrote {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    }
    let started = Instant::now();
    let status = loop {
        if cancel.load(Ordering::Acquire) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(Error::Policy("AI 调色已取消；没有应用参数".into()));
        }
        if started.elapsed() > TIMEOUT {
            let _ = child.kill();
            let _ = child.wait();
            return Err(Error::Policy(
                "AI 接口超过 180 秒，已停止；可手动重试".into(),
            ));
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => std::thread::sleep(Duration::from_millis(25)),
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(Error::Policy("AI 接口进程状态异常".into()));
            }
        }
    };
    if !status.success() {
        return Err(Error::Policy(format!(
            "AI 接口请求失败（curl 退出码 {}）；检查地址、模型、Key 或网络",
            status.code().unwrap_or(-1)
        )));
    }
    let mut response = Vec::new();
    fs::File::open(temp.path().join("response.json"))?
        .take(100_001)
        .read_to_end(&mut response)?;
    if response.len() > 100_000 {
        return Err(Error::Policy("AI 接口响应超过 100 KiB".into()));
    }
    let completion: Completion = serde_json::from_slice(&response)
        .map_err(|_| Error::Policy("AI 接口响应格式不受支持；没有应用参数".into()))?;
    let content = completion
        .choices
        .first()
        .ok_or_else(|| Error::Policy("AI 接口没有返回建议".into()))?
        .message
        .content
        .as_bytes();
    Advice::parse(content, recipe)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn endpoint_key_and_model_validation_fail_closed() {
        assert!(Config::from_selection(2, "http://example.com/v1", "vision", "key").is_err());
        assert!(Config::from_selection(2, "https://u:p@example.com/v1", "vision", "key").is_err());
        assert!(Config::from_selection(2, "https://example.com/v1", "bad model", "key").is_err());
        assert!(Config::from_selection(2, "https://example.com/v1", "vision", "bad\nkey").is_err());
        let local = Config::from_selection(2, "http://127.0.0.1:3000/v1", "vision", "").unwrap();
        assert_eq!(
            local.endpoint.as_str(),
            "http://127.0.0.1:3000/v1/chat/completions"
        );
        assert!(local.key.is_none());
        let deepseek = Config::from_selection(1, "", "", "sk-offline-test").unwrap();
        assert_eq!(
            deepseek.endpoint.as_str(),
            "https://api.deepseek.com/chat/completions"
        );
        assert_eq!(deepseek.model, "deepseek-flash");
    }
}
