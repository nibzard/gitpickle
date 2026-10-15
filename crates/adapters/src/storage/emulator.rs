use super::digest;
use gitpickle_application::storage::*;
use std::collections::{BTreeMap, VecDeque};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Operation {
    Get,
    Put,
    List,
    Begin,
    Part,
    Complete,
    Abort,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Fault {
    Unavailable,
    LostResponse,
    CorruptRead,
    HiddenRead,
    EmptyPage,
}
struct Pending {
    digest: ArchiveDigest,
    condition: Condition,
    parts: BTreeMap<u16, Vec<u8>>,
}
/// Faults fire once, in queue order, on the next matching operation.
/// No wall clock, random source, filesystem, or external resource is used.
pub struct Emulator {
    capabilities: Capabilities,
    objects: BTreeMap<ObjectKey, Object>,
    uploads: BTreeMap<(ObjectKey, String), Pending>,
    faults: VecDeque<(Operation, Fault)>,
    sequence: u64,
    pub calls: Vec<Operation>,
}
impl Default for Emulator {
    fn default() -> Self {
        Self::new(Capabilities {
            completed_write_visibility: true,
            conditional_create: true,
            conditional_replace: true,
            multipart: true,
            multipart_conditional_create: true,
            multipart_conditional_replace: true,
            versioning: false,
            retention: RetentionCapability::Unsupported,
        })
    }
}
impl Emulator {
    pub fn new(capabilities: Capabilities) -> Self {
        Self {
            capabilities,
            objects: BTreeMap::new(),
            uploads: BTreeMap::new(),
            faults: VecDeque::new(),
            sequence: 0,
            calls: Vec::new(),
        }
    }
    pub fn inject(&mut self, operation: Operation, fault: Fault) {
        self.faults.push_back((operation, fault));
    }
    fn enter(&mut self, operation: Operation) -> Result<Option<Fault>> {
        self.calls.push(operation);
        let fault = if self.faults.front().is_some_and(|(op, _)| *op == operation) {
            self.faults.pop_front().map(|(_, fault)| fault)
        } else {
            None
        };
        if fault == Some(Fault::Unavailable) {
            return Err(StorageError::Unavailable);
        }
        Ok(fault)
    }
    fn token(&mut self) -> String {
        self.sequence += 1;
        format!("opaque-{}-not-a-checksum", self.sequence)
    }
    fn write(
        &mut self,
        key: &ObjectKey,
        bytes: &[u8],
        expected: ArchiveDigest,
        condition: Condition,
    ) -> Result<Revision> {
        if digest(bytes) != expected {
            return Err(StorageError::Integrity);
        }
        match &condition {
            Condition::Absent if !self.capabilities.conditional_create => {
                return Err(StorageError::Unsupported);
            }
            Condition::Matches(_) if !self.capabilities.conditional_replace => {
                return Err(StorageError::Unsupported);
            }
            _ => {}
        }
        let current = self.objects.get(key);
        match condition {
            Condition::Absent if current.is_some() => return Err(StorageError::Conflict),
            Condition::Matches(revision) if current.map(|o| &o.revision) != Some(&revision) => {
                return Err(StorageError::Conflict);
            }
            _ => {}
        }
        let revision = Revision(self.token());
        self.objects.insert(
            key.clone(),
            Object {
                bytes: bytes.to_vec(),
                revision: revision.clone(),
            },
        );
        Ok(revision)
    }
}
fn response<T>(fault: Option<Fault>, value: T) -> Result<T> {
    if fault == Some(Fault::LostResponse) {
        Err(StorageError::UnknownOutcome)
    } else {
        Ok(value)
    }
}
impl Storage for Emulator {
    fn capabilities(&self) -> Capabilities {
        self.capabilities
    }
    fn get(&mut self, key: &ObjectKey) -> Result<Object> {
        let fault = self.enter(Operation::Get)?;
        if fault == Some(Fault::HiddenRead) {
            return Err(StorageError::NotFound);
        }
        let mut object = self
            .objects
            .get(key)
            .cloned()
            .ok_or(StorageError::NotFound)?;
        if fault == Some(Fault::CorruptRead) {
            object.bytes.push(0);
        }
        response(fault, object)
    }
    fn put(
        &mut self,
        key: &ObjectKey,
        bytes: &[u8],
        expected: ArchiveDigest,
        condition: Condition,
    ) -> Result<Revision> {
        let fault = self.enter(Operation::Put)?;
        let revision = self.write(key, bytes, expected, condition)?;
        response(fault, revision)
    }
    fn list(&mut self, prefix: &str, cursor: Option<&str>, limit: u16) -> Result<ObjectPage> {
        let fault = self.enter(Operation::List)?;
        if !(1..=1000).contains(&limit) {
            return Err(StorageError::InvalidInput);
        }
        let marker = format!("{}:{prefix}:", prefix.len());
        let after = match cursor {
            Some(cursor) => Some(
                cursor
                    .strip_prefix(&marker)
                    .ok_or(StorageError::InvalidCursor)?,
            ),
            None => None,
        };
        if fault == Some(Fault::EmptyPage) {
            return Ok(ObjectPage {
                keys: vec![],
                next: Some(cursor.unwrap_or(&marker).to_owned()),
            });
        }
        let mut keys: Vec<_> = self
            .objects
            .keys()
            .filter(|k| k.as_str().starts_with(prefix) && after.is_none_or(|a| k.as_str() > a))
            .take(usize::from(limit) + 1)
            .cloned()
            .collect();
        let next = if keys.len() > usize::from(limit) {
            keys.pop();
            Some(format!("{marker}{}", keys.last().unwrap().as_str()))
        } else {
            None
        };
        response(fault, ObjectPage { keys, next })
    }
    fn begin(
        &mut self,
        key: &ObjectKey,
        expected: ArchiveDigest,
        condition: Condition,
    ) -> Result<Upload> {
        let fault = self.enter(Operation::Begin)?;
        if !self.capabilities.multipart {
            return Err(StorageError::Unsupported);
        }
        match &condition {
            Condition::Absent if !self.capabilities.multipart_conditional_create => {
                return Err(StorageError::Unsupported);
            }
            Condition::Matches(_) if !self.capabilities.multipart_conditional_replace => {
                return Err(StorageError::Unsupported);
            }
            _ => {}
        }
        let upload = Upload {
            key: key.clone(),
            id: self.token(),
        };
        self.uploads.insert(
            (key.clone(), upload.id.clone()),
            Pending {
                digest: expected,
                condition,
                parts: BTreeMap::new(),
            },
        );
        response(fault, upload)
    }
    fn part(&mut self, upload: &Upload, number: u16, bytes: &[u8]) -> Result<()> {
        let fault = self.enter(Operation::Part)?;
        if number == 0 {
            return Err(StorageError::InvalidInput);
        }
        self.uploads
            .get_mut(&(upload.key.clone(), upload.id.clone()))
            .ok_or(StorageError::NotFound)?
            .parts
            .insert(number, bytes.to_vec());
        response(fault, ())
    }
    fn complete(&mut self, upload: &Upload, parts: &[u16]) -> Result<Revision> {
        let fault = self.enter(Operation::Complete)?;
        if parts.is_empty() || parts[0] == 0 || parts.windows(2).any(|p| p[0] >= p[1]) {
            return Err(StorageError::InvalidInput);
        }
        let pending = self
            .uploads
            .get(&(upload.key.clone(), upload.id.clone()))
            .ok_or(StorageError::NotFound)?;
        let mut bytes = Vec::new();
        for number in parts {
            bytes.extend_from_slice(
                pending
                    .parts
                    .get(number)
                    .ok_or(StorageError::InvalidInput)?,
            );
        }
        let revision = self.write(
            &upload.key,
            &bytes,
            pending.digest,
            pending.condition.clone(),
        )?;
        self.uploads
            .remove(&(upload.key.clone(), upload.id.clone()));
        response(fault, revision)
    }
    fn abort(&mut self, upload: &Upload) -> Result<()> {
        let fault = self.enter(Operation::Abort)?;
        self.uploads
            .remove(&(upload.key.clone(), upload.id.clone()));
        response(fault, ())
    }
}
