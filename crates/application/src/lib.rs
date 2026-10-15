//! Shared use cases and ports. Implementations enforce authorization here.
//! No transport, provider, or database representation crosses these interfaces.
use domain::{ErrorCode, archive::*, execution::*, identity::*, protection::*, recovery::*};
pub use gitpickle_domain as domain;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RetryAdvice {
    Never,
    AfterSeconds(u64),
    ReconcileOperation(OperationId),
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApplicationError {
    pub code: ErrorCode,
    /// Redacted, actionable text. Never an SDK error dump.
    pub message: String,
    pub retry: RetryAdvice,
    pub next_action: String,
}
pub type Result<T> = std::result::Result<T, ApplicationError>;
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Scope {
    pub repositories: Vec<RepositoryId>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PageRequest {
    limit: u16,
    cursor: Option<String>,
}
impl PageRequest {
    pub fn new(limit: u16, cursor: Option<String>) -> std::result::Result<Self, ErrorCode> {
        if !(1..=100).contains(&limit) || cursor.as_ref().is_some_and(|c| c.len() > 1024) {
            return Err(ErrorCode::InvalidInput);
        }
        Ok(Self { limit, cursor })
    }
    pub fn limit(&self) -> u16 {
        self.limit
    }
    pub fn cursor(&self) -> Option<&str> {
        self.cursor.as_deref()
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Page<T> {
    pub items: Vec<T>,
    pub next_cursor: Option<String>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtectionRequest {
    pub context: MutationContext,
    pub scope: Scope,
    pub policy: ProtectionPolicy,
    pub approval: ApprovalReference,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CaptureRequest {
    pub context: MutationContext,
    pub scope: Scope,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RestoreRequest {
    pub actor: ActorId,
    pub archive: ArchiveId,
    pub repository: RepositoryId,
    pub selection: PointSelection,
    pub destination: String,
    pub from_storage: bool,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AcceptedJob {
    pub id: JobId,
    pub accepted_at: Timestamp,
}

/// All frontends use these services. There is no frontend-specific authority.
/// A returned AcceptedJob requires durable acceptance, not a local queue insert.
pub trait Application {
    fn protection_status(&self, actor: &ActorId, scope: &Scope) -> Result<Vec<ProtectionStatus>>;
    fn repositories_list(
        &self,
        actor: &ActorId,
        page: PageRequest,
    ) -> Result<Page<ProtectedRepository>>;
    fn recovery_points_list(
        &self,
        actor: &ActorId,
        repository: &RepositoryId,
        page: PageRequest,
    ) -> Result<Page<RecoveryPoint>>;
    fn jobs_get(&self, actor: &ActorId, job: &JobId) -> Result<Job>;
    fn protect(&self, request: ProtectionRequest) -> Result<AcceptedJob>;
    fn backup_run(&self, request: CaptureRequest) -> Result<AcceptedJob>;
    fn sync(&self, request: CaptureRequest) -> Result<AcceptedJob>;
    fn restore_plan(&self, request: RestoreRequest) -> Result<RecoveryPlan>;
    /// Revalidate stored approval, digest, expiry, actor, scope, and preconditions.
    fn plan_apply(
        &self,
        context: MutationContext,
        approval: ApprovalReference,
    ) -> Result<AcceptedJob>;
    /// Safe cancellation never removes committed recovery points.
    fn cancel(&self, context: MutationContext, job: &JobId) -> Result<Job>;
}

/// Narrow boundary for isolated, caller-owned staging repositories.
/// Inventory is not archive publication or evidence of independent recovery.
pub trait GitEngine {
    fn inventory(&self, staging: &std::path::Path) -> Result<GitInventory>;
    fn verify(&self, staging: &std::path::Path) -> Result<()>;
    /// Serve an authenticated request from an isolated committed repository.
    /// The caller enforces archive authority and authorization. Output uses bounded memory.
    fn smart_http(
        &self,
        staging: &std::path::Path,
        request: &GitHttpRequest,
        output: &mut dyn std::io::Write,
    ) -> Result<()>;
    /// A successful local receive-pack is never a durable push receipt.
    fn writable_hosting(&self) -> bool;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GitInventory {
    pub hash_algorithm: String,
    pub refs: std::collections::BTreeMap<String, String>,
    /// Symbolic ref name, including an unborn branch, or a detached object ID.
    pub head: String,
    pub symbolic_head: bool,
}
pub trait Clock {
    fn now(&self) -> Timestamp;
}
pub trait Authorization {
    fn require_permission(
        &self,
        actor: &ActorId,
        permission: Permission,
        scope: &Scope,
    ) -> Result<()>;
    fn require(&self, actor: &ActorId, operation: Operation, scope: &Scope) -> Result<()>;
    fn validate_approval(
        &self,
        actor: &ActorId,
        approval: &ApprovalReference,
    ) -> Result<RecoveryPlan>;
}
/// Returning success means the request is recoverable without the local database.
pub trait DurableJobs {
    fn accept(
        &self,
        context: &MutationContext,
        scope: &Scope,
        arguments_digest: &PlanDigest,
    ) -> Result<AcceptedJob>;
    fn inspect(&self, job: &JobId) -> Result<Job>;
}

pub mod storage;

/// Only read-side Git Smart HTTP crosses the engine boundary.
pub struct GitHttpRequest {
    pub method: String,
    pub endpoint: String,
    pub query: String,
    pub protocol: String,
    pub body: Vec<u8>,
}
