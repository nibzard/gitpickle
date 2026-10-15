//! Pure domain contracts. Contexts own their state and exchange stable identities.
pub mod archive;
pub mod execution;
pub mod git_access;
pub mod identity;
pub mod protection;
pub mod recovery;

/// Stable errors. Messages are diagnostic text, never a machine discriminator.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ErrorCode {
    InvalidInput,
    Configuration,
    Authentication,
    Forbidden,
    Conflict,
    SourceUnavailable,
    RateLimited,
    StorageUnavailable,
    IntegrityFailure,
    IncompleteCoverage,
    StaleCoverage,
    LowDisk,
    UnknownOutcome,
    UnsupportedVersion,
    UnsupportedCapability,
    ReadOnly,
    UnsafeDestination,
    ApprovalRequired,
    NotFound,
    OperationalFailure,
}

impl ErrorCode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidInput => "invalid_input",
            Self::Configuration => "configuration",
            Self::Authentication => "authentication",
            Self::Forbidden => "forbidden",
            Self::Conflict => "conflict",
            Self::SourceUnavailable => "source_unavailable",
            Self::RateLimited => "rate_limited",
            Self::StorageUnavailable => "storage_unavailable",
            Self::IntegrityFailure => "integrity_failure",
            Self::IncompleteCoverage => "incomplete_coverage",
            Self::StaleCoverage => "stale_coverage",
            Self::LowDisk => "low_disk",
            Self::UnknownOutcome => "unknown_outcome",
            Self::UnsupportedVersion => "unsupported_version",
            Self::UnsupportedCapability => "unsupported_capability",
            Self::ReadOnly => "read_only",
            Self::UnsafeDestination => "unsafe_destination",
            Self::ApprovalRequired => "approval_required",
            Self::NotFound => "not_found",
            Self::OperationalFailure => "operational_failure",
        }
    }

    pub const fn exit_code(self) -> u8 {
        match self {
            Self::InvalidInput | Self::Configuration => 2,
            Self::Authentication | Self::Forbidden | Self::ApprovalRequired => 3,
            Self::Conflict | Self::ReadOnly | Self::UnsafeDestination => 4,
            Self::IncompleteCoverage | Self::StaleCoverage => 6,
            Self::UnsupportedVersion | Self::UnsupportedCapability => 7,
            _ => 5,
        }
    }
}
