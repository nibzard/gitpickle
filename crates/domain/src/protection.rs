//! Owns source mappings, registration, policy, coverage, and freshness.
use crate::identity::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Component {
    Git,
    Lfs,
    Wiki,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CoverageState {
    NotIncluded,
    Missing,
    Captured,
    Verified,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ComponentCoverage {
    pub component: Component,
    pub required: bool,
    pub state: CoverageState,
    pub evidence: Vec<EvidenceId>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceMapping {
    pub source: SourceId,
    pub upstream_repository: SourceRepositoryId,
    pub alias: String,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtectionPolicy {
    pub revision: u64,
    pub required: Vec<Component>,
    pub capture_interval_seconds: u64,
    pub discovery_interval_seconds: u64,
    pub freshness_seconds: u64,
    pub include_future_authorized: bool,
}
impl Default for ProtectionPolicy {
    fn default() -> Self {
        Self {
            revision: 1,
            required: vec![Component::Git, Component::Lfs],
            capture_interval_seconds: 6 * 3600,
            discovery_interval_seconds: 24 * 3600,
            freshness_seconds: 8 * 3600,
            include_future_authorized: false,
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtectedRepository {
    pub id: RepositoryId,
    pub source: SourceMapping,
    pub policy: ProtectionPolicy,
}

/// Each timestamp has its own meaning. Restore evidence must also retain its scope.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtectionStatus {
    pub repository: RepositoryId,
    pub source_observed_at: Option<Timestamp>,
    pub content_changed_at: Option<Timestamp>,
    pub capture_completed_at: Option<Timestamp>,
    pub verified_at: Option<Timestamp>,
    pub restore_tested_at: Option<Timestamp>,
    pub point: Option<RecoveryPointId>,
    pub coverage: Vec<ComponentCoverage>,
    pub source_available: bool,
    pub matches_observed_state: bool,
}
impl ProtectionStatus {
    pub fn current_and_verified(&self, now: Timestamp, policy: &ProtectionPolicy) -> bool {
        self.source_available
            && self.matches_observed_state
            && self.point.is_some()
            && self.verified_at.is_some_and(|at| at <= now)
            && self.source_observed_at.is_some_and(|at| {
                now.0
                    .checked_sub(at.0)
                    .is_some_and(|age| age <= policy.freshness_seconds)
            })
            && policy.required.contains(&Component::Git)
            && policy.required.iter().all(|component| {
                self.coverage
                    .iter()
                    .filter(|c| c.component == *component)
                    .count()
                    == 1
                    && self.coverage.iter().any(|c| {
                        c.component == *component
                            && c.required
                            && c.state == CoverageState::Verified
                            && !c.evidence.is_empty()
                    })
            })
    }
}
