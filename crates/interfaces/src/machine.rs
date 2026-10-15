//! Version 1 wire envelope. Serialize only here, outside the domain.
use gitpickle_application::domain::ErrorCode;
use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u16 = 1;
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Ok,
    Queued,
    Running,
    Committed,
    Verified,
    Failed,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Scope {
    pub repository_ids: Vec<String>,
    pub archive_id: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Gap {
    pub repository_id: String,
    pub component: String,
    pub code: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct NextAction {
    pub operation: String,
    pub arguments: serde_json::Value,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Retry {
    Never,
    AfterSeconds { seconds: u64 },
    Reconcile { operation_id: String },
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Failure {
    pub code: String,
    pub message: String,
    pub retry: Retry,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ResultV1<T> {
    pub schema_version: u16,
    pub observed_at_unix_seconds: u64,
    pub scope: Scope,
    pub outcome: Outcome,
    pub job_id: Option<String>,
    pub coverage_gaps: Vec<Gap>,
    pub evidence_ids: Vec<String>,
    pub next_action: Option<NextAction>,
    pub data: Option<T>,
    pub error: Option<Failure>,
}
impl<T> ResultV1<T> {
    pub fn validate(&self) -> Result<(), ErrorCode> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(ErrorCode::UnsupportedVersion);
        }
        if (self.outcome == Outcome::Failed) != self.error.is_some()
            || (self.error.is_some() && self.data.is_some())
            || (matches!(self.outcome, Outcome::Queued | Outcome::Running) && self.job_id.is_none())
            || (self.outcome == Outcome::Verified
                && (!self.coverage_gaps.is_empty() || self.evidence_ids.is_empty()))
        {
            return Err(ErrorCode::InvalidInput);
        }
        Ok(())
    }
}
impl<T: Serialize> ResultV1<T> {
    /// Exactly one JSON value plus newline. Callers send diagnostics to stderr.
    pub fn to_json_line(&self) -> Result<String, ErrorCode> {
        self.validate()?;
        serde_json::to_string(self)
            .map(|value| value + "\n")
            .map_err(|_| ErrorCode::InvalidInput)
    }
}
/// A successful query and a successful health check are different contracts.
pub fn status_exit(check: bool, healthy: bool, failure: Option<ErrorCode>) -> u8 {
    failure.map_or(if check && !healthy { 6 } else { 0 }, ErrorCode::exit_code)
}

impl<T: serde::de::DeserializeOwned> ResultV1<T> {
    /// Validate the version and outcome before exposing decoded operation data.
    pub fn from_json(value: &str) -> Result<Self, ErrorCode> {
        let wire: serde_json::Value =
            serde_json::from_str(value).map_err(|_| ErrorCode::InvalidInput)?;
        if wire
            .get("schema_version")
            .and_then(serde_json::Value::as_u64)
            != Some(u64::from(SCHEMA_VERSION))
        {
            return Err(ErrorCode::UnsupportedVersion);
        }
        let result: Self = serde_json::from_value(wire).map_err(|_| ErrorCode::InvalidInput)?;
        result.validate()?;
        Ok(result)
    }
}
