//! Owns durable jobs, actors, authority, approvals, and audit facts.
use crate::{ErrorCode, identity::*};
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum JobState {
    Queued,
    Running,
    Committed,
    Verified,
    Failed,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Operation {
    Protect,
    Backup,
    Sync,
    Restore,
    ConvertToHosted,
    ReconcileMirror,
    Cancel,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MutationContext {
    pub actor: ActorId,
    pub operation: Operation,
    /// Scoped to actor and operation. Different arguments with this key conflict.
    pub idempotency_key: OperationId,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Job {
    pub id: JobId,
    pub request: MutationContext,
    pub repositories: Vec<RepositoryId>,
    pub state: JobState,
    pub accepted_at: Timestamp,
    pub error: Option<ErrorCode>,
}
/// Reference to server-held approval; a caller boolean cannot grant authority.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalReference(pub PlanId);

/// Publish only after the associated durable record commits.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum DomainEvent {
    CaptureCommitted {
        repository: RepositoryId,
        point: RecoveryPointId,
    },
    VerificationRecorded {
        evidence: EvidenceId,
    },
    SourceAccessLost {
        repository: RepositoryId,
        observed_at: Timestamp,
    },
    HostedGenerationCommitted {
        repository: RepositoryId,
        generation: GenerationId,
    },
    RestoreCompleted {
        job: JobId,
        evidence: EvidenceId,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Permission {
    ReadStatus,
    ReadSource,
    ReadLogs,
    PlanRestore,
    RunBackup,
    ApplyPlan,
    ManageProtection,
    ManageHosting,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Grant {
    pub actor: ActorId,
    pub permission: Permission,
    pub repositories: Vec<RepositoryId>,
    pub expires_at: Option<Timestamp>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AuditRecord {
    pub actor: ActorId,
    pub repositories: Vec<RepositoryId>,
    pub operation: Operation,
    pub policy_revision: u64,
    pub recorded_at: Timestamp,
    pub error: Option<ErrorCode>,
}
