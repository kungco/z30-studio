//! One replaceable pending request: dragging/selecting cannot create an unbounded queue.
use std::sync::{
    Arc, Condvar, Mutex,
    atomic::{AtomicBool, AtomicU64, Ordering},
};

pub struct LatestWorker<J: Send + 'static, R: Send + 'static> {
    shared: Arc<Shared<J, R>>,
}
struct Shared<J, R> {
    pending: Mutex<Option<(u64, J)>>,
    result: Mutex<Option<(u64, R)>>,
    wake: Condvar,
    revision: Arc<AtomicU64>,
    stop: Arc<AtomicBool>,
}
#[derive(Clone)]
pub struct Cancellation {
    revision: u64,
    current: Arc<AtomicU64>,
    stop: Arc<AtomicBool>,
}
impl Cancellation {
    pub fn cancelled(&self) -> bool {
        self.stop.load(Ordering::Acquire) || self.current.load(Ordering::Acquire) != self.revision
    }
}

impl<J: Send + 'static, R: Send + 'static> LatestWorker<J, R> {
    pub fn new(mut work: impl FnMut(J) -> R + Send + 'static) -> Self {
        Self::new_cancellable(move |input, _| work(input))
    }
    pub fn new_cancellable(mut work: impl FnMut(J, Cancellation) -> R + Send + 'static) -> Self {
        let shared = Arc::new(Shared {
            pending: Mutex::new(None),
            result: Mutex::new(None),
            wake: Condvar::new(),
            revision: Arc::new(AtomicU64::new(0)),
            stop: Arc::new(AtomicBool::new(false)),
        });
        let state = Arc::clone(&shared);
        std::thread::spawn(move || {
            loop {
                let job = {
                    let mut pending = state.pending.lock().unwrap_or_else(|e| e.into_inner());
                    while pending.is_none() && !state.stop.load(Ordering::Acquire) {
                        pending = state.wake.wait(pending).unwrap_or_else(|e| e.into_inner());
                    }
                    if state.stop.load(Ordering::Acquire) {
                        return;
                    }
                    pending.take().unwrap()
                };
                let (revision, input) = job;
                let token = Cancellation {
                    revision,
                    current: Arc::clone(&state.revision),
                    stop: Arc::clone(&state.stop),
                };
                let result = work(input, token);
                if state.revision.load(Ordering::Acquire) == revision
                    && !state.stop.load(Ordering::Acquire)
                {
                    // Replace an older result; a full result queue must never lose the newest preview.
                    *state.result.lock().unwrap_or_else(|e| e.into_inner()) =
                        Some((revision, result));
                }
            }
        });
        Self { shared }
    }
    pub fn submit(&self, input: J) -> u64 {
        let revision = self.shared.revision.fetch_add(1, Ordering::AcqRel) + 1;
        *self
            .shared
            .pending
            .lock()
            .unwrap_or_else(|e| e.into_inner()) = Some((revision, input));
        self.shared.wake.notify_one();
        revision
    }
    pub fn current(&self, revision: u64) -> bool {
        self.shared.revision.load(Ordering::Acquire) == revision
    }
    pub fn take_result(&self) -> Option<(u64, R)> {
        self.shared
            .result
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
    }
    pub fn cancel(&self) {
        self.shared.revision.fetch_add(1, Ordering::AcqRel);
        self.shared
            .pending
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take();
        self.shared
            .result
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take();
    }
}
impl<J: Send + 'static, R: Send + 'static> Drop for LatestWorker<J, R> {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        self.shared.wake.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::mpsc,
        time::{Duration, Instant},
    };
    fn wait_result<J: Send + 'static, R: Send + 'static>(worker: &LatestWorker<J, R>) -> (u64, R) {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if let Some(result) = worker.take_result() {
                if worker.current(result.0) {
                    return result;
                }
            }
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    #[test]
    fn superseded_and_cancelled_requests_cannot_publish() {
        let (started, wait) = mpsc::channel();
        let (unblock, blocked) = mpsc::channel();
        let worker = LatestWorker::new(move |n| {
            if n == 1 {
                started.send(()).unwrap();
                blocked.recv().unwrap();
            }
            n * 2
        });
        worker.submit(1);
        wait.recv().unwrap();
        worker.submit(2);
        let last = worker.submit(3);
        unblock.send(()).unwrap();
        let (revision, value) = wait_result(&worker);
        assert_eq!((revision, value), (last, 6));
        worker.cancel();
        assert!(!worker.current(last));
        assert!(worker.take_result().is_none());
    }
    #[test]
    fn undrained_results_are_replaced_by_the_newest_revision() {
        let worker = LatestWorker::new(|n: u32| n * 2);
        for n in 0..10 {
            worker.submit(n);
            std::thread::sleep(Duration::from_millis(5));
        }
        let (_, value) = wait_result(&worker);
        assert_eq!(value, 18);
    }
    #[test]
    fn worker_owns_mutable_cache_state_without_shared_ui_locks() {
        let mut count = 0;
        let worker = LatestWorker::new(move |n: u32| {
            count += 1;
            (n, count)
        });
        worker.submit(10);
        assert_eq!(wait_result(&worker).1, (10, 1));
        worker.submit(20);
        assert_eq!(wait_result(&worker).1, (20, 2));
    }
    #[test]
    fn cancellation_token_changes_on_supersede_cancel_and_drop() {
        let (started, wait) = mpsc::channel();
        let (release, blocked) = mpsc::channel();
        let worker = LatestWorker::new_cancellable(move |n: u32, token| {
            if n == 1 {
                started.send(token).unwrap();
                blocked.recv().unwrap();
            }
            n
        });
        worker.submit(1);
        let token = wait.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(!token.cancelled());
        let revision = worker.submit(2);
        assert!(token.cancelled());
        release.send(()).unwrap();
        assert_eq!(wait_result(&worker), (revision, 2));
        let (started, wait) = mpsc::channel();
        let worker = LatestWorker::new_cancellable(move |_: (), token| {
            started.send(token).unwrap();
        });
        worker.submit(());
        let token = wait.recv_timeout(Duration::from_secs(2)).unwrap();
        worker.cancel();
        assert!(token.cancelled());
        worker.submit(());
        let token = wait.recv_timeout(Duration::from_secs(2)).unwrap();
        drop(worker);
        assert!(token.cancelled());
    }
}
