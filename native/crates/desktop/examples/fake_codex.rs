//! Offline process fixture for end-to-end QA. Never packaged or used by users.
use std::{
    env, fs,
    io::{self, Read},
    time::Duration,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().is_some_and(|v| v == "--version") {
        println!("codex-cli offline-fixture");
        return Ok(());
    }
    if args.first().is_some_and(|v| v == "login") {
        println!("Logged in using ChatGPT");
        return Ok(());
    }
    if args.first().is_none_or(|v| v != "exec") {
        return Err("expected codex exec".into());
    }
    let value = |flag: &str| -> Result<String, io::Error> {
        let i = args
            .iter()
            .position(|v| v == flag)
            .ok_or_else(|| io::Error::other(format!("missing {flag}")))?;
        args.get(i + 1)
            .cloned()
            .ok_or_else(|| io::Error::other(format!("missing {flag} value")))
    };
    if value("--sandbox")? != "read-only"
        || !args.windows(2).any(|v| v == ["--disable", "shell_tool"])
        || !args.contains(&"--ephemeral".to_owned())
        || !args.contains(&"--ignore-user-config".to_owned())
    {
        return Err("unsafe CLI options".into());
    }
    let mut prompt = String::new();
    io::stdin().read_to_string(&mut prompt)?;
    if !prompt.contains("当前九参数") || !prompt.contains("当前白平衡模式") {
        return Err("prompt omitted contract".into());
    }
    if let Ok(forbidden) = env::var("Z30_FAKE_FORBIDDEN") {
        if prompt.contains(&forbidden) {
            return Err("source name leaked into prompt".into());
        }
    }
    let mut dims = Vec::new();
    let mut lengths = Vec::new();
    for image in ["neutral.jpg", "current.jpg"] {
        if !args.windows(2).any(|v| v == ["--image", image]) {
            return Err("missing image argument".into());
        }
        let bytes = fs::read(image)?;
        if bytes.windows(4).any(|v| v == b"Exif") || bytes.windows(3).any(|v| v == b"GPS") {
            return Err("metadata in preview".into());
        }
        let image = image::load_from_memory(&bytes)?;
        if image.width().max(image.height()) > 1024 {
            return Err("preview too large".into());
        }
        dims.push((image.width(), image.height()));
        lengths.push(bytes.len());
    }
    let schema: serde_json::Value = serde_json::from_slice(&fs::read(value("--output-schema")?)?)?;
    if schema["properties"]["adjustments"]["required"]
        .as_array()
        .is_none_or(|v| v.len() != 10)
    {
        return Err("schema missing exact parameters".into());
    }
    if let Ok(trace) = env::var("Z30_FAKE_TRACE") {
        fs::write(
            trace,
            serde_json::to_vec(&serde_json::json!({
                "image_dimensions":dims,"jpeg_bytes":lengths,"prompt_bytes":prompt.len(),
                "read_only":true,"schema_fields":10
            }))?,
        )?;
    }
    match env::var("Z30_FAKE_MODE").as_deref() {
        Ok("slow") => std::thread::sleep(Duration::from_secs(30)),
        Ok("exit") => return Err("intentional failure".into()),
        _ => {}
    }
    let exposure = match env::var("Z30_FAKE_MODE").as_deref() {
        Ok("invalid") => 9.0,
        Ok("overexpose") => 4.0,
        Ok("warm") => 0.0,
        _ => 0.7,
    };
    let temperature = if env::var("Z30_FAKE_MODE").as_deref() == Ok("warm") { 100.0 } else { 0.0 };
    let advice = serde_json::json!({
        "title":"离线测试建议","summary":"提升一点曝光，保留自然层次",
        "observations":["画面可略提亮"],
        "adjustments":{"exposure":exposure,"contrast":7,"highlights":-8,"shadows":8,
            "temperature":temperature,"tint":0,"saturation":0,"vibrance":0,"sharpen":0,"wb":"camera"},
        "limitations":"只验证本机流程；没有调用真实模型"
    });
    fs::write(
        value("--output-last-message")?,
        serde_json::to_vec(&advice)?,
    )?;
    Ok(())
}
