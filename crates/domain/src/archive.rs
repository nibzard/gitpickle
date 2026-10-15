//! Owns immutable recovery points and durable evidence, not discovery or prompts.
use crate::{identity::*, protection::ComponentCoverage};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RecoveryPoint {
    pub id: RecoveryPointId,
    pub repository: RepositoryId,
    pub archive: ArchiveId,
    pub captured_at: Timestamp,
    pub dependencies: Vec<ArchiveObjectId>,
    pub coverage: Vec<ComponentCoverage>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EvidenceMethod {
    ArchiveReadback,
    GitIntegrity,
    LfsDigest,
    IndependentRestore,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Verification {
    pub id: EvidenceId,
    pub repository: RepositoryId,
    pub point: RecoveryPointId,
    pub components: Vec<crate::protection::Component>,
    pub method: EvidenceMethod,
    pub passed: bool,
    pub recorded_at: Timestamp,
}
