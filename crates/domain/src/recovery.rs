//! Owns selection and recovery plans. Source access is not a prerequisite.
use crate::identity::*;
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PointSelection {
    NewestCompleteVerified,
    Explicit {
        point: RecoveryPointId,
        allow_incomplete: bool,
    },
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Visibility {
    Private,
    Public,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RecoveryPlan {
    pub id: PlanId,
    pub version: u32,
    pub actor: ActorId,
    pub repositories: Vec<RepositoryId>,
    pub selection: PointSelection,
    /// Logical destination; adapters validate filesystem paths or remote targets.
    pub destination: String,
    pub visibility: Visibility,
    pub expected_versions: Vec<(RepositoryId, u64)>,
    pub digest: PlanDigest,
    pub expires_at: Timestamp,
    pub maximum_bytes: u64,
    pub maximum_repositories: u32,
    pub overwrite: bool,
}
