//! Owns mode and committed hosted generations. Archive history is independent.
use crate::{ErrorCode, identity::*};
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RepositoryMode {
    ProtectedMirror,
    Hosted,
}
impl RepositoryMode {
    pub fn require_push(self, writable_capability: bool) -> Result<(), ErrorCode> {
        match self {
            Self::ProtectedMirror => Err(ErrorCode::ReadOnly),
            Self::Hosted if !writable_capability => Err(ErrorCode::UnsupportedCapability),
            Self::Hosted => Ok(()),
        }
    }
    pub fn require_source_update(self) -> Result<(), ErrorCode> {
        match self {
            Self::ProtectedMirror => Ok(()),
            Self::Hosted => Err(ErrorCode::Conflict),
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HostedRepository {
    pub repository: RepositoryId,
    pub mode: RepositoryMode,
    pub committed_generation: Option<GenerationId>,
}
