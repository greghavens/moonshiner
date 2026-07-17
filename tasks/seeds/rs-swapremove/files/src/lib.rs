//! In-memory build queue for the CI runners.
//!
//! Queue position is not a scheduling promise — runners select work by build
//! id — so the queue is free to keep its backing storage compact however it
//! likes. What must hold is membership: after a repo is archived, none of its
//! builds may remain queued, and the audit log records exactly how many were
//! cancelled.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Build {
    pub id: u32,
    pub repo: String,
    pub branch: String,
}

#[derive(Debug, Default)]
pub struct BuildQueue {
    queue: Vec<Build>,
}

impl BuildQueue {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn enqueue(&mut self, id: u32, repo: &str, branch: &str) {
        self.queue.push(Build {
            id,
            repo: repo.to_string(),
            branch: branch.to_string(),
        });
    }

    pub fn len(&self) -> usize {
        self.queue.len()
    }

    pub fn is_empty(&self) -> bool {
        self.queue.is_empty()
    }

    /// Ids of every queued build, ascending.
    pub fn queued_ids(&self) -> Vec<u32> {
        let mut ids: Vec<u32> = self.queue.iter().map(|b| b.id).collect();
        ids.sort_unstable();
        ids
    }

    /// Ids of the builds queued for one repo, ascending.
    pub fn ids_for_repo(&self, repo: &str) -> Vec<u32> {
        let mut ids: Vec<u32> = self
            .queue
            .iter()
            .filter(|b| b.repo == repo)
            .map(|b| b.id)
            .collect();
        ids.sort_unstable();
        ids
    }

    /// Cancel every queued build for an archived repo. Returns the number of
    /// builds cancelled, which the caller writes to the audit log.
    pub fn cancel_repo(&mut self, repo: &str) -> usize {
        let mut cancelled = 0;
        let mut i = 0;
        while i < self.queue.len() {
            if self.queue[i].repo == repo {
                self.queue.swap_remove(i);
                cancelled += 1;
            }
            i += 1;
        }
        cancelled
    }

    /// Drop the stale builds a force-push obsoletes: everything queued for
    /// this repo + branch. Returns the number of builds dropped.
    pub fn cancel_branch(&mut self, repo: &str, branch: &str) -> usize {
        let before = self.queue.len();
        self.queue.retain(|b| !(b.repo == repo && b.branch == branch));
        before - self.queue.len()
    }
}
