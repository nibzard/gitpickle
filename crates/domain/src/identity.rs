//! IDs are opaque and names are aliases. Hash kinds cannot be interchanged.
use crate::ErrorCode;

macro_rules! identity {
    ($($name:ident),+ $(,)?) => {$(
        #[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
        pub struct $name(String);
        impl $name {
            pub fn new(value: impl Into<String>) -> Result<Self, ErrorCode> {
                let value = value.into();
                if value.is_empty() || value.len() > 128 || !value.bytes().all(|b|
                    b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
                    return Err(ErrorCode::InvalidInput);
                }
                Ok(Self(value))
            }
            pub fn as_str(&self) -> &str { &self.0 }
        }
    )+};
}
identity!(
    RepositoryId,
    SourceId,
    SourceRepositoryId,
    ArchiveId,
    RecoveryPointId,
    JobId,
    OperationId,
    ActorId,
    PlanId,
    EvidenceId,
    GenerationId,
    WriterId
);

fn valid_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
macro_rules! digest {
    ($($name:ident),+) => {$(
        #[derive(Clone, Debug, Eq, PartialEq)]
        pub struct $name(String);
        impl $name {
            pub fn sha256(value: impl Into<String>) -> Result<Self, ErrorCode> {
                let value = value.into();
                if !valid_hex(&value, 64) { return Err(ErrorCode::InvalidInput); }
                Ok(Self(value))
            }
            pub fn as_str(&self) -> &str { &self.0 }
        }
    )+};
}
digest!(ArchiveObjectId, PlanDigest, LfsObjectId);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GitHashAlgorithm {
    Sha1,
    Sha256,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GitObjectId {
    algorithm: GitHashAlgorithm,
    hex: String,
}
impl GitObjectId {
    pub fn new(algorithm: GitHashAlgorithm, hex: impl Into<String>) -> Result<Self, ErrorCode> {
        let hex = hex.into();
        let length = match algorithm {
            GitHashAlgorithm::Sha1 => 40,
            GitHashAlgorithm::Sha256 => 64,
        };
        if !valid_hex(&hex, length) {
            return Err(ErrorCode::InvalidInput);
        }
        Ok(Self { algorithm, hex })
    }
    pub fn algorithm(&self) -> GitHashAlgorithm {
        self.algorithm
    }
    pub fn as_str(&self) -> &str {
        &self.hex
    }
}

/// Seconds since the Unix epoch in UTC. Wire timestamps use this same unit.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct Timestamp(pub u64);

/// Compile-time separation of Git hashes and archive digests.
/// ```compile_fail
/// use gitpickle_domain::identity::{ArchiveObjectId, GitObjectId};
/// fn archive_object(_: ArchiveObjectId) {}
/// fn wrong_kind(git: GitObjectId) { archive_object(git); }
/// ```
pub struct IdentityContract;
