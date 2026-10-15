//! Implement application ports for Git, GitHub, object storage, and SQLite.
//! Provider types stay inside this crate.
pub mod storage;

pub mod recovery;
pub mod status;

pub mod daemon;

pub mod archive_store;
