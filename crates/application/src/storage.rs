//! Provider-neutral object storage. Success means a complete object is durable.
//! A timeout after mutation is an unknown outcome. Reconcile by archive digest.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ArchiveDigest(pub [u8; 32]);

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct ObjectKey(String);
impl ObjectKey {
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        if value.is_empty()
            || value.len() > 1024
            || value
                .split('/')
                .any(|s| s.is_empty() || s == "." || s == "..")
            || value.chars().any(|c| c.is_control() || c == '\\')
        {
            return Err(StorageError::InvalidInput);
        }
        Ok(Self(value))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Opaque provider comparison token. Never use it as a content checksum.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Revision(pub String);
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Condition {
    Any,
    Absent,
    Matches(Revision),
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetentionCapability {
    Unsupported,
    Available,
    Enforced,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Capabilities {
    pub completed_write_visibility: bool,
    pub conditional_create: bool,
    pub conditional_replace: bool,
    pub multipart: bool,
    /// Conditional creation at multipart completion, independently of PUT.
    pub multipart_conditional_create: bool,
    pub multipart_conditional_replace: bool,
    pub versioning: bool,
    /// Enforced refers to the configured scope, not general provider support.
    pub retention: RetentionCapability,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StorageError {
    InvalidInput,
    NotFound,
    Conflict,
    Unsupported,
    Unavailable,
    Unauthorized,
    Throttled,
    UnknownOutcome,
    Integrity,
    InvalidCursor,
}
pub type Result<T> = std::result::Result<T, StorageError>;
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Object {
    pub bytes: Vec<u8>,
    pub revision: Revision,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObjectPage {
    pub keys: Vec<ObjectKey>,
    pub next: Option<String>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Upload {
    pub key: ObjectKey,
    pub id: String,
}

/// No delete operation is exposed to routine archive writers.
/// Multipart parts are invisible until atomic completion. Conditions are checked
/// at completion, not at initiation. Part numbers start at one.
/// Listing uses opaque, prefix-bound cursors. Limits are in 1..=1000.
/// Reads and listings must expose completed writes when the capability is true.
pub trait Storage {
    fn capabilities(&self) -> Capabilities;
    fn get(&mut self, key: &ObjectKey) -> Result<Object>;
    fn put(
        &mut self,
        key: &ObjectKey,
        bytes: &[u8],
        digest: ArchiveDigest,
        condition: Condition,
    ) -> Result<Revision>;
    fn list(&mut self, prefix: &str, cursor: Option<&str>, limit: u16) -> Result<ObjectPage>;
    fn begin(
        &mut self,
        key: &ObjectKey,
        digest: ArchiveDigest,
        condition: Condition,
    ) -> Result<Upload>;
    fn part(&mut self, upload: &Upload, number: u16, bytes: &[u8]) -> Result<()>;
    fn complete(&mut self, upload: &Upload, parts: &[u16]) -> Result<Revision>;
    /// Idempotent. Must not remove a completed object.
    fn abort(&mut self, upload: &Upload) -> Result<()>;
}
