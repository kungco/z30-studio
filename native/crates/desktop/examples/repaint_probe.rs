//! Developer regression: corrupt/invalidate ONLY the Slint window created here.
//! No window discovery, desktop capture, input injection or user files.
#[cfg(windows)]
mod probe {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    use slint::ComponentHandle;
    use std::{
        cell::RefCell,
        path::PathBuf,
        rc::Rc,
        time::{Duration, Instant},
    };
    use windows::Win32::{
        Foundation::{COLORREF, HWND, RECT},
        Graphics::Gdi::{
            CreateSolidBrush, DeleteObject, FillRect, GetDC, GetPixel, GetUpdateRect, HGDIOBJ,
            InvalidateRect, ReleaseDC,
        },
    };
    slint::slint! {
        export component PaintProbe inherits Window {
            title: "Z30 Studio · repaint regression";
            width: 360px;
            height: 240px;
            background: #203040;
            Rectangle { x: 160px; y: 20px; width: 30px; height: 30px; background: #e0a040; }
        }
    }
    const EXPECTED: u32 = 0x00403020; // COLORREF is 0x00BBGGRR.
    const DAMAGED: u32 = 0x00ff00ff;
    fn sample(hwnd: HWND, x: i32, y: i32) -> Result<u32, String> {
        // HWND is obtained exclusively from this probe's own Slint window.
        unsafe {
            let dc = GetDC(Some(hwnd));
            if dc.0.is_null() {
                return Err("own window DC unavailable".into());
            }
            let pixel = GetPixel(dc, x, y).0;
            ReleaseDC(Some(hwnd), dc);
            if pixel == u32::MAX {
                return Err("own window pixel unavailable".into());
            }
            Ok(pixel)
        }
    }
    fn damage(hwnd: HWND, rect: &RECT) -> Result<(), String> {
        unsafe {
            let dc = GetDC(Some(hwnd));
            if dc.0.is_null() {
                return Err("own window DC unavailable".into());
            }
            let brush = CreateSolidBrush(COLORREF(DAMAGED));
            if brush.0.is_null() {
                ReleaseDC(Some(hwnd), dc);
                return Err("brush unavailable".into());
            }
            let written = FillRect(dc, rect, brush);
            let _ = DeleteObject(HGDIOBJ(brush.0));
            ReleaseDC(Some(hwnd), dc);
            if written == 0 {
                return Err("own window damage not written".into());
            }
            let (x, y) = ((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2);
            if sample(hwnd, x, y)? != DAMAGED {
                return Err("damage not observable".into());
            }
            if !InvalidateRect(Some(hwnd), Some(rect), false).as_bool() {
                return Err("invalidation failed".into());
            }
            let mut update = RECT::default();
            if !GetUpdateRect(hwnd, Some(&mut update), false).as_bool()
                || update.left > rect.left
                || update.top > rect.top
                || update.right < rect.right
                || update.bottom < rect.bottom
            {
                return Err("OS update region did not include damaged pixels".into());
            }
            Ok(())
        }
    }
    struct State {
        phase: usize,
        deadline: Instant,
        rects: Vec<RECT>,
        records: Vec<serde_json::Value>,
        error: Option<String>,
    }
    pub fn run() -> Result<bool, Box<dyn std::error::Error>> {
        let out = PathBuf::from(
            std::env::args_os()
                .nth(1)
                .ok_or("usage: repaint_probe NEW.json")?,
        );
        if out.exists() {
            return Err("choose a new report".into());
        }
        slint::BackendSelector::new()
            .backend_name("winit".into())
            .renderer_name("software".into())
            .select()?;
        let ui = PaintProbe::new()?;
        ui.show()?;
        let state = Rc::new(RefCell::new(State {
            phase: 0,
            deadline: Instant::now() + Duration::from_millis(400),
            rects: vec![],
            records: vec![],
            error: None,
        }));
        let timer = slint::Timer::default();
        let observed = Rc::clone(&state);
        let weak = ui.as_weak();
        timer.start(slint::TimerMode::Repeated,Duration::from_millis(20),move||{
            let mut s=observed.borrow_mut();
            if Instant::now()<s.deadline {return;}
            let Some(ui)=weak.upgrade() else {return;};
            let result=(||->Result<bool,String>{
                // Native handles become available once winit's event loop is active.
                let holder=ui.window().window_handle();
                let native=holder.window_handle().map_err(|e|e.to_string())?;
                let RawWindowHandle::Win32(handle)=native.as_raw() else {return Err("not Win32".into());};
                let hwnd=HWND(handle.hwnd.get() as *mut core::ffi::c_void);
                if s.rects.is_empty() {
                    let size=ui.window().size();
                    s.rects=vec![RECT{left:13,top:17,right:87,bottom:71},
                        RECT{left:5,top:131,right:51,bottom:201},
                        RECT{left:size.width as i32-45,top:size.height as i32-45,right:size.width as i32-5,bottom:size.height as i32-5}];
                }
                let rect=s.rects[s.phase/2];
                let (x,y)=((rect.left+rect.right)/2,(rect.top+rect.bottom)/2);
                if s.phase%2==0 {
                    let original=sample(hwnd,x,y)?;
                    if original!=EXPECTED {return Err(format!("initial paint mismatch: {original:#x}"));}
                    damage(hwnd,&rect)?;
                    s.deadline=Instant::now()+Duration::from_millis(120);
                } else {
                    let actual=sample(hwnd,x,y)?;
                    let index=s.phase/2;
                    s.records.push(serde_json::json!({"case":index,"rect":[rect.left,rect.top,rect.right,rect.bottom],"expected":EXPECTED,"damaged":DAMAGED,"actual":actual,"restored":actual==EXPECTED}));
                }
                s.phase+=1;
                Ok(s.phase==s.rects.len()*2)
            })();
            match result {
                Ok(false)=>{},
                Ok(true)=>{let _=slint::quit_event_loop();},
                Err(e)=>{s.error=Some(e);let _=slint::quit_event_loop();},
            }
        });
        slint::run_event_loop()?;
        timer.stop();
        let s = state.borrow();
        let passed = s.error.is_none()
            && s.records.len() == 3
            && s.records.iter().all(|r| r["restored"] == true);
        let report = serde_json::json!({"passed":passed,"own_window_only":true,"renderer":"software","scale_factor":ui.window().scale_factor(),"cases":s.records,"error":s.error,"app_exposed_to_other_window":false});
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&out)?;
        file.write_all(serde_json::to_string_pretty(&report)?.as_bytes())?;
        println!("{}", serde_json::to_string(&report)?);
        Ok(passed)
    }
}
fn main() {
    #[cfg(windows)]
    match probe::run() {
        Ok(true) => {}
        Ok(false) => std::process::exit(1),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    }
    #[cfg(not(windows))]
    {
        eprintln!("Windows GDI regression requires Windows");
        std::process::exit(2);
    }
}
