//! Developer-only repeated full-size export benchmark for the same source.
#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::path::PathBuf;
    use z30_core::{
        editing::EditRequest,
        export::Format,
        export_job::{ExportControl, ExportSource, FullFrameCache},
        recipe::Recipe,
    };
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 4 {
        return Err("usage: full_cache_probe DLL INPUT.NEF NEW_DIRECTORY cached|uncached".into());
    }
    let (dll, input, output) = (
        PathBuf::from(&args[0]),
        PathBuf::from(&args[1]),
        PathBuf::from(&args[2]),
    );
    let mode = args[3].to_string_lossy();
    if !output.is_absolute() || !matches!(mode.as_ref(), "cached" | "uncached") {
        return Err("absolute new output directory and cached|uncached mode required".into());
    }
    std::fs::create_dir(&output)?;
    let source = ExportSource::new(EditRequest {
        path: input,
        recipe: Recipe::default(),
    })?;
    let mut cache = FullFrameCache::default();
    let mut receipts = Vec::new();
    for name in ["first.tif", "second.tif"] {
        let destination = output.join(name);
        let control = ExportControl::default();
        let receipt = if mode == "cached" {
            source.run_with_cache(
                &destination,
                Format::Tiff16,
                Some(&dll),
                &control,
                &mut cache,
            )?
        } else {
            source.run(&destination, Format::Tiff16, Some(&dll), &control)?
        };
        receipts.push(serde_json::json!({"name":name,"seconds":receipt.seconds,
            "decode_seconds":receipt.decode_seconds,"render_seconds":receipt.render_seconds,
            "encode_seconds":receipt.encode_seconds,"reused_decode":receipt.reused_decode,
            "bytes":destination.metadata()?.len()}));
    }
    println!("{}", serde_json::json!({"mode":mode,"receipts":receipts}));
    Ok(())
}
#[cfg(not(windows))]
fn main() {
    eprintln!("The full-size RAW cache benchmark requires Windows");
    std::process::exit(1);
}
